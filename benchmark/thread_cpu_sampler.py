#!/usr/bin/env python3
"""Poll Mach thread CPU counters for a target PID (epoch A only)."""

from __future__ import annotations

import argparse
import ctypes
import ctypes.util
import json
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import cpu_delta
import eu4_benchmark as base

THREAD_BASIC_INFO = 3
THREAD_IDENTIFIER_INFO = 4
KERN_SUCCESS = 0

_libsystem: ctypes.CDLL | None = None
_SELF_TASK: int | None = None


def _init_mach() -> ctypes.CDLL:
    global _libsystem, _SELF_TASK
    if _libsystem is not None:
        return _libsystem
    if sys.platform != "darwin":
        raise base.BenchmarkError("Mach thread CPU sampling requires macOS")
    lib = ctypes.CDLL(ctypes.util.find_library("System"))
    lib.task_for_pid.argtypes = [ctypes.c_uint, ctypes.c_int, ctypes.POINTER(ctypes.c_uint)]
    lib.task_for_pid.restype = ctypes.c_int
    lib.task_threads.argtypes = [
        ctypes.c_uint,
        ctypes.POINTER(ctypes.POINTER(ctypes.c_uint)),
        ctypes.POINTER(ctypes.c_uint),
    ]
    lib.task_threads.restype = ctypes.c_int
    lib.thread_info.argtypes = [ctypes.c_uint, ctypes.c_int, ctypes.c_void_p, ctypes.POINTER(ctypes.c_uint)]
    lib.thread_info.restype = ctypes.c_int
    lib.mach_port_deallocate.argtypes = [ctypes.c_uint, ctypes.c_uint]
    lib.mach_port_deallocate.restype = ctypes.c_int
    lib.vm_deallocate.argtypes = [ctypes.c_uint, ctypes.c_uint64, ctypes.c_uint64]
    lib.vm_deallocate.restype = ctypes.c_int
    _libsystem = lib
    _SELF_TASK = ctypes.c_uint.in_dll(lib, "mach_task_self_").value
    return lib


class TimeValue(ctypes.Structure):
    _fields_ = [("seconds", ctypes.c_int), ("microseconds", ctypes.c_int)]


class ThreadBasicInfo(ctypes.Structure):
    _fields_ = [
        ("user_time", TimeValue),
        ("system_time", TimeValue),
        ("cpu_usage", ctypes.c_int),
        ("policy", ctypes.c_int),
        ("run_state", ctypes.c_int),
        ("flags", ctypes.c_int),
        ("suspend_count", ctypes.c_int),
        ("sleep_time", ctypes.c_int),
    ]


class ThreadIdentifierInfo(ctypes.Structure):
    _fields_ = [
        ("thread_id", ctypes.c_uint64),
        ("thread_handle", ctypes.c_uint64),
        ("dispatch_qaddr", ctypes.c_uint64),
    ]


@dataclass
class ThreadSample:
    thread_id: int
    user_us: int
    system_us: int

    @property
    def total_us(self) -> int:
        return self.user_us + self.system_us


def _timevalue_us(tv: TimeValue) -> int:
    return int(tv.seconds) * 1_000_000 + int(tv.microseconds)


def _mach_task_for_pid(pid: int) -> int:
    lib = _init_mach()
    task = ctypes.c_uint()
    result = lib.task_for_pid(_SELF_TASK, pid, ctypes.byref(task))
    if result != KERN_SUCCESS:
        raise base.BenchmarkError(f"task_for_pid failed ({result}); run with appropriate permissions")
    return int(task.value)


def _deallocate_thread_list(target_task: int, thread_array: ctypes.POINTER(ctypes.c_uint), count: int) -> None:
    lib = _init_mach()
    if count:
        for index in range(count):
            lib.mach_port_deallocate(_SELF_TASK, thread_array[index])
        lib.vm_deallocate(
            _SELF_TASK,
            ctypes.c_uint64(ctypes.cast(thread_array, ctypes.c_void_p).value or 0),
            ctypes.c_uint64(count * ctypes.sizeof(ctypes.c_uint)),
        )
    lib.mach_port_deallocate(_SELF_TASK, target_task)


def sample_threads(pid: int) -> list[ThreadSample]:
    lib = _init_mach()
    target_task = _mach_task_for_pid(pid)
    thread_array = ctypes.POINTER(ctypes.c_uint)()
    count = ctypes.c_uint()
    try:
        if lib.task_threads(target_task, ctypes.byref(thread_array), ctypes.byref(count)) != KERN_SUCCESS:
            raise base.BenchmarkError("task_threads failed")
        samples: list[ThreadSample] = []
        basic = ThreadBasicInfo()
        ident = ThreadIdentifierInfo()
        thread_count = int(count.value)
        for index in range(thread_count):
            thread = thread_array[index]
            info_count = ctypes.c_uint(ctypes.sizeof(ThreadBasicInfo) // ctypes.sizeof(ctypes.c_int))
            if lib.thread_info(thread, THREAD_BASIC_INFO, ctypes.byref(basic), ctypes.byref(info_count)) != KERN_SUCCESS:
                continue
            info_count = ctypes.c_uint(ctypes.sizeof(ThreadIdentifierInfo) // ctypes.sizeof(ctypes.c_int))
            thread_id = 0
            if lib.thread_info(thread, THREAD_IDENTIFIER_INFO, ctypes.byref(ident), ctypes.byref(info_count)) == KERN_SUCCESS:
                thread_id = int(ident.thread_id)
            samples.append(
                ThreadSample(
                    thread_id=thread_id,
                    user_us=_timevalue_us(basic.user_time),
                    system_us=_timevalue_us(basic.system_time),
                )
            )
        return samples
    finally:
        _deallocate_thread_list(target_task, thread_array, int(count.value))


def poll_window(
    pid: int,
    *,
    duration_s: float,
    interval_s: float,
    process_cpu_ms_per_s: float | None = None,
    reconcile_tolerance_ms_per_s: float | None = None,
    main_thread_id: int | None = None,
) -> dict[str, Any]:
    window_start_ns = time.monotonic_ns()
    polls: list[dict[int, int]] = []
    polls.append({s.thread_id: s.total_us for s in sample_threads(pid) if s.thread_id})
    deadline = time.monotonic() + duration_s
    while time.monotonic() < deadline:
        time.sleep(interval_s)
        polls.append({s.thread_id: s.total_us for s in sample_threads(pid) if s.thread_id})
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

    return {
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
        "vm_deallocate_task": "mach_task_self_",
    }


def preflight(pid: int) -> dict[str, Any]:
    try:
        _mach_task_for_pid(pid)
        return {"status": "ready", "pid_tested": pid}
    except base.BenchmarkError as error:
        return {"status": "needs_permission", "error": str(error), "pid_tested": pid}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("pid", type=int)
    parser.add_argument("--duration", type=float, default=5.0)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    report = poll_window(args.pid, duration_s=args.duration, interval_s=0.1)
    payload = json.dumps(report, indent=2)
    if args.output:
        args.output.write_text(payload + "\n")
    else:
        print(payload)


if __name__ == "__main__":
    main()
