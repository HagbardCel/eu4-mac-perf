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
TRANSITION_DISCARD_FRAMES = 3


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
    uptime_ns: int = 0
    gl_error_observed: int = 0
    flush_ok: bool = True
    pthread_tid: int = 0


def load_frames_from_log(path: Path) -> list[FrameRecord]:
    frames: list[FrameRecord] = []
    for line in path.read_text(errors="replace").splitlines():
        parts = line.split(",")
        if len(parts) < 13 or parts[0] != "F":
            continue
        flush_ok = True
        uptime_ns = 0
        gl_error = 0
        pthread_tid = 0
        if len(parts) >= 18:
            uptime_ns = int(parts[13])
            gl_error = int(parts[14])
            flush_ok = bool(int(parts[15]))
            pthread_tid = int(parts[17])
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
                uptime_ns=uptime_ns,
                gl_error_observed=gl_error,
                flush_ok=flush_ok,
                pthread_tid=pthread_tid,
            )
        )
    return frames


def load_events(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    events: list[dict[str, Any]] = []
    for line in path.read_text(errors="replace").splitlines():
        if line.strip():
            events.append(json.loads(line))
    return events


def accepted_census_frames(frames: Iterable[FrameRecord]) -> list[FrameRecord]:
    return [
        frame
        for frame in frames
        if frame.census_attempted
        and frame.census_ok
        and frame.pause_verified
        and frame.width > 0
        and frame.flush_ok
        and frame.gl_error_observed == 0
    ]


def scenario_duration_seconds(frames: list[FrameRecord]) -> float | None:
    if len(frames) < 2 or not frames[0].uptime_ns:
        return None
    return (frames[-1].uptime_ns - frames[0].uptime_ns) / 1e9


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


def classify_scenario(
    frames: list[FrameRecord], *, min_frames: int, min_seconds: float, fps_hint: float
) -> dict[str, Any]:
    duration_s = scenario_duration_seconds(frames)
    if len(frames) < min_frames or (duration_s is not None and duration_s < min_seconds):
        return {
            "classification": "insufficient_data",
            "confidence": "low",
            "duration_frames": len(frames),
            "duration_seconds": duration_s,
            "duration_seconds_estimate": len(frames) / fps_hint if fps_hint else None,
        }
    equal_flags = [f.memcmp_equal_prev == 1 for f in frames[1:]]
    if frames and all(equal_flags):
        return {
            "classification": "static",
            "observation": "observed",
            "confidence": "medium" if len(frames) >= min_frames * 2 else "low",
            "duration_frames": len(frames),
            "duration_seconds": duration_s,
            "duration_seconds_estimate": len(frames) / fps_hint if fps_hint else None,
            "uncertainty": "short_window",
        }
    if equal_flags and not any(equal_flags):
        return {
            "classification": "continuous",
            "observation": "observed",
            "confidence": "medium",
            "duration_frames": len(frames),
            "duration_seconds": duration_s,
            "duration_seconds_estimate": len(frames) / fps_hint if fps_hint else None,
            "uncertainty": "no_stable_run",
        }
    transitions = sum(1 for flag in equal_flags if not flag)
    periodicity = "suspected" if transitions <= max(2, len(equal_flags) // 10) else "observed_nonstatic"
    return {
        "classification": "periodic_or_mixed",
        "observation": periodicity,
        "confidence": "low",
        "duration_frames": len(frames),
        "duration_seconds": duration_s,
        "duration_seconds_estimate": len(frames) / fps_hint if fps_hint else None,
        "uncertainty": "needs_longer_capture",
    }


def tile_mask_from_crc_series(frames: list[FrameRecord], tile_size: int = TILE_SIZE) -> dict[str, Any]:
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


def evaluate_positive_control_windows(
    pre: list[FrameRecord], post: list[FrameRecord], *, stable_tail: int = 5, stable_head: int = 5
) -> dict[str, Any]:
    if len(pre) < stable_tail or len(post) < stable_head:
        return {"status": "failed", "reason": "insufficient stable window frames"}
    pre_tail = pre[-stable_tail:]
    post_head = post[:stable_head]
    if all(f.crc64 == pre_tail[0].crc64 for f in pre_tail) is False:
        pass
    pre_crcs = {f.crc64 for f in pre_tail}
    post_crcs = {f.crc64 for f in post_head}
    if pre_crcs == post_crcs:
        return {"status": "failed", "reason": "pre/post stable windows identical"}
    return {
        "status": "passed",
        "reason": "stable windows differ",
        "pre_crcs": sorted(pre_crcs),
        "post_crcs": sorted(post_crcs),
    }


def positive_control_segments(
    frames: list[FrameRecord],
    events: list[dict[str, Any]],
    *,
    clock_offset_ns: int = 0,
    discard_frames: int = TRANSITION_DISCARD_FRAMES,
) -> dict[str, list[FrameRecord]] | None:
    action_events = [e for e in events if e.get("phase") == "ui_action_confirmed"]
    if not action_events:
        return None
    action_ns = int(action_events[0].get("monotonic_ns", 0)) + clock_offset_ns
    pre = [f for f in frames if f.uptime_ns and f.uptime_ns <= action_ns]
    post = [f for f in frames if f.uptime_ns and f.uptime_ns > action_ns]
    if discard_frames:
        post = post[discard_frames:]
    return {"before": pre, "after": post}


def build_report(
    *,
    log_path: Path,
    scenario_segments: dict[str, list[FrameRecord]],
    positive_control: dict[str, list[FrameRecord]] | None,
    fps_hint: float = 53.0,
    min_frames: int = 120,
    min_seconds: float = 2.0,
    positive_control_passed: bool = False,
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
        if (
            positive_control_passed
            and summary.get("classification") == "static"
            and summary.get("confidence") in {"medium", "high"}
        ):
            candidates.append(
                {
                    "scenario_id": scenario_id,
                    "verdict": "candidate_for_lossless_skip",
                    "confidence": summary["confidence"],
                }
            )

    positive = {"status": "not_run"}
    if positive_control and "before" in positive_control and "after" in positive_control:
        positive = evaluate_positive_control_windows(
            positive_control["before"], positive_control["after"]
        )
        positive_control_passed = positive.get("status") == "passed"

    if not positive_control_passed:
        candidates = []

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
    parser.add_argument("--events", type=Path)
    parser.add_argument("--clock-offset-ns", type=int, default=0)
    parser.add_argument("--fps-hint", type=float, default=53.0)
    args = parser.parse_args()
    frames = load_frames_from_log(args.log)
    by_scenario: dict[str, list[FrameRecord]] = {}
    for frame in accepted_census_frames(frames):
        key = str(frame.scenario_id)
        by_scenario.setdefault(key, []).append(frame)
    events = load_events(args.events) if args.events else []
    positive = positive_control_segments(frames, events, clock_offset_ns=args.clock_offset_ns)
    report = build_report(
        log_path=args.log,
        scenario_segments=by_scenario,
        positive_control=positive,
        fps_hint=args.fps_hint,
        positive_control_passed=positive is not None
        and evaluate_positive_control_windows(positive["before"], positive["after"]).get("status") == "passed",
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n")


if __name__ == "__main__":
    main()
