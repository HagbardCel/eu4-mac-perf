"""One-shot QBDI capture of the controlled x86-64 subject in this package.

The subject is not `_GfxSetConstantBuffer` and it has no EU4 binary. A later
live review has to refuse a 5-byte jump that cannot reach the EU4 entry from
the mapping that holds the hook. This module does not switch mechanisms and
does not launch EU4.
"""
from __future__ import annotations

import hashlib
import json
import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path

from reconstruction_trace import fixture, schema
from reconstruction_trace.tr3 import adapter, observe

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
SOURCES = ("subject.S", "intercept_stub.S", "probe.c", "native.py")
CFLAGS = ("-arch", "x86_64", "-O2", "-Wall", "-Wextra", "-Werror", "-mno-avx")
QBDI_PREFIX_ENV = "TR0_QBDI_PREFIX"
DEFAULT_QBDI_PREFIX = Path.home() / ".cache" / "eu4-mac-perf" / "qbdi" / "0.12.1"
FUNCTION_ID = "synth:tr3-oneshot"
EVIDENCE = "qbdi-rosetta"
RECEIPT_SCHEMA = "tr3-native-receipt/v1"
DECOY_ADDRESS = 0x100000000

MEMORY_READ = 1
MEMORY_WRITE = 2
MEMORY_UNKNOWN_SIZE = 1
MEMORY_MINIMUM_SIZE = 2
MEMORY_UNKNOWN_VALUE = 4
FLAG_NAMES = {
    MEMORY_UNKNOWN_SIZE: "MEMORY_UNKNOWN_SIZE",
    MEMORY_MINIMUM_SIZE: "MEMORY_MINIMUM_SIZE",
    MEMORY_UNKNOWN_VALUE: "MEMORY_UNKNOWN_VALUE",
}
ENTRY_REGISTERS = ("rax", "rbx", "rcx", "rdx", "rsi", "rdi", "r8", "r9", "r10", "r11",
                   "r12", "r13", "r14", "r15", "rbp", "rsp")
EXIT_REGISTERS = ("rax",)
SUBJECT_BYTES = 10
SUBJECT = ((0, 3, "MOV64rm"), (3, 3, "INC64r"), (6, 3, "MOV64mr"), (9, 1, "RET64"))
_DROP = "events dropped: {instructions} instructions, {accesses} accesses, {transfers} transfers"


def qbdi_prefix() -> Path:
    return Path(os.environ.get(QBDI_PREFIX_ENV, DEFAULT_QBDI_PREFIX)).expanduser()


def _x86_runner() -> list[str]:
    return [] if platform.machine() == "x86_64" else ["arch", "-x86_64"]


def unsupported_host_reason() -> str | None:
    if platform.system() != "Darwin":
        return "requires macOS"
    if shutil.which("clang") is None:
        return "clang not found"
    prefix = qbdi_prefix()
    if not (prefix / "lib" / "libQBDI.a").is_file() or not (prefix / "include" / "QBDI.h").is_file():
        return f"QBDI x86-64 release not found at {prefix}"
    probe = subprocess.run([*_x86_runner(), "/usr/bin/true"], capture_output=True, check=False)
    if probe.returncode != 0:
        return "host cannot execute x86-64 code (Rosetta unavailable)"
    return None


def _int(text: str) -> int:
    return int(text, 16)


def _le(value: int, size: int) -> str:
    return (value % (1 << (8 * size))).to_bytes(size, "little").hex()


def _flag_names(flags: int) -> list[str]:
    names = [name for bit, name in FLAG_NAMES.items() if flags & bit]
    unknown = flags & ~sum(FLAG_NAMES)
    if unknown:
        names.append(hex(unknown))
    return names or ["MEMORY_NO_FLAGS"]


def _region_of(regions: list[dict], address: int, size: int) -> dict | None:
    for region in regions:
        base = _int(region["base"])
        if base <= address and address + size <= base + region["size"]:
            return region
    return None


