"""Build and drive the synthetic x86-64 invocation harness (TRACE_V1.md, Native replay).

Every ``record`` or ``replay`` is a fresh harness process. ``record`` is the
reference run: it constructs the declared allocations, writes the pointer edges
it was asked to build, calls the original subject and returns the generic entry
inventory and exit observations. ``replay`` materializes a fixture at new
bases, rewrites only the fixture's kept edges, and calls the same subject.
``compare`` checks a replay against the fixture.

macOS only, like the repository's other native harnesses: an x86-64 host runs
the binary directly, Apple Silicon runs it under Rosetta.
"""
from __future__ import annotations

import platform
import shutil
import subprocess
from pathlib import Path

HERE = Path(__file__).resolve().parent
SOURCES = ("harness.c", "entry.S", "subjects.S")
REGISTERS = ("rax", "rbx", "rcx", "rdx", "rsi", "rdi", "rbp", "rsp",
             "r8", "r9", "r10", "r11", "r12", "r13", "r14", "r15")
SUBJECT_PREFIX = "synth:"


class Unavailable(RuntimeError):
    """This host cannot build or execute an x86-64 harness."""


class HarnessError(RuntimeError):
    """The harness process refused the request or did not exit cleanly."""


def _toolchain() -> tuple[list[str], list[str]]:
    if platform.system() != "Darwin":
        raise Unavailable("the synthetic harness is Mach-O x86-64 and needs macOS")
    if shutil.which("clang") is None:
        raise Unavailable("clang is not on PATH")
    if platform.machine().lower() in ("x86_64", "amd64"):
        return ["-arch", "x86_64"], []
    return ["-arch", "x86_64"], ["arch", "-x86_64"]


def load(workdir: Path) -> "Harness":
    """Build the harness into ``workdir`` and prove the host can execute it."""
    flags, runner = _toolchain()
    binary = Path(workdir) / "trace_harness"
    subprocess.run(
        ["clang", *flags, "-O2", "-Wall", "-Wextra", "-Werror", "-o", str(binary),
         *(str(HERE / source) for source in SOURCES)],
        check=True,
    )
    try:
        probe = subprocess.run([*runner, str(binary), "probe"], capture_output=True,
                               text=True, timeout=60)
    except OSError as error:
        raise Unavailable(f"x86-64 execution unavailable: {error}") from error
    if probe.returncode != 0 or probe.stdout != "ok\n":
        raise Unavailable(f"x86-64 execution unavailable: {probe.stderr.strip()}")
    return Harness(binary, runner)


def _subject(function_id: str) -> str:
    if not function_id.startswith(SUBJECT_PREFIX):
        raise HarnessError(f"{function_id!r} is not a synthetic subject")
    return function_id[len(SUBJECT_PREFIX):]


def _edge_line(edge: dict, index: dict[str, int]) -> str:
    source, target = edge["from"], edge["to"]
    if "register" in source:
        head = f"edge register {source['register']}"
    else:
        head = f"edge region {index[source['region']]} {source['offset']}"
    return f"{head} {index[target['region']]} {target['offset']}"


