#!/usr/bin/env python3
"""Per-thread CPU counters for epoch A (libproc default on macOS)."""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import cpu_delta
import eu4_benchmark as base

ROOT = Path(__file__).resolve().parents[1]
_SPIN_WORKER_SOURCE = ROOT / "tests/sample_spin_worker.c"
_SPIN_WORKER_BINARY = ROOT / "benchmark/.build/sample_spin_worker"

_SELECTED_BACKEND: str | None = None


@dataclass
class ThreadSample:
    thread_id: int
    user_us: int
    system_us: int

    @property
    def total_us(self) -> int:
        return self.user_us + self.system_us


@dataclass
class ThreadSnapshot:
    samples: list[ThreadSample]
    diagnostics: dict[str, int]


def empty_diagnostics() -> dict[str, int]:
    return {
        "threads_enumerated": 0,
        "threads_sampled_ok": 0,
        "threads_skipped_esrch": 0,
    }


def selected_backend() -> str:
    global _SELECTED_BACKEND
    if _SELECTED_BACKEND is None:
        _SELECTED_BACKEND = resolve_backend()
    return _SELECTED_BACKEND


def resolve_backend() -> str:
    if sys.platform != "darwin":
        raise base.BenchmarkError("thread CPU sampling requires macOS")
    value = os.environ.get("EU4_THREAD_CPU_BACKEND", "libproc").strip().lower()
    if value not in ("libproc", "mach"):
        raise base.BenchmarkError(f"invalid EU4_THREAD_CPU_BACKEND: {value!r}")
    return value


def set_session_backend(value: str | None = None) -> str:
    """Resolve and pin backend for a measurement session."""
    global _SELECTED_BACKEND
    if value is None:
        _SELECTED_BACKEND = resolve_backend()
    else:
        normalized = value.strip().lower()
        if normalized not in ("libproc", "mach"):
            raise base.BenchmarkError(f"invalid thread CPU backend: {value!r}")
        _SELECTED_BACKEND = normalized
    return _SELECTED_BACKEND


def sample_snapshot(pid: int) -> ThreadSnapshot:
    backend = selected_backend()
    if backend == "mach":
        import thread_cpu_mach

        return thread_cpu_mach.sample_snapshot_mach(pid)
    import thread_cpu_libproc

    return thread_cpu_libproc.sample_snapshot_libproc(pid)


def sample_threads(pid: int) -> list[ThreadSample]:
    return sample_snapshot(pid).samples


def _build_spin_worker() -> Path:
    _SPIN_WORKER_BINARY.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            "clang",
            "-O2",
            "-Wall",
            "-Wextra",
            "-Werror",
            "-o",
            str(_SPIN_WORKER_BINARY),
            str(_SPIN_WORKER_SOURCE),
        ],
        check=True,
    )
    return _SPIN_WORKER_BINARY


