#!/usr/bin/env python3
"""libproc per-thread CPU sampling (default macOS backend)."""

from __future__ import annotations

import ctypes
import errno
import sys
from typing import TYPE_CHECKING

import eu4_benchmark as base

if TYPE_CHECKING:
    from thread_cpu_sampler import ThreadSample, ThreadSnapshot

PROC_PIDLISTTHREADIDS = 28
PROC_PIDTHREADID64INFO = 15
PROC_PIDTASKINFO = 4
PROC_THREADINFO_SIZE = 112
_INITIAL_THREADID_BUF = 256
_MAX_THREADID_BUF = 65536
_MAX_ENUM_GROWTH = 16

_libproc: ctypes.CDLL | None = None


class ProcThreadInfo(ctypes.Structure):
    _fields_ = [
        ("pth_user_time", ctypes.c_uint64),
        ("pth_system_time", ctypes.c_uint64),
        ("_padding", ctypes.c_uint8 * (PROC_THREADINFO_SIZE - 16)),
    ]


class ProcTaskInfoPartial(ctypes.Structure):
    _fields_ = [
        ("pti_virtual_size", ctypes.c_uint64),
        ("pti_resident_size", ctypes.c_uint64),
        ("pti_total_user", ctypes.c_uint64),
        ("pti_total_system", ctypes.c_uint64),
    ]


def _init_libproc() -> ctypes.CDLL:
    global _libproc
    if _libproc is not None:
        return _libproc
    if sys.platform != "darwin":
        raise base.BenchmarkError("libproc thread CPU sampling requires macOS")
    if ctypes.sizeof(ProcThreadInfo) != PROC_THREADINFO_SIZE:
        raise base.BenchmarkError(
            f"unexpected proc_threadinfo size {ctypes.sizeof(ProcThreadInfo)} (expected {PROC_THREADINFO_SIZE})"
        )
    lib = ctypes.CDLL("/usr/lib/libproc.dylib", use_errno=True)
    lib.proc_pidinfo.argtypes = [ctypes.c_int, ctypes.c_int, ctypes.c_uint64, ctypes.c_void_p, ctypes.c_int]
    lib.proc_pidinfo.restype = ctypes.c_int
    _libproc = lib
    return lib


def _proc_pidinfo(pid: int, flavor: int, arg: int, buffer: ctypes.c_void_p, bufsize: int) -> int:
    lib = _init_libproc()
    ctypes.set_errno(0)
    returned = int(lib.proc_pidinfo(pid, flavor, arg, buffer, bufsize))
    if returned == 0:
        err = ctypes.get_errno()
        if err == 0:
            raise base.BenchmarkError("proc_pidinfo returned 0 with errno unset")
        raise base.BenchmarkError(f"proc_pidinfo failed: errno {err} ({errno.errorcode.get(err, err)})")
    return returned


def _list_thread_ids(pid: int) -> list[int]:
    bufsize = _INITIAL_THREADID_BUF
    growth = 0
    while bufsize <= _MAX_THREADID_BUF and growth < _MAX_ENUM_GROWTH:
        raw = (ctypes.c_uint8 * bufsize)()
        ctypes.set_errno(0)
        lib = _init_libproc()
        returned = int(lib.proc_pidinfo(pid, PROC_PIDLISTTHREADIDS, 0, raw, bufsize))
        if returned == 0:
            err = ctypes.get_errno()
            if err == 0:
                raise base.BenchmarkError("PROC_PIDLISTTHREADIDS returned 0 bytes")
            if err in (errno.EPERM, errno.EACCES):
                raise base.BenchmarkError(f"PROC_PIDLISTTHREADIDS denied: errno {err}")
            raise base.BenchmarkError(f"PROC_PIDLISTTHREADIDS failed: errno {err}")
        if returned == bufsize:
            bufsize *= 2
            growth += 1
            continue
        if returned % 8 != 0:
            raise base.BenchmarkError(f"PROC_PIDLISTTHREADIDS returned non-multiple-of-8 size {returned}")
        count = returned // 8
        ids: list[int] = []
        for index in range(count):
            chunk = raw[index * 8 : (index + 1) * 8]
            ids.append(int.from_bytes(chunk, "little", signed=False))
        return ids
    raise base.BenchmarkError("PROC_PIDLISTTHREADIDS buffer did not stabilize")


