#!/usr/bin/env python3
"""Offline analysis for R0 frame-evolution probe logs and tile masks."""

from __future__ import annotations

import argparse
import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

TILE_SIZE = 64


@dataclass(frozen=True)
class FrameRecord:
    frame_index: int
    mode: int
    scenario_id: int
    pause_verified: bool
    census_ok: bool
    width: int
    height: int
    memcmp_equal_prev: int
    census_attempted: bool
    crc64: int


def load_frames_from_log(path: Path) -> list[FrameRecord]:
    frames: list[FrameRecord] = []
    for line in path.read_text(errors="replace").splitlines():
        parts = line.split(",")
        if len(parts) < 13 or parts[0] != "F":
            continue
        frames.append(
            FrameRecord(
                frame_index=int(parts[1]),
                mode=int(parts[2]),
                scenario_id=int(parts[3]),
                pause_verified=bool(int(parts[4])),
                census_ok=bool(int(parts[5])),
                width=int(parts[7]),
                height=int(parts[8]),
                memcmp_equal_prev=int(parts[9]),
                census_attempted=bool(int(parts[10])),
                crc64=int(parts[11]),
            )
        )
    return frames


def accepted_census_frames(frames: Iterable[FrameRecord]) -> list[FrameRecord]:
    return [
        frame
        for frame in frames
        if frame.census_attempted and frame.census_ok and frame.pause_verified and frame.width > 0
    ]


def consecutive_equal_runs(frames: list[FrameRecord]) -> list[dict[str, Any]]:
    if len(frames) < 2:
        return []
    runs: list[dict[str, Any]] = []
    index = 1
    while index < len(frames):
        if frames[index].memcmp_equal_prev != 1:
            index += 1
            continue
        start = frames[index].frame_index
        length = 1
        index += 1
        while index < len(frames) and frames[index].memcmp_equal_prev == 1:
            length += 1
            index += 1
        runs.append({"start_frame": start, "length": length, "memcmp_equal": True})
    return runs


def classify_scenario(frames: list[FrameRecord], *, min_frames: int, min_seconds: float, fps_hint: float) -> dict[str, Any]:
    if len(frames) < min_frames:
        return {
            "classification": "insufficient_data",
            "confidence": "low",
            "duration_frames": len(frames),
            "duration_seconds_estimate": len(frames) / fps_hint if fps_hint else None,
        }
    equal_flags = [f.memcmp_equal_prev == 1 for f in frames[1:]]
    if frames and all(equal_flags):
        return {
            "classification": "static",
            "observation": "observed",
            "confidence": "medium" if len(frames) >= min_frames * 2 else "low",
            "duration_frames": len(frames),
            "duration_seconds_estimate": len(frames) / fps_hint if fps_hint else None,
            "uncertainty": "short_window",
        }
    if equal_flags and not any(equal_flags):
        return {
            "classification": "continuous",
            "observation": "observed",
            "confidence": "medium",
            "duration_frames": len(frames),
            "duration_seconds_estimate": len(frames) / fps_hint if fps_hint else None,
            "uncertainty": "no_stable_run",
        }
    # crude periodicity hint: count transitions
    transitions = sum(1 for flag in equal_flags if not flag)
    if transitions <= max(2, len(equal_flags) // 10):
        periodicity = "suspected"
    else:
        periodicity = "observed_nonstatic"
    return {
        "classification": "periodic_or_mixed",
        "observation": periodicity,
        "confidence": "low",
        "duration_frames": len(frames),
        "duration_seconds_estimate": len(frames) / fps_hint if fps_hint else None,
        "uncertainty": "needs_longer_capture",
    }


def tile_mask_from_crc_series(frames: list[FrameRecord], tile_size: int = TILE_SIZE) -> dict[str, Any]:
    """Without raw pixels, tile mask cannot be reconstructed from log-only data."""
    if len(frames) < 2:
        return {"status": "unavailable", "reason": "need raw captures for tile mask"}
    width = frames[0].width
    height = frames[0].height
    cols = math.ceil(width / tile_size) if width else 0
    rows = math.ceil(height / tile_size) if height else 0
    return {
        "status": "metadata_only",
        "tile_size": tile_size,
        "grid_cols": cols,
        "grid_rows": rows,
        "note": "attach raw pair files to compute changed tiles",
    }


def evaluate_positive_control(
    before: list[FrameRecord], after: list[FrameRecord]
) -> dict[str, Any]:
    if not before or not after:
        return {"status": "failed", "reason": "missing scenario frames"}
    last_before = before[-1]
    first_after = after[0]
    if first_after.memcmp_equal_prev == 1 and last_before.crc64 == first_after.crc64:
        return {"status": "failed", "reason": "visible change not detected in memcmp/crc"}
    return {"status": "passed", "reason": "frame buffer changed between control segments"}


def build_report(
    *,
    log_path: Path,
    scenario_segments: dict[str, list[FrameRecord]],
    positive_control: dict[str, list[FrameRecord]] | None,
    fps_hint: float = 53.0,
    min_frames: int = 120,
    min_seconds: float = 2.0,
) -> dict[str, Any]:
    all_frames = load_frames_from_log(log_path)
    candidates: list[dict[str, Any]] = []
    scenarios_out: dict[str, Any] = {}
    for scenario_id, segment in scenario_segments.items():
        accepted = accepted_census_frames(segment)
        summary = classify_scenario(accepted, min_frames=min_frames, min_seconds=min_seconds, fps_hint=fps_hint)
        scenarios_out[scenario_id] = {
            "summary": summary,
            "equal_runs": consecutive_equal_runs(accepted),
            "spatial": tile_mask_from_crc_series(accepted),
            "accepted_frames": len(accepted),
        }
        if summary.get("classification") == "static" and summary.get("confidence") in {"medium", "high"}:
            candidates.append(
                {
                    "scenario_id": scenario_id,
                    "verdict": "candidate_for_lossless_skip",
                    "confidence": summary["confidence"],
                }
            )

    positive = {"status": "not_run"}
    if positive_control and "before" in positive_control and "after" in positive_control:
        positive = evaluate_positive_control(positive_control["before"], positive_control["after"])

    return {
        "log_path": str(log_path),
        "frame_records_total": len(all_frames),
        "scenarios": scenarios_out,
        "candidate_for_lossless_skip": candidates,
        "positive_control": positive,
        "r1_dirty_state_proof": "unresolved",
        "pass_cacheability": "not_claimed",
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("log", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--fps-hint", type=float, default=53.0)
    args = parser.parse_args()
    frames = load_frames_from_log(args.log)
    by_scenario: dict[str, list[FrameRecord]] = {}
    for frame in accepted_census_frames(frames):
        key = str(frame.scenario_id)
        by_scenario.setdefault(key, []).append(frame)
    report = build_report(log_path=args.log, scenario_segments=by_scenario, positive_control=None, fps_hint=args.fps_hint)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n")


if __name__ == "__main__":
    main()
