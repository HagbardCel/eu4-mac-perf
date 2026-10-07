#!/usr/bin/env python3
"""Poll Mach thread CPU counters for a target PID (epoch A only)."""

from __future__ import annotations

import argparse
import ctypes
import ctypes.util
import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import eu4_benchmark as base

libsystem = ctypes.CDLL(ctypes.util.find_library("System"))

THREAD_BASIC_INFO = 3
THREAD_IDENTIFIER_INFO = 4
KERN_SUCCESS = 0

mach_port_t = ctypes.c_uint
kern_return_t = ctypes.c_int
thread_t = ctypes.c_uint
natural_t = ctypes.c_uint
integer_t = ctypes.c_int
vm_address_t = ctypes.c_uint64
vm_size_t = ctypes.c_uint64

SELF_TASK = ctypes.c_uint.in_dll(libsystem, "mach_task_self_").value

libsystem.task_for_pid.argtypes = [mach_port_t, ctypes.c_int, ctypes.POINTER(mach_port_t)]
libsystem.task_for_pid.restype = kern_return_t
libsystem.task_threads.argtypes = [
    mach_port_t,
    ctypes.POINTER(ctypes.POINTER(thread_t)),
    ctypes.POINTER(natural_t),
]
libsystem.task_threads.restype = kern_return_t
libsystem.thread_info.argtypes = [thread_t, ctypes.c_int, ctypes.c_void_p, ctypes.POINTER(natural_t)]
libsystem.thread_info.restype = kern_return_t
libsystem.mach_port_deallocate.argtypes = [mach_port_t, mach_port_t]
libsystem.mach_port_deallocate.restype = kern_return_t
libsystem.vm_deallocate.argtypes = [mach_port_t, vm_address_t, vm_size_t]
libsystem.vm_deallocate.restype = kern_return_t


class TimeValue(ctypes.Structure):
    _fields_ = [("seconds", integer_t), ("microseconds", integer_t)]


class ThreadBasicInfo(ctypes.Structure):
    _fields_ = [
        ("user_time", TimeValue),
        ("system_time", TimeValue),
        ("cpu_usage", ctypes.c_int),
        ("policy", ctypes.c_int),
        ("run_state", ctypes.c_int),
        ("flags", ctypes.c_int),
        ("suspend_count", ctypes.c_int),
        ("sleep_time", integer_t),
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


def _mach_task_for_pid(pid: int) -> mach_port_t:
    task = mach_port_t()
    result = libsystem.task_for_pid(SELF_TASK, pid, ctypes.byref(task))
    if result != KERN_SUCCESS:
        raise base.BenchmarkError(f"task_for_pid failed ({result}); run with appropriate permissions")
    return task


def _deallocate_thread_list(target_task: mach_port_t, thread_array: ctypes.POINTER(thread_t), count: int) -> None:
    if count:
        for index in range(count):
            libsystem.mach_port_deallocate(SELF_TASK, thread_array[index])
        libsystem.vm_deallocate(
            SELF_TASK,
            vm_address_t(ctypes.cast(thread_array, ctypes.c_void_p).value),
            vm_size_t(count * ctypes.sizeof(thread_t)),
        )
    libsystem.mach_port_deallocate(SELF_TASK, target_task)


def sample_threads(pid: int) -> list[ThreadSample]:
    target_task = _mach_task_for_pid(pid)
    thread_array = ctypes.POINTER(thread_t)()
    count = natural_t()
    try:
        if libsystem.task_threads(target_task, ctypes.byref(thread_array), ctypes.byref(count)) != KERN_SUCCESS:
            raise base.BenchmarkError("task_threads failed")
        samples: list[ThreadSample] = []
        basic = ThreadBasicInfo()
        ident = ThreadIdentifierInfo()
        thread_count = int(count.value)
        for index in range(thread_count):
            thread = thread_array[index]
            info_count = natural_t(ctypes.sizeof(ThreadBasicInfo) // ctypes.sizeof(integer_t))
            if libsystem.thread_info(thread, THREAD_BASIC_INFO, ctypes.byref(basic), ctypes.byref(info_count)) != KERN_SUCCESS:
                continue
            info_count = natural_t(ctypes.sizeof(ThreadIdentifierInfo) // ctypes.sizeof(integer_t))
            thread_id = 0
            if libsystem.thread_info(thread, THREAD_IDENTIFIER_INFO, ctypes.byref(ident), ctypes.byref(info_count)) == KERN_SUCCESS:
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
) -> dict[str, Any]:
    window_start_ns = time.monotonic_ns()
    start_wall = time.time()
    accumulated: dict[int, int] = {}
    appeared = 0
    disappeared = 0
    previous_ids: set[int] = set()
    start_by_id = {s.thread_id: s.total_us for s in sample_threads(pid) if s.thread_id}
    previous_ids = set(start_by_id)
    deadline = time.monotonic() + duration_s
    while time.monotonic() < deadline:
        time.sleep(interval_s)
        current = sample_threads(pid)
        current_ids = {s.thread_id for s in current if s.thread_id}
        appeared += len(current_ids - previous_ids)
        disappeared += len(previous_ids - current_ids)
        for sample in current:
            if not sample.thread_id or sample.thread_id not in start_by_id:
                continue
            delta = sample.total_us - start_by_id[sample.thread_id]
            if delta >= 0:
                accumulated[sample.thread_id] = delta
        previous_ids = current_ids

    window_end_ns = time.monotonic_ns()
    observed_duration_s = (window_end_ns - window_start_ns) / 1e9
    if observed_duration_s <= 0:
        observed_duration_s = duration_s

    deltas = [{"thread_id": tid, "delta_us": delta} for tid, delta in accumulated.items() if delta > 0]
    total_delta_us = sum(item["delta_us"] for item in deltas)
    thread_cpu_ms_per_s = total_delta_us / 1000.0 / observed_duration_s if observed_duration_s else 0.0
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
        "wall_start_s": start_wall,
        "thread_deltas": deltas,
        "total_attributed_us": total_delta_us,
        "thread_cpu_ms_per_s": thread_cpu_ms_per_s,
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
