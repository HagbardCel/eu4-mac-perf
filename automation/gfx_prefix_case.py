"""Bounded portable reproduction for an exact-prefix divergence.

A full 283-operation sealed frame can exceed the portable-case budget.  This
module derives a smaller frame containing only the first N operations and the
resources they reference.  The derived frame is explicitly labelled with the
original seal/manifest/event-stream/prefix digests; it is never presented as the
original full seal.
"""

from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path

from automation import gfx_frame_graph, gfx_prefix_check, m6_frame

SCHEMA = "m7-prefix-case/v1"


class PrefixCaseError(RuntimeError):
    pass


def _sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _iter_blob_members(value):
    if isinstance(value, dict):
        if isinstance(value.get("file"), str) and value.get("file"):
            yield value
        for item in value.values():
            yield from _iter_blob_members(item)
    elif isinstance(value, list):
        for item in value:
            yield from _iter_blob_members(item)


def _event_blob_names(event: dict) -> set[str]:
    names = {member["file"] for member in _iter_blob_members(event)}
    for attribute in event.get("attributes") or []:
        if isinstance(attribute, dict) and attribute.get("data"):
            names.add(attribute["data"])
    return {name for name in names if name}


def build_prefix_frame(frame_dir: Path | str, end: int,
                       destination: Path | str) -> dict:
    """Copy events 0..end and every referenced blob into a derived frame."""
    frame_dir = Path(frame_dir).expanduser().resolve()
    destination = Path(destination).expanduser().resolve()
    if end < 0:
        raise PrefixCaseError("prefix end must be non-negative")
    seal = m6_frame.load_seal(frame_dir)
    trace = m6_frame.load_trace(frame_dir, verify_blobs=True)
    events = trace.get("_events") or []
    if end >= len(events):
        raise PrefixCaseError("prefix end is beyond the event stream")
    prefix = m6_frame.load_prefix_observations(trace, frame_dir)
    if prefix is None or prefix["end"] < end:
        raise PrefixCaseError("frame has no complete prefix through the end")
    selected = events[:end + 1]
    post = prefix["post_events"][:end + 1]
    initial = prefix["initial"]
    if len(post) != end + 1:
        raise PrefixCaseError("prefix observations are not contiguous")
    if destination.exists():
        shutil.rmtree(destination)
    (destination / "blobs").mkdir(parents=True)
    blob_names: set[str] = set()
    for event in selected:
        blob_names |= _event_blob_names(event)
    for record in [initial, *post]:
        blob_names |= _event_blob_names(record)
    for name in sorted(blob_names):
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
    shutil.copyfile(frame_dir / str(reference["reference"]),
                    destination / str(reference["reference"]))
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
        "observations": [initial, *post],
    })
    manifest = dict(trace)
    manifest.pop("_events", None)
    manifest.pop("_draws", None)
    manifest.pop("_frame_dir", None)
    manifest.pop("_prefix", None)
    manifest.pop("_blob_count_verified", None)
    manifest.update({
        "schema": m6_frame.TRACE_SCHEMA,
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
        "schema": m6_frame.SEAL_SCHEMA,
        "sealed": True,
        "complete": True,
        "struct_size": 2256,
        "abi_version": m6_frame.SEAL_ABI_VERSION,
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
    return {
        "frame_dir": str(destination),
        "events": len(selected),
        "end": end,
        "bytes": sum(path.stat().st_size for path in destination.rglob("*")
                     if path.is_file()),
        "derived_from": manifest["derived_from"],
    }


def build_case(frame_dir: Path | str, end: int,
               destination: Path | str) -> dict:
    """Build and verify a bounded derived-prefix case."""
    frame = build_prefix_frame(frame_dir, end, destination)
    derived = Path(frame["frame_dir"])
    result = gfx_prefix_check.evaluate(
        derived, None, require_end=end, trusted_execution=True)
    case = {
        "schema": SCHEMA,
        "case_id": f"m7-prefix-{end}-{frame['derived_from']['original_session_id']}",
        "frame": frame,
        "checker": {
            "verdict": result.get("verdict"),
            "first_divergence": result.get("first_divergence"),
            "verified_prefix": result.get("verified_prefix"),
            "problems_count": result.get("problems_count"),
        },
        "files": [
            {"path": path.relative_to(derived).as_posix(),
             "bytes": path.stat().st_size, "sha256": _sha(path)}
            for path in sorted(derived.rglob("*")) if path.is_file()
        ],
        "reproduction": {
            "command": [
                "python3.14", "-c",
                "from automation import gfx_prefix_check; "
                f"gfx_prefix_check.evaluate({str(derived)!r}, None, "
                f"require_end={end}, trusted_execution=True)",
            ],
            "note": "derived prefix; not the original full seal",
        },
    }
    (derived.parent / "case.json").write_text(json.dumps(case, indent=2) + "\n")
    return case


def build_event_subset_frame(frame_dir: Path | str, event_indices: list[int],
                             destination: Path | str) -> dict:
    """Copy an ordered event subset into a derived, explicitly labelled frame.

    Events are reindexed to 0..N-1.  Post-event observations are remapped and
    the selected original events are recorded in ``derived_from``.  The result
    is a controlled fixture, never a claim about the original full seal.
    """
    frame_dir = Path(frame_dir).expanduser().resolve()
    destination = Path(destination).expanduser().resolve()
    indices = [int(value) for value in event_indices]
    if not indices or indices != sorted(set(indices)):
        raise PrefixCaseError("event subset must be ordered and unique")
    seal = m6_frame.load_seal(frame_dir)
    trace = m6_frame.load_trace(frame_dir, verify_blobs=True)
    events = trace.get("_events") or []
    prefix = m6_frame.load_prefix_observations(trace, frame_dir)
    if prefix is None:
        raise PrefixCaseError("frame has no prefix observations")
    if any(index < 0 or index >= len(events) for index in indices):
        raise PrefixCaseError("event subset is outside the frame")
    post_by_old = {int(record["event"]): record
                   for record in prefix["post_events"]}
    selected = [events[index] for index in indices]
    post = []
    for new_index, old_index in enumerate(indices):
        record = post_by_old.get(old_index)
        if record is None:
            raise PrefixCaseError(
                f"selected event {old_index} has no post-event observation")
        record = dict(record)
        record["event"] = new_index
        post.append(record)
    if destination.exists():
        shutil.rmtree(destination)
    (destination / "blobs").mkdir(parents=True)
    blob_names: set[str] = set()
    for event in selected:
        blob_names |= _event_blob_names(event)
    for record in [prefix["initial"], *post]:
        blob_names |= _event_blob_names(record)
    for name in sorted(blob_names):
        source = frame_dir / "blobs" / name
        if not source.is_file():
            raise PrefixCaseError(f"referenced blob is absent: {name}")
        shutil.copyfile(source, destination / "blobs" / name)
    (destination / "frame-events.jsonl").write_text(
        "".join(json.dumps(event, sort_keys=True) + "\n" for event in selected))
    initial = dict(prefix["initial"])
    initial["event"] = 0
    observation_path = destination / "prefix-observations.jsonl"
    observation_path.write_text(
        "".join(json.dumps(record, sort_keys=True) + "\n"
                for record in [initial, *post]))
    reference = trace.get("frame") or {}
    shutil.copyfile(frame_dir / str(reference["reference"]),
                    destination / str(reference["reference"]))
    manifest = dict(trace)
    for key in ("_events", "_draws", "_frame_dir", "_prefix",
                "_blob_count_verified"):
        manifest.pop(key, None)
    manifest.update({
        "schema": m6_frame.TRACE_SCHEMA,
        "complete": True,
        "reason": "derived_event_subset",
        "truncated": False,
        "events": len(selected),
        "prefix": {
            "schema": "gfx-prefix-observations/v1",
            "end": len(selected) - 1,
            "status": "complete",
            "count": len(post),
            "post_event_count": len(post),
            "initial": True,
            "observation_file": observation_path.name,
            "observation_sha256": _sha(observation_path),
            "observation_bytes": observation_path.stat().st_size,
            "observations": [initial, *post],
        },
        "default_checkpoint_events": [],
        "default_checkpoint_count": 0,
        "derived_from": {
            "original_session_id": seal.get("session_id"),
            "original_capture_frame_id": seal.get("capture_frame_id"),
            "original_operation_count": len(events),
            "original_manifest_sha256": seal.get("manifest_sha256"),
            "original_event_stream_sha256": seal.get("event_stream_sha256"),
            "original_reference_sha256": seal.get("reference_sha256"),
            "selected_original_events": indices,
            "scope": "derived event subset; not the original full seal",
        },
    })
    (destination / "frame-trace.json").write_text(
        json.dumps(manifest, indent=2) + "\n")
    derived_seal = dict(seal)
    derived_seal.update({
        "sealed": True,
        "complete": True,
        "capture_dir": str(destination),
        "operation_count": len(selected),
        "draw_count": sum(1 for event in selected if event.get("kind") == "draw"),
        "clear_count": sum(1 for event in selected if event.get("kind") == "clear"),
        "copy_count": sum(1 for event in selected
                          if event.get("kind") in ("resolve", "copy")),
        "event_stream_file": "frame-events.jsonl",
        "event_stream_bytes": (destination / "frame-events.jsonl").stat().st_size,
        "event_stream_sha256": _sha(destination / "frame-events.jsonl"),
        "event_stream_verified": True,
        "manifest_file": "frame-trace.json",
        "manifest_sha256": _sha(destination / "frame-trace.json"),
        "trace_complete": True,
        "trace_integrity": True,
        "readback_ok": True,
        "failure_reason": "",
        "derived_from": manifest["derived_from"],
    })
    (destination / "frame-seal.json").write_text(
        json.dumps(derived_seal, indent=2) + "\n")
    return {
        "frame_dir": str(destination),
        "events": len(selected),
        "selected_original_events": indices,
        "bytes": sum(path.stat().st_size for path in destination.rglob("*")
                     if path.is_file()),
        "derived_from": manifest["derived_from"],
    }
