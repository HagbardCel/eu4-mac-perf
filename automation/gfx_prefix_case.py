"""Bounded portable reproduction for a captured graphics prefix.

A full sealed frame can exceed the portable-case budget.  This module derives a
smaller frame containing only the first N operations and the resources they
reference.  The derived frame is explicitly labelled with the original
seal/manifest/event-stream/observation digests and is never presented as the
original full seal.  A derived case does not inherit the authority of the
complete seal it was cut from.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

from automation import gfx_frame_graph, gfx_frame
from automation.gfx_artifacts import is_leaf_name, sha256_file as _sha
from automation.gfx_independent_replay import _path_overlap

SCHEMA = "gfx-observation-case/v1"


class PrefixCaseError(RuntimeError):
    pass


def build_prefix_frame(frame_dir: Path | str, end: int,
                       destination: Path | str) -> dict:
    """Copy events 0..end and every referenced blob into a derived frame."""
    frame_dir = Path(frame_dir).expanduser().resolve()
    destination = Path(destination).expanduser().resolve()
    if _path_overlap(destination, frame_dir):
        raise PrefixCaseError(
            "derived case destination overlaps the source frame")
    if destination.exists():
        raise PrefixCaseError(
            "derived case destination already exists; choose a new path")
    if end < 0:
        raise PrefixCaseError("prefix end must be non-negative")
    trace = gfx_frame.load_trace(frame_dir, verify_blobs=True)
    seal = gfx_frame.load_seal(frame_dir, trace=trace)
    events = trace.get("_events") or []
    if end >= len(events):
        raise PrefixCaseError("prefix end is beyond the event stream")
    prefix = trace.get("_prefix")
    if prefix is None or prefix.get("mode") != "prefix":
        raise PrefixCaseError(
            "frame has no contiguous prefix; a derived case cannot promote "
            "selected checkpoints")
    if prefix["end"] < end:
        raise PrefixCaseError("frame has no complete prefix through the end")
    selected = events[:end + 1]
    post = prefix["post_events"][:end + 1]
    initial = prefix["initial"]
    if len(post) != end + 1:
        raise PrefixCaseError("prefix observations are not contiguous")
    (destination / "blobs").mkdir(parents=True)
    blob_names = gfx_frame.event_blob_names(selected)
    for record in [initial, *post]:
        blob_names |= gfx_frame.event_blob_names([record])
    for name in sorted(blob_names):
        if not is_leaf_name(name):
            raise PrefixCaseError(f"invalid blob member: {name}")
        source = frame_dir / "blobs" / name
        if not source.is_file():
            raise PrefixCaseError(f"referenced blob is absent: {name}")
        shutil.copyfile(source, destination / "blobs" / name)
    events_text = "".join(json.dumps(event, sort_keys=True) + "\n"
                          for event in selected)
    (destination / "frame-events.jsonl").write_text(events_text)
    observations_text = "".join(json.dumps(record, sort_keys=True) + "\n"
                                for record in [initial, *post])
    observation_path = destination / "prefix-observations.jsonl"
    observation_path.write_text(observations_text)
    reference = trace.get("frame") or {}
    reference_name = reference.get("reference")
    if not is_leaf_name(reference_name):
        raise PrefixCaseError("invalid reference member")
    shutil.copyfile(frame_dir / reference_name, destination / reference_name)
    prefix_block = dict(trace.get("prefix") or {})
    prefix_block.update({
        "end": end,
        "status": "complete",
        "count": end + 1,
        "post_event_count": end + 1,
        "initial": True,
        "observation_file": observation_path.name,
        "observation_sha256": _sha(observation_path),
        "observation_bytes": observation_path.stat().st_size,
    })
    manifest = {key: value for key, value in trace.items() if not key.startswith("_")}
    manifest.update({
        "schema": gfx_frame.TRACE_SCHEMA,
        "complete": True,
        "reason": "derived_prefix",
        "truncated": False,
        "events": len(selected),
        "prefix": prefix_block,
        "default_checkpoint_events": [
            index for index, event in enumerate(selected)
            if event.get("default_checkpoint")],
        "default_checkpoint_count": sum(
            1 for event in selected if event.get("default_checkpoint")),
        "derived_from": {
            "original_session_id": seal.get("session_id"),
            "original_capture_frame_id": seal.get("capture_frame_id"),
            "original_operation_count": len(events),
            "original_manifest_sha256": seal.get("manifest_sha256"),
            "original_event_stream_sha256": seal.get("event_stream_sha256"),
            "original_reference_sha256": seal.get("reference_sha256"),
            "original_prefix_end": prefix["end"],
            "selected_events": [0, end],
            "scope": "derived prefix reproduction; not the original full seal",
        },
    })
    (destination / "frame-trace.json").write_text(
        json.dumps(manifest, indent=2) + "\n")
    manifest_sha = _sha(destination / "frame-trace.json")
    draw_count = sum(1 for event in selected if event.get("kind") == "draw")
    clear_count = sum(1 for event in selected if event.get("kind") == "clear")
    copy_count = sum(1 for event in selected
                     if event.get("kind") in ("resolve", "copy"))
    derived_seal = {
        "schema": gfx_frame.SEAL_SCHEMA,
        "sealed": True,
        "complete": True,
        "struct_size": 2256,
        "abi_version": gfx_frame.SEAL_ABI_VERSION,
        "capture_frame_id": seal.get("capture_frame_id"),
        "session_id": seal.get("session_id"),
        "pid": seal.get("pid"),
        "process_start_ns": seal.get("process_start_ns"),
        "boundary": seal.get("boundary"),
        "present_index": seal.get("present_index"),
        "scene_index": seal.get("scene_index"),
        "operation_count": len(selected),
        "draw_count": draw_count,
        "clear_count": clear_count,
        "copy_count": copy_count,
        "event_stream_file": "frame-events.jsonl",
        "event_stream_bytes": (destination / "frame-events.jsonl").stat().st_size,
        "event_stream_sha256": _sha(destination / "frame-events.jsonl"),
        "event_stream_verified": True,
        "width": seal.get("width"),
        "height": seal.get("height"),
        "pixel_format": seal.get("pixel_format"),
        "trace_complete": True,
        "trace_integrity": True,
        "readback_ok": True,
        "reference_file": reference.get("reference"),
        "reference_bytes": reference.get("reference_bytes"),
        "reference_sha256": reference.get("reference_sha256"),
        "manifest_file": "frame-trace.json",
        "manifest_sha256": manifest_sha,
        "capture_dir": str(destination),
        "gpu_status": seal.get("gpu_status"),
        "failure_reason": "",
        "derived_from": manifest["derived_from"],
    }
    (destination / "frame-seal.json").write_text(
        json.dumps(derived_seal, indent=2) + "\n")
    derived_graph = gfx_frame_graph.build(destination)
    derived_prefix = derived_graph.get("prefix") or {}
    if derived_prefix.get("status") != "complete":
        raise PrefixCaseError(
            "derived events are not a self-contained prefix: "
            + "; ".join(derived_prefix.get("problems") or
                        [str(derived_prefix.get("status") or "unknown")]))
    return {
        "frame_dir": str(destination),
        "events": len(selected),
        "end": end,
        "bytes": sum(path.stat().st_size for path in destination.rglob("*")
                     if path.is_file()),
        "derived_from": manifest["derived_from"],
    }
