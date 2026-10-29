#!/usr/bin/env python3
"""TR0 producer: build the x86-64 probe, run it under QBDI on Rosetta, and write
``execution-observation/v1`` traces plus the auditable receipt.

    python3 -m reconstruction_trace.qbdi.producer --out reconstruction_trace/qbdi

QBDI is not vendored. Set ``TR0_QBDI_PREFIX`` to an extracted official release
(the ``usr/local`` payload of ``QBDI_PACKAGE``); the default is under ~/.cache.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import shutil
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
QBDI_PREFIX_ENV = "TR0_QBDI_PREFIX"
DEFAULT_QBDI_PREFIX = Path.home() / ".cache" / "eu4-mac-perf" / "qbdi" / "0.12.1"
QBDI_PACKAGE = {
    "name": "QBDI-0.12.1-osx-X86_64.pkg",
    "url": "https://github.com/QBDI/QBDI/releases/download/v0.12.1/QBDI-0.12.1-osx-X86_64.pkg",
    "sha256": "5fc095acd69f0417db9fad58e75061d391413301d6a19d6d39636d1199d9c7d3",
}
SOURCES = ("probe_subject.h", "probe_subject.c", "probe_main.c", "intercept_stub.S")
CFLAGS = ("-arch", "x86_64", "-O2", "-Wall", "-Wextra", "-Werror", "-mno-avx")
SCOPE_SECTION = "__TEXT,__tr0_scope"
FUNCTION_ID = "synth:tr0_chase"
EVIDENCE = "qbdi-rosetta"
RECEIPT_SCHEMA = "tr0-qbdi-receipt/v1"
MODES = ("vm_call", "interception")

# Declared known-answer inputs. The harness allocates heap0 (head), heap1,
# heap2 (tail) and heap3 (out slot) in that order; see build_inventory().
HEAD_VALUE = 3
TAIL_VALUE = 50

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

OUTCOMES = (
    "Reliable Rosetta traces with explicit gaps: continue with QBDI.",
    "Incomplete values or sizes: those traces stay `incomplete`.",
    "Offline QBDI works and natural-call interception does not: keep QBDI for offline "
    "experiments. Report interception as unsupported. Do not substitute Ghidra traces "
    "and call TR0 complete.",
    "QBDI cannot run under Rosetta: report native instrumentation as unsupported and amend "
    "the milestone. The QBDI-to-fixture integration is blocked, not passed.",
    "A callee or environment that cannot be replayed: record an unresolved dependency and "
    "do not build a fixture.",
)

QBDI_LIMITS = {
    "threads": "A QBDI VM traces only the thread that calls run/call; other threads run "
               "natively. run, call, setOptions and precacheBasicBlock are not reentrant "
               "and must not be called from a callback (QBDI 0.12.1 "
               "docs/source/api_description.rst, 'User callbacks'). Not exercised: the "
               "probe is single-threaded.",
    "signals": "VMEvent SIGNAL, SYSCALL_ENTRY, SYSCALL_EXIT and VMState.lastSignal are "
               "'Not implemented' (QBDI 0.12.1 include/QBDI/Callback.h). A signal handler "
               "is not traced. Not exercised.",
    "exceptions": "The ExecBroker does not support any exception mechanism, including "
                  "setjmp/longjmp (QBDI 0.12.1 docs/source/api_description.rst, "
                  "'Instrumentation ranges'). Not exercised.",
    "shared_process": "Host and guest share heap and libraries; non-reentrant code such as "
                      "the allocator must stay outside the instrumented range (QBDI 0.12.1 "
                      "docs/source/intro.rst, 'Limitations'). The declared scope holds only "
                      "the probe subject.",
    "memory_access_coverage": "x86-64 memory-access information is missing for AVX512, "
                              "CET, HLE/RTM, MPX, APX, XOP and VGATHER*/VPGATHER* "
                              "(QBDI 0.12.1 docs/source/architecture_support.rst).",
}

INTERCEPTION_MECHANISM = (
    "5-byte jmp rel32 at tr0_chase entry to intercept_stub.S; the stub saves the natural "
    "caller's GPRs and FXSAVE state, switches to a private host stack, removes the hook, "
    "and qbdi_run executes from the entry to the caller's return address on the caller's "
    "own stack; the hook is reinstalled and the caller resumes from QBDI's exit state. "
    "This is QBDI's documented hook-then-run pattern without Frida."
)
INTERCEPTION_LIMITS = (
    "The hook is removed while QBDI runs, so a concurrent call from another thread runs "
    "natively and untraced.",
    "A recursive call into the hooked function during the run is traced by the same VM, "
    "not intercepted again.",
    "Unwinding or longjmp through the stub is unsupported.",
)


class HostUnsupported(RuntimeError):
    """The host cannot produce a ``qbdi-rosetta`` observation."""


# ---- encoding ---------------------------------------------------------------


def _int(text: str) -> int:
    return int(text, 16)


def _le(value: int, size: int) -> str:
    return (value % (1 << (8 * size))).to_bytes(size, "little").hex()


def flag_names(flags: int) -> list[str]:
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


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


# ---- declared expectations --------------------------------------------------


def declared_accesses() -> list[dict]:
    """Known-answer target accesses, relative to the harness allocations."""
    product = TAIL_VALUE * HEAD_VALUE if TAIL_VALUE > 40 else TAIL_VALUE
    return [
        {"label": "head->next", "kind": "read", "region": "heap0", "offset": 0, "size": 8,
         "value": {"pointer_to": "heap1", "offset": 0}},
        {"label": "head->next->next", "kind": "read", "region": "heap1", "offset": 0,
         "size": 8, "value": {"pointer_to": "heap2", "offset": 0}},
        {"label": "tail->value", "kind": "read", "region": "heap2", "offset": 8, "size": 8,
         "value": {"integer": TAIL_VALUE}},
        {"label": "helper reads *factor", "kind": "read", "region": "heap0", "offset": 8,
         "size": 8, "value": {"integer": HEAD_VALUE}, "inside_helper_call": True},
        {"label": "*out", "kind": "write", "region": "heap3", "offset": 0, "size": 8,
         "value": {"integer": product}},
    ]


def expected_result() -> int:
    return TAIL_VALUE * HEAD_VALUE if TAIL_VALUE > 40 else TAIL_VALUE


def _resolve_declared(regions: list[dict]) -> list[dict]:
    bases = {region["id"]: _int(region["base"]) for region in regions}
    resolved = []
    for item in declared_accesses():
        value = item["value"]
        number = (bases[value["pointer_to"]] + value["offset"]) if "pointer_to" in value \
            else value["integer"]
        resolved.append({
            "kind": item["kind"],
            "address": hex(bases[item["region"]] + item["offset"]),
            "size": item["size"],
            "value": _le(number, item["size"]),
        })
    return resolved


# ---- evaluation of the raw probe output ------------------------------------


def _reconcile(inst: dict) -> list[str]:
    """Problems that keep one executed instruction from being accounted for."""
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
            problems.append("QBDI flags " + "|".join(flag_names(access["flags"])))
        if access["inst_address"] != inst["pc"]:
            problems.append("access attributed to another instruction")
    return problems


def _run_gaps(run: dict) -> list[str]:
    gaps = []
    if run["failure"]:
        gaps.append(f"run failed: {run['failure']}")
    if not run["ran"]:
        gaps.append("QBDI executed no block")
    if not run["entered"]:
        gaps.append("entry state was not captured")
    if not run["memory_recording"]:
        gaps.append("QBDI memory access recording is unsupported")
    if run["dropped_instructions"] or run["dropped_accesses"] or run["dropped_transfers"]:
        gaps.append("events dropped: "
                    f"{run['dropped_instructions']} instructions, "
                    f"{run['dropped_accesses']} accesses, "
                    f"{run['dropped_transfers']} transfers")
    for target in run["exec_transfers"]:
        gaps.append(f"execution left the declared scope to {target}")
    return gaps


def build_observation(run: dict, qbdi_version: str) -> dict:
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
        problems = _reconcile(inst)
        if problems:
            reasons.append(f"instruction {inst['pc']} ({inst['disassembly']}): "
                           + "; ".join(problems))
        for access in inst["accesses"]:
            kind = {MEMORY_READ: "read", MEMORY_WRITE: "write"}.get(access["type"])
            if kind is None:
                continue
            address, size, flags = _int(access["address"]), access["size"], access["flags"]
            event = {"kind": kind, "pc": inst["pc"], "address": access["address"], "size": size}
            if flags & (MEMORY_UNKNOWN_SIZE | MEMORY_MINIMUM_SIZE | MEMORY_UNKNOWN_VALUE) == 0 \
                    and size <= 8:
                event["value"] = _le(_int(access["value"]), size)
                event["value_source"] = "observed"
            else:
                reasons.append(f"{kind} at {access['address']} by {inst['pc']} has no known "
                               "value or size")
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
    reasons.extend(_run_gaps(run))
    return {
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
        "completeness": "incomplete" if reasons or unresolved else "complete",
        "incomplete_reasons": reasons,
        "unresolved_dependencies": unresolved,
    }


def _memory_results(run: dict, observation: dict) -> dict:
    regions = observation["entry_state"]["regions"]
    target, compiler, unclassified = [], [], []
    for event in observation["events"]:
        if event["kind"] not in ("read", "write"):
            continue
        region = _region_of(regions, _int(event["address"]), event["size"])
        bucket = unclassified if region is None else \
            compiler if region["kind"] == "stack" else target
        bucket.append(event)
    declared = _resolve_declared(regions)
    observed = [{k: e.get(k) for k in ("kind", "address", "size", "value")} for e in target]

    insts = run["instructions"]
    unreconciled = [{"pc": i["pc"], "disassembly": i["disassembly"], "problems": p}
                    for i in insts if (p := _reconcile(i))]
    flagged = [{"pc": i["pc"], "address": a["address"], "size": a["size"], "type": a["type"],
                "flags": a["flags"], "flag_names": flag_names(a["flags"])}
               for i in insts for a in i["accesses"] if a["flags"]]
    gaps = _run_gaps(run)
    memory_insts = [i for i in insts if i["may_load"] or i["may_store"]]
    complete = bool(insts) and not unreconciled and not flagged and not gaps

    blockers = ["TR0 builds no fixture: qualification belongs to reconstruction_trace/fixture.py "
                "(TR1) and the QBDI-to-fixture integration (TR2)"]
    if observation["completeness"] != "complete":
        blockers.append("trace is incomplete")
    return {
        "expected_accesses_match": {
            "value": observed == declared,
            "declared": declared,
            "observed_target_accesses": observed,
            "compiler_generated_accesses": {
                "count": len(compiler),
                "reads": sum(e["kind"] == "read" for e in compiler),
                "writes": sum(e["kind"] == "write" for e in compiler),
            },
            "unclassified_accesses": len(unclassified),
        },
        "instrumentation_complete": {
            "value": complete,
            "executed_instructions": len(insts),
            "memory_instructions": len(memory_insts),
            "recorded_accesses": sum(len(i["accesses"]) for i in insts),
            "unreconciled_instructions": unreconciled,
            "flagged_accesses": flagged,
            "run_gaps": gaps,
        },
        "fixture_qualified": {"value": False, "blocked_by": blockers},
    }


def _call_boundary(observation: dict, helper: str) -> dict:
    events = observation["events"]
    enters = [i for i, e in enumerate(events) if e["kind"] == "call_enter"]
    exits = [i for i, e in enumerate(events) if e["kind"] == "call_exit"]
    result = {"result": "unsupported", "helper_target": helper}
    if len(enters) != 1 or len(exits) != 1 or exits[0] < enters[0]:
        result["reason"] = (f"call-boundary unsupported: {len(enters)} call_enter and "
                            f"{len(exits)} call_exit events")
        return result
    enter, leave = events[enters[0]], events[exits[0]]
    nested = events[enters[0] + 1:exits[0]]
    regions = observation["entry_state"]["regions"]
    declared = _resolve_declared(regions)
    inside = [d for item, d in zip(declared_accesses(), declared) if item.get("inside_helper_call")]
    nested_memory = [{k: e.get(k) for k in ("kind", "address", "size", "value")}
                     for e in nested if e["kind"] in ("read", "write")]
    result.update({
        "call_enter": enter,
        "call_exit": leave,
        "nested_events": len(nested),
        "declared_nested_accesses_inside": all(d in nested_memory for d in inside),
    })
    if enter["target"] != helper:
        result["reason"] = f"call-boundary unsupported: resolved target {enter['target']}"
    elif leave["target"] != enter["target"]:
        result["reason"] = "call-boundary unsupported: call_exit target differs"
    elif not result["declared_nested_accesses_inside"]:
        result["reason"] = "call-boundary unsupported: helper access outside the frame"
    else:
        result["result"] = "supported"
        result["reason"] = None
    return result


def _interception(raw: dict, runs: dict) -> dict:
    run = next(r for r in raw["runs"] if r["mode"] == "interception")
    vm_call = next(r for r in raw["runs"] if r["mode"] == "vm_call")
    same_path = [i["pc"] for i in run["instructions"]] == \
        [i["pc"] for i in vm_call["instructions"]]
    checks = {
        "natural_call_entered_qbdi": run["entered"] and run["ran"],
        "caller_resumed_with_expected_result": _int(run["result"]) == expected_result()
        and run["out_value"] == expected_result(),
        "hook_removed_after_call": raw["hook"]["removed"] and not raw["hook"]["installed_at_exit"],
        "same_instruction_path_as_vm_call": same_path and bool(run["instructions"]),
    }
    supported = run["failure"] is None and all(checks.values())
    return {
        "result": "supported" if supported else "unsupported",
        "reason": None if supported else (run["failure"] or "failed checks: " + ", ".join(
            name for name, ok in checks.items() if not ok)),
        "mechanism": INTERCEPTION_MECHANISM,
        "checks": checks,
        "limits": list(INTERCEPTION_LIMITS),
        "trace_completeness": runs["interception"]["completeness"],
    }


def _has_value_or_size_gap(run: dict, observation: dict) -> bool:
    complete = run["results"]["instrumentation_complete"]
    return bool(complete["unreconciled_instructions"] or complete["flagged_accesses"]) or any(
        e["kind"] in ("read", "write") and "value" not in e for e in observation["events"])


def _decision(raw: dict, observations: dict, runs: dict, interception: dict) -> str:
    vm_call = next(r for r in raw["runs"] if r["mode"] == "vm_call")
    if raw["proc_translated"] != 1 or not (vm_call["ran"] and vm_call["entered"]
                                           and vm_call["memory_recording"]
                                           and vm_call["instructions"]):
        return OUTCOMES[3]
    if any(_has_value_or_size_gap(runs[mode], observations[mode]) for mode in runs):
        return OUTCOMES[1]
    if any(r["unresolved_dependencies"] for r in runs.values()) or any(
            r["exec_transfers"] for r in raw["runs"]):
        return OUTCOMES[4]
    if interception["result"] != "supported":
        return OUTCOMES[2]
    return OUTCOMES[0]


def evaluate(raw: dict) -> dict:
    """Observations, the separate results and the decision for one probe output."""
    observations, per_run = {}, {}
    for run in raw["runs"]:
        observation = build_observation(run, raw["qbdi_version"])
        observations[run["mode"]] = observation
        per_run[run["mode"]] = {
            "completeness": observation["completeness"],
            "incomplete_reasons": observation["incomplete_reasons"],
            "unresolved_dependencies": observation["unresolved_dependencies"],
            "results": _memory_results(run, observation),
            "call_boundary": _call_boundary(observation, raw["symbols"]["tr0_scale"]),
            "wall_ns": run["wall_ns"],
        }
    interception = _interception(raw, per_run)
    return {
        "observations": observations,
        "runs": per_run,
        "interception": interception,
        "decision": _decision(raw, observations, per_run, interception),
    }


# ---- host, build and run ----------------------------------------------------


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
        return (f"QBDI x86-64 release not found at {prefix} (set {QBDI_PREFIX_ENV} to the "
                f"extracted usr/local of {QBDI_PACKAGE['name']})")
    probe = subprocess.run([*_x86_runner(), "/usr/bin/true"], capture_output=True, check=False)
    if probe.returncode != 0:
        return "host cannot execute x86-64 code (Rosetta unavailable)"
    return None


def build(build_dir: Path) -> tuple[Path, list[str]]:
    prefix = qbdi_prefix()
    build_dir.mkdir(parents=True, exist_ok=True)
    exe = build_dir / "tr0_qbdi_probe"
    command = ["clang", *CFLAGS, "-I", str(prefix / "include"), "-o", str(exe),
               *(str(HERE / s) for s in SOURCES if s.endswith((".c", ".S"))),
               str(prefix / "lib" / "libQBDI.a"), "-lc++"]
    subprocess.run(command, check=True)
    return exe, command


def run_probe(exe: Path) -> tuple[dict, list[str], int]:
    command = [*_x86_runner(), str(exe), str(HEAD_VALUE), str(TAIL_VALUE)]
    started = time.perf_counter_ns()
    proc = subprocess.run(command, capture_output=True, text=True, check=False)
    wall = time.perf_counter_ns() - started
    if proc.returncode != 0:
        raise RuntimeError(f"probe exited {proc.returncode}: {proc.stderr.strip()}")
    return json.loads(proc.stdout), command, wall


def _first_line(command: list[str]) -> str:
    out = subprocess.run(command, capture_output=True, text=True, check=False).stdout
    return out.splitlines()[0].strip() if out else ""


def _portable(command: list[str]) -> list[str]:
    """Paths relative to the repository root, and $HOME for the user's home."""
    root, home = str(HERE.parents[1]) + "/", str(Path.home())
    return [part.replace(root, "").replace(home, "$HOME") for part in command]


