#!/usr/bin/env python3
"""Mmap control plane for profiler-off Gfx submission A-B-A-B-A experiments."""

from __future__ import annotations

import mmap
import os
import struct
import subprocess
import time
from pathlib import Path

import eu4_benchmark as base

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "benchmark/eu4_submission_experiment.c"
LIBRARY = ROOT / "benchmark/.build/libeu4_submission_experiment.dylib"

MAGIC = 0x53425545  # 'EUBS'
PROTOCOL_VERSION = 1
CONTROL_SIZE = 4096

MODE_REFERENCE = 0
MODE_CANDIDATE = 1

_HEADER = struct.Struct("<IIIIIIII")
_TAIL = struct.Struct("<QQ")


def build() -> None:
    LIBRARY.parent.mkdir(parents=True, exist_ok=True)
    command = [
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
    ]
    result = subprocess.run(command, capture_output=True, text=True, check=False)
    if result.returncode:
        raise base.BenchmarkError(f"Submission experiment dylib build failed: {result.stderr.strip()}")


class SubmissionControl:
    def __init__(self, path: Path, *, candidate_capability_id: int = 0):
        self.path = path
        self.candidate_capability_id = int(candidate_capability_id)
        self.file = path.open("r+b")
        self.map = mmap.mmap(self.file.fileno(), CONTROL_SIZE)
        self.command_generation = 0
        self._write_header(
            command_generation=0,
            requested_mode=MODE_REFERENCE,
            ack_generation=0,
            ack_mode=MODE_REFERENCE,
            capability_id=self.candidate_capability_id,
        )
        struct.pack_into("<Q", self.map, _HEADER.size, 0, 0)

    def _write_header(
        self,
        *,
        command_generation: int,
        requested_mode: int,
        ack_generation: int,
        ack_mode: int,
        capability_id: int,
    ) -> None:
        self.map[: _HEADER.size] = _HEADER.pack(
            MAGIC,
            PROTOCOL_VERSION,
            command_generation,
            requested_mode,
            ack_generation,
            ack_mode,
            capability_id,
            0,
        )

    def snapshot(self) -> dict[str, int]:
        magic, version, cmd_gen, req_mode, ack_gen, ack_mode, cap_id, _pad = _HEADER.unpack(
            self.map[: _HEADER.size]
        )
        hook_attempts, effective_actions = _TAIL.unpack(self.map[_HEADER.size : _HEADER.size + _TAIL.size])
        return {
            "magic": magic,
            "protocol_version": version,
            "command_generation": cmd_gen,
            "requested_mode": req_mode,
            "ack_generation": ack_gen,
            "ack_mode": ack_mode,
            "candidate_capability_id": cap_id,
            "candidate_hook_attempts": hook_attempts,
            "candidate_effective_actions": effective_actions,
        }

    def request_mode(self, mode: int) -> int:
        if mode not in (MODE_REFERENCE, MODE_CANDIDATE):
            raise ValueError(mode)
        self.command_generation += 1
        snap = self.snapshot()
        self._write_header(
            command_generation=self.command_generation,
            requested_mode=mode,
            ack_generation=snap["ack_generation"],
            ack_mode=snap["ack_mode"],
            capability_id=self.candidate_capability_id,
        )
        self.map.flush()
        return self.command_generation

    def wait_ack(self, generation: int, *, timeout_s: float = 8.0) -> dict[str, int]:
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            snap = self.snapshot()
            if snap["ack_generation"] >= generation:
                return snap
            time.sleep(0.01)
        raise base.BenchmarkError(f"Submission control did not acknowledge generation {generation}")

    def close(self) -> None:
        self.map.close()
        self.file.close()


def dylib_env(control_path: Path, probe_path: Path, *, active_capability_id: int) -> dict[str, str]:
    return {
        **os.environ,
        "DYLD_INSERT_LIBRARIES": f"{probe_path}:{LIBRARY}",
        "EU4_SUBMISSION_CONTROL": str(control_path),
        "EU4_SUBMISSION_ACTIVE_CAPABILITY": str(int(active_capability_id)),
    }
