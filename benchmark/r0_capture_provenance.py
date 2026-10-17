#!/usr/bin/env python3
"""Shared capture artifact verification for session orchestrator and offline analyzers."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from r0_crc64 import crc64_ecma


def load_capture_sidecar(capture_dir: Path, generation: int) -> dict[str, Any] | None:
    path = capture_dir / f"capture_{generation}.json"
    if not path.is_file():
        return None
    return json.loads(path.read_text())


def find_frame_for_generation_and_index(
    frames: list[dict[str, Any]], generation: int, frame_index: int
) -> dict[str, Any] | None:
    for frame in frames:
        if (
            frame.get("control_generation") == generation
            and frame.get("frame_index") == frame_index
            and frame.get("census_attempted")
        ):
            return frame
    return None


def _event_uptime_ns(events: list[dict[str, Any]], name: str) -> int | None:
    for item in events:
        if item.get("phase") == name or item.get("event") == name:
            value = item.get("event_uptime_ns")
            if value is not None:
                return int(value)
    return None


def verify_capture_provenance(
    capture_dir: Path,
    generation: int,
    log_frames: list[dict[str, Any]],
    *,
    events: list[dict[str, Any]] | None = None,
    window: str | None = None,
) -> dict[str, Any]:
    """window: 'pre' (uptime <= pre_window_end) or 'post' (uptime >= post_window_start)."""
    sidecar = load_capture_sidecar(capture_dir, generation)
    if not sidecar or not sidecar.get("write_ok"):
        return {"status": "failed", "reason": "missing_or_incomplete_sidecar"}
    rgba_path = capture_dir / f"capture_{generation}.rgba"
    if not rgba_path.is_file():
        return {"status": "failed", "reason": "missing_rgba"}
    width = int(sidecar["width"])
    height = int(sidecar["height"])
    expected_bytes = width * height * 4
    data = rgba_path.read_bytes()
    if len(data) != expected_bytes:
        return {"status": "failed", "reason": "rgba_size_mismatch"}
    frame_index = int(sidecar["frame_index"])
    frame = find_frame_for_generation_and_index(log_frames, generation, frame_index)
    if not frame:
        return {"status": "failed", "reason": "no_matching_f_record"}
    mismatches: list[str] = []
    for key in ("control_generation", "width", "height", "crc64", "scenario_id"):
        if int(sidecar.get(key, -1)) != int(frame.get(key, -2)):
            mismatches.append(key)
    if int(sidecar.get("frame_index", -1)) != int(frame.get("frame_index", -2)):
        mismatches.append("frame_index")
    if mismatches:
        return {"status": "failed", "reason": "log_sidecar_mismatch", "fields": mismatches}
    if not (
        frame.get("census_ok")
        and frame.get("pause_verified")
        and frame.get("flush_ok")
        and int(frame.get("gl_error_observed", 0)) == 0
    ):
        return {"status": "failed", "reason": "frame_quality_gate_failed"}
    computed = crc64_ecma(data)
    sidecar_crc = int(sidecar["crc64"])
    log_crc = int(frame.get("crc64", -1))
    if computed != sidecar_crc or computed != log_crc:
        return {"status": "failed", "reason": "crc64_mismatch", "computed": computed}
    frame_uptime = frame.get("uptime_ns")
    if frame_uptime is None or int(frame_uptime) <= 0:
        return {"status": "failed", "reason": "missing_frame_uptime_ns"}
    uptime_ns = int(frame_uptime)
    if "uptime_ns" not in sidecar:
        return {"status": "failed", "reason": "missing_sidecar_uptime_ns"}
    if int(sidecar["uptime_ns"]) != uptime_ns:
        return {"status": "failed", "reason": "sidecar_frame_uptime_mismatch"}
    if window in {"pre", "post"}:
        if not events:
            return {"status": "failed", "reason": "missing_acquisition_events"}
        if window == "pre":
            pre_end = _event_uptime_ns(events, "pre_window_end")
            if pre_end is None:
                return {"status": "failed", "reason": "missing_pre_window_end"}
            if uptime_ns > pre_end:
                return {"status": "failed", "reason": "capture_outside_pre_window"}
        else:
            post_start = _event_uptime_ns(events, "post_window_start")
            if post_start is None:
                return {"status": "failed", "reason": "missing_post_window_start"}
            if uptime_ns < post_start:
                return {"status": "failed", "reason": "capture_outside_post_window"}
    return {
        "status": "verified",
        "sidecar": sidecar,
        "frame": frame,
        "crc64": computed,
        "width": width,
        "height": height,
    }
