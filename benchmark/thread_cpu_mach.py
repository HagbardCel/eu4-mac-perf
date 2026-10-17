#!/usr/bin/env python3
"""Mach task_for_pid thread CPU sampling (diagnostic backend)."""

from __future__ import annotations

import ctypes
import ctypes.util
import sys
from typing import TYPE_CHECKING

import eu4_benchmark as base

if TYPE_CHECKING:
    from thread_cpu_sampler import ThreadSample, ThreadSnapshot

THREAD_BASIC_INFO = 3
THREAD_IDENTIFIER_INFO = 4
KERN_SUCCESS = 0

_libsystem: ctypes.CDLL | None = None
_SELF_TASK: int | None = None


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


def sample_snapshot_mach(pid: int) -> ThreadSnapshot:
    from thread_cpu_sampler import ThreadSample, ThreadSnapshot, empty_diagnostics

    lib = _init_mach()
    target_task = _mach_task_for_pid(pid)
    thread_array = ctypes.POINTER(ctypes.c_uint)()
    count = ctypes.c_uint()
    diagnostics = empty_diagnostics()
    try:
        if lib.task_threads(target_task, ctypes.byref(thread_array), ctypes.byref(count)) != KERN_SUCCESS:
            raise base.BenchmarkError("task_threads failed")
        samples: list[ThreadSample] = []
        basic = ThreadBasicInfo()
        ident = ThreadIdentifierInfo()
        thread_count = int(count.value)
        diagnostics["threads_enumerated"] = thread_count
        for index in range(thread_count):
            thread = thread_array[index]
            info_count = ctypes.c_uint(ctypes.sizeof(ThreadBasicInfo) // ctypes.sizeof(ctypes.c_int))
            if lib.thread_info(thread, THREAD_BASIC_INFO, ctypes.byref(basic), ctypes.byref(info_count)) != KERN_SUCCESS:
                continue
            info_count = ctypes.c_uint(ctypes.sizeof(ThreadIdentifierInfo) // ctypes.sizeof(ctypes.c_int))
            thread_id = 0
            if lib.thread_info(thread, THREAD_IDENTIFIER_INFO, ctypes.byref(ident), ctypes.byref(info_count)) == KERN_SUCCESS:
                thread_id = int(ident.thread_id)
            if thread_id:
                diagnostics["threads_sampled_ok"] += 1
            samples.append(
                ThreadSample(
                    thread_id=thread_id,
                    user_us=_timevalue_us(basic.user_time),
                    system_us=_timevalue_us(basic.system_time),
                )
            )
        return ThreadSnapshot(samples=samples, diagnostics=diagnostics)
    finally:
        _deallocate_thread_list(target_task, thread_array, int(count.value))


def preflight_target_mach(pid: int) -> dict:
    try:
        _mach_task_for_pid(pid)
        snapshot = sample_snapshot_mach(pid)
        if snapshot.diagnostics.get("threads_sampled_ok", 0) < 1:
            return {
                "status": "failed",
                "error": "no thread identifiers sampled",
                "pid_tested": pid,
            }
        return {"status": "ready", "pid_tested": pid, "phase": "target"}
    except base.BenchmarkError as error:
        message = str(error)
        status = "needs_permission" if "task_for_pid" in message else "failed"
        return {"status": status, "error": message, "pid_tested": pid, "phase": "target"}
