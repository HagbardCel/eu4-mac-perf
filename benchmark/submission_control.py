#!/usr/bin/env python3
"""Mmap control plane for profiler-off Gfx submission A-B-A-B-A experiments (protocol v2)."""

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
HARNESS = ROOT / "benchmark/.build/submission_dual_dylib_harness"
HARNESS_SOURCE = ROOT / "tests/submission_dual_dylib_harness.c"

MAGIC = 0x53425545  # 'EUBS'
PROTOCOL_VERSION = 2
CONTROL_SIZE = 4096

MODE_REFERENCE = 0
MODE_CANDIDATE = 1

_HEADER = struct.Struct("<IIIIIIII")
_COUNTER = struct.Struct("<QQQQ")
_COUNTER_BYTES = _COUNTER.size


def build() -> None:
    LIBRARY.parent.mkdir(parents=True, exist_ok=True)
    commands = (
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
            str(HARNESS),
            str(HARNESS_SOURCE),
        ],
    )
    for command in commands:
        result = subprocess.run(command, capture_output=True, text=True, check=False)
        if result.returncode:
            raise base.BenchmarkError(f"Submission experiment build failed: {result.stderr.strip()}")


def read_snapshot(path: Path) -> dict[str, int]:
    data = Path(path).read_bytes()[: _HEADER.size + _COUNTER_BYTES]
    magic, version, cmd_gen, req_mode, ack_gen, ack_mode, req_cap, adv_cap = _HEADER.unpack(
        data[: _HEADER.size]
    )
    ticks, site_entries, pair_hits, effective = _COUNTER.unpack(
        data[_HEADER.size : _HEADER.size + _COUNTER_BYTES]
    )
    return {
        "magic": magic,
        "protocol_version": version,
        "command_generation": cmd_gen,
        "requested_mode": req_mode,
        "ack_generation": ack_gen,
        "ack_mode": ack_mode,
        "requested_capability_id": req_cap,
        "advertised_capability_id": adv_cap,
        "control_ticks": ticks,
        "candidate_site_entries": site_entries,
        "eligible_pair_hits": pair_hits,
        "candidate_effective_actions": effective,
        "candidate_hook_attempts": ticks,
    }


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
            requested_capability_id=self.candidate_capability_id,
            advertised_capability_id=0,
        )
        self.map[_HEADER.size : _HEADER.size + _COUNTER_BYTES] = _COUNTER.pack(0, 0, 0, 0)

    def _write_header(
        self,
        *,
        command_generation: int,
        requested_mode: int,
        ack_generation: int,
        ack_mode: int,
        requested_capability_id: int,
        advertised_capability_id: int | None = None,
    ) -> None:
        if advertised_capability_id is None:
            snap = self.snapshot()
            advertised_capability_id = snap.get("advertised_capability_id", 0)
        self.map[: _HEADER.size] = _HEADER.pack(
            MAGIC,
            PROTOCOL_VERSION,
            command_generation,
            requested_mode,
            ack_generation,
            ack_mode,
            requested_capability_id,
            advertised_capability_id,
        )

    def snapshot(self) -> dict[str, int]:
        magic, version, cmd_gen, req_mode, ack_gen, ack_mode, req_cap, adv_cap = _HEADER.unpack(
            self.map[: _HEADER.size]
        )
        ticks, site_entries, pair_hits, effective = _COUNTER.unpack(
            self.map[_HEADER.size : _HEADER.size + _COUNTER_BYTES]
        )
        return {
            "magic": magic,
            "protocol_version": version,
            "command_generation": cmd_gen,
            "requested_mode": req_mode,
            "ack_generation": ack_gen,
            "ack_mode": ack_mode,
            "requested_capability_id": req_cap,
            "advertised_capability_id": adv_cap,
            "control_ticks": ticks,
            "candidate_site_entries": site_entries,
            "eligible_pair_hits": pair_hits,
            "candidate_effective_actions": effective,
            "candidate_hook_attempts": ticks,
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
            requested_capability_id=self.candidate_capability_id,
            advertised_capability_id=snap["advertised_capability_id"],
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
        "EU4_SUBMISSION_COMPILED_CAPABILITY": str(int(active_capability_id)),
        "EU4_SUBMISSION_STUB_CANDIDATE_HOOK": "1",
        "EU4_SUBMISSION_STUB_CANDIDATE_SITE": "1",
    }


def engagement_smoke_gate(phases: list[dict], *, expected_capability_id: int) -> dict:
    """Capability 1 observer: site entries without effective actions; never Pareto passed."""
    candidate_phases = [p for p in phases if p.get("role") == "candidate"]
    site = sum((p.get("control_validation") or {}).get("candidate_site_entries_delta", 0) for p in candidate_phases)
    hits = sum((p.get("control_validation") or {}).get("eligible_pair_hits_delta", 0) for p in candidate_phases)
    actions = sum(
        (p.get("control_validation") or {}).get("effective_actions_delta", 0) for p in candidate_phases
    )
    ok = site > 0 and actions == 0
    if expected_capability_id == 1:
        ok = ok and hits >= 0
    return {
        "status": "engagement_only" if ok else "failed",
        "expected_candidate_capability_id": expected_capability_id,
        "candidate_site_entries_delta": site,
        "eligible_pair_hits_delta": hits,
        "effective_actions_delta": actions,
        "pareto_eligible": False,
    }
