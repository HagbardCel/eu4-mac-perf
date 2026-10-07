#!/usr/bin/env python3
"""Build, control, and parse logs for the R0 frame-evolution probe."""

from __future__ import annotations

import mmap
import os
import struct
import subprocess
import tempfile
from pathlib import Path
from typing import Any

import eu4_benchmark as base

import eu4_r0_control as r0_control

ROOT = Path(__file__).resolve().parents[1]
SOURCE = Path(__file__).with_name("eu4_r0_probe.c")
CONTROL_HEADER = Path(__file__).with_name("eu4_r0_control.h")
LIBRARY = SOURCE.parent / ".build/libeu4_r0_probe.dylib"
HARNESS = ROOT / "tests/r0_probe_harness.c"
HARNESS_EXE = SOURCE.parent / ".build/r0_probe_harness"
GOG_SHA256 = "b3d38876abf4e61cdae57509186d7cb7dcb03bfeaca4c95c10c713794715141d"


def build() -> None:
    LIBRARY.parent.mkdir(parents=True, exist_ok=True)
    commands = [
        [
            "clang",
            "-arch",
            "x86_64",
            "-O2",
            "-Wall",
            "-Wextra",
            "-Werror",
            "-dynamiclib",
            "-framework",
            "OpenGL",
            "-o",
            str(LIBRARY),
            str(SOURCE),
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
            str(ROOT / "benchmark/.build/r0_readback_harness"),
            str(ROOT / "tests/r0_readback_harness.c"),
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


def write_control(path: Path, *, generation: int, mode: int, scenario_id: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        path.write_bytes(b"\x00" * r0_control.EU4_R0_CONTROL_PAGE_SIZE)
    payload = r0_control.pack_control_snapshot(
        generation=generation, mode=mode, scenario_id=scenario_id
    )
    with path.open("r+b") as handle:
        with mmap.mmap(handle.fileno(), r0_control.EU4_R0_CONTROL_PAGE_SIZE) as mapping:
            mapping[0 : len(payload)] = payload
            mapping.flush()


def probe_env(control_path: Path, log_path: Path) -> dict[str, str]:
    return {
        "EU4_R0_CONTROL": str(control_path),
        "EU4_R0_LOG": str(log_path),
        "DYLD_INSERT_LIBRARIES": str(LIBRARY),
    }


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
        if kind == "ACK" and len(parts) >= 4:
            acks.append(
                {
                    "generation": int(parts[1]),
                    "frame_index": int(parts[2]),
                    "uptime_ns": int(parts[3]),
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
                }
            )
        elif kind == "S" and len(parts) >= 6:
            swaps.append(
                {
                    "uptime_ns": int(parts[1]),
                    "mode": int(parts[3]),
                    "swaps": int(parts[5]),
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
        census_frames = [f for f in parsed["frames"] if f["census_attempted"]]
        if not census_frames:
            raise base.BenchmarkError("R0 preflight did not attempt census readback")
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