def _spawn_spin_worker() -> tuple[subprocess.Popen[str], int]:
    worker = _build_spin_worker()
    process = subprocess.Popen(
        [str(worker)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    assert process.stdout is not None
    line = process.stdout.readline()
    match = re.search(r"MAIN_TID=(\d+)", line)
    if match is None:
        process.kill()
        raise base.BenchmarkError("spin worker did not report MAIN_TID")
    return process, int(match.group(1))


def preflight_offline() -> dict[str, Any]:
    if sys.platform != "darwin":
        return {"status": "failed", "error": "requires macOS", "phase": "offline"}
    try:
        set_session_backend()
    except base.BenchmarkError as error:
        return {"status": "failed", "error": str(error), "phase": "offline"}
    process: subprocess.Popen[str] | None = None
    try:
        process, main_tid = _spawn_spin_worker()
        snapshot = sample_snapshot(process.pid)
        if snapshot.diagnostics.get("threads_enumerated", 0) < 1:
            return {
                "status": "failed",
                "thread_enumeration": "failed",
                "error": "no threads enumerated",
                "phase": "offline",
            }
        if snapshot.diagnostics.get("threads_sampled_ok", 0) < 1:
            return {
                "status": "failed",
                "thread_enumeration": "failed",
                "error": "no complete thread-info records",
                "phase": "offline",
            }
        first_total = sum(item.total_us for item in snapshot.samples if item.thread_id)
        time.sleep(0.2)
        second = sample_snapshot(process.pid)
        second_total = sum(item.total_us for item in second.samples if item.thread_id)
        if second_total <= first_total:
            return {
                "status": "failed",
                "thread_enumeration": "passed",
                "cpu_delta": "failed",
                "error": "CPU counters did not increase on spin worker",
                "phase": "offline",
                "pid_tested": process.pid,
                "main_thread_id": main_tid,
            }
        record_bytes = 112
        if selected_backend() == "libproc":
            import thread_cpu_libproc as libproc_mod

            record_bytes = libproc_mod.PROC_THREADINFO_SIZE
        return {
            "status": "ready",
            "thread_enumeration": "passed",
            "cpu_delta": "passed",
            "record_bytes": record_bytes,
            "pid_tested": process.pid,
            "main_thread_id": main_tid,
            "phase": "offline",
            "thread_cpu_backend": selected_backend(),
        }
    except base.BenchmarkError as error:
        message = str(error)
        status = "needs_permission" if "denied" in message else "failed"
        return {"status": status, "error": message, "phase": "offline"}
    finally:
        if process is not None:
            if process.stdout:
                process.stdout.close()
            if process.stderr:
                process.stderr.close()
            process.kill()
            process.wait(timeout=5)


def preflight(pid: int) -> dict[str, Any]:
    try:
        backend = selected_backend()
    except base.BenchmarkError as error:
        return {"status": "failed", "error": str(error), "pid_tested": pid, "phase": "target"}
    if backend == "mach":
        import thread_cpu_mach

        result = thread_cpu_mach.preflight_target_mach(pid)
    else:
        import thread_cpu_libproc

        result = thread_cpu_libproc.preflight_target_libproc(pid)
    result["thread_cpu_backend"] = backend
    return result


def poll_window(
    pid: int,
    *,
    duration_s: float,
    interval_s: float,
    process_cpu_ms_per_s: float | None = None,
    reconcile_tolerance_ms_per_s: float | None = None,
    main_thread_id: int | None = None,
) -> dict[str, Any]:
    backend = selected_backend()
    window_start_ns = time.monotonic_ns()
    polls: list[dict[int, int]] = []
    poll_diagnostics: list[dict[str, int]] = []
    first = sample_snapshot(pid)
    poll_diagnostics.append(dict(first.diagnostics))
    polls.append({s.thread_id: s.total_us for s in first.samples if s.thread_id})
    deadline = time.monotonic() + duration_s
    while time.monotonic() < deadline:
        time.sleep(interval_s)
        snap = sample_snapshot(pid)
        poll_diagnostics.append(dict(snap.diagnostics))
        polls.append({s.thread_id: s.total_us for s in snap.samples if s.thread_id})
    window_end_ns = time.monotonic_ns()
    observed_duration_s = (window_end_ns - window_start_ns) / 1e9
    if observed_duration_s <= 0:
        observed_duration_s = duration_s

    accumulated, appeared, disappeared = cpu_delta.accumulate_thread_deltas(polls)
    deltas = [{"thread_id": tid, "delta_us": us} for tid, us in accumulated.items() if us > 0]
    total_delta_us = sum(item["delta_us"] for item in deltas)
    thread_cpu_ms_per_s = total_delta_us / 1000.0 / observed_duration_s if observed_duration_s else 0.0
    main_us, non_main_us, mapping_status = cpu_delta.split_main_thread_cpu(accumulated, main_thread_id)
    main_thread_cpu_ms_per_s = main_us / 1000.0 / observed_duration_s if observed_duration_s else 0.0
    non_main_thread_cpu_ms_per_s = non_main_us / 1000.0 / observed_duration_s if observed_duration_s else 0.0

    reconciliation_residual_ms_per_s = None
    reconcile_ok = None
    if process_cpu_ms_per_s is not None:
        reconciliation_residual_ms_per_s = process_cpu_ms_per_s - thread_cpu_ms_per_s
        if reconcile_tolerance_ms_per_s is not None:
            reconcile_ok = abs(reconciliation_residual_ms_per_s) <= reconcile_tolerance_ms_per_s

    report: dict[str, Any] = {
        "pid": pid,
        "duration_s_requested": duration_s,
        "interval_s": interval_s,
        "window_start_ns": window_start_ns,
        "window_end_ns": window_end_ns,
        "observed_duration_s": observed_duration_s,
        "thread_deltas": deltas,
        "total_attributed_us": total_delta_us,
        "thread_cpu_ms_per_s": thread_cpu_ms_per_s,
        "main_thread_id": main_thread_id,
        "main_thread_cpu_ms_per_s": main_thread_cpu_ms_per_s,
        "non_main_thread_cpu_ms_per_s": non_main_thread_cpu_ms_per_s,
        "main_thread_mapping_status": mapping_status,
        "process_cpu_ms_per_s": process_cpu_ms_per_s,
        "reconciliation_residual_ms_per_s": reconciliation_residual_ms_per_s,
        "reconcile_ok": reconcile_ok,
        "threads_appeared_mid_window": appeared,
        "threads_disappeared_mid_window": disappeared,
        "thread_cpu_backend": backend,
        "poll_diagnostics": poll_diagnostics,
    }
    if backend == "mach":
        report["vm_deallocate_task"] = "mach_task_self_"
    return report


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("pid", type=int, nargs="?", help="target pid for poll_window")
    parser.add_argument("--duration", type=float, default=5.0)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--preflight-offline", action="store_true")
    parser.add_argument("--preflight-target", type=int, metavar="PID")
    args = parser.parse_args()
    set_session_backend()
    if args.preflight_offline:
        print(json.dumps(preflight_offline(), indent=2))
        return
    if args.preflight_target is not None:
        print(json.dumps(preflight(args.preflight_target), indent=2))
        return
    if args.pid is None:
        parser.error("pid required unless --preflight-offline or --preflight-target")
    report = poll_window(args.pid, duration_s=args.duration, interval_s=0.1)
    payload = json.dumps(report, indent=2)
    if args.output:
        args.output.write_text(payload + "\n")
    else:
        print(payload)


if __name__ == "__main__":
    main()