def _write_json(path: Path, data: dict) -> None:
    path.write_text(json.dumps(data, indent=2) + "\n")


def produce(out_dir: Path, build_dir: Path) -> dict:
    exe, build_command = build(build_dir)
    raw, run_command, process_wall_ns = run_probe(exe)
    if raw["proc_translated"] != 1:
        raise HostUnsupported("sysctl.proc_translated is not 1: the probe did not run under "
                              f"Rosetta ({raw['proc_translated']}), so it cannot be "
                              f"'{EVIDENCE}' evidence")
    evaluation = evaluate(raw)
    out_dir.mkdir(parents=True, exist_ok=True)
    runs = {}
    for mode in MODES:
        name = f"observation-{mode}.json"
        _write_json(out_dir / name, evaluation["observations"][mode])
        runs[mode] = {"observation": name, "observation_sha256": sha256_file(out_dir / name),
                      **evaluation["runs"][mode]}
    by_mode = {r["mode"]: r for r in raw["runs"]}
    receipt = {
        "schema": RECEIPT_SCHEMA,
        "work_order": "TR0",
        "contract": "execution-observation/v1",
        "evidence": EVIDENCE,
        "host": {
            "machine": platform.machine(),
            "macos": _first_line(["sw_vers", "-productVersion"]),
            "probe_architecture": "x86_64",
            "rosetta": {"sysctl.proc_translated": raw["proc_translated"],
                        "status": "translated"},
        },
        "toolchain": {"clang": _first_line(["clang", "--version"]), "cflags": list(CFLAGS)},
        "qbdi": {
            "version": raw["qbdi_version"],
            "architecture": raw["qbdi_architecture"],
            "platform": raw["qbdi_platform"],
            "package": QBDI_PACKAGE,
            "linkage": "static lib/libQBDI.a from the package payload",
        },
        "probe": {
            "sources_sha256": {s: sha256_file(HERE / s) for s in SOURCES},
            "executable_sha256": sha256_file(exe),
            "subject": "tr0_chase",
            "helper": "tr0_scale",
            "instrumentation_scope": {"section": SCOPE_SECTION,
                                      "start": raw["symbols"]["scope_start"],
                                      "end": raw["symbols"]["scope_end"]},
        },
        "commands": {
            "producer": "python3 -m reconstruction_trace.qbdi.producer --out "
                        "reconstruction_trace/qbdi",
            "build": _portable(build_command),
            "run": _portable(run_command),
        },
        "declared_expectations": {
            "inputs": {"head_value": HEAD_VALUE, "tail_value": TAIL_VALUE},
            "target_accesses": declared_accesses(),
            "exit_rax": hex(expected_result()),
            "native_result": raw["native"]["result"],
        },
        "runs": runs,
        "interception": evaluation["interception"],
        "measurements": {
            "process_wall_ns": process_wall_ns,
            "native_call_ns": raw["native"]["wall_ns"],
            "vm_call_ns": by_mode["vm_call"]["wall_ns"],
            "interception_call_ns": by_mode["interception"]["wall_ns"],
            "peak_rss_bytes": raw["max_rss_bytes"],
            "slowdown_threshold": None,
        },
        "qbdi_limits": QBDI_LIMITS,
        "decision": evaluation["decision"],
        "probe_output": raw,
    }
    _write_json(out_dir / "receipt.json", receipt)
    return receipt


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", type=Path, default=HERE)
    parser.add_argument("--build-dir", type=Path,
                        default=Path.home() / ".cache" / "eu4-mac-perf" / "tr0-build")
    args = parser.parse_args(argv)
    reason = unsupported_host_reason()
    if reason:
        print(f"TR0 probe unsupported on this host: {reason}", file=sys.stderr)
        return 2
    receipt = produce(args.out, args.build_dir)
    print(receipt["decision"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