def _ns_to_us(value: int) -> int:
    return int(value // 1000)


def process_total_cpu_us(pid: int) -> int:
    bufsize = 1024
    raw = (ctypes.c_uint8 * bufsize)()
    ctypes.set_errno(0)
    lib = _init_libproc()
    returned = int(lib.proc_pidinfo(pid, PROC_PIDTASKINFO, 0, raw, bufsize))
    if returned < ctypes.sizeof(ProcTaskInfoPartial):
        err = ctypes.get_errno()
        raise base.BenchmarkError(f"PROC_PIDTASKINFO failed: returned {returned}, errno {err}")
    info = ProcTaskInfoPartial.from_buffer_copy(raw)
    return _ns_to_us(int(info.pti_total_user) + int(info.pti_total_system))


def sample_snapshot_libproc(pid: int) -> ThreadSnapshot:
    """Same as sample_snapshot_libproc but re-enumerates on ambiguous disappearance."""
    from thread_cpu_sampler import ThreadSample, ThreadSnapshot, empty_diagnostics

    thread_ids = _list_thread_ids(pid)
    diagnostics = empty_diagnostics()
    diagnostics["threads_enumerated"] = len(thread_ids)
    samples: list[ThreadSample] = []
    for thread_id in thread_ids:
        ctypes.set_errno(0)
        lib = _init_libproc()
        info_struct = ProcThreadInfo()
        returned = int(
            lib.proc_pidinfo(pid, PROC_PIDTHREADID64INFO, thread_id, ctypes.byref(info_struct), PROC_THREADINFO_SIZE)
        )
        if returned == PROC_THREADINFO_SIZE:
            diagnostics["threads_sampled_ok"] += 1
            samples.append(
                ThreadSample(
                    thread_id=int(thread_id),
                    user_us=_ns_to_us(int(info_struct.pth_user_time)),
                    system_us=_ns_to_us(int(info_struct.pth_system_time)),
                )
            )
            continue
        if returned == 0:
            err = ctypes.get_errno()
            if err == errno.ESRCH:
                diagnostics["threads_skipped_esrch"] += 1
                continue
            if err in (errno.EPERM, errno.EACCES):
                raise base.BenchmarkError(f"PROC_PIDTHREADID64INFO denied: errno {err}")
            current = set(_list_thread_ids(pid))
            if thread_id not in current:
                diagnostics["threads_skipped_esrch"] += 1
                continue
            if err == 0:
                raise base.BenchmarkError("PROC_PIDTHREADID64INFO returned 0 with errno unset")
            raise base.BenchmarkError(f"PROC_PIDTHREADID64INFO failed for thread {thread_id}: errno {err}")
        raise base.BenchmarkError(
            f"PROC_PIDTHREADID64INFO returned {returned} bytes for thread {thread_id}"
        )
    return ThreadSnapshot(samples=samples, diagnostics=diagnostics)


def preflight_target_libproc(pid: int) -> dict:
    try:
        snapshot = sample_snapshot_libproc(pid)
        if snapshot.diagnostics.get("threads_enumerated", 0) < 1:
            return {
                "status": "failed",
                "error": "no threads enumerated",
                "pid_tested": pid,
                "phase": "target",
            }
        if snapshot.diagnostics.get("threads_sampled_ok", 0) < 1:
            return {
                "status": "failed",
                "error": "no complete thread-info records",
                "pid_tested": pid,
                "phase": "target",
            }
        return {
            "status": "ready",
            "pid_tested": pid,
            "phase": "target",
            "record_bytes": PROC_THREADINFO_SIZE,
        }
    except base.BenchmarkError as error:
        message = str(error)
        status = "needs_permission" if "denied" in message or "errno 1" in message else "failed"
        return {"status": status, "error": message, "pid_tested": pid, "phase": "target"}