def reconcile(inst: dict) -> list[str]:
    """Problems that keep one executed instruction from being accounted for.

    Same fail-closed checks as the TR0 producer. Copied so this package does
    not import that module's private functions.
    """
    accesses = inst["accesses"]
    reads = [a for a in accesses if a["type"] == MEMORY_READ]
    writes = [a for a in accesses if a["type"] == MEMORY_WRITE]
    problems = []
    if inst["may_load"] and not reads:
        problems.append("mayLoad without a recorded read")
    if inst["may_store"] and not writes:
        problems.append("mayStore without a recorded write")
    if reads and not inst["may_load"]:
        problems.append("recorded read without mayLoad")
    if writes and not inst["may_store"]:
        problems.append("recorded write without mayStore")
    if inst["load_size"] and any(a["size"] != inst["load_size"] for a in reads):
        problems.append("read size differs from loadSize")
    if inst["store_size"] and any(a["size"] != inst["store_size"] for a in writes):
        problems.append("write size differs from storeSize")
    for access in accesses:
        if access["type"] not in (MEMORY_READ, MEMORY_WRITE):
            problems.append(f"unclassified access type {access['type']}")
        if access["flags"]:
            problems.append("QBDI flags " + "|".join(_flag_names(access["flags"])))
        if access["size"] <= 0:
            problems.append("non-positive access size")
        if access["inst_address"] != inst["pc"]:
            problems.append("access attributed to another instruction")
    return problems


def run_gaps(run: dict) -> list[str]:
    """Run-level gaps. Drop counts are named by ``observe.apply_accounting``."""
    gaps = []
    if run["failure"]:
        gaps.append(f"run failed: {run['failure']}")
    if not run["ran"]:
        gaps.append("QBDI executed no block")
    if not run["entered"]:
        gaps.append("entry state was not captured")
    if not run["memory_recording"]:
        gaps.append("QBDI memory access recording is unsupported")
    for target in run["exec_transfers"]:
        gaps.append(f"execution left the declared scope to {target}")
    return gaps


def _drop_reason(run: dict) -> str:
    return _DROP.format(instructions=run["dropped_instructions"],
                        accesses=run["dropped_accesses"],
                        transfers=run["dropped_transfers"])


def observation_from_run(run: dict, qbdi_version: str) -> dict:
    """Build one observation. A gap stays on the trace; it is not omitted."""
    regions = [
        {"id": r["id"], "kind": r["kind"], "base": r["base"], "size": r["size"],
         "bytes": r["bytes"], "preservation": "excluded" if r["kind"] == "stack" else "required"}
        for r in run["regions"]
    ]
    events: list[dict] = []
    reasons: list[str] = []
    unresolved: list[dict] = []
    frames: list[dict] = []
    for inst in run["instructions"]:
        problems = reconcile(inst)
        if problems:
            reasons.append(f"instruction {inst['pc']} ({inst['disassembly']}): "
                           + "; ".join(problems))
        for access in inst["accesses"]:
            kind = {MEMORY_READ: "read", MEMORY_WRITE: "write"}.get(access["type"])
            if kind is None:
                continue
            address, size, flags = _int(access["address"]), access["size"], access["flags"]
            if flags & (MEMORY_UNKNOWN_SIZE | MEMORY_MINIMUM_SIZE) or size <= 0:
                reasons.append(f"{kind} at {access['address']} by {inst['pc']} has no known size")
                continue
            event = {"kind": kind, "pc": inst["pc"], "address": access["address"], "size": size}
            if flags & MEMORY_UNKNOWN_VALUE == 0 and size <= 8:
                event["value"] = _le(_int(access["value"]), size)
                event["value_source"] = "observed"
            else:
                reasons.append(f"{kind} at {access['address']} by {inst['pc']} has no known value")
            events.append(event)
            if _region_of(regions, address, size) is None:
                if kind == "read":
                    unresolved.append({"address": access["address"], "size": size,
                                       "reason": "read outside the harness inventory"})
                else:
                    reasons.append(f"write outside the harness inventory at "
                                   f"{access['address']} size {size}")
        if inst["is_call"]:
            events.append({"kind": "call_enter", "pc": inst["pc"], "target": inst["next_pc"]})
            frames.append({"target": inst["next_pc"],
                           "return": hex(_int(inst["pc"]) + inst["inst_size"])})
        elif inst["is_return"] and frames and inst["next_pc"] == frames[-1]["return"]:
            events.append({"kind": "call_exit", "target": frames.pop()["target"],
                           "effects_complete": False})
    if frames:
        reasons.append("call_enter without call_exit")
    reasons.extend(run_gaps(run))
    for item in unresolved:
        reasons.append(f"{item['reason']} at {item['address']} size {item['size']}")
    trace = {
        "schema": "execution-observation/v1",
        "function_id": FUNCTION_ID,
        "binary": None,
        "evidence": EVIDENCE,
        "instrumentation": {"name": "QBDI", "version": qbdi_version},
        "entry_state": {
            "registers": {name: run["entry_gpr"][name] for name in ENTRY_REGISTERS},
            "regions": regions,
            "pointers": run["pointers"],
        },
        "events": events,
        "exit_state": {"registers": {name: run["exit_gpr"][name] for name in EXIT_REGISTERS}},
        "completeness": "complete",
        "incomplete_reasons": [],
        "unresolved_dependencies": unresolved,
    }
    traced = observe.apply_accounting(
        trace, dropped_instructions=run["dropped_instructions"],
        dropped_accesses=run["dropped_accesses"],
        dropped_transfers=run["dropped_transfers"])
    merged = list(traced["incomplete_reasons"])
    for reason in reasons:
        if reason not in merged:
            merged.append(reason)
    if run["dropped_instructions"] or run["dropped_accesses"] or run["dropped_transfers"]:
        drop = _drop_reason(run)
        if drop not in merged:
            merged.append(drop)
    traced["incomplete_reasons"] = merged
    traced["completeness"] = "incomplete" if merged or unresolved else "complete"
    traced["unresolved_dependencies"] = unresolved
    return traced


