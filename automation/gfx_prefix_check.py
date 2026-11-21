"""Independent exact-prefix qualification for events 0 through 20.

This module is the acceptance authority for the M7 event-20 campaign.  It does
not trust the renderer, the replay report, or the model:

* the sealed frame, its event stream and its opt-in prefix observations are
  verified from disk;
* the checkpoint request is bound to the frame by the shader receipt;
* every prefix comparison is recomputed byte-for-byte from the original-GL
  reference blob and the Metal output file with the exact four-channel
  comparator;
* a missing, stale, foreign or normalized observation is a failure.

It returns a structured verdict.  ``EVENT20_EXACT`` is only possible when every
event from 0 through 20 has a matching, identity-bound, full BGRA observation.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from automation import gfx_frame_graph, gfx_independent_replay, m6_compare, m6_frame


class PrefixCheckError(RuntimeError):
    pass


def _read_json(path: Path) -> dict:
    try:
        value = json.loads(path.read_text())
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise PrefixCheckError(f"unreadable JSON {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise PrefixCheckError(f"not an object: {path}")
    return value


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _require(condition: bool, problems: list[str], message: str) -> None:
    if not condition:
        problems.append(message)


def classify_prefix(events_out: list[dict], *, problems: list[str],
                    end: int, require_end: int) -> dict:
    """Classify reconciled/excluded observations without trusting a report."""
    divergent = sorted(
        int(item["event"]) for item in events_out
        if item.get("status") == "divergent")
    invalid_events = sorted(
        int(item["event"]) for item in events_out
        if item.get("status") not in ("match", "divergent",
                                      "unsupported_auxiliary"))
    excluded_events = sorted(
        int(item["event"]) for item in events_out
        if item.get("status") == "unsupported_auxiliary")
    first_divergence = divergent[0] if divergent else None
    verified_color_events = [int(item["event"]) for item in events_out
                             if item.get("status") == "match"]
    if first_divergence is not None:
        verified_prefix = ({"start": 0, "end": first_divergence - 1}
                           if first_divergence > 0 else None)
        verdict = "FIRST_DIVERGENCE"
        status = "divergent"
    elif end >= require_end and not problems and not excluded_events:
        verified_prefix = {"start": 0, "end": require_end}
        verdict = "EVENT20_EXACT"
        status = "qualified"
    elif end >= require_end and not problems and excluded_events:
        verified_prefix = {"start": 0, "end": require_end,
                           "excluded_events": excluded_events}
        verdict = "EVENT20_BGRA_EXACT_WITH_EXCLUDED_AUX"
        status = "qualified_with_exclusions"
    elif invalid_events:
        verified_prefix = None
        verdict = "requires_additional_evidence"
        status = "incomplete"
    elif not problems:
        verified_prefix = {"start": 0, "end": end}
        verdict = "partial_prefix"
        status = "partial"
    else:
        verified_prefix = None
        verdict = "requires_additional_evidence"
        status = "incomplete"
    return {
        "first_divergence": first_divergence,
        "invalid_events": invalid_events,
        "excluded_events": excluded_events,
        "verified_color_events": verified_color_events,
        "verified_prefix": verified_prefix,
        "verdict": verdict,
        "status": status,
    }


def evaluate(frame_dir: Path | str, metal_dir: Path | str,
             *, request_path: Path | str | None = None,
             require_end: int = 20) -> dict:
    """Verify one sealed frame's exact prefix against its Metal replay.

    ``frame_dir`` contains frame-seal.json, frame-trace.json,
    frame-events.jsonl, blobs/ and prefix-observations.jsonl.  ``metal_dir``
    contains replay.json plus the Metal checkpoint outputs.  The trusted
    checker must run this function outside the candidate workspace.
    """
    frame_dir = Path(frame_dir).expanduser().resolve()
    metal_dir = Path(metal_dir).expanduser().resolve()
    problems: list[str] = []
    result: dict = {
        "schema": "m7-event20-prefix-check/v1",
        "frame_dir": str(frame_dir),
        "metal_dir": str(metal_dir),
        "required_through": require_end,
        "problems": problems,
    }
    try:
        seal = m6_frame.load_seal(frame_dir)
        trace = m6_frame.load_trace(frame_dir, verify_blobs=True)
    except (m6_frame.FrameError, OSError, ValueError) as exc:
        problems.append(f"sealed frame is not usable: {exc}")
        return dict(result, verdict="requires_additional_evidence",
                    status="invalid", first_divergence=None,
                    verified_prefix=None, events=[])
    graph = gfx_frame_graph.build(frame_dir)
    result["capture"] = gfx_independent_replay.capture_identity(
        seal, trace)
    result["graph_status"] = graph.get("status")
    result["graph_reasons"] = graph.get("reasons") or []
    prefix = graph.get("prefix") or {}
    result["prefix_status"] = prefix.get("status")
    result["prefix_end"] = prefix.get("end")
    if prefix.get("status") != "complete":
        problems.append("exact-prefix coverage is not complete")
        for problem in prefix.get("problems") or []:
            problems.append(str(problem))
        return dict(result, verdict="requires_additional_evidence",
                    status="incomplete", first_divergence=None,
                    verified_prefix=None, events=[])
    end = int(prefix["end"])
    if end < require_end:
        problems.append(
            f"capture prefix ends at event {end}, not {require_end}")
    request_file = (Path(request_path).expanduser().resolve()
                    if request_path is not None
                    else metal_dir / "checkpoint-request.json")
    try:
        request = _read_json(request_file)
    except PrefixCheckError as exc:
        problems.append(str(exc))
        return dict(result, verdict="requires_additional_evidence",
                    status="invalid", first_divergence=None,
                    verified_prefix=None, events=[])
    request_sha = _sha256(request_file)
    result["request"] = {"path": str(request_file),
                         "sha256": request_sha}
    receipt_path = metal_dir / "shader-receipt.json"
    try:
        receipt = _read_json(receipt_path)
    except PrefixCheckError as exc:
        problems.append(f"shader receipt is not usable: {exc}")
        receipt = {}
    result["receipt"] = {
        "path": str(receipt_path),
        "sha256": _sha256(receipt_path) if receipt_path.is_file() else None,
    }
    _require(receipt.get("schema") == "m7-shadow-shader-receipt/v1",
             problems, "shader receipt schema is not recognized")
    _require(receipt.get("checkpoint_request_sha256") == request_sha,
             problems,
             "shader receipt does not bind the published checkpoint request")
    _require(receipt.get("checkpoint_request_file")
             == request_file.name,
             problems, "shader receipt names a different request file")
    requested = request.get("checkpoints")
    if not isinstance(requested, list):
        problems.append("checkpoint request has no checkpoint array")
        requested = []
    _require(receipt.get("checkpoint_count") == len(requested),
             problems, "shader receipt checkpoint count disagrees with the "
                       "published request")
    for field, seal_field in (
            ("capture_frame_id", "capture_frame_id"),
            ("pid", "pid"),
            ("session_id", "session_id"),
            ("trace_manifest_sha256", "manifest_sha256"),
            ("event_stream_sha256", "event_stream_sha256")):
        if seal.get(seal_field) is not None:
            _require(receipt.get(field) == seal.get(seal_field), problems,
                     f"shader receipt {field} does not match the sealed frame")
    replay_path = metal_dir / "replay.json"
    try:
        replay = _read_json(replay_path)
    except PrefixCheckError as exc:
        problems.append(f"Metal replay result is not usable: {exc}")
        return dict(result, verdict="requires_additional_evidence",
                    status="invalid", first_divergence=None,
                    verified_prefix=None, events=[])
    result["replay"] = {
        "path": str(replay_path),
        "sha256": _sha256(replay_path),
        "status": replay.get("status"),
        "events": replay.get("events"),
        "encoded": replay.get("encoded"),
        "failed": replay.get("failed"),
    }
    _require(replay.get("status") == "ok", problems,
             "Metal replay status is not ok")
    _require(replay.get("failed") == 0, problems,
             "Metal replay reported failed events")
    _require(replay.get("events") == len(trace.get("_events") or []),
             problems, "Metal replay event count does not match the frame")
    _require(replay.get("encoded") == replay.get("events"), problems,
             "Metal replay did not encode every event")
    for field, value in (
            ("checkpoint_request_sha256", request_sha),
            ("checkpoint_request_file", request_file.name)):
        if replay.get(field) is not None:
            _require(replay.get(field) == value, problems,
                     f"Metal replay {field} does not bind the request")
    capture = result["capture"]
    try:
        results, validation = gfx_independent_replay.reconcile_checkpoints(
            graph, request, replay, frame_dir, metal_dir, capture=capture)
    except (gfx_independent_replay.ReplayError, OSError, ValueError) as exc:
        problems.append(f"checkpoint reconciliation failed: {exc}")
        return dict(result, verdict="requires_additional_evidence",
                    status="invalid", first_divergence=None,
                    verified_prefix=None, events=[])
    result["checkpoint_validation"] = validation
    _require(validation.get("status") == "ok", problems,
             "checkpoint reconciliation reported invalid evidence")

    events_out: list[dict] = []
    expected_post = {int(record["event"]): record
                     for record in prefix.get("post_events") or []}
    excluded = {int(event) for event in prefix.get("excluded_events") or []}
    result["excluded_events"] = sorted(excluded)
    seen: set[int] = set()
    for entry in results:
        if entry.get("timing") != "post_event":
            continue
        event = int(entry.get("event"))
        seen.add(event)
        observation = expected_post.get(event)
        row = {
            "event": event,
            "target": entry.get("target"),
            "resource": entry.get("resource"),
            "timing": entry.get("timing"),
            "status": entry.get("status"),
            "reference_file": entry.get("reference_file"),
            "metal_file": entry.get("metal_file"),
            "reference_sha256": entry.get("reference_sha256"),
            "metal_sha256": entry.get("metal_sha256"),
            "dimensions": [entry.get("width"), entry.get("height")],
            "reference_pixel_format": entry.get("reference_pixel_format"),
            "candidate_pixel_format": entry.get("candidate_pixel_format"),
        }
        statistics = entry.get("statistics") or {}
        row["statistics"] = statistics
        if event in excluded:
            problems.append(
                f"prefix event {event} is excluded but has a Metal result")
            row["status"] = "invalid"
            row["problem"] = "unexpected result for an excluded event"
            events_out.append(row)
            continue
        if observation is None:
            problems.append(f"prefix event {event} was not requested")
            row["status"] = "invalid"
            row["problem"] = "unexpected prefix observation"
            events_out.append(row)
            continue
        if (entry.get("resource") != observation.get("resource")
                or str(entry.get("target"))
                != str(observation.get("target_name"))):
            problems.append(
                f"prefix event {event} resource or target disagrees with the "
                "sealed observation")
            row["status"] = "invalid"
            row["problem"] = "resource identity mismatch"
        if entry.get("reference_pixel_format") not in (None, "BGRA8", "RGBA8"):
            problems.append(
                f"prefix event {event} has an unsupported reference pixel "
                "format")
            row["status"] = "invalid"
            row["problem"] = "unsupported pixel format"
        # Recompute exact four-channel equality from the original bytes.  The
        # reconciled statistics are not trusted for the verdict.
        try:
            width = int(entry.get("width") or 0)
            height = int(entry.get("height") or 0)
            reference_path = Path(entry.get("reference_file") or "")
            metal_path = Path(entry.get("metal_file") or "")
            reference = reference_path.read_bytes()
            candidate = metal_path.read_bytes()
            if (len(reference) != width * height * 4
                    or len(candidate) != width * height * 4):
                raise PrefixCheckError("pixel payload size mismatch")
            exact = m6_compare.compare_images(
                reference, candidate, width, height)
            row["independent"] = {
                "mismatched_pixels": exact["mismatched_pixels"],
                "max_channel_error": exact["max_channel_error"],
                "alpha_different_pixels": exact["alpha_different_pixels"],
                "pixels": exact["pixels"],
            }
            if (exact["mismatched_pixels"] != 0
                    or exact["max_channel_error"] != 0
                    or exact["alpha_different_pixels"] != 0):
                row["status"] = "divergent"
                row["problem"] = "independent exact comparison failed"
            elif row["status"] == "match":
                row["status"] = "match"
            else:
                row["status"] = "invalid"
                row["problem"] = (row.get("problem")
                                  or entry.get("reason")
                                  or "reconciled status is not match")
        except (OSError, ValueError, PrefixCheckError) as exc:
            row["status"] = "invalid"
            row["problem"] = f"independent comparison failed: {exc}"
            problems.append(f"prefix event {event}: {row['problem']}")
        events_out.append(row)
    for event in range(end + 1):
        if event in seen:
            continue
        if event in excluded:
            events_out.append({
                "event": event,
                "status": "unsupported_auxiliary",
                "excluded": True,
                "problem": "isolated auxiliary target with an unsupported "
                           "non-BGRA colour format; the clear is accounted "
                           "for but not compared",
            })
            continue
        problems.append(f"prefix event {event} has no reconciled result")
        events_out.append({
            "event": event,
            "status": "missing",
            "problem": "no reconciled Metal result",
        })
    events_out.sort(key=lambda item: item["event"])
    result["events"] = events_out
    result["observed_events"] = len(events_out)

    classification = classify_prefix(
        events_out, problems=problems, end=end, require_end=require_end)
    result.update(classification)
    result["verdict"] = classification["verdict"]
    result["problems_count"] = len(problems)
    return result
