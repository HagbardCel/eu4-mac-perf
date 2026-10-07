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
THREAD_INFO_MAX = 1024

mach_port_t = ctypes.c_uint
kern_return_t = ctypes.c_int
thread_t = ctypes.c_uint
natural_t = ctypes.c_uint
integer_t = ctypes.c_int


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


def _mach_task_threads(
    task: int, threads_out: ctypes.POINTER(ctypes.POINTER(thread_t)), count_out: ctypes.POINTER(natural_t)
) -> kern_return_t:
    return libsystem.task_threads(task, threads_out, count_out)


def _mach_thread_info(
    thread: thread_t, flavor: int, info: ctypes.c_void_p, count: ctypes.POINTER(natural_t)
) -> kern_return_t:
    return libsystem.thread_info(thread, flavor, info, count)


def _mach_task_for_pid(target: int) -> mach_port_t:
    task = mach_port_t()
    result = libsystem.task_for_pid(mach_port_t(0), target, ctypes.byref(task))
    if result != 0:
        raise base.BenchmarkError(f"task_for_pid failed ({result}); run with appropriate permissions")
    return task


def _timevalue_us(tv: TimeValue) -> int:
    return int(tv.seconds) * 1_000_000 + int(tv.microseconds)


def sample_threads(pid: int) -> list[ThreadSample]:
    task = _mach_task_for_pid(pid)
    thread_array = ctypes.POINTER(thread_t)()
    count = natural_t()
    if _mach_task_threads(task, ctypes.byref(thread_array), ctypes.byref(count)) != 0:
        raise base.BenchmarkError("task_threads failed")
    samples: list[ThreadSample] = []
    basic = ThreadBasicInfo()
    ident = ThreadIdentifierInfo()
    for index in range(int(count.value)):
        thread = thread_array[index]
        info_count = natural_t(ctypes.sizeof(ThreadBasicInfo) // ctypes.sizeof(integer_t))
        if _mach_thread_info(thread, THREAD_BASIC_INFO, ctypes.byref(basic), ctypes.byref(info_count)) != 0:
            continue
        info_count = natural_t(ctypes.sizeof(ThreadIdentifierInfo) // ctypes.sizeof(integer_t))
        thread_id = 0
        if _mach_thread_info(thread, THREAD_IDENTIFIER_INFO, ctypes.byref(ident), ctypes.byref(info_count)) == 0:
            thread_id = int(ident.thread_id)
        samples.append(
            ThreadSample(
                thread_id=thread_id,
                user_us=_timevalue_us(basic.user_time),
                system_us=_timevalue_us(basic.system_time),
            )
        )
    for index in range(int(count.value)):
        libsystem.mach_port_deallocate(task, thread_array[index])
    return samples


def poll_window(pid: int, *, duration_s: float, interval_s: float) -> dict[str, Any]:
    start = sample_threads(pid)
    time.sleep(duration_s)
    end = sample_threads(pid)
    by_id_start = {sample.thread_id: sample for sample in start if sample.thread_id}
    by_id_end = {sample.thread_id: sample for sample in end if sample.thread_id}
    deltas: list[dict[str, int]] = []
    for thread_id, end_sample in by_id_end.items():
        begin = by_id_start.get(thread_id)
        if not begin:
            continue
        delta_us = end_sample.total_us - begin.total_us
        if delta_us < 0:
            continue
        deltas.append({"thread_id": thread_id, "delta_us": delta_us})
    total_delta_us = sum(item["delta_us"] for item in deltas)
    window_us = int(duration_s * 1_000_000)
    unattributed_us = max(0, window_us - total_delta_us) if window_us else 0
    return {
        "pid": pid,
        "duration_s": duration_s,
        "interval_s": interval_s,
        "thread_deltas": deltas,
        "total_attributed_us": total_delta_us,
        "potentially_unattributed_us": unattributed_us,
        "cpu_ms_per_s": total_delta_us / 1000.0 / duration_s if duration_s else 0.0,
    }


def preflight(pid: int | None = None) -> dict[str, Any]:
    target = pid or 1
    try:
        _mach_task_for_pid(target)
        return {"status": "ready", "pid_tested": target}
    except base.BenchmarkError as error:
        return {"status": "needs_permission", "error": str(error)}


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