def subject_gap(raw: dict) -> str | None:
    """Reason the raw run is not the four instructions of this subject.

    ``ret`` leaves the function, so continuity is ``pc`` plus ``inst_size``,
    not ``next_pc``.
    """
    symbols = raw["symbols"]
    subject = _int(symbols["subject"])
    if _int(symbols["scope_start"]) != subject:
        return "scope does not start at the subject"
    if _int(symbols["scope_end"]) != subject + SUBJECT_BYTES:
        return "scope is not the 10-byte subject"
    instructions = raw["run"]["instructions"]
    if len(instructions) != len(SUBJECT):
        return f"{len(instructions)} instructions, expected {len(SUBJECT)}"
    cursor = subject
    for inst, (offset, size, mnemonic) in zip(instructions, SUBJECT):
        pc = _int(inst["pc"])
        if (pc != subject + offset or pc != cursor or inst["inst_size"] != size
                or inst["mnemonic"] != mnemonic):
            return f"{inst.get('mnemonic')} at {inst.get('pc')} is not {mnemonic} at +{offset}"
        cursor += inst["inst_size"]
    if cursor != subject + SUBJECT_BYTES:
        return "instructions do not cover the 10-byte subject"
    return None


def _first_line(command: list[str]) -> str:
    out = subprocess.run(command, capture_output=True, text=True, check=False).stdout
    return out.splitlines()[0].strip() if out else ""


def _portable(command: list[str]) -> list[str]:
    root, home = str(ROOT) + "/", str(Path.home())
    return [part.replace(root, "").replace(home, "$HOME") for part in command]


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def build(build_dir: Path) -> tuple[Path, list[str]]:
    prefix = qbdi_prefix()
    build_dir.mkdir(parents=True, exist_ok=True)
    exe = build_dir / "tr3_qbdi_probe"
    command = ["clang", *CFLAGS, "-I", str(prefix / "include"), "-o", str(exe),
               str(HERE / "probe.c"), str(HERE / "subject.S"), str(HERE / "intercept_stub.S"),
               str(prefix / "lib" / "libQBDI.a"), "-lc++"]
    subprocess.run(command, check=True)
    return exe, command


