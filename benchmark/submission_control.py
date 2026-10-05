#!/usr/bin/env python3
"""Mmap control plane for profiler-off Gfx submission experiments (protocol v3)."""

from __future__ import annotations

import mmap
import os
import struct
import subprocess
import time
from pathlib import Path

import eu4_benchmark as base

ROOT = Path(__file__).resolve().parents[1]
BENCH = ROOT / "benchmark"
SUBMISSION_DYLIB_SOURCES_OBSERVER = (
    BENCH / "eu4_submission_experiment.c",
    BENCH / "eu4_submission_observation.c",
    BENCH / "eu4_submission_mesh_site.c",
    BENCH / "eu4_submission_mesh_chain.c",
    BENCH / "eu4_submission_mesh_install.c",
    BENCH / "eu4_submission_mesh_thunk.S",
    BENCH / "eu4_submission_renderbuckets_entry_thunk.S",
    BENCH / "subrecord_equivalence.c",
)
SUBMISSION_DYLIB_SOURCES_BORDER = (
    BENCH / "eu4_submission_experiment.c",
    BENCH / "border_loop_classifier.c",
    BENCH / "eu4_submission_border_batch.c",
    BENCH / "eu4_submission_border_gl_interpose.c",
    BENCH / "eu4_submission_border_loop_head.c",
    BENCH / "eu4_submission_border_loop_head_gateway.c",
    BENCH / "eu4_submission_border_loop_head_gateway.S",
    BENCH / "eu4_submission_border_loop_head_install.c",
    BENCH / "eu4_submission_border_install.c",
)
LIBRARY = BENCH / ".build/libeu4_submission_experiment.dylib"
LIBRARY_BORDER = BENCH / ".build/libeu4_submission_border_multidraw.dylib"
HARNESS = ROOT / "benchmark/.build/submission_dual_dylib_harness"
HARNESS_SOURCE = ROOT / "tests/submission_dual_dylib_harness.c"

MAGIC = 0x53425545  # 'EUBS'
PROTOCOL_VERSION = 3
PROTOCOL_VERSION_V2 = 2
CONTROL_SIZE = 4096

MODE_REFERENCE = 0
MODE_CANDIDATE = 1

OBS_DISARMED = 0
OBS_ARM = 1
OBS_FREEZE = 2
OBS_ACK_ARMED = 1
OBS_ACK_FROZEN = 2

_HEADER = struct.Struct("<IIIIIIII")
_META = struct.Struct("<IIII")
_OBS = struct.Struct("<IIII")
_BORDER_FLAGS_OFFSET = 56
_COUNTER_REGION_OFFSET = 64

from submission_counter_schema import COUNTER_COUNT, COUNTER_SLOTS  # noqa: E402


def build() -> None:
    """Build observer dylib (compiled capability 1 only)."""
    _build_dylib(LIBRARY, compiled_capability=1, sources=SUBMISSION_DYLIB_SOURCES_OBSERVER)
    _build_harness()


def build_border_multidraw() -> None:
    """Build border multidraw dylib (capability 2)."""
    _build_dylib(LIBRARY_BORDER, compiled_capability=2, sources=SUBMISSION_DYLIB_SOURCES_BORDER)


def build_test_dylib(output: Path, *, compiled_capability: int) -> None:
    """Test-only dylib with explicit capability (harness / regression)."""
    sources = SUBMISSION_DYLIB_SOURCES_BORDER if int(compiled_capability) == 2 else SUBMISSION_DYLIB_SOURCES_OBSERVER
    _build_dylib(output, compiled_capability=int(compiled_capability), sources=sources)


def _build_dylib(output: Path, *, compiled_capability: int, sources: tuple[Path, ...]) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    dylib_command = [
        "clang",
        "-arch",
        "x86_64",
        "-O2",
        "-Wall",
        "-Wextra",
        "-Werror",
        "-mno-avx",
        "-dynamiclib",
        "-framework",
        "OpenGL",
        "-I",
        str(BENCH),
        f"-DEU4_SUBMISSION_COMPILED_CAPABILITY={int(compiled_capability)}",
        "-o",
        str(output),
        *[str(path) for path in sources],
    ]
    result = subprocess.run(dylib_command, capture_output=True, text=True, check=False)
    if result.returncode:
        raise base.BenchmarkError(f"Submission experiment build failed: {result.stderr.strip()}")


