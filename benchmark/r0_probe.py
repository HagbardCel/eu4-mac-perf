#!/usr/bin/env python3
"""Build, control, and parse logs for the R0 frame-evolution probe."""

from __future__ import annotations

import ctypes
import mmap
import os
import platform
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any

import eu4_benchmark as base

import eu4_r0_control as r0_control

ROOT = Path(__file__).resolve().parents[1]
BENCHMARK = Path(__file__).resolve().parent
SOURCE = BENCHMARK / "eu4_r0_probe.c"
READBACK_SOURCE = BENCHMARK / "eu4_r0_readback.c"
CONTROL_SOURCE = BENCHMARK / "eu4_r0_control.c"
LIBRARY = BENCHMARK / ".build/libeu4_r0_probe.dylib"
HOST_CONTROL_LIBRARY = BENCHMARK / ".build/libeu4_r0_control_host.dylib"
HOST_ARCH = platform.machine()
HARNESS = ROOT / "tests/r0_probe_harness.c"
HARNESS_EXE = BENCHMARK / ".build/r0_probe_harness"
READBACK_HARNESS = BENCHMARK / ".build/r0_readback_harness"
GOG_SHA256 = "b3d38876abf4e61cdae57509186d7cb7dcb03bfeaca4c95c10c713794715141d"

_PROBE_SOURCES = [SOURCE, READBACK_SOURCE, CONTROL_SOURCE]


def build() -> None:
    LIBRARY.parent.mkdir(parents=True, exist_ok=True)
    commands = [
        [
            "clang",
            "-arch",
            HOST_ARCH,
            "-O2",
            "-Wall",
            "-Wextra",
            "-Werror",
            "-std=c11",
            "-dynamiclib",
            "-o",
            str(HOST_CONTROL_LIBRARY),
            str(CONTROL_SOURCE),
        ],
        [
            "clang",
            "-arch",
            "x86_64",
            "-O2",
            "-Wall",
            "-Wextra",
            "-Werror",
            "-std=c11",
            "-dynamiclib",
            "-framework",
            "OpenGL",
            "-o",
            str(LIBRARY),
            *[str(path) for path in _PROBE_SOURCES],
        ],
        [
            "clang",
            "-arch",
            "x86_64",
            "-O2",
            "-Wall",
            "-Wextra",
            "-Werror",
            "-I",
            str(BENCHMARK),
            "-framework",
            "OpenGL",
            "-o",
            str(READBACK_HARNESS),
            str(ROOT / "tests/r0_readback_harness.c"),
            str(READBACK_SOURCE),
        ],
        [
            "clang",
            "-arch",
            "x86_64",
            "-O2",
            "-Wall",
            "-Wextra",
            "-Werror",
            "-framework",
            "OpenGL",
            "-o",
            str(HARNESS_EXE),
            str(HARNESS),
        ],
    ]
    for command in commands:
        result = subprocess.run(command, capture_output=True, text=True, check=False)
        if result.returncode:
            raise base.BenchmarkError(f"R0 probe build failed: {result.stderr.strip()}")


def _publish_native() -> ctypes._CFuncPtr:
    if not HOST_CONTROL_LIBRARY.is_file():
        build()
    lib = ctypes.CDLL(str(HOST_CONTROL_LIBRARY))
    publish = lib.eu4_r0_control_publish
    publish.argtypes = [ctypes.c_void_p, ctypes.c_uint32, ctypes.c_uint32, ctypes.c_uint32]
    publish.restype = None
    return publish


def write_control(path: Path, *, generation: int, mode: int, scenario_id: int) -> None:
    if not LIBRARY.is_file():
        build()
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        path.write_bytes(b"\x00" * r0_control.EU4_R0_CONTROL_PAGE_SIZE)
    publish = _publish_native()
    with path.open("r+b") as handle:
        with mmap.mmap(
            handle.fileno(), r0_control.EU4_R0_CONTROL_PAGE_SIZE, access=mmap.ACCESS_WRITE
        ) as mapping:
            page = (ctypes.c_uint8 * r0_control.EU4_R0_CONTROL_PAGE_SIZE).from_buffer(mapping)
            publish(ctypes.c_void_p(ctypes.addressof(page)), generation, mode, scenario_id)
            del page
            mapping.flush()


def probe_env(control_path: Path, log_path: Path) -> dict[str, str]:
    return {
        "EU4_R0_CONTROL": str(control_path),
        "EU4_R0_LOG": str(log_path),
        "DYLD_INSERT_LIBRARIES": str(LIBRARY),
    }


def event_uptime_ns() -> int:
    return time.clock_gettime_ns(time.CLOCK_UPTIME_RAW)


def wait_for_ack(log_path: Path, generation: int, timeout_s: float = 10.0) -> dict[str, Any]:
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        parsed = parse_log(log_path)
        for ack in parsed["acks"]:
            if ack["generation"] == generation:
                return ack
        time.sleep(0.1)
    raise base.BenchmarkError(f"Timed out waiting for ACK generation={generation}")


def probe_production_drawable(
    control_path: Path, log_path: Path, generation: int, *, timeout_s: float = 15.0
) -> dict[str, Any]:
    """One census generation against the real drawable (no EU4_R0_TEST_CENSUS)."""
    write_control(
        control_path,
        generation=generation,
        mode=r0_control.EU4_R0_MODE_CENSUS,
        scenario_id=0,
    )
    wait_for_ack(log_path, generation, timeout_s=timeout_s)
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        parsed = parse_log(log_path)
        for frame in reversed(parsed["frames"]):
            if not frame.get("census_attempted"):
                continue
            return {
                "supported": bool(frame.get("census_ok")),
                "frame": frame,
                "generation": generation,
            }
        time.sleep(0.2)
    return {"supported": False, "reason": "no census frame observed", "generation": generation}