def run_probe(exe: Path) -> tuple[dict, list[str]]:
    command = [*_x86_runner(), str(exe)]
    proc = subprocess.run(command, capture_output=True, text=True, check=False)
    if proc.returncode != 0:
        raise RuntimeError(f"probe exited {proc.returncode}: {proc.stderr.strip()}")
    return json.loads(proc.stdout), command


def capture(build_dir: Path) -> tuple[dict, dict]:
    """Run the subject once and return the observation and the diagnostic receipt."""
    reason = unsupported_host_reason()
    if reason:
        raise RuntimeError(reason)
    exe, build_command = build(build_dir)
    raw, run_command = run_probe(exe)
    if raw["proc_translated"] != 1:
        raise RuntimeError(f"sysctl.proc_translated is {raw['proc_translated']}, not 1")
    gap = subject_gap(raw)
    if gap:
        raise RuntimeError(gap)
    observation = observation_from_run(raw["run"], raw["qbdi_version"])
    schema.validate(observation)
    if observation["completeness"] != "complete":
        raise RuntimeError("incomplete capture: " + "; ".join(observation["incomplete_reasons"]))
    qualified = fixture.qualify(observation)
    if not qualified["fixture_qualified"]:
        raise RuntimeError("subject trace did not qualify: " + "; ".join(qualified["reasons"]))
    prepared = adapter.prepare_scenario(observation)
    if prepared["refusal"] != "unauthenticated-target":
        raise RuntimeError(f"controlled subject was not refused: {prepared['refusal']}")
    hook = raw["hook"]
    if (hook["count"] != 1 or hook["qbdi_invocations"] != 1 or hook["native_call_count"] != 2
            or not hook["restored_before_qbdi"] or not hook["entry_bytes_match_after"]):
        raise RuntimeError(f"one-shot counters were not met: {hook}")
    receipt = {
        "schema": RECEIPT_SCHEMA,
        "work_order": "TR3",
        "contract": "execution-observation/v1",
        "evidence": EVIDENCE,
        "function_id": FUNCTION_ID,
        "binary": None,
        "decoy_address": raw["decoy_address"],
        "host": {
            "probe_architecture": "x86_64",
            "qbdi_architecture": raw["qbdi_architecture"],
            "rosetta": {"sysctl.proc_translated": raw["proc_translated"],
                        "status": "translated"},
        },
        "toolchain": {"clang": _first_line(["clang", "--version"]), "cflags": list(CFLAGS)},
        "qbdi": {"version": raw["qbdi_version"], "architecture": raw["qbdi_architecture"],
                 "platform": raw["qbdi_platform"]},
        "probe": {
            "sources_sha256": {name: _sha256(HERE / name) for name in SOURCES},
            "executable_sha256": _sha256(exe),
        },
        "commands": {"build": _portable(build_command), "run": _portable(run_command)},
        "hook": hook,
        "drops": {
            "instructions": raw["run"]["dropped_instructions"],
            "accesses": raw["run"]["dropped_accesses"],
            "transfers": raw["run"]["dropped_transfers"],
        },
        "completeness": observation["completeness"],
        "probe_output": raw,
    }
    return observation, receipt


def write_capture(out_dir: Path, build_dir: Path) -> dict:
    observation, receipt = capture(build_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    observation_path = out_dir / "observation.json"
    observation_path.write_text(json.dumps(observation, indent=2) + "\n")
    receipt["observation_sha256"] = _sha256(observation_path)
    (out_dir / "receipt.json").write_text(json.dumps(receipt, indent=2) + "\n")
    return receipt


def main(argv: list[str] | None = None) -> int:
    import argparse
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", type=Path, default=HERE)
    parser.add_argument("--build-dir", type=Path,
                        default=Path.home() / ".cache" / "eu4-mac-perf" / "tr3-build")
    args = parser.parse_args(argv)
    reason = unsupported_host_reason()
    if reason:
        print(f"TR3 probe unsupported on this host: {reason}", file=sys.stderr)
        return 2
    receipt = write_capture(args.out, args.build_dir)
    print(receipt["completeness"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