def _build_harness() -> None:
    commands = (
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


def _read_counters_blob(map_view: mmap.mmap | memoryview) -> list[int]:
    start = _COUNTER_REGION_OFFSET
    end = start + COUNTER_COUNT * 8
    return list(struct.unpack(f"<{COUNTER_COUNT}Q", map_view[start:end]))


def read_snapshot(path: Path) -> dict[str, int]:
    data = path.read_bytes()
    magic, version, cmd_gen, req_mode, ack_gen, ack_mode, req_cap, adv_cap = _HEADER.unpack(
        data[: _HEADER.size]
    )
    snap: dict[str, int] = {
        "magic": magic,
        "protocol_version": version,
        "command_generation": cmd_gen,
        "requested_mode": req_mode,
        "ack_generation": ack_gen,
        "ack_mode": ack_mode,
        "requested_capability_id": req_cap,
        "advertised_capability_id": adv_cap,
    }
    if version >= PROTOCOL_VERSION:
        snap.update(dict(zip(("counter_schema_version", "counter_count", "supported_hypothesis_mask", "safety_qualified_hypothesis_mask"), _META.unpack(data[_HEADER.size : _HEADER.size + _META.size]))))
        obs = _OBS.unpack(data[_HEADER.size + _META.size : _HEADER.size + _META.size + _OBS.size])
        snap["observation_generation"] = obs[0]
        snap["observation_requested_state"] = obs[1]
        snap["observation_ack_generation"] = obs[2]
        snap["observation_ack_state"] = obs[3]
        counters = list(struct.unpack(f"<{COUNTER_COUNT}Q", data[_COUNTER_REGION_OFFSET : _COUNTER_REGION_OFFSET + COUNTER_COUNT * 8]))
        for name, slot in COUNTER_SLOTS.items():
            snap[name] = counters[slot]
    else:
        ticks, site_entries, pair_hits, effective = struct.unpack("<QQQQ", data[_HEADER.size : _HEADER.size + 32])
        snap["control_ticks"] = ticks
        snap["candidate_site_entries"] = site_entries
        snap["eligible_pair_hits"] = pair_hits
        snap["candidate_effective_actions"] = effective
    snap["candidate_hook_attempts"] = snap.get("control_ticks", 0)
    if "eligible_pair_hits" not in snap:
        snap["eligible_pair_hits"] = snap.get("legacy_eligible_pair_hits", 0)
    if "candidate_site_entries" not in snap:
        snap["candidate_site_entries"] = snap.get("site_entries", 0)
    if "candidate_effective_actions" not in snap:
        snap["candidate_effective_actions"] = snap.get("effective_actions", 0)
    return snap


class SubmissionControl:
    def __init__(self, path: Path, *, candidate_capability_id: int = 0):
        self.path = path
        self.candidate_capability_id = int(candidate_capability_id)
        self.file = path.open("r+b")
        self.map = mmap.mmap(self.file.fileno(), CONTROL_SIZE)
        self.command_generation = 0
        self.observation_generation = 0
        self._write_header(
            command_generation=0,
            requested_mode=MODE_REFERENCE,
            ack_generation=0,
            ack_mode=MODE_REFERENCE,
            requested_capability_id=self.candidate_capability_id,
            advertised_capability_id=0,
        )
        self._write_meta()
        self._write_observation(0, OBS_DISARMED, 0, OBS_DISARMED)
        self._zero_counters()

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

    def _write_meta(self) -> None:
        from submission_counter_schema import (
            COUNTER_SCHEMA_VERSION,
            COUNTER_COUNT,
            SAFETY_HYPOTHESIS_MASK_DEFAULT,
            SUPPORTED_HYPOTHESIS_MASK_DEFAULT,
        )

        offset = _HEADER.size
        self.map[offset : offset + _META.size] = _META.pack(
            COUNTER_SCHEMA_VERSION,
            COUNTER_COUNT,
            SUPPORTED_HYPOTHESIS_MASK_DEFAULT,
            SAFETY_HYPOTHESIS_MASK_DEFAULT,
        )

    def _write_observation(self, generation: int, requested: int, ack_gen: int, ack_state: int) -> None:
        offset = _HEADER.size + _META.size
        self.map[offset : offset + _OBS.size] = _OBS.pack(generation, requested, ack_gen, ack_state)

    def _zero_counters(self) -> None:
        self.map[_COUNTER_REGION_OFFSET : _COUNTER_REGION_OFFSET + COUNTER_COUNT * 8] = bytes(COUNTER_COUNT * 8)

    def snapshot(self) -> dict[str, int]:
        return read_snapshot(self.path)

    def request_observation_arm(self) -> int:
        self.observation_generation += 1
        snap = self.snapshot()
        self._write_observation(self.observation_generation, OBS_ARM, snap.get("observation_ack_generation", 0), snap.get("observation_ack_state", 0))
        self.map.flush()
        return self.observation_generation

    def request_observation_freeze(self) -> int:
        self.observation_generation += 1
        snap = self.snapshot()
        self._write_observation(
            self.observation_generation, OBS_FREEZE, snap.get("observation_ack_generation", 0), snap.get("observation_ack_state", 0)
        )
        self.map.flush()
        return self.observation_generation

    def wait_observation_ack(self, generation: int, *, expected_state: int, timeout_s: float = 8.0) -> dict[str, int]:
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            snap = self.snapshot()
            if snap.get("observation_ack_generation", 0) >= generation and snap.get("observation_ack_state") == expected_state:
                return snap
            time.sleep(0.01)
        raise base.BenchmarkError(f"Observation ack {expected_state} not received for generation {generation}")

    def snapshot_frozen_candidate_bank(self) -> dict[str, int]:
        return self.snapshot()

    def set_border_flags(self, *, minimal: bool = False, mutate: bool = False) -> None:
        flags = (1 if minimal else 0) | (2 if mutate else 0)
        self.map[_BORDER_FLAGS_OFFSET : _BORDER_FLAGS_OFFSET + 4] = struct.pack("<I", flags)
        self.map.flush()

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


def dylib_env(control_path: Path, probe_path: Path, *, submission_dylib: Path | None = None) -> dict[str, str]:
    lib = submission_dylib or LIBRARY
    return {
        **os.environ,
        "DYLD_INSERT_LIBRARIES": f"{probe_path}:{lib}",
        "EU4_SUBMISSION_CONTROL": str(control_path),
    }


def engagement_smoke_gate(phases: list[dict], *, expected_capability_id: int) -> dict:
    """Per-phase capability 1 observer gates (no Pareto)."""
    phase_reports: list[dict] = []
    all_ok = True
    for phase in phases:
        cv = phase.get("control_validation") or {}
        role = phase.get("role")
        site = int(cv.get("candidate_site_entries_delta", 0))
        hits = int(cv.get("eligible_pair_hits_delta", 0))
        actions = int(cv.get("effective_actions_delta", 0))
        status = cv.get("status")
        if role == "reference":
            phase_ok = status == "passed" and site > 0 and hits == 0 and actions == 0
        elif role == "candidate":
            phase_ok = status == "engagement_only" and site > 0 and hits > 0 and actions == 0
        else:
            phase_ok = False
        if not phase_ok:
            all_ok = False
        phase_reports.append(
            {
                "name": phase.get("name"),
                "role": role,
                "control_validation": cv,
                "phase_ok": phase_ok,
            }
        )
    return {
        "status": "engagement_only" if all_ok else "failed",
        "expected_candidate_capability_id": expected_capability_id,
        "phases": phase_reports,
        "pareto_eligible": False,
    }