def infer_main_thread_id(frames: list[dict[str, Any]]) -> int | None:
    votes: dict[int, int] = {}
    for frame in frames:
        if frame.get("flush_is_main_thread") and frame.get("pthread_tid"):
            tid = int(frame["pthread_tid"])
            votes[tid] = votes.get(tid, 0) + 1
    if not votes:
        return None
    return max(votes, key=votes.get)


def parse_log(path: Path) -> dict[str, Any]:
    acks: list[dict[str, int]] = []
    frames: list[dict[str, Any]] = []
    swaps: list[dict[str, Any]] = []
    if not path.is_file():
        return {"acks": acks, "frames": frames, "swaps": swaps}
    for raw in path.read_text(errors="replace").splitlines():
        parts = raw.split(",")
        if not parts:
            continue
        kind = parts[0]
        if kind == "ACK" and len(parts) >= 6:
            acks.append(
                {
                    "generation": int(parts[1]),
                    "frame_index": int(parts[2]),
                    "uptime_ns": int(parts[3]),
                    "mode": int(parts[4]),
                    "scenario_id": int(parts[5]),
                }
            )
        elif kind == "ACK" and len(parts) >= 4:
            acks.append(
                {
                    "generation": int(parts[1]),
                    "frame_index": int(parts[2]),
                    "uptime_ns": int(parts[3]),
                    "mode": 0,
                    "scenario_id": 0,
                }
            )
        elif kind == "F" and len(parts) >= 18:
            frames.append(
                {
                    "frame_index": int(parts[1]),
                    "mode": int(parts[2]),
                    "scenario_id": int(parts[3]),
                    "pause_verified": int(parts[4]),
                    "census_ok": int(parts[5]),
                    "context_id": int(parts[6]),
                    "width": int(parts[7]),
                    "height": int(parts[8]),
                    "memcmp_equal_prev": int(parts[9]),
                    "census_attempted": int(parts[10]),
                    "crc64": int(parts[11]),
                    "flush_is_main_thread": int(parts[12]),
                    "uptime_ns": int(parts[13]),
                    "gl_error_observed": int(parts[14]),
                    "flush_ok": int(parts[15]),
                    "flush_cgl_error": int(parts[16]),
                    "pthread_tid": int(parts[17]),
                }
            )
        elif kind == "F" and len(parts) >= 13:
            frames.append(
                {
                    "frame_index": int(parts[1]),
                    "mode": int(parts[2]),
                    "scenario_id": int(parts[3]),
                    "pause_verified": int(parts[4]),
                    "census_ok": int(parts[5]),
                    "context_id": int(parts[6]),
                    "width": int(parts[7]),
                    "height": int(parts[8]),
                    "memcmp_equal_prev": int(parts[9]),
                    "census_attempted": int(parts[10]),
                    "crc64": int(parts[11]),
                    "flush_is_main_thread": int(parts[12]),
                    "uptime_ns": 0,
                    "gl_error_observed": 0,
                    "flush_ok": 1,
                    "flush_cgl_error": 0,
                    "pthread_tid": 0,
                }
            )
        elif kind == "S" and len(parts) >= 7:
            swaps.append(
                {
                    "uptime_ns": int(parts[1]),
                    "wall_ns": int(parts[2]),
                    "mode": int(parts[3]),
                    "interval_ns": int(parts[4]),
                    "swaps": int(parts[5]),
                    "paused_swaps": int(parts[6]),
                }
            )
    return {"acks": acks, "frames": frames, "swaps": swaps}


def preflight() -> dict[str, Any]:
    identity = base.gog_identity()
    if identity["executable_sha256"] != GOG_SHA256:
        raise base.BenchmarkError("GOG executable changed; review version-pinned pause offsets")
    build()
    with tempfile.TemporaryDirectory(prefix="eu4-r0-preflight-") as temporary:
        root = Path(temporary)
        control = root / "control.bin"
        log = root / "probe.log"
        write_control(control, generation=1, mode=r0_control.EU4_R0_MODE_CENSUS, scenario_id=0)
        env = os.environ.copy()
        env.update(probe_env(control, log))
        env["EU4_R0_TEST_CENSUS"] = "1"
        result = subprocess.run([str(HARNESS_EXE)], env=env, capture_output=True, text=True, check=False, timeout=30)
        if result.returncode:
            raise base.BenchmarkError(
                f"R0 probe harness failed ({result.returncode}): "
                f"{result.stderr.strip() or result.stdout.strip()}"
            )
        parsed = parse_log(log)
        if not parsed["acks"] or not parsed["frames"]:
            raise base.BenchmarkError("R0 preflight log missing ACK or frame records")
        census_ok_frames = [f for f in parsed["frames"] if f.get("census_ok")]
        if not census_ok_frames:
            raise base.BenchmarkError("R0 preflight did not produce census_ok readback")
        return {"status": "ready", "acks": len(parsed["acks"]), "frames": len(parsed["frames"])}


if __name__ == "__main__":
    import argparse
    import json

    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=["build", "preflight"])
    args = parser.parse_args()
    if args.command == "build":
        build()
        print(LIBRARY)
    else:
        print(json.dumps(preflight(), indent=2))