class Harness:
    def __init__(self, binary: Path, runner: list[str]):
        self.binary = binary
        self.runner = runner

    def _run(self, lines: list[str], ids: list[str]) -> dict:
        process = subprocess.run([*self.runner, str(self.binary)],
                                 input="\n".join([*lines, "end", ""]),
                                 capture_output=True, text=True, timeout=60)
        if process.returncode != 0 or not process.stdout.endswith("ok\n"):
            raise HarnessError(f"harness exited {process.returncode}: "
                               f"{process.stderr.strip()}")
        result: dict = {"bases": {}, "entry": {}, "entry_registers": {},
                        "registers": {}, "regions": {}}
        for line in process.stdout.splitlines()[:-1]:
            label, key, value = line.split()
            if label in ("base", "entry", "region"):
                key = ids[int(key)]
            if label == "base":
                result["bases"][key] = int(value, 16)
            elif label == "entry_register":
                result["entry_registers"][key] = value
            elif label == "register":
                result["registers"][key] = value
            else:
                result[{"entry": "entry", "region": "regions"}[label]][key] = value
        return result

    def record(self, function_id: str, regions: list[dict], pointers: list[dict],
               registers: dict[str, str], observe: list[str]) -> dict:
        """Reference run: return the generic entry inventory and the exit observations.

        ``regions`` are construction requests (``id``, ``kind``, ``size``, ``bytes``);
        the harness chooses the bases and writes ``pointers`` with real addresses.
        """
        ids = [region["id"] for region in regions]
        index = {region_id: number for number, region_id in enumerate(ids)}
        lines = [f"subject {_subject(function_id)}"]
        lines += [f"region {r['kind']} {r['size']} 0x0 {r['bytes']}" for r in regions]
        lines += [_edge_line(edge, index) for edge in pointers]
        lines += [f"register {name} {value}" for name, value in registers.items()]
        lines += [f"observe {name}" for name in observe]
        result = self._run(lines, ids)
        entry_regions = [
            {"id": r["id"], "kind": r["kind"], "base": hex(result["bases"][r["id"]]),
             "size": r["size"], "bytes": result["entry"][r["id"]],
             "preservation": "required"}
            for r in regions
        ]
        return {
            "entry_state": {"registers": result["entry_registers"],
                            "regions": entry_regions, "pointers": list(pointers)},
            "exit_state": {
                "registers": result["registers"],
                "regions": [{"id": r["id"], "base": r["base"], "size": r["size"],
                             "bytes": result["regions"][r["id"]]} for r in entry_regions],
            },
        }

    def replay(self, fixture: dict) -> dict:
        """Fresh-process replay of ``fixture`` at bases away from every original range."""
        ids = [region["id"] for region in fixture["regions"]]
        index = {region_id: number for number, region_id in enumerate(ids)}
        lines = [f"subject {_subject(fixture['function_id'])}", "avoid"]
        lines += [f"region {r['kind']} {r['size']} {r['base']} {r.get('bytes', '-')}"
                  for r in fixture["regions"]]
        lines += [_edge_line(edge, index) for edge in fixture["pointers"]]
        lines += [f"register {name} {value}"
                  for name, value in fixture["registers"].items()]
        lines += [f"observe {name}" for name in fixture["exit_registers"]]
        return self._run(lines, ids)


def compare(fixture: dict, replayed: dict) -> list[str]:
    """Mismatches between a replay and the fixture's expected observations."""
    mismatches = []
    regions = {region["id"]: region for region in fixture["regions"]}
    for region in fixture["regions"]:
        original = int(region["base"], 16)
        new = replayed["bases"][region["id"]]
        if new < original + region["size"] and original < new + region["size"]:
            mismatches.append(f"region {region['id']} was not moved to a new base")

    expected = {region_id: bytearray.fromhex(region["bytes"]) if "bytes" in region
                else bytearray(region["size"]) for region_id, region in regions.items()}
    for edge in fixture["pointers"]:
        source, target = edge["from"], edge["to"]
        if "region" in source:
            value = replayed["bases"][target["region"]] + target["offset"]
            start = source["offset"]
            expected[source["region"]][start:start + 8] = value.to_bytes(8, "little")
    written: dict[str, set[int]] = {region_id: set() for region_id in regions}
    for effect in fixture["effects"]:
        start = effect["offset"]
        written[effect["region"]].update(range(start, start + effect["size"]))
        expected[effect["region"]][start:start + effect["size"]] = (
            bytes.fromhex(effect["bytes"]))

    for region_id, region in regions.items():
        final = bytes.fromhex(replayed["regions"][region_id])
        data = expected[region_id]
        whole = "bytes" in region and region.get("preservation", "required") == "required"
        for at in range(region["size"]) if whole else sorted(written[region_id]):
            if final[at] != data[at]:
                mismatches.append(f"region {region_id} differs at offset {at}")
                break
    for name, value in fixture["exit_registers"].items():
        if replayed["registers"].get(name) != value:
            mismatches.append(f"register {name} is {replayed['registers'].get(name)}, "
                              f"expected {value}")
    return mismatches
