"""Independent Metal replay of a captured OpenGL frame (Strategy B, Phase 3/4).

This is the execution layer over the existing M6 replay host.  It does not
implement a second renderer: it prepares the captured workload, invokes the
production replay host, reads back the same checkpoints from both APIs, and
classifies the result.  A replay is never called *complete* because the final
image matches; renderer-generated resources must have been produced by Metal
and every required operation must have executed.

Fault injection is limited to the replay process (environment switches), so the
historical sampler-binding defect can be evaluated without touching the M6
development branch.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import struct
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

from automation import gfx_frame_graph, m6_compare, m6_frame

SCHEMA = "gfx-independent-replay/v1"
DIAGNOSIS_SCHEMA = "gfx-frame-diagnosis/v1"
DEFAULT_POLICY = Path(__file__).resolve().parents[1] / "analysis" / "m6" / "comparison-policy.json"
ROOT = Path(__file__).resolve().parents[1]
OUTPUT_MARKER = ".gfx-replay-output.json"

# Final classifications from the M7 PR3 qualification contract.  They are
# deliberately disjoint: an incomplete diagnosis must never be reported as a
# renderer difference, and a completed pixel difference is not a tool failure.
CLASSIFICATIONS = (
    "qualified_match",
    "qualified_difference",
    "difference_with_partial_state_coverage",
    "incomplete_capture",
    "invalid_provenance",
    "unsupported_state",
    "gpu_execution_failure",
    "incomplete_diagnostics",
    "fault_experiment",
)

# What the Metal replay host actually implements.  ``implemented`` means the
# captured value changes Metal encoding; ``unsupported`` means the value is
# captured but has no Metal effect or is rejected by the host.  Anything not
# listed is treated as unproven rather than silently equivalent.
STATE_IMPLEMENTATION = {
    "shader_bindings": "implemented",
    "render_targets": "implemented",
    "samplers": "implemented",
    "viewport": "implemented",
    "scissor": "implemented",
    "blend": "implemented",
    "depth": "implemented",
    "color_mask": "implemented",
    "cull": "implemented",
    "gl_dither": "unsupported",
    "multisample": "unsupported",
    "stencil_test": "unsupported",
    "polygon_mode": "unsupported",
    "polygon_offset": "unsupported",
    "alpha_test": "unsupported",
}


class ReplayError(RuntimeError):
    def __init__(self, kind: str, detail: str = ""):
        super().__init__(kind if not detail else f"{kind}: {detail}")
        self.kind = kind
        self.detail = detail


def _path_overlap(left: Path, right: Path) -> bool:
    left = left.resolve()
    right = right.resolve()
    return left == right or left in right.parents or right in left.parents


def _protected_output(path: Path) -> bool:
    """Reject output paths whose replacement could delete a workspace root."""
    path = path.resolve()
    for protected in (ROOT.resolve(), Path.cwd().resolve(),
                      Path.home().resolve()):
        if path == protected or path in protected.parents:
            return True
    return path.parent == path


def _known_replay_output(path: Path) -> bool:
    marker = path / OUTPUT_MARKER
    try:
        value = json.loads(marker.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        value = None
    if isinstance(value, dict) and value.get("schema") == "gfx-replay-output-marker/v1":
        return True
    for name, schema in (("replay-report.json", SCHEMA),
                         ("replay.json", "gfx-metal-frame-replay/v1")):
        try:
            value = json.loads((path / name).read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            continue
        if isinstance(value, dict) and value.get("schema") == schema:
            return True
    return False


def prepare_replay_output(out_dir: Path, inputs: list[Path]) -> Path:
    """Validate and prepare one replay output directory without data loss."""
    out_dir = out_dir.expanduser().resolve()
    if _protected_output(out_dir):
        raise ReplayError("output_protected",
                          f"refusing to replace protected output {out_dir}")
    for source in inputs:
        if _path_overlap(out_dir, source.expanduser().resolve()):
            raise ReplayError(
                "output_overlap",
                f"replay output {out_dir} overlaps input {source}")
    if out_dir.exists():
        if not out_dir.is_dir():
            raise ReplayError("output_conflict", f"{out_dir} is not a directory")
        entries = list(out_dir.iterdir())
        if entries and not _known_replay_output(out_dir):
            raise ReplayError(
                "output_not_owned",
                f"{out_dir} is nonempty and is not a prior replay output")
        if entries:
            shutil.rmtree(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / OUTPUT_MARKER).write_text(
        json.dumps({
            "schema": "gfx-replay-output-marker/v1",
            "tool": "gfx_independent_replay",
            "created_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
        }, indent=2) + "\n",
        encoding="utf-8")
    return out_dir


def _sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _read_jsonl(path: Path) -> list[dict]:
    rows: list[dict] = []
    if not path.is_file():
        return rows
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            value = json.loads(line)
            if isinstance(value, dict):
                rows.append(value)
    return rows


def _shader_dir(frame_dir: Path, explicit: Path | str | None) -> Path:
    if explicit is not None:
        path = Path(explicit).expanduser().resolve()
        if not path.is_dir():
            raise ReplayError("shaders_missing", str(path))
        return path
    for candidate in (frame_dir / "shaders", frame_dir.parent / "translated"):
        if (candidate / "shaders.json").is_file():
            return candidate.resolve()
    raise ReplayError(
        "shaders_missing",
        "no translated shader directory (frame/shaders or sibling translated/)",
    )


def _opaque_alpha(payload: bytes) -> bytes:
    """Canonicalize an alpha-less target's implicit alpha for comparison.

    The original OpenGL RGB8 attachment has no alpha channel; a BGRA8 Metal
    materialization carries an arbitrary alpha byte.  Alpha is not an
    applicable channel for that checkpoint, so both sides are compared with
    opaque alpha while every stored colour channel remains exact.
    """
    data = bytearray(payload)
    for offset in range(3, len(data), 4):
        data[offset] = 255
    return bytes(data)


def _swap_red_blue(payload: bytes) -> bytes:
    data = bytearray(payload)
    for offset in range(0, len(data) - 3, 4):
        data[offset], data[offset + 2] = data[offset + 2], data[offset]
    return bytes(data)


def _load_pixels(path: Path, width: int, height: int) -> bytes:
    payload = path.read_bytes()
    if len(payload) != width * height * 4:
        raise ReplayError(
            "checkpoint_size",
            f"{path} is {len(payload)} bytes, expected {width * height * 4}",
        )
    return payload


def compare_r32f(reference: bytes, candidate: bytes, width: int,
                 height: int) -> dict:
    """Exact bitwise comparison of two R32F payloads.

    R32F is one 32-bit IEEE-754 value per pixel, not four colour channels.
    Equality is exact bit identity; the reported error is the maximum ULP
    distance so the result is not described as an RGBA channel error.
    """
    total = width * height
    expected = total * 4
    if len(reference) != expected or len(candidate) != expected:
        raise ReplayError(
            "r32f_payload",
            f"R32F payload size {len(reference)}/{len(candidate)} != {expected}")
    mismatched = 0
    max_ulp = 0
    first = None
    for pixel in range(total):
        offset = pixel * 4
        left = struct.unpack_from("<I", reference, offset)[0]
        right = struct.unpack_from("<I", candidate, offset)[0]
        if left == right:
            continue
        mismatched += 1
        distance = abs(left - right)
        if distance > max_ulp:
            max_ulp = distance
        if first is None:
            first = {"x": pixel % width, "y": pixel // width,
                     "offset": pixel}
    return {
        "format_scope": "R32F",
        "pixels": total,
        "mismatched_pixels": mismatched,
        "mismatched_fraction": mismatched / total if total else 0.0,
        "alpha_different_pixels": 0,
        "max_channel_error": 0,
        "max_ulp_error": max_ulp,
        "mean_abs_error": [0, 0, 0, 0],
        "mean_max_channel_error": 0.0,
        "first_differing_pixel": first,
        "differing_bounds": _bounds_for_points(
            [first] if first else [], width, height),
        "regions": [],
        "exact_bitwise": mismatched == 0,
    }


def _bounds_for_points(points, width: int, height: int):
    if not points:
        return None
    xs = [point["x"] for point in points]
    ys = [point["y"] for point in points]
    return {"x": min(xs), "y": min(ys),
            "width": max(xs) - min(xs) + 1,
            "height": max(ys) - min(ys) + 1}


def _normalize_format(value) -> str:
    if value is None:
        return ""
    if isinstance(value, int):
        return {
            0x8058: "RGBA8", 0x8051: "RGB8", 0x8814: "RGBA32F",
            0x8815: "RGB32F", 0x881A: "RGBA16F", 0x881B: "RGB16F",
            0x822E: "R32F",
            0x805B: "RGBA16", 0x8C43: "SRGB8_ALPHA8",
        }.get(value, str(value))
    return str(value).upper()


def _checkpoint_key(item: dict) -> tuple[int, str, str]:
    """Checkpoint identity is (event, target, timing).

    A pre-event observation and a post-event observation of the same target
    are distinct scientific observations and must never overwrite each other.
    """
    timing = str(item.get("timing") or "pre_event")
    return (_as_int(item.get("event"), -1), str(item.get("target")), timing)


def _valid_producer_events(value) -> bool:
    return (isinstance(value, list)
            and all(isinstance(item, int) and not isinstance(item, bool)
                    for item in value))


def _producer_event_list(value) -> list[int]:
    """A producer list from untrusted evidence, without raising on scalars.

    A malformed ``producer_events`` value (an integer, object or string) is
    invalid evidence, not a reason to traceback while building an invalid
    checkpoint record.  Callers that need strict validation still report the
    malformed container explicitly.
    """
    if not isinstance(value, list):
        return []
    return [item for item in value
            if isinstance(item, int) and not isinstance(item, bool)]


def _invalid_checkpoint(item: dict, graph: dict, reason: str,
                        *, requested: bool) -> dict:
    target = str(item.get("target"))
    event = item.get("event")
    resource_id = "fb:0" if target == "0" else f"tex:{target}"
    resource = (graph.get("resources") or {}).get(resource_id) or {}
    return {
        "event": event,
        "target": target,
        "resource": resource_id,
        "width": _as_int(item.get("reference_width")
                         or item.get("width"), 0),
        "height": _as_int(item.get("reference_height")
                          or item.get("height"), 0),
        "format": item.get("format") or resource.get("format"),
        "producer_events": _producer_event_list(
            item.get("producer_events")),
        "metal_file": str(item.get("_metal_path") or ""),
        "reference_file": str(item.get("_reference_path") or ""),
        "authoritative": True,
        "status": "invalid",
        "reason": reason,
        "requested": requested,
        "timing": str(item.get("timing") or "pre_event"),
    }


def capture_identity(seal: dict | None, trace: dict,
                     replay: dict | None = None) -> dict | None:
    """Authoritative identity of the one sealed frame a result belongs to.

    Causal bounds may only be derived from checkpoints observed in the same
    capture.  ``session_id`` changes on every EU4 launch, ``capture_frame_id``
    disambiguates frames in one continuous session and
    ``event_stream_sha256`` binds the observation to the exact ordered event
    stream.  A capture that cannot supply these fields is reported as
    ``None`` rather than guessed.
    """
    trace = trace or {}
    frame = trace.get("frame") or {}
    seal = seal or {}
    identity = {
        "session_id": seal.get("session_id"),
        "capture_frame_id": seal.get("capture_frame_id"),
        "present_index": seal.get("present_index", trace.get("present_index")),
        "scene_index": seal.get("scene_index", trace.get("scene_index")),
        "event_stream_sha256": seal.get("event_stream_sha256"),
        "reference_sha256": (seal.get("reference_sha256")
                             or frame.get("reference_sha256")),
        "manifest_sha256": seal.get("manifest_sha256"),
    }
    if all(value is None for value in identity.values()):
        return None
    return identity


def _capture_key(value) -> tuple[str, str, str] | None:
    if not isinstance(value, dict):
        return None
    parts = (value.get("session_id"), value.get("capture_frame_id"),
             value.get("event_stream_sha256"))
    if any(part is None for part in parts):
        return None
    return tuple(str(part) for part in parts)


def _checkpoint_capture_matches(left, right) -> bool:
    """True only when two observations provably come from the same capture.

    Unknown identity is not a wildcard: if either side names a capture and the
    other does not, they cannot bound each other.  Legacy hand-built fixtures
    that carry no capture identity at all remain comparable, which keeps the
    offline unit contract unchanged without weakening the sealed path.
    """
    left_key = _capture_key(left)
    right_key = _capture_key(right)
    if left_key is None and right_key is None:
        return True
    return left_key is not None and left_key == right_key


def reconcile_checkpoints(graph: dict, request: dict, replay: dict,
                          frame_dir: Path, out_dir: Path,
                          capture: dict | None = None
                          ) -> tuple[list[dict], dict]:
    """Reconcile every requested checkpoint with exactly one replay result.

    Missing, duplicate, unexpected, unreadable or identity-mismatched evidence
    is an instrumentation/qualification failure.  A later matching final frame
    cannot hide it.
    """
    problems: list[dict] = []

    if not isinstance(request, dict):
        plan = []
        problems.append({
            "kind": "malformed_request",
            "reason": "checkpoint request is not an object",
        })
    else:
        raw_plan = request.get("checkpoints")
        if raw_plan is None:
            plan = []
        elif isinstance(raw_plan, list):
            plan = raw_plan
        else:
            plan = []
            problems.append({
                "kind": "malformed_request",
                "reason": "checkpoint request checkpoints is not an array",
            })

    if not isinstance(replay, dict):
        actual = []
        problems.append({"kind": "malformed_result",
                         "reason": "replay result is not an object"})
    else:
        raw_actual = replay.get("checkpoints")
        if raw_actual is None:
            actual = []
        elif isinstance(raw_actual, list):
            actual = raw_actual
        else:
            actual = []
            problems.append({
                "kind": "malformed_result",
                "reason": "replay checkpoints is not an array",
            })

    requested: dict[tuple[int, str], dict] = {}
    for item in plan:
        if not isinstance(item, dict):
            problems.append({
                "kind": "malformed_request",
                "reason": "checkpoint request entry is not an object",
            })
            continue
        key = _checkpoint_key(item)
        if key in requested:
            problems.append({"kind": "duplicate_request", "event": key[0],
                             "target": key[1], "timing": key[2]})
            continue
        target = key[1]
        expected_resource = "fb:0" if target == "0" else f"tex:{target}"
        if str(item.get("resource") or expected_resource) != expected_resource:
            problems.append({
                "kind": "substituted_request", "event": key[0],
                "target": key[1], "timing": key[2],
                "expected_resource": expected_resource,
                "actual_resource": item.get("resource")})
            continue
        if ("producer_events" in item
                and not _valid_producer_events(item.get("producer_events"))):
            problems.append({
                "kind": "malformed_request", "event": key[0],
                "target": key[1], "timing": key[2],
                "reason": "producer_events is not an integer array"})
            continue
        requested[key] = item

    returned: dict[tuple[int, str], list[dict]] = {}
    for item in actual:
        if not isinstance(item, dict):
            problems.append({"kind": "malformed_result",
                             "reason": "checkpoint result is not an object"})
            continue
        key = _checkpoint_key(item)
        returned.setdefault(key, []).append(item)

    for key in returned:
        if key not in requested:
            problems.append({"kind": "unexpected_result", "event": key[0],
                             "target": key[1], "timing": key[2]})

    results: list[dict] = []
    for key, wanted in requested.items():
        event, target, timing = key
        rows = returned.get(key, [])
        if len(rows) == 0:
            problems.append({"kind": "missing_result", "event": event,
                             "target": target, "timing": timing})
            results.append(_invalid_checkpoint(
                wanted, graph, "requested checkpoint missing from replay response",
                requested=True))
            continue
        if len(rows) > 1:
            problems.append({"kind": "duplicate_result", "event": event,
                             "target": target, "timing": timing,
                             "count": len(rows)})
            results.append(_invalid_checkpoint(
                wanted, graph, "duplicate checkpoint results in replay response",
                requested=True))
            continue
        item = rows[0]
        expected_resource = "fb:0" if target == "0" else f"tex:{target}"
        if item.get("target") is not None and str(item.get("target")) != target:
            problems.append({"kind": "substituted_result", "event": event,
                             "target": target,
                             "returned_target": item.get("target")})
            results.append(_invalid_checkpoint(
                wanted, graph,
                "checkpoint result names a different render target",
                requested=True))
            continue
        if (item.get("resource") is not None
                and str(item.get("resource")) != expected_resource):
            problems.append({"kind": "substituted_result", "event": event,
                             "target": target,
                             "expected_resource": expected_resource,
                             "returned_resource": item.get("resource")})
            results.append(_invalid_checkpoint(
                wanted, graph,
                "checkpoint result names a different resource identity",
                requested=True))
            continue
        requested_width = _as_int(wanted.get("reference_width")
                                  or wanted.get("width"), 0)
        requested_height = _as_int(wanted.get("reference_height")
                                   or wanted.get("height"), 0)
        actual_width = _as_int(item.get("width"), 0)
        actual_height = _as_int(item.get("height"), 0)
        file_name = str(wanted.get("file") or item.get("file") or "")
        reference_name = str(wanted.get("reference_file")
                             or item.get("reference_file") or "")
        expected_reference_sha = str(wanted.get("reference_sha256")
                                     or item.get("reference_sha256") or "")
        metal_path = out_dir / Path(file_name).name
        reference_path = frame_dir / "blobs" / Path(reference_name).name
        merged = dict(wanted)
        merged.update(item)
        merged["_metal_path"] = str(metal_path)
        merged["_reference_path"] = str(reference_path)

        def fail(reason: str) -> None:
            problems.append({"kind": "invalid_result", "event": event,
                             "target": target, "reason": reason})
            results.append(_invalid_checkpoint(merged, graph, reason,
                                               requested=True))

        if capture is not None:
            requested_session = wanted.get("session_id")
            if (requested_session is not None
                    and capture.get("session_id") is not None
                    and str(requested_session)
                    != str(capture["session_id"])):
                fail("checkpoint names a foreign capture session")
                continue
            requested_frame = wanted.get("capture_frame_id")
            if (requested_frame is not None
                    and capture.get("capture_frame_id") is not None
                    and int(requested_frame)
                    != int(capture["capture_frame_id"])):
                fail("checkpoint names a foreign capture frame")
                continue

        if item.get("ok") is not True:
            fail(str(item.get("error") or "checkpoint readback reported failure"))
            continue
        if requested_width and actual_width != requested_width:
            fail(f"checkpoint width {actual_width} != requested "
                 f"{requested_width}")
            continue
        if requested_height and actual_height != requested_height:
            fail(f"checkpoint height {actual_height} != requested "
                 f"{requested_height}")
            continue
        if not file_name:
            fail("checkpoint file name is missing")
            continue
        if item.get("file") and str(item.get("file")) != file_name:
            fail("checkpoint file name disagrees with the request")
            continue
        if item.get("reference_sha256") and expected_reference_sha and str(
                item.get("reference_sha256")) != expected_reference_sha:
            fail("checkpoint reference sha256 disagrees with the request")
            continue
        returned_reference = item.get("reference_file")
        requested_reference = wanted.get("reference_file")
        if returned_reference and requested_reference and Path(
                str(returned_reference)).name != Path(
                    str(requested_reference)).name:
            fail("checkpoint reference file disagrees with the request")
            continue
        if not metal_path.is_file():
            fail("checkpoint output file is absent")
            continue
        if not reference_path.is_file():
            fail("captured reference blob is absent")
            continue
        width = requested_width or actual_width
        height = requested_height or actual_height
        if width <= 0 or height <= 0:
            fail("checkpoint dimensions are invalid")
            continue
        try:
            metal = _load_pixels(metal_path, width, height)
            reference = _load_pixels(reference_path, width, height)
        except ReplayError as exc:
            fail(str(exc))
            continue
        reference_sha = _sha(reference_path)
        if expected_reference_sha and reference_sha != expected_reference_sha:
            fail("captured reference sha256 does not match the request")
            continue
        resource_id = "fb:0" if target == "0" else f"tex:{target}"
        resource = (graph.get("resources") or {}).get(resource_id) or {}
        captured_format = _normalize_format(resource.get("format"))
        reported_format = _normalize_format(item.get("format"))
        reference_format = _normalize_format(
            wanted.get("reference_pixel_format"))
        if reference_format:
            # Exact-prefix entries compare the bytes the original GL readback
            # produced with the bytes the Metal target materialized.  No
            # channel-order normalization is permitted: the declared pixel
            # formats must agree exactly.
            reported_bytes = _normalize_format(item.get("pixel_format"))
            if reported_bytes != reference_format:
                fail(
                    f"prefix candidate pixel format {reported_bytes or '<missing>'} "
                    f"does not match the reference format {reference_format}")
                continue
            channel_swap = False
            internal = reference_format
        elif captured_format:
            if not reported_format:
                fail("checkpoint is missing its reported format")
                continue
            if captured_format in ("RGB8", "SRGB8_ALPHA8"):
                allowed_formats = {captured_format, "BGRA8"}
                channel_swap = True
            else:
                allowed_formats = {captured_format}
                channel_swap = False
            if reported_format not in allowed_formats:
                fail(
                    f"checkpoint format {reported_format} disagrees with "
                    f"captured format {captured_format}")
                continue
            internal = captured_format
        else:
            # Default-framebuffer conventions are the only formats without a
            # captured resource format; an RGB8 downgrade is not accepted.
            if reported_format not in ("RGBA8", "BGRA8"):
                fail(
                    f"checkpoint format {reported_format or '<missing>'} has "
                    "no authoritative captured format")
                continue
            channel_swap = False
            internal = reported_format
        candidate = _swap_red_blue(metal) if channel_swap else metal
        channels = wanted.get("compared_channels")
        if channels is None:
            if reference_format == "R32F":
                channels = "R32F"
            elif reference_format and captured_format == "RGB8":
                channels = "RGB"
            else:
                channels = "RGBA"
        alpha_declared = wanted.get("alpha_applicable")
        if alpha_declared is None:
            alpha_applicable = channels != "RGB" and channels != "R32F"
        else:
            alpha_applicable = alpha_declared is not False
        if alpha_applicable != (channels == "RGBA"):
            fail("alpha applicability contradicts the compared channel scope")
            continue
        if channels == "R32F":
            statistics = compare_r32f(reference, candidate, width, height)
        else:
            if not alpha_applicable:
                reference = _opaque_alpha(reference)
                candidate = _opaque_alpha(candidate)
            statistics = m6_compare.compare_images(
                reference, candidate, width, height)
        entry = {
            "event": event,
            "target": target,
            "resource": resource_id,
            "width": width,
            "height": height,
            "format": internal or None,
            "producer_events": _producer_event_list(
                wanted.get("producer_events")),
            "metal_file": str(metal_path),
            "reference_file": str(reference_path),
            "authoritative": not channel_swap,
            "requested": True,
            "normalization": {
                "channel_swap": "bgra_to_rgba" if channel_swap else None,
                "row_origin": "declared_lower_left",
            },
            "statistics": {
                key: statistics[key] for key in (
                    "pixels", "mismatched_pixels", "mismatched_fraction",
                    "alpha_different_pixels", "max_channel_error",
                    "mean_abs_error", "mean_max_channel_error",
                    "first_differing_pixel", "differing_bounds",
                )
            },
            "worst_regions": statistics["regions"][:4],
            "metal_sha256": _sha(metal_path),
            "reference_sha256": reference_sha,
            "timing": timing,
            "alpha_applicable": alpha_applicable,
            "compared_channels": channels,
            "format_scope": channels,
            "reference_pixel_format": reference_format or None,
            "candidate_pixel_format": _normalize_format(item.get("pixel_format"))
            or None,
        }
        if statistics["mismatched_pixels"] == 0:
            entry["status"] = "match"
        elif entry["authoritative"]:
            entry["status"] = "divergent"
        else:
            entry["status"] = "divergent_diagnostic"
        results.append(entry)

    results.sort(key=lambda item: (item.get("event") or 0,
                                   item.get("target") or ""))
    if capture is not None:
        # Every reconciled observation belongs to the one capture whose frame
        # and replay produced it.  Stamping here makes a later cross-capture
        # combination detectable instead of silently trusted.
        for item in results:
            item.setdefault("capture", capture)
    validation = {
        "schema": "gfx-checkpoint-validation/v1",
        "status": "ok" if not problems else "invalid",
        "requested": len(plan),
        "returned": len(actual),
        "matched": sum(1 for item in results if item["status"] == "match"),
        "diagnostic": sum(1 for item in results
                          if item["status"] == "divergent_diagnostic"),
        "divergent": sum(1 for item in results
                         if item["status"] == "divergent"),
        "invalid": sum(1 for item in results if item["status"] == "invalid"),
        "problems": problems[:100],
        "problems_count": len(problems),
    }
    return results, validation


def compare_checkpoints(graph: dict, replay: dict, frame_dir: Path,
                        out_dir: Path, request: dict | None = None
                        ) -> list[dict]:
    """Backward-compatible wrapper around checkpoint reconciliation."""
    if request is None:
        request = {"checkpoints": replay.get("_checkpoint_plan") or []}
    return reconcile_checkpoints(graph, request, replay, frame_dir,
                                 out_dir)[0]

def binding_audit(replay: dict) -> dict:
    """Verify each shader's declared texture unit resolves to its texture.

    The capture contract is ``texN`` -> engine texture unit N.  A translation
    that assigns samplers sequentially can move a payload to a different unit;
    the shader then samples a neighbouring texture.  This audit uses the
    replay's own provenance and reports the exact sampler/unit/texture that no
    longer agrees with the translated declaration.
    """
    samples = replay.get("_provenance_samples") or []
    by_event: dict[int, list[dict]] = {}
    for sample in samples:
        by_event.setdefault(int(sample.get("event") or 0), []).append(sample)
    mismatches: list[dict] = []
    for event in sorted(by_event):
        rows = by_event[event]
        bound = {}
        for row in rows:
            bound[int(row.get("unit") or 0)] = str(row.get("texture") or "")
        for row in rows:
            shader_unit = int(row.get("fault_shader_unit")
                              if row.get("fault_shader_unit") is not None
                              else row.get("unit") or 0)
            expected = str(row.get("texture") or "")
            actual = bound.get(shader_unit, "")
            if expected != actual:
                mismatches.append({
                    "event": event,
                    "kind": "shader_unit_collision",
                    "sampler": row.get("sampler"),
                    "shader_unit": shader_unit,
                    "bound_unit": int(row.get("unit") or 0),
                    "expected_texture": expected,
                    "actual_texture_at_shader_unit": actual,
                    "shift": row.get("fault_sampler_shift") or 0,
                })
            missing_raw = row.get("fault_missing_engine_unit")
            missing_unit = -1 if missing_raw is None else int(missing_raw)
            if missing_unit >= 0:
                mismatches.append({
                    "event": event,
                    "kind": "missing_texture_unit",
                    "sampler": row.get("sampler"),
                    "shader_unit": shader_unit,
                    "engine_unit": missing_unit,
                    "expected_texture": str(row.get("expected_texture") or ""),
                    "selected_texture": "",
                    "shift": row.get("fault_sampler_shift") or 0,
                    "note": "the selected engine unit has no captured texture; "
                            "the shader would sample the default texture",
                })
            declared = str(row.get("expected_texture") or "")
            selected = str(row.get("selected_texture") or "")
            if declared and selected and declared != selected:
                mismatches.append({
                    "event": event,
                    "kind": "wrong_texture_selected",
                    "sampler": row.get("sampler"),
                    "shader_unit": shader_unit,
                    "engine_unit": (-1 if row.get("selected_engine_unit") is None
                                    else int(row.get("selected_engine_unit"))),
                    "expected_texture": declared,
                    "selected_texture": selected,
                    "shift": row.get("fault_sampler_shift") or 0,
                })
    return {
        "schema": "gfx-replay-binding-audit/v1",
        "checked_events": len(by_event),
        "checked_bindings": len(samples),
        "mismatched_bindings": len(mismatches),
        "earliest_event": mismatches[0]["event"] if mismatches else None,
        "mismatches": mismatches[:256],
        "ok": not mismatches,
    }


def fault_evidence(provenance: list[dict], request: dict | None) -> dict | None:
    """Separate the selected fault request from evidence that it fired.

    The native replay records ``fault_sampler_shift`` on every binding it
    actually substituted.  A request that targets a missing event, or a draw
    with no eligible sampler above the threshold, leaves every recorded shift
    at zero.  A fired fault can still be pixel-neutral when the affected draw
    is overdrawn.  Neither case may be reported as a qualified campaign.
    """
    if not request:
        return None
    shifted = [
        row for row in provenance
        if int(row.get("fault_sampler_shift") or 0) != 0
        or (row.get("fault_missing_engine_unit") is not None
            and int(row.get("fault_missing_engine_unit")) >= 0)
    ]
    events = sorted({
        int(row.get("event")) for row in shifted
        if row.get("event") is not None
    })
    return {
        "schema": "gfx-fault-evidence/v1",
        "requested": dict(request),
        "fired": bool(shifted),
        "shifted_bindings": len(shifted),
        "events": events[:32],
        "reason": (
            "the replay provenance records at least one substituted sampler "
            "binding" if shifted else
            "the replay provenance records no substituted sampler binding; "
            "the selected fault did not fire"),
    }


_FINAL_STATISTICS = (
    "pixels", "mismatched_pixels", "mismatched_fraction",
    "alpha_different_pixels", "max_channel_error", "mean_abs_error",
    "mean_max_channel_error", "first_differing_pixel", "differing_bounds",
)


def exact_comparison_statistics(statistics: dict, width: int,
                                height: int) -> tuple[bool | None, str]:
    """Return raw four-channel exactness for a comparison statistics block.

    A policy pass is not exactness.  Exact equality requires a complete,
    dimensionally consistent BGRA8 measurement with zero mismatched pixels,
    zero alpha differences and zero maximum channel error on all four
    channels.  Missing or inconsistent statistics return ``(None, reason)`` so
    callers fail closed instead of advertising a match.

    The function deliberately consumes the raw statistics rather than the
    policy verdict: a caller-supplied threshold must never be able to promote
    a one-byte difference into an exact match.
    """
    if not isinstance(statistics, dict):
        return None, "comparison statistics are absent"
    try:
        pixels = int(statistics["pixels"])
        mismatched = int(statistics["mismatched_pixels"])
        max_error = int(statistics["max_channel_error"])
        alpha = int(statistics["alpha_different_pixels"])
    except (KeyError, TypeError, ValueError) as exc:
        return None, f"comparison statistics are incomplete: {exc}"
    mean_abs = statistics.get("mean_abs_error")
    if not isinstance(mean_abs, (list, tuple)) or len(mean_abs) != 4:
        return None, "comparison did not measure all four BGRA channels"
    if pixels <= 0 or width <= 0 or height <= 0:
        return None, "comparison extent is missing or empty"
    if pixels != width * height:
        return None, (f"comparison pixel count {pixels} disagrees with "
                      f"{width}x{height}")
    if mismatched < 0 or mismatched > pixels:
        return None, f"comparison mismatch count is out of range: {mismatched}"
    if alpha < 0 or alpha > mismatched:
        return None, f"comparison alpha count is out of range: {alpha}"
    if max_error < 0 or max_error > 255:
        return None, f"comparison maximum error is out of range: {max_error}"
    if (mismatched == 0 and max_error == 0
            and alpha == 0 and all(float(value) == 0.0 for value in mean_abs)):
        return True, ""
    return False, ""


def _exact_report_status(report: dict) -> tuple[str, bool | None, str]:
    """Status for a full comparison report, ignoring policy tolerances."""
    reference = report.get("reference") or {}
    width = reference.get("width")
    height = reference.get("height")
    if not isinstance(width, int) or not isinstance(height, int):
        return "invalid", None, "comparison reference extent is missing"
    exact, reason = exact_comparison_statistics(
        report.get("statistics") or {}, width, height)
    if exact is None:
        return "invalid", None, reason
    return ("match" if exact else "divergent"), exact, ""


def final_comparison_report(frame_dir: Path, out_dir: Path,
                            policy_path: Path | str | None = None) -> dict:
    """Full-frame differential through the shared comparison engine.

    The complete ``m6_compare`` report (statistics, worst region and draw
    attribution) is retained under ``detail`` so the diagnostic does not
    duplicate the comparison engine or discard region evidence.

    ``status`` is raw exactness under the four-channel BGRA measurement.  The
    caller's policy remains available as ``policy_status``/``policy_pass`` for
    callers that explicitly want a tolerance decision; it can never turn a
    nonzero difference into ``match``.
    """
    policy_path = Path(policy_path or DEFAULT_POLICY)
    try:
        policy = json.loads(policy_path.read_text())
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        return {"status": "invalid", "reason": f"comparison policy: {exc}"}
    try:
        report = m6_compare.compare(Path(frame_dir), Path(out_dir), policy)
    except (m6_compare.CompareError, OSError, ValueError) as exc:
        return {"status": "invalid", "reason": str(exc)}
    statistics = report.get("statistics") or {}
    status, exact, exact_reason = _exact_report_status(report)
    if exact is None:
        return {"status": "invalid", "reason": exact_reason,
                "policy_status": report.get("status")}
    value = {
        "status": status,
        "exact": exact,
        "policy_status": report.get("status"),
        "policy_pass": report.get("status") == "pass",
        "width": report["reference"]["width"],
        "height": report["reference"]["height"],
        "reference_sha256": report["reference"]["sha256"],
        "candidate_sha256": report["candidate"]["sha256"],
        "statistics": {key: statistics[key] for key in _FINAL_STATISTICS
                       if key in statistics},
        "worst_regions": (statistics.get("regions") or [])[:4],
        "worst_region": report.get("worst_region"),
        "attribution": report.get("attribution"),
        "normalization": report.get("normalization"),
        "policy": str(policy_path),
        "detail": report,
    }
    return value


def final_comparison(frame_dir: Path, out_dir: Path,
                     policy_path: Path | str | None = None) -> dict:
    """Backward-compatible exact final comparison.

    The shared engine is used when the replay result is available; the direct
    BGRA path remains as the offline fallback for synthetic result directories
    that do not contain ``replay.json``.
    """
    report = final_comparison_report(frame_dir, out_dir, policy_path)
    if report.get("status") in ("match", "divergent"):
        return report
    manifest = json.loads((frame_dir / "frame-trace.json").read_text())
    frame = manifest.get("frame") or {}
    width = int(frame.get("width") or 0)
    height = int(frame.get("height") or 0)
    reference = frame_dir / str(frame.get("reference") or "")
    candidate = out_dir / "metal-frame.bgra"
    if not candidate.is_file():
        return {"status": "invalid", "reason": "Metal final readback is absent"}
    statistics = m6_compare.compare_images(
        reference.read_bytes(), candidate.read_bytes(), width, height)
    exact, exact_reason = exact_comparison_statistics(statistics, width, height)
    if exact is None:
        return {"status": "invalid", "reason": exact_reason}
    policy = json.loads(Path(policy_path or DEFAULT_POLICY).read_text())
    thresholds = policy.get("thresholds") or {}
    max_channel = int(thresholds.get("max_channel_error", 0))
    max_fraction = float(thresholds.get("mismatched_fraction", 0.0))
    max_mean = float(thresholds.get("mean_abs_error", 0.0))
    mean_total = sum(float(value) for value in statistics["mean_abs_error"])
    policy_pass = (
        statistics["max_channel_error"] <= max_channel
        and statistics["mismatched_fraction"] <= max_fraction
        and mean_total <= max_mean
    )
    return {
        "status": "match" if exact else "divergent",
        "exact": exact,
        "policy_status": "pass" if policy_pass else "fail",
        "policy_pass": policy_pass,
        "width": width,
        "height": height,
        "reference_sha256": _sha(reference),
        "candidate_sha256": _sha(candidate),
        "statistics": {key: statistics[key] for key in _FINAL_STATISTICS},
        "worst_regions": statistics["regions"][:4],
        "policy": str(policy_path or DEFAULT_POLICY),
    }


def _event_index(row: dict) -> int:
    value = row.get("event")
    if value is None:
        value = row.get("index")
    return -1 if value is None else int(value)


def _event_context(graph: dict, event) -> dict:
    if event is None:
        return {}
    for row in graph.get("events") or []:
        if _event_index(row) == int(event):
            return {
                "event": int(event),
                "kind": row.get("kind"),
                "operation": row.get("operation"),
                "entry": row.get("entry"),
                "caller_rva": row.get("caller_rva"),
                "program": row.get("program"),
                "render_target": row.get("target"),
                "writes": list(row.get("writes") or []),
                "samples": list(row.get("samples") or []),
                "state": row.get("state") or {},
            }
    return {"event": int(event)}


def _dependent_consumers(graph: dict, resource_id: str,
                         after_event: int) -> list[dict]:
    resource = (graph.get("resources") or {}).get(resource_id) or {}
    return [
        {
            "event": int(item.get("event")),
            "unit": item.get("unit"),
            "sampler": item.get("sampler"),
            "operation": item.get("operation"),
            "destination": item.get("destination"),
        }
        for item in resource.get("consumers") or []
        if item.get("event") is not None
        and int(item["event"]) > after_event
    ]


def _suggested_checkpoint(graph: dict, final: dict) -> dict | None:
    """Propose one additional checkpoint when the final image differs alone."""
    events = list(graph.get("events") or [])
    if not events:
        return None
    last = max(events, key=_event_index)
    event = _event_index(last)
    if event < 0:
        return None
    resource = str(last.get("target") or "fb:0")
    return {
        "event": event,
        "target": "0" if resource == "fb:0" else resource.split(":", 1)[-1],
        "resource": resource,
        "requires_capture": True,
        "reason": (
            "checkpoint the last captured event's target; when it is not the "
            "final framebuffer, a checkpoint at or after the last default-"
            "framebuffer write is required to separate the final pass"),
        "final_statistics": final.get("statistics"),
    }


def _copy_source_evidence(checkpoints: list[dict],
                          producer_event: int | None,
                          source_target: str = "0") -> dict:
    """Return independent evidence for a copy source checkpoint.

    Destination ``captured_versions`` are not source evidence.  The only
    source evidence is a checkpoint whose event is the copy producer and
    whose target is the actual source resource (``0`` for the default
    framebuffer, the texture name for an attachment copy).
    """
    if producer_event is None:
        return {"status": "unobserved",
                "reason": "the copy producer event is unknown"}
    matching = [
        item for item in checkpoints
        if item.get("event") == producer_event
        and str(item.get("target")) == str(source_target)
    ]
    if not matching:
        return {
            "status": "unobserved",
            "reason": (
                "no copy-source checkpoint was captured at the producer "
                "event"),
            "event": producer_event,
            "target": str(source_target),
        }
    invalid = [item for item in matching if item.get("status") == "invalid"]
    if invalid:
        return {
            "status": "invalid",
            "reason": "the copy-source checkpoint is invalid",
            "checkpoint": invalid[0],
        }
    divergent = [item for item in matching
                 if item.get("status") == "divergent"
                 and item.get("authoritative")]
    if divergent:
        return {
            "status": "divergent",
            "reason": "the copy-source checkpoint was independently observed "
                      "to differ",
            "checkpoint": divergent[0],
            "event": producer_event,
            "target": str(source_target),
        }
    diagnostic = [item for item in matching
                  if item.get("status") == "divergent_diagnostic"]
    if diagnostic:
        return {
            "status": "unverified",
            "reason": (
                "the copy-source checkpoint is only a non-authoritative "
                "diagnostic observation"),
            "checkpoint": diagnostic[0],
        }
    if all(item.get("status") == "match" for item in matching):
        return {
            "status": "match",
            "reason": "the copy-source checkpoint was independently observed "
                      "to match",
            "checkpoint": matching[0],
        }
    return {
        "status": "unobserved",
        "reason": "the copy-source checkpoint has no usable status",
        "checkpoint": matching[0],
    }


def _normalise_checkpoint(value: dict | None,
                          source: str) -> dict | None:
    if not isinstance(value, dict):
        return None
    return {
        "event": value.get("event"),
        "target": value.get("target"),
        "resource": value.get("resource"),
        "requires_capture": bool(value.get("requires_capture")),
        "reason": str(value.get("reason") or ""),
        "source": source,
        "interval": value.get("interval"),
        "lower_bound": value.get("lower_bound"),
    }


def _checkpoint_matches_resource(item: dict, resource, target) -> bool:
    """True when a checkpoint observes the same relevant resource.

    Checkpoints produced by ``reconcile_checkpoints()`` carry ``resource``
    (``fb:0``/``tex:N``); older hand-built fixtures may carry only ``target``.
    A matching observation of an unrelated texture must never bound a
    framebuffer divergence.
    """
    item_resource = item.get("resource")
    if item_resource is not None and resource is not None:
        return str(item_resource) == str(resource)
    return str(item.get("target") or "") == str(target or "")


def first_divergence(graph: dict, checkpoints: list[dict],
                     final: dict, *, binding_audit: dict | None = None,
                     fault: dict | None = None,
                     checkpoint_validation: dict | None = None,
                     frame_id=None, state_coverage_report: dict | None = None,
                     event_count: int | None = None) -> dict:
    """Earliest confirmed semantic, intermediate or final divergence.

    This localizes the earliest *observed* difference.  It never claims that
    the observed event is the root cause: the producer and its inputs are
    reported so a later focused experiment can establish causality.
    """
    validation = checkpoint_validation or {}
    if validation.get("status") in ("invalid", "incomplete"):
        complete = validation.get("status") == "invalid"
        return {
            "schema": "gfx-first-divergence/v1",
            "kind": "invalid_checkpoint" if complete else "incomplete_checkpoint",
            "frame_id": frame_id,
            "event": None,
            "statistics": None,
            "semantic": None,
            "intermediate": None,
            "confidence": "inconclusive",
            "root_cause": "not_established",
            "reason": (
                "required checkpoint evidence is missing or unusable; "
                "this is an instrumentation failure, not a Metal difference"),
            "better_checkpoint_validation": validation,
            "recommended_checkpoint": None,
            "recommended_action": {
                "action": "repair_evidence",
                "reason": "repair or recapture the missing checkpoint evidence",
            },
            "fault": fault,
        }
    audit = binding_audit or {}
    first_semantic = (audit.get("mismatches") or [None])[0]
    divergent = [
        item for item in checkpoints
        if item.get("status") == "divergent" and item.get("authoritative")
    ]
    semantic_event = first_semantic.get("event") if first_semantic else None
    intermediate_event = divergent[0]["event"] if divergent else None
    candidates = [
        ("semantic", semantic_event if not audit.get("ok") else None),
        ("intermediate", intermediate_event),
    ]
    candidates = [item for item in candidates if item[1] is not None]
    earliest = min(candidates, key=lambda item: item[1]) if candidates else None
    unsupported = list((state_coverage_report or {}).get("unsupported") or [])
    if earliest and earliest[1] == semantic_event:
        context = _event_context(graph, semantic_event)
        return {
            "schema": "gfx-first-divergence/v1",
            "frame_id": frame_id,
            "kind": "semantic",
            "event": semantic_event,
            "target": context.get("render_target"),
            "operation": context.get("operation") or context.get("kind"),
            "entry": context.get("entry"),
            "caller_rva": context.get("caller_rva"),
            "program": context.get("program"),
            "inputs": context.get("samples") or [],
            "render_target": context.get("render_target"),
            "dependent_resources": [],
            "state": context.get("state") or {},
            "semantic": first_semantic,
            "intermediate": (divergent[0] if divergent else None),
            "final": final.get("status"),
            "fault": fault,
            "unsupported_state": unsupported,
            "confidence": "confirmed",
            "root_cause": "not_established",
            "authoritative_because": (
                "the replay's own binding provenance disagrees with the "
                "captured shader/texture contract at this event"),
            "recommended_checkpoint": None,
            "recommended_action": {
                "action": "investigate_binding",
                "event": semantic_event,
                "reason": (
                    "the binding/state mismatch is the earliest confirmed "
                    "difference; inspect that event before choosing another "
                    "checkpoint"),
            },
            "reason": (
                "the earliest confirmed difference is a binding/state mismatch; "
                "its pixel effect may be overdrawn later"),
        }
    if divergent:
        first = divergent[0]
        event = first.get("event")
        resource = (graph.get("resources") or {}).get(first["resource"]) or {}
        producers = [item for item in resource.get("producers") or []
                     if item.get("event") is not None
                     and int(item["event"]) < int(event or 0)]
        producer = producers[-1] if producers else None
        context = _event_context(graph, event)
        producer_context = _event_context(
            graph, producer.get("event") if producer else event)
        divergent_resource = first.get("resource")
        divergent_target = first.get("target")
        divergent_capture = first.get("capture")
        matching: list[int] = []
        foreign_capture: list[int] = []
        for item in checkpoints:
            if (item.get("status") != "match"
                    or item.get("event") is None
                    or int(item["event"]) >= int(event or 0)
                    or not _checkpoint_matches_resource(
                        item, divergent_resource, divergent_target)):
                continue
            if _checkpoint_capture_matches(item.get("capture"),
                                           divergent_capture):
                matching.append(int(item["event"]))
            else:
                # A match observed in another frame/session may describe a
                # different engine state.  It is not a causal bound for this
                # observation, even when the scene parameters look alike.
                foreign_capture.append(int(item["event"]))
        previous = matching[-1] if matching else None
        event_kind = str(context.get("kind") or "")
        event_is_transfer = event_kind in ("resolve", "copy")
        limitations: list[str] = []
        if foreign_capture:
            limitations.append(
                "a same-resource matching checkpoint from a different "
                "capture was excluded from the causal bound; checkpoints "
                "only bound an interval within one sealed frame")
        next_checkpoint = None
        recommended_action = None
        producer_event = producer.get("event") if producer else None
        copy_case = bool(
            event_is_transfer
            or (producer and str(producer.get("kind")) in ("resolve", "copy")))
        source_resource = "fb:0"
        if producer:
            source = str(producer.get("source") or "")
            source_fbo = producer.get("source_framebuffer")
            source_resource = ("fb:0" if source in ("", "0")
                               or source_fbo == 0 else f"tex:{source}")
        source_target = ("0" if source_resource == "fb:0"
                         else source_resource.split(":", 1)[-1])
        if event_is_transfer:
            observed_target = str(first.get("target") or "0")
            source_target = observed_target
            source_resource = ("fb:0" if observed_target == "0"
                               else f"tex:{observed_target}")
        source_lookup_event = (
            event if event_is_transfer
            else producer_event if producer is not None else event)
        source_evidence = _copy_source_evidence(
            checkpoints, source_lookup_event, source_target)
        if event_is_transfer and str(first.get("target")) == "0":
            # This checkpoint is itself the copy source.  It is independent
            # source evidence, not a destination-version proxy.
            source_evidence = {
                "status": "divergent",
                "reason": (
                    "the earliest authoritative divergent checkpoint is the "
                    "copy source"),
                "checkpoint": first,
                "event": event,
                "target": source_target,
            }
        source_status = source_evidence.get("status")
        # Localise the source using observations before the transfer.  A
        # destination divergence must not treat a post-transfer source match
        # as evidence about the destination version.
        source_bound = event if event_is_transfer else producer_event
        source_matching = []
        source_foreign: list[int] = []
        for item in checkpoints:
            if (item.get("status") != "match"
                    or item.get("event") is None
                    or int(item["event"]) >= int(source_bound or event or 0)
                    or not (str(item.get("resource")) == source_resource
                            or str(item.get("target")) == source_target)):
                continue
            if _checkpoint_capture_matches(item.get("capture"),
                                           divergent_capture):
                source_matching.append(int(item["event"]))
            else:
                source_foreign.append(int(item["event"]))
        if source_foreign and copy_case:
            limitations.append(
                "a same-resource copy-source match from a different capture "
                "was excluded; it cannot establish the source matched in "
                "this frame")
        source_previous = max(source_matching) if source_matching else None
        if not copy_case:
            # A draw/clear producer is not a transfer.  Its output checkpoint
            # is not copy-source evidence; do not emit a copy-source block or
            # a copy-source limitation for it.
            source_evidence = None
            source_status = None
            source_previous = None
        if copy_case and source_status == "divergent":
            limitations.append(
                "the copy source was independently observed to differ; "
                "the copy execution is not established as the cause")
            start_event = (source_previous
                           if source_previous is not None else 0)
            if source_previous is not None and source_previous + 1 >= int(event):
                limitations.append(
                    "the source resource already matched immediately before "
                    "the divergent event; no interior checkpoint remains")
                recommended_action = {
                    "action": "investigate_event",
                    "event": source_previous,
                    "observation_event": event,
                    "target": first.get("target"),
                    "resource": first.get("resource"),
                    "reason": (
                        "the last matching same-resource checkpoint is "
                        "immediately before the divergence; checkpoints "
                        "observe state before their event, so operation "
                        f"{source_previous} is the candidate and event "
                        f"{event} only observes the result"),
                }
            elif source_previous is None and int(event) == 0:
                limitations.append(
                    "the divergence is already present at event 0; no "
                    "earlier captured operation exists to bisect")
                recommended_action = {
                    "action": "investigate_initial_state",
                    "event": 0,
                    "observation_event": 0,
                    "target": first.get("target"),
                    "resource": first.get("resource"),
                    "reason": (
                        "the source is already divergent at the first "
                        "captured event; inspect the initial state rather "
                        "than recapturing event 0"),
                }
            else:
                if source_previous is None:
                    # Event 0 is operational search scope, not observed
                    # matching evidence.  It is a valid new checkpoint.
                    midpoint = int(event) // 2
                else:
                    midpoint = max(int(start_event) + 1,
                                   (int(start_event) + int(event)) // 2)
                next_checkpoint = {
                    "event": midpoint,
                    "target": source_target,
                    "resource": source_resource,
                    "requires_capture": True,
                    "reason": (
                        "the copy source already differs; capture a "
                        f"{source_resource} checkpoint near the midpoint of "
                        "the unresolved interval to bisect the origin"),
                    "interval": [int(start_event), int(event)],
                    "lower_bound": ("same_resource_match"
                                    if source_previous is not None
                                    else "unbounded"),
                }
        elif str(divergent_resource) == "fb:0":
            # The captured default-framebuffer checkpoint before a draw or
            # clear already differs.  The only valid lower bound is an earlier
            # matching fb:0 observation; never manufacture one from another
            # resource.
            if previous is not None and previous + 1 >= int(event):
                limitations.append(
                    "the default framebuffer matched immediately before the "
                    "divergent checkpoint; no interior checkpoint remains")
                recommended_action = {
                    "action": "investigate_event",
                    "event": previous,
                    "observation_event": int(event),
                    "target": first.get("target"),
                    "resource": first.get("resource"),
                    "reason": (
                        "the last matching default-framebuffer checkpoint is "
                        "immediately before the divergence; checkpoints "
                        "observe state before their event, so operation "
                        f"{previous} is the candidate and event {event} only "
                        "observes the result"),
                }
            elif previous is None and int(event) == 0:
                limitations.append(
                    "the default framebuffer is already divergent at event 0; "
                    "no earlier captured operation exists to bisect")
                recommended_action = {
                    "action": "investigate_initial_state",
                    "event": 0,
                    "observation_event": 0,
                    "target": first.get("target"),
                    "resource": first.get("resource"),
                    "reason": (
                        "the default framebuffer is already divergent at the "
                        "first captured event; inspect the initial state "
                        "rather than recapturing event 0"),
                }
            else:
                midpoint = ((int(event) // 2) if previous is None else
                            max(int(previous) + 1,
                                (int(previous) + int(event)) // 2))
                next_checkpoint = {
                    "event": midpoint,
                    "target": "0",
                    "resource": "fb:0",
                    "requires_capture": True,
                    "reason": (
                        "the default framebuffer differs at the first "
                        "divergent checkpoint; capture an fb:0 checkpoint "
                        "near the midpoint of the unresolved interval to "
                        "bisect the origin"),
                    "interval": [previous if previous is not None else 0,
                                 int(event)],
                    "lower_bound": ("same_resource_match"
                                    if previous is not None else "unbounded"),
                }
        elif copy_case and producer:
            if source_status == "match":
                limitations.append(
                    "the copy source was independently observed to match; "
                    "the difference is at or after the transfer")
                recommended_action = {
                    "action": "investigate_transfer",
                    "event": producer_event,
                    "observation_event": event,
                    "target": first.get("target"),
                    "resource": first.get("resource"),
                    "reason": (
                        "the source checkpoint matches, so do not bisect "
                        "upstream; inspect the copy operation or the "
                        "destination consumer"),
                }
            elif source_status == "invalid":
                next_checkpoint = {
                    "event": source_lookup_event,
                    "target": source_target,
                    "resource": source_resource,
                    "requires_capture": True,
                    "reason": (
                        "the copy-source checkpoint is invalid; repair or "
                        "recapture the source before choosing an upstream "
                        "interval"),
                }
            else:
                limitations.append(
                    "the copy source has no independent captured checkpoint; "
                    "the difference may originate before the copy, but "
                    "upstream bisection is not yet justified")
                next_checkpoint = {
                    "event": source_lookup_event,
                    "target": source_target,
                    "resource": source_resource,
                    "requires_capture": True,
                    "reason": (
                        "capture the copy source immediately before the "
                        "transfer to establish whether the source or the "
                        "copy execution differs"),
                }
        interval_start = (source_previous
                          if copy_case and event_is_transfer else previous)
        if (copy_case and source_evidence is not None
                and source_evidence.get("status") == "unobserved"
                and not (event_is_transfer
                         and str(first.get("target")) == "0")):
            limitations.append(
                "no independent copy-source evidence exists; the next "
                "checkpoint must capture it")
        limitations.append(
            "the earliest observed differing event is not a proven root "
            "cause; earlier operations in the unresolved interval were not "
            "checkpointed")
        if unsupported:
            limitations.append(
                "captured state not implemented by Metal: "
                + ", ".join(unsupported)
                + "; no A/B experiment attributes the difference to it")
        return {
            "schema": "gfx-first-divergence/v1",
            "frame_id": frame_id,
            "kind": "intermediate",
            "event": event,
            "target": first.get("target"),
            "resource": first.get("resource"),
            "operation": (
                (context.get("operation") or context.get("kind"))
                if event_is_transfer else
                ((producer_context.get("operation")
                  or producer_context.get("kind")) if producer
                 else (context.get("operation") or context.get("kind")))),
            "entry": (context.get("entry") if event_is_transfer else
                      (producer_context.get("entry") if producer
                       else context.get("entry"))),
            "caller_rva": (context.get("caller_rva") if event_is_transfer
                           else (producer_context.get("caller_rva")
                                 if producer else context.get("caller_rva"))),
            "program": (context.get("program") if event_is_transfer else
                        (producer_context.get("program") if producer
                         else context.get("program"))),
            "producer_program": producer_context.get("program"),
            "producer_entry": producer_context.get("entry"),
            "producer_caller_rva": producer_context.get("caller_rva"),
            "producer_event": producer.get("event") if producer else None,
            "producer": producer,
            "prior_producers": [
                {"event": item.get("event"), "kind": item.get("kind"),
                 "operation": item.get("operation"),
                 "program": item.get("program")}
                for item in producers[-8:]
            ],
            "inputs": producer_context.get("samples") or [],
            "render_target": first.get("resource"),
            "dependent_resources": _dependent_consumers(
                graph, first.get("resource"), int(event or 0)),
            "state": producer_context.get("state") or {},
            "gl_sha256": first.get("reference_sha256"),
            "metal_sha256": first.get("metal_sha256"),
            "statistics": first.get("statistics"),
            "pixel_location": (
                (first.get("statistics") or {}).get("first_differing_pixel")),
            "differing_bounds": (
                (first.get("statistics") or {}).get("differing_bounds")),
            "upstream_checkpoints": [
                {"event": row.get("event"), "target": row.get("target"),
                 "status": next(
                     (item["status"] for item in checkpoints
                      if item.get("event") == row.get("event")
                      and str(item.get("target")) == row.get("target")),
                     "not_checkpointed")}
                for row in graph.get("events") or []
                if row.get("event") is not None
                and int(row["event"]) < int(event or 0)
                and row.get("samples")
                and row.get("target") == first.get("resource")
            ],
            "semantic": first_semantic,
            "final": final.get("status"),
            "unresolved_interval": {
                "start_event": interval_start,
                "end_event": event,
                "lower_bound": ("same_resource_match"
                                if interval_start is not None
                                else "unbounded"),
                "search_start_event": (interval_start
                                       if interval_start is not None else 0),
                "note": (
                    "the first confirmed divergent checkpoint; the lower "
                    "bound is the last matching checkpoint for the same "
                    "resource when one exists, otherwise it is unbounded. "
                    "Event 0 is only an operational search start, not "
                    "observed matching evidence"),
            },
            "next_checkpoint": next_checkpoint,
            "copy_source_evidence": (source_evidence if copy_case else None),
            "cross_capture_excluded": {
                "capture_matches": sorted(foreign_capture),
                "copy_source_matches": sorted(source_foreign),
                "note": (
                    "same-resource matches from another capture were excluded "
                    "from this interval; they cannot bound a sealed frame"),
            },
            "recommended_action": recommended_action,
            "recommended_checkpoint": _normalise_checkpoint(
                next_checkpoint, "next_checkpoint"),
            "diagnostic_checkpoints": [
                {"event": item.get("event"), "target": item.get("target"),
                 "statistics": item.get("statistics"),
                 "reason": ("non-authoritative checkpoint format; useful as "
                            "a diagnostic observation, not a confirmed "
                            "divergence")}
                for item in checkpoints
                if item.get("status") == "divergent_diagnostic"
                and item.get("event") is not None
                and int(item["event"]) < int(event or 0)
            ],
            "earliest_observed_difference": (
                min((item for item in checkpoints
                     if item.get("status") in ("divergent",
                                               "divergent_diagnostic")
                     and item.get("event") is not None),
                    key=lambda item: int(item["event"]), default=None)),
            "limitations": limitations,
            "unsupported_state": unsupported,
            "confidence": "confirmed",
            "root_cause": "not_established",
            "authoritative_because": (
                "this is a reconciled request/response checkpoint whose "
                "captured reference digest and Metal output were verified; it "
                "is the earliest such checkpoint before the final framebuffer"),
            "reason": (
                "the earliest authoritative intermediate checkpoint differs; "
                "its inputs are not necessarily the cause"),
            "fault": fault,
        }
    if final.get("status") == "divergent":
        events = list(graph.get("events") or [])
        last_event = max((_event_index(row) for row in events), default=-1)
        final_matching = [
            int(item["event"]) for item in checkpoints
            if item.get("status") == "match"
            and item.get("event") is not None
            and _checkpoint_matches_resource(item, "fb:0", "0")
        ]
        final_previous = max(final_matching) if final_matching else None
        return {
            "schema": "gfx-first-divergence/v1",
            "frame_id": frame_id,
            "kind": "final_only",
            "event": None,
            "last_event": last_event if last_event >= 0 else event_count,
            "statistics": final.get("statistics"),
            "pixel_location": (
                (final.get("statistics") or {}).get("first_differing_pixel")),
            "differing_bounds": (
                (final.get("statistics") or {}).get("differing_bounds")),
            "semantic": first_semantic,
            "intermediate": None,
            "suggested_checkpoint": _suggested_checkpoint(graph, final),
            "recommended_checkpoint": _normalise_checkpoint(
                _suggested_checkpoint(graph, final), "suggested_checkpoint"),
            "unresolved_interval": {
                "start_event": final_previous,
                "end_event": last_event if last_event >= 0 else event_count,
                "lower_bound": ("same_resource_match"
                                if final_previous is not None
                                else "unbounded"),
                "search_start_event": (final_previous
                                       if final_previous is not None else 0),
                "note": (
                    "the final image differs but no authoritative intermediate "
                    "checkpoint did; an unrelated resource match cannot bound "
                    "the default-framebuffer interval, so the lower bound is "
                    "unbounded unless a matching fb:0 observation exists"),
            },
            "unsupported_state": unsupported,
            "confidence": "final_only",
            "root_cause": "not_established",
            "reason": (
                "no authoritative intermediate checkpoint diverged before the "
                "final output; increase checkpoint detail around the last pass"),
            "fault": fault,
        }
    diagnostic = [item for item in checkpoints
                  if item.get("status") == "divergent_diagnostic"]
    return {
        "schema": "gfx-first-divergence/v1",
        "frame_id": frame_id,
        "kind": "none",
        "event": None,
        "diagnostic_checkpoints": [
            {"event": item["event"], "target": item["target"],
             "statistics": item.get("statistics")} for item in diagnostic
        ],
        "semantic": first_semantic,
        "intermediate": None,
        "unsupported_state": unsupported,
        "confidence": "none",
        "root_cause": "none_observed",
        "recommended_checkpoint": None,
        "recommended_action": None,
        "reason": "all authoritative checkpoints and the final output match",
        "fault": fault,
    }


def _enabled(state: dict, name: str) -> bool:
    value = state.get(name)
    return isinstance(value, dict) and bool(value.get("enabled"))


def _state_evidence(frame_dir: Path) -> dict:
    """Optional focused-test evidence for a captured state.

    A frame may carry ``state-equivalence.json`` (schema
    ``m7-state-equivalence/v1``) naming states that a focused A/B test proved
    equivalent.  This is evidence supplied by a test, never an assumption by
    the diagnostic.
    """
    path = Path(frame_dir) / "state-equivalence.json"
    if not path.is_file():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return {}
    if not isinstance(value, dict) or value.get(
            "schema") != "m7-state-equivalence/v1":
        return {}
    evidence = {}
    for item in value.get("states") or []:
        if not isinstance(item, dict):
            continue
        name = str(item.get("state") or "")
        if name and item.get("status") == "proven_equivalent":
            evidence[name] = {
                "state": name,
                "status": "proven_equivalent",
                "evidence": item.get("evidence"),
            }
    return evidence


def state_coverage(frame_dir: Path, *, trace: dict | None = None,
                   evidence: dict | None = None) -> dict:
    """Classify captured render state by what Metal actually implements.

    The report distinguishes four states explicitly:

    * ``implemented`` — captured and used by the Metal encoder;
    * ``unsupported`` — captured but not implemented (the known
      ``gl_dither``/``multisample`` gaps and states the host rejects);
    * ``unproven`` — captured or referenced without a focused equivalence test;
    * ``proven_equivalent`` — supported by a focused online/offline test.

    Unsupported state never explains a pixel difference by itself.  It narrows
    what the qualification can claim, and the first-divergence report keeps the
    difference unproven rather than attributing it to the state.
    """
    try:
        if trace is None:
            trace = m6_frame.load_trace(Path(frame_dir))
    except m6_frame.FrameError as exc:
        return {
            "schema": "gfx-state-coverage/v1",
            "status": "invalid",
            "supported": [],
            "unsupported": [],
            "unproven": [],
            "categories": [],
            "reason": str(exc),
        }
    evidence = dict(evidence if evidence is not None
                    else _state_evidence(Path(frame_dir)))
    observations: dict[str, dict] = {}
    observed_fields: set[str] = set()
    draws = 0
    saw_program = False
    saw_textures = False
    saw_target = False
    for event in trace.get("_events") or []:
        state = event.get("state") or {}
        if not isinstance(state, dict):
            state = {}
        observed_fields.update(str(key) for key in state)
        if event.get("kind") == "draw":
            draws += 1
            if event.get("program"):
                saw_program = True
            if event.get("textures"):
                saw_textures = True
            if event.get("target"):
                saw_target = True
        stencil = state.get("stencil")
        observed = {
            "gl_dither": state.get("dither") is True,
            "multisample": state.get("multisample") is True,
            "stencil_test": (isinstance(stencil, dict)
                             and bool(stencil.get("test"))),
            "alpha_test": _enabled(state, "alpha_test"),
        }
        polygon = state.get("polygon")
        if isinstance(polygon, dict):
            modes = polygon.get("mode") or []
            observed["polygon_mode"] = any(
                str(mode).lower() not in
                ("0x1b02", "0x1b00", "0x1b01", "fill", "gl_fill")
                for mode in modes)
            observed["polygon_offset"] = bool(polygon.get("offset_fill"))
        for name, active in observed.items():
            if not active:
                continue
            row = observations.setdefault(
                name, {"state": name, "draws": 0, "status": "unsupported"})
            row["draws"] += 1
    categories = []
    if saw_program:
        categories.append({
            "state": "shader_bindings", "status": "implemented",
            "captured": True,
            "evidence": "captured draw state is consumed by the Metal encoder",
        })
    if saw_target:
        categories.append({
            "state": "render_targets", "status": "implemented",
            "captured": True,
            "evidence": "captured draw state is consumed by the Metal encoder",
        })
    if saw_textures:
        categories.append({
            "state": "samplers", "status": "implemented",
            "captured": True,
            "evidence": "captured draw state is consumed by the Metal encoder",
        })
    for field, name in (
        ("viewport", "viewport"), ("scissor", "scissor"),
        ("blend", "blend"), ("depth", "depth"),
        ("color_mask", "color_mask"), ("cull", "cull"),
    ):
        if field in observed_fields:
            categories.append({
                "state": name, "status": "implemented", "captured": True,
                "evidence": "captured draw state is consumed by the Metal encoder",
            })
    known_observations = {
        "dither", "multisample", "stencil", "polygon", "alpha_test",
        "viewport", "scissor", "blend", "depth", "color_mask", "cull",
    }
    for field in sorted(observed_fields - known_observations):
        categories.append({
            "state": field, "status": "unproven", "captured": True,
            "evidence": "captured state has no documented Metal mapping",
        })
    for name, row in sorted(observations.items()):
        if name in evidence:
            row = dict(row, status="proven_equivalent",
                       evidence=evidence[name].get("evidence"))
        else:
            row["status"] = STATE_IMPLEMENTATION.get(name, "unproven")
            if row["status"] == "implemented":
                row["status"] = "implemented"
        categories.append(row)
    if not categories:
        categories.append({
            "state": "draw_state", "status": "unproven",
            "captured": False,
            "evidence": "the trace contains no draw state to qualify",
        })
    unsupported = sorted(row["state"] for row in categories
                         if row.get("status") == "unsupported")
    unproven = sorted(row["state"] for row in categories
                      if row.get("status") == "unproven")
    proven = sorted(row["state"] for row in categories
                    if row.get("status") == "proven_equivalent")
    status = "partial" if (unsupported or unproven) else "complete"
    return {
        "schema": "gfx-state-coverage/v1",
        "status": status,
        "draws": draws,
        "supported": sorted(row["state"] for row in categories
                            if row.get("status") == "implemented"),
        "unsupported": unsupported,
        "unproven": unproven,
        "proven_equivalent": proven,
        "categories": categories,
        "scope": "captured draw state observed by the replay host",
        "claim": (
            "unsupported states prevent a full renderer-equivalence claim; "
            "they are not evidence that they caused a particular difference"),
    }



def _optional_json(path: Path) -> dict | None:
    value = _optional_json_value(path)
    return value if isinstance(value, dict) else None


def _optional_json_value(path: Path):
    """Read a JSON value, distinguishing absence from a malformed container.

    ``None`` means the file is missing or unparseable.  A present scalar,
    array or string is returned as-is so validation can reject it explicitly
    instead of silently treating it as absent.
    """
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None


def load_policy(policy_path: Path | str | None) -> tuple[dict, dict]:
    """Load a comparison policy and its content identity, fail closed.

    An explicitly requested policy is never allowed to fall back to the
    default.  The returned evidence is embedded in diagnosis/campaign reports
    so a saved result names the exact policy bytes that produced it.
    """
    path = Path(policy_path or DEFAULT_POLICY).expanduser().resolve()
    source = "explicit" if policy_path is not None else "default"
    try:
        payload = path.read_bytes()
    except OSError as exc:
        raise ReplayError("policy_unavailable", f"{path}: {exc}") from exc
    try:
        value = json.loads(payload)
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise ReplayError("policy_invalid", f"{path}: {exc}") from exc
    if not isinstance(value, dict):
        raise ReplayError("policy_invalid", f"{path}: policy is not an object")
    return value, {
        "path": str(path),
        "sha256": hashlib.sha256(payload).hexdigest(),
        "bytes": len(payload),
        "source": source,
        "schema": value.get("schema"),
        "name": value.get("name"),
    }


def _load_policy(policy_path: Path | str | None) -> dict:
    return load_policy(policy_path)[0]


def _capture_error_class(message: str) -> str:
    """Separate incomplete capture from evidence that actively disagrees."""
    lowered = str(message).lower()
    integrity = ("digest", "sha256", "size does not match", "does not match",
                 "blob", "belongs to another")
    return "invalid" if any(token in lowered for token in integrity) else "incomplete"


def identity_axis(seal: dict | None, trace: dict, replay: dict,
                  shadow: dict | None = None, *,
                  require_seal: bool = True) -> dict:
    """Public projection of the shared replay-identity axis.

    Correction acceptance must apply the same identity contract the diagnosis
    path uses; it may not invent a weaker one.  ``verified`` is the only status
    that can support complete qualification.
    """
    return _identity_check(seal, trace, replay, shadow,
                           require_seal=require_seal)


def _identity_check(seal: dict | None, trace: dict, replay: dict,
                    shadow: dict | None, *, require_seal: bool) -> dict:
    checks: list[dict] = []
    problems: list[str] = []

    def check(name: str, expected, actual) -> None:
        ok = expected == actual
        checks.append({"check": name, "expected": expected, "actual": actual,
                       "ok": ok})
        if not ok:
            problems.append(f"{name} mismatch")

    if seal is None:
        if require_seal:
            problems.append("frame has no seal")
        status = "missing" if require_seal else "unsealed"
        if not require_seal and shadow is not None:
            # A shadow report claims an in-process identity, but without the
            # seal there is nothing authoritative to check it against.
            problems.append("shadow report has no frame seal to verify")
            status = "unverified_session"
        return {
            "status": status,
            "checks": checks,
            "problems": problems,
            "note": ("offline fixture: no same-process seal is attached"
                     if not require_seal else ""),
        }
    required = (
        "capture_frame_id", "pid", "session_id", "trace_manifest_sha256",
        "gl_reference_sha256", "operation_count",
    )
    if shadow is None:
        # A standalone offline replay has no same-process session/pid
        # report.  Its input digests may still agree with the seal, but that
        # is not proof of the original execution identity: fail closed and
        # report the missing evidence instead of copying the seal into the
        # observed column.
        for field in required:
            checks.append({"check": field, "expected": seal.get(field),
                           "actual": None, "ok": False})
        problems.append("no in-process shadow identity report")
        replay_reference = replay.get("manifest_reference_sha256")
        if replay_reference and replay_reference != seal.get("reference_sha256"):
            problems.append("offline replay reference disagrees with the seal")
        if (not replay.get("partial")
                and replay.get("events") != seal.get("operation_count")):
            problems.append("offline replay event count disagrees with the seal")
        return {
            "status": "unverified_session",
            "checks": checks,
            "problems": problems,
            "capture_frame_id": seal.get("capture_frame_id"),
            "session_id": seal.get("session_id"),
            "pid": seal.get("pid"),
        }
    missing = [field for field in required if shadow.get(field) is None]
    if missing:
        for field in required:
            checks.append({"check": field, "expected": seal.get(field),
                           "actual": shadow.get(field),
                           "ok": shadow.get(field) is not None and
                                 shadow.get(field) == seal.get(field)})
        problems.append(
            "shadow report is missing identity field(s): "
            + ", ".join(missing))
        return {
            "status": "incomplete",
            "checks": checks,
            "problems": problems,
            "capture_frame_id": seal.get("capture_frame_id"),
            "session_id": seal.get("session_id"),
            "pid": seal.get("pid"),
        }
    check("capture_frame_id", seal.get("capture_frame_id"),
          shadow.get("capture_frame_id"))
    check("pid", seal.get("pid"), shadow.get("pid"))
    check("session_id", seal.get("session_id"), shadow.get("session_id"))
    check("trace_manifest_sha256", seal.get("manifest_sha256"),
          shadow.get("trace_manifest_sha256"))
    check("gl_reference_sha256", seal.get("reference_sha256"),
          shadow.get("gl_reference_sha256"))
    check("operation_count", seal.get("operation_count"),
          shadow.get("operation_count"))
    check("trace_events", seal.get("operation_count"), trace.get("events"))
    if replay.get("manifest_reference_sha256"):
        check("replay_reference_sha256", seal.get("reference_sha256"),
              replay.get("manifest_reference_sha256"))
    if not replay.get("partial"):
        check("replay_events", seal.get("operation_count"), replay.get("events"))
    return {
        "status": "verified" if not problems else "mismatch",
        "checks": checks,
        "problems": problems,
        "capture_frame_id": seal.get("capture_frame_id"),
        "session_id": seal.get("session_id"),
        "pid": seal.get("pid"),
    }


def _as_int(value, default: int = 0) -> int:
    try:
        if isinstance(value, bool):
            return default
        return int(value)
    except (TypeError, ValueError, OverflowError):
        return default


def _safe_member_name(value) -> str | None:
    text = str(value or "")
    path = Path(text)
    if (not text or path.is_absolute() or path.name != text
            or "/" in text or "\\" in text):
        return None
    return text


def _checkpoint_problem(kind: str, detail: str, *, severity: str) -> dict:
    """One request/provenance problem with fail-closed severity.

    ``invalid`` means evidence actively disagrees (a stale request, a wrong
    digest, a substituted plan).  ``incomplete`` means required authoritative
    metadata is absent, so no equivalence claim may be made but the evidence
    can still be inspected.
    """
    return {"kind": kind, "detail": detail, "severity": severity}


def _checkpoint_request_plan_problems(graph: dict,
                                      checkpoints: list) -> list[dict]:
    """Require the published request to equal the graph-derived plan exactly."""
    expected = gfx_frame_graph.checkpoint_request(graph).get("checkpoints") or []
    expected_by_key = {_checkpoint_key(item): item for item in expected}
    problems: list[dict] = []
    seen: set[tuple[int, str]] = set()
    for item in checkpoints:
        if not isinstance(item, dict):
            problems.append(_checkpoint_problem(
                "malformed_request", "checkpoint entry is not an object",
                severity="invalid"))
            continue
        key = _checkpoint_key(item)
        if key in seen:
            problems.append(_checkpoint_problem(
                "duplicate_request",
                f"checkpoint ({key[0]}, {key[1]}) is requested more than once",
                severity="invalid"))
            continue
        seen.add(key)
        wanted = expected_by_key.get(key)
        if wanted is None:
            problems.append(_checkpoint_problem(
                "unexpected_request",
                f"checkpoint ({key[0]}, {key[1]}) is not required by the "
                "validated capture graph",
                severity="invalid"))
            continue
        target = str(item.get("target"))
        expected_resource = ("fb:0" if target == "0" else f"tex:{target}")
        resource = str(item.get("resource") or expected_resource)
        if resource != expected_resource:
            problems.append(_checkpoint_problem(
                "substituted_request",
                f"checkpoint ({key[0]}, {key[1]}) names resource {resource} "
                f"but the graph requires {expected_resource}",
                severity="invalid"))
            continue
        for field in ("file", "reference_file", "reference_sha256",
                      "reference_width", "reference_height"):
            if item.get(field) != wanted.get(field):
                problems.append(_checkpoint_problem(
                    "substituted_request",
                    f"checkpoint ({key[0]}, {key[1]}) {field} disagrees with "
                    "the validated capture graph",
                    severity="invalid"))
                break
        raw_producers = item.get("producer_events", [])
        if ("producer_events" in item
                and not _valid_producer_events(raw_producers)):
            problems.append(_checkpoint_problem(
                "malformed_request",
                f"checkpoint ({key[0]}, {key[1]}) producer history is not "
                "an integer array",
                severity="invalid"))
            continue
        published_producers = raw_producers if raw_producers is not None else []
        # ``writer_authority`` marks requests produced after writer history was
        # added to the plan.  A legacy published request has no such marker; its
        # empty producer list is retained as declared legacy evidence rather
        # than being reinterpreted against a newer derived plan.
        if "writer_authority" in item:
            if item.get("writer_authority") != wanted.get("writer_authority"):
                problems.append(_checkpoint_problem(
                    "substituted_request",
                    f"checkpoint ({key[0]}, {key[1]}) writer authority "
                    "disagrees with the validated capture graph",
                    severity="invalid"))
            elif (not isinstance(published_producers, list)
                  or any(not isinstance(value, int)
                         or isinstance(value, bool)
                         for value in published_producers)
                  or published_producers != (wanted.get("producer_events")
                                             or [])):
                problems.append(_checkpoint_problem(
                    "substituted_request",
                    f"checkpoint ({key[0]}, {key[1]}) producer history "
                    "disagrees with the validated capture graph",
                    severity="invalid"))
        else:
            # Legacy compatibility is limited to captures that legitimately
            # published no writer history.  A nonempty legacy list is still
            # checked against the graph rather than trusted by age.
            if (not isinstance(published_producers, list)
                    or any(not isinstance(value, int)
                           or isinstance(value, bool)
                           for value in published_producers)):
                problems.append(_checkpoint_problem(
                    "malformed_request",
                    f"checkpoint ({key[0]}, {key[1]}) legacy producer history "
                    "is not an integer array",
                    severity="invalid"))
            elif (published_producers
                  and published_producers != (wanted.get("producer_events")
                                              or [])):
                problems.append(_checkpoint_problem(
                    "substituted_request",
                    f"checkpoint ({key[0]}, {key[1]}) nonempty legacy producer "
                    "history disagrees with the validated capture graph",
                    severity="invalid"))
            elif (not published_producers
                  and (wanted.get("producer_events") or [])
                  and str(item.get("resource") or expected_resource).startswith(
                      "tex:")):
                # Only fb:0 copy/default-checkpoint entries were legitimately
                # empty in old captures.  Sampled textures always carried
                # their writer history; dropping it must not erase a writer.
                problems.append(_checkpoint_problem(
                    "substituted_request",
                    f"checkpoint ({key[0]}, {key[1]}) legacy request omits "
                    "nonempty graph producer history for a texture",
                    severity="invalid"))
    for key in expected_by_key:
        if key not in seen:
            problems.append(_checkpoint_problem(
                "missing_required_request",
                f"required checkpoint ({key[0]}, {key[1]}) is absent from the "
                "published request",
                severity="invalid"))
    return problems


def _checkpoint_receipt_dict(metal_dir: Path) -> dict | None:
    """Load the coordinator's authoritative bridge receipt, if published."""
    return _optional_json(Path(metal_dir) / "shader-receipt.json")


def _checkpoint_binding_problems(metal_dir: Path, request: dict,
                                 shadow: dict | None, receipt: dict | None,
                                 seal: dict | None) -> tuple[list[dict], list[str]]:
    """Bind the request to the exact sealed frame through authoritative data.

    The request file alone is only a plan; it proves nothing about which frame
    or execution produced it.  The shadow report (live coordinator/bridge) or
    the shader receipt (coordinator publication) records the request digest,
    name and count.  At least one must bind it, and every present binding must
    agree.
    """
    path = Path(metal_dir) / "checkpoint-request.json"
    problems: list[dict] = []
    bindings: list[str] = []

    if shadow is not None and not isinstance(shadow, dict):
        problems.append(_checkpoint_problem(
            "malformed_evidence",
            "shadow report is not an object",
            severity="invalid"))
        shadow = None
    if seal is not None and not isinstance(seal, dict):
        problems.append(_checkpoint_problem(
            "malformed_evidence",
            "capture seal is not an object",
            severity="invalid"))
        seal = None
    if not isinstance(request, dict):
        return [
            _checkpoint_problem(
                "malformed_request",
                "checkpoint request is not an object",
                severity="invalid"),
            *problems,
        ], []
    request_sha = _sha(path) if path.is_file() else None
    raw_checkpoints = request.get("checkpoints")
    if raw_checkpoints is None:
        count = 0
    elif isinstance(raw_checkpoints, list):
        count = len(raw_checkpoints)
    else:
        count = 0
        problems.append(_checkpoint_problem(
            "malformed_request",
            "checkpoint request checkpoints is not an array",
            severity="invalid"))

    if shadow is not None:
        sha = shadow.get("checkpoint_request_sha256")
        name = shadow.get("checkpoint_request_file")
        reported_count = shadow.get("checkpoint_count")
        if sha or name or reported_count is not None:
            bindings.append("shadow-report")
            if not sha:
                problems.append(_checkpoint_problem(
                    "unbound_request",
                    "shadow report has no checkpoint request digest",
                    severity="incomplete"))
            elif str(sha) != request_sha:
                problems.append(_checkpoint_problem(
                    "request_digest_mismatch",
                    "checkpoint request sha256 disagrees with the shadow "
                    "report that produced this result",
                    severity="invalid"))
            if not name:
                problems.append(_checkpoint_problem(
                    "unbound_request",
                    "shadow report has no checkpoint request file name",
                    severity="incomplete"))
            elif _safe_member_name(name) != path.name:
                problems.append(_checkpoint_problem(
                    "request_name_mismatch",
                    "checkpoint request file name disagrees with the shadow "
                    "report",
                    severity="invalid"))
            if reported_count is not None and _as_int(reported_count, -1) != count:
                problems.append(_checkpoint_problem(
                    "request_count_mismatch",
                    "shadow report checkpoint count disagrees with the "
                    "published request",
                    severity="invalid"))

    if receipt is not None and not isinstance(receipt, dict):
        problems.append(_checkpoint_problem(
            "receipt_schema",
            "shader receipt is not an object",
            severity="invalid"))
        receipt = None
    if receipt is not None:
        bindings.append("shader-receipt")
        if receipt.get("schema") != "m7-shadow-shader-receipt/v1":
            problems.append(_checkpoint_problem(
                "receipt_schema",
                "shader receipt schema is invalid",
                severity="invalid"))
        receipt_sha = receipt.get("checkpoint_request_sha256")
        receipt_name = receipt.get("checkpoint_request_file")
        receipt_count = receipt.get("checkpoint_count")
        if not receipt_sha or not receipt_name:
            problems.append(_checkpoint_problem(
                "unbound_request",
                "shader receipt has no checkpoint request binding",
                severity="incomplete"))
        else:
            if str(receipt_sha) != request_sha:
                problems.append(_checkpoint_problem(
                    "request_digest_mismatch",
                    "checkpoint request sha256 disagrees with the shader "
                    "receipt",
                    severity="invalid"))
            if _safe_member_name(receipt_name) != path.name:
                problems.append(_checkpoint_problem(
                    "request_name_mismatch",
                    "checkpoint request file name disagrees with the shader "
                    "receipt",
                    severity="invalid"))
            if receipt_count is None or _as_int(receipt_count, -1) != count:
                problems.append(_checkpoint_problem(
                    "request_count_mismatch",
                    "shader receipt checkpoint count disagrees with the "
                    "published request",
                    severity="invalid"))
        if seal is not None:
            for field in ("capture_frame_id", "pid", "session_id",
                          "trace_manifest_sha256"):
                expected = seal.get(
                    "manifest_sha256" if field == "trace_manifest_sha256"
                    else field)
                actual = receipt.get(field)
                if actual is None:
                    problems.append(_checkpoint_problem(
                        "unbound_request",
                        f"shader receipt is missing identity field {field}",
                        severity="incomplete"))
                elif actual != expected:
                    problems.append(_checkpoint_problem(
                        "request_frame_mismatch",
                        f"shader receipt {field} does not belong to the "
                        "sealed frame",
                        severity="invalid"))

    if not bindings:
        problems.append(_checkpoint_problem(
            "unbound_request",
            "checkpoint request is not bound to an authoritative shadow "
            "report or shader receipt",
            severity="incomplete"))
    return problems, bindings


def checkpoint_request_evidence(graph: dict, metal_dir: Path,
                                shadow: dict | None = None,
                                seal: dict | None = None
                                ) -> tuple[dict, str, list[dict], dict]:
    """Load and validate the checkpoint request bound to one Metal result.

    An unbound or disagreeing request is reported with an explicit severity;
    it is never silently replaced by a derived plan, and a later valid pixel
    match cannot repair it.  ``correction_accept`` and the in-process diagnosis
    share this one definition so an acceptance caller cannot bypass the graph
    agreement and binding checks.
    """
    metal_dir = Path(metal_dir)
    request_path = metal_dir / "checkpoint-request.json"
    published = _optional_json_value(request_path)
    if published is None:
        return gfx_frame_graph.checkpoint_request(graph), "derived", [], {
            "status": "derived",
            "bindings": [],
            "sha256": None,
        }
    if not isinstance(published, dict):
        # A present but malformed container must be rejected, not treated as
        # absent and silently replaced by a derived plan.
        problem = _checkpoint_problem(
            "malformed_request",
            "checkpoint request is not an object",
            severity="invalid")
        return {}, "published", [problem], {
            "status": "invalid",
            "bindings": [],
            "sha256": _sha(request_path),
            "receipt": _checkpoint_receipt_dict(metal_dir) is not None,
        }
    problems: list[dict] = []
    if published.get("schema") != "gfx-frame-checkpoint-request/v1":
        problems.append(_checkpoint_problem(
            "malformed_request", "checkpoint request schema is invalid",
            severity="invalid"))
    checkpoints = published.get("checkpoints")
    if not isinstance(checkpoints, list):
        problems.append(_checkpoint_problem(
            "malformed_request", "checkpoint request is not an array",
            severity="invalid"))
        checkpoints = []
    if not problems:
        problems.extend(_checkpoint_request_plan_problems(graph, checkpoints))
    receipt = _checkpoint_receipt_dict(metal_dir)
    binding_problems, bindings = _checkpoint_binding_problems(
        metal_dir, published, shadow, receipt, seal)
    problems.extend(binding_problems)
    status = "invalid" if any(
        problem.get("severity") == "invalid" for problem in problems) else (
        "incomplete" if problems else "bound")
    return published, "published", problems, {
        "status": status,
        "bindings": bindings,
        "sha256": _sha(metal_dir / "checkpoint-request.json"),
        "receipt": receipt is not None,
    }


def _provenance_axis(evaluation: dict) -> dict:
    consumer = evaluation.get("consumer_validation") or {}
    transfer = evaluation.get("transfer_validation") or {}
    seeded = evaluation.get("seeded_intermediates") or []
    external = [item for item in seeded
                if item.get("external_bindings")
                or item.get("first_sample_before_any_write")]
    problems = (consumer.get("problems") or []) + (transfer.get("problems") or [])
    if problems or external:
        status = "invalid"
    elif seeded:
        status = "incomplete"
    else:
        status = "verified"
    return {
        "status": status,
        "evaluation": evaluation.get("status"),
        "consumer_problems": consumer.get("problems") or [],
        "consumer_problems_count": consumer.get("problems_count", 0),
        "transfer_problems": transfer.get("problems") or [],
        "transfer_problems_count": transfer.get("problems_count", 0),
        "seeded_intermediates": seeded,
        "bindings": (evaluation.get("provenance") or {}).get("bindings"),
        "metal_render_target_bindings": (
            evaluation.get("provenance") or {}).get(
                "metal_render_target_bindings"),
        "external_capture_bindings": (
            evaluation.get("provenance") or {}).get(
                "external_capture_bindings"),
    }


def classify_diagnosis(*, capture_validity: str, identity: str,
                       execution: str, provenance: str,
                       operation_coverage: str, state_cov: str,
                       pixels: str, divergence: str,
                       checkpoint_validation: dict,
                       fault_experiment: bool = False,
                       fault_status: str | None = None) -> tuple[str, dict]:
    """Apply the PR3 qualification contract to independent evidence axes.

    Missing evidence fails closed into ``incomplete_*``; it is never promoted
    to a renderer difference.  A completed comparison that finds a difference
    is ``qualified_difference`` when every required axis is complete, and
    ``difference_with_partial_state_coverage`` when captured state is not fully
    implemented.
    """
    checkpoint_status = (checkpoint_validation or {}).get("status")
    if execution == "failed":
        classification = "gpu_execution_failure"
    elif capture_validity == "invalid" or provenance == "invalid":
        classification = "invalid_provenance"
    elif identity == "mismatch":
        classification = "invalid_provenance"
    elif identity in ("missing", "incomplete", "unverified_session"):
        # A standalone/relocated replay has no verified session binding.  That
        # is an identity limitation, not evidence that the captured frame is
        # incomplete; report it as missing diagnostic evidence.
        classification = "incomplete_diagnostics"
    elif capture_validity != "valid":
        classification = "incomplete_capture"
    elif identity != "verified":
        # Unknown/unsealed/standalone identities are never positive evidence.
        # A legacy classifier must not be able to advertise equivalence for a
        # replay whose session/frame binding was not verified.
        classification = "incomplete_diagnostics"
    elif provenance != "verified":
        classification = "incomplete_diagnostics"
    elif checkpoint_status == "invalid":
        classification = "invalid_provenance"
    elif checkpoint_status != "ok":
        classification = "incomplete_diagnostics"
    elif execution != "ok":
        classification = "incomplete_diagnostics"
    elif pixels not in ("match", "divergent"):
        classification = "incomplete_diagnostics"
    elif operation_coverage != "complete":
        classification = "incomplete_capture"
    elif divergence == "invalid_checkpoint":
        classification = "incomplete_diagnostics"
    elif pixels == "divergent" or divergence in (
            "semantic", "intermediate", "final_only", "confirmed"):
        if state_cov != "complete":
            classification = "difference_with_partial_state_coverage"
        else:
            classification = "qualified_difference"
    elif state_cov != "complete":
        classification = "unsupported_state"
    else:
        classification = "qualified_match"
    base_classification = classification
    if fault_experiment and classification not in (
            "gpu_execution_failure", "incomplete_capture",
            "incomplete_diagnostics", "invalid_provenance"):
        # Selecting a fault must not hide unrelated evidence corruption.
        classification = "fault_experiment"
    axes = {
        "capture_validity": capture_validity,
        "frame_shader_identity": identity,
        "gpu_execution": execution,
        "resource_provenance": provenance,
        "operation_coverage": operation_coverage,
        "rendering_state_coverage": state_cov,
        "pixel_comparison": pixels,
        "first_divergence_confidence": divergence,
    }
    if fault_experiment:
        axes["fault_experiment"] = fault_status or "requested"
        axes["base_classification"] = base_classification
    qualified_match = classification == "qualified_match"
    return classification, {
        "schema": "m7-qualification-contract/v1",
        "axes": axes,
        "classification": classification,
        "qualified": qualified_match,
        "diagnostic_qualified": classification in (
            "qualified_match", "qualified_difference")
        or (classification == "fault_experiment"
            and base_classification in ("qualified_match",
                                        "qualified_difference")),
        "renderer_equivalence": qualified_match,
        "claim": {
            "qualified_match": "complete evidence, no observed difference",
            "qualified_difference": "complete evidence, confirmed difference",
            "difference_with_partial_state_coverage": (
                "a difference is confirmed, but captured state is not fully "
                "implemented; this does not prove unsupported state caused it"),
            "incomplete_capture": "capture evidence is missing or incomplete",
            "invalid_provenance": "evidence identity or integrity disagrees",
            "unsupported_state": (
                "no difference was observed, but state coverage prevents an "
                "equivalence claim"),
            "gpu_execution_failure": "Metal execution did not complete",
            "incomplete_diagnostics": (
                "required diagnostic evidence is missing; this is not a "
                "renderer difference"),
            "fault_experiment": (
                "a selected fault experiment; it is never a qualified "
                "campaign, even when the fault is pixel-neutral or does not "
                "fire"),
        }.get(classification, ""),
    }


def diagnosis_exit_code(report: dict) -> int:
    """Stable CLI policy for a diagnostic report.

    0  qualified match
    2  a completed diagnostic that is not an equivalence claim
    1  incomplete or invalid evidence, or GPU execution failure
    """
    classification = (report.get("qualification") or {}).get(
        "classification") or report.get("classification")
    if classification == "qualified_match":
        return 0
    if classification in ("qualified_difference",
                          "difference_with_partial_state_coverage",
                          "unsupported_state"):
        return 2
    return 1


def _minimal_diagnosis(frame_dir: Path, metal_dir: Path, *, message: str,
                       classification: str) -> dict:
    value = {
        "schema": DIAGNOSIS_SCHEMA,
        "frame_dir": str(frame_dir),
        "metal_result_dir": str(metal_dir),
        "status": classification,
        "capture": {"validity": "invalid", "error": message},
        "identity": {"status": "unknown", "problems": [message]},
        "execution": {"status": "missing"},
        "provenance": {"status": "incomplete"},
        "operation_coverage": {"status": "incomplete"},
        "state_coverage": {"status": "invalid"},
        "pixels": {"status": "unavailable"},
        "checkpoints": [],
        "checkpoint_validation": {"status": "incomplete",
                                  "problems": [{"kind": "missing_capture"}]},
        "first_divergence": {
            "schema": "gfx-first-divergence/v1",
            "kind": "incomplete",
            "confidence": "inconclusive",
            "root_cause": "not_established",
            "reason": message,
        },
    }
    if classification == "incomplete_diagnostics":
        value["capture"] = {"validity": "valid", "error": message}
        classification_value, qualification = classify_diagnosis(
            capture_validity="valid",
            identity="unknown",
            execution="missing",
            provenance="incomplete",
            operation_coverage="incomplete",
            state_cov="invalid",
            pixels="unavailable",
            divergence="inconclusive",
            checkpoint_validation={"status": "incomplete"},
        )
    elif classification == "incomplete_capture":
        value["capture"] = {"validity": "incomplete", "error": message}
        classification_value, qualification = classify_diagnosis(
            capture_validity="incomplete",
            identity="unknown",
            execution="missing",
            provenance="incomplete",
            operation_coverage="incomplete",
            state_cov="invalid",
            pixels="unavailable",
            divergence="inconclusive",
            checkpoint_validation={"status": "incomplete"},
        )
    else:
        value["capture"] = {"validity": "invalid", "error": message}
        classification_value, qualification = classify_diagnosis(
            capture_validity="invalid",
            identity="unknown",
            execution="missing",
            provenance="incomplete",
            operation_coverage="incomplete",
            state_cov="invalid",
            pixels="unavailable",
            divergence="inconclusive",
            checkpoint_validation={"status": "incomplete"},
        )
    value["status"] = classification
    value["classification"] = classification_value
    value["qualification"] = qualification
    return value


def analyse_frame(frame_dir: Path | str, metal_result_dir: Path | str,
                  policy: Path | str | None = None, *,
                  trace: dict | None = None, seal: dict | None = None,
                  graph: dict | None = None, request: dict | None = None,
                  request_source: str | None = None,
                  require_seal: bool = False,
                  shaders: Path | str | None = None,
                  faults: dict | None = None) -> dict:
    """Diagnose already-produced Metal results without executing Metal again.

    This is the reusable analysis half of :func:`replay`.  It validates the
    frame seal and capture, verifies the replay's identity and resource
    provenance, reconciles checkpoints, audits shader bindings, computes pixel
    metrics, localizes the earliest confirmed difference and applies the
    qualification contract.  It never launches a host, EU4 or a GPU replay.
    """
    frame_dir = Path(frame_dir).expanduser().resolve()
    metal_dir = Path(metal_result_dir).expanduser().resolve()
    policy_value, policy_evidence = load_policy(policy)
    state_evidence = {}
    configured = policy_value.get("state_equivalence")
    if isinstance(configured, dict):
        state_evidence.update(configured)
    elif isinstance(configured, list):
        for item in configured:
            if isinstance(item, dict) and item.get("state"):
                state_evidence[str(item["state"])] = item
    state_evidence.update(_state_evidence(frame_dir))

    if trace is None:
        try:
            trace = m6_frame.load_trace(frame_dir, verify_blobs=True)
        except m6_frame.FrameError as exc:
            classification = ("invalid_provenance"
                              if _capture_error_class(str(exc)) == "invalid"
                              else "incomplete_capture")
            return _minimal_diagnosis(frame_dir, metal_dir, message=str(exc),
                                      classification=classification)
    seal_error = None
    if seal is None:
        seal_path = frame_dir / "frame-seal.json"
        if seal_path.is_file():
            try:
                seal = m6_frame.load_seal(frame_dir, trace=trace)
            except m6_frame.FrameError as exc:
                seal_error = str(exc)
    if graph is None:
        graph = gfx_frame_graph.build(frame_dir, trace=trace)
    replay = _optional_json(metal_dir / "replay.json")
    if replay is None:
        value = _minimal_diagnosis(
            frame_dir, metal_dir,
            message="Metal result directory has no replay.json",
            classification="incomplete_diagnostics")
        value["capture"] = {
            "validity": "valid",
            "graph_status": graph.get("status"),
            "reasons": graph.get("reasons"),
        }
        return value
    replay = dict(replay)
    replay["_provenance_samples"] = _read_jsonl(
        metal_dir / "replay-provenance.jsonl")
    shadow = _optional_json(metal_dir / "shadow-report.json")
    request_binding: dict = {"status": "in_process", "bindings": []}
    if request is None:
        request, request_source, request_problems, request_binding = (
            checkpoint_request_evidence(graph, metal_dir, shadow, seal))
    else:
        request_source = request_source or "provided"
        request_problems = []
    trace_events = len(trace.get("_events") or [])
    capture = capture_identity(seal, trace, replay)
    identity = _identity_check(seal, trace, replay, shadow,
                               require_seal=require_seal)
    if (identity.get("status") == "unsealed"
            and (request_binding or {}).get("receipt")):
        # A receipt is only authoritative when a seal binds it to the frame.
        identity = dict(
            identity, status="unverified_session",
            problems=list(identity.get("problems") or []) + [
                "shader receipt has no frame seal to verify"])
    if seal_error:
        identity = dict(identity, status="mismatch",
                        problems=list(identity.get("problems") or [])
                        + [f"frame seal: {seal_error}"])
    evaluation = gfx_frame_graph.evaluate_replay(graph, replay)
    if request_problems:
        plan = request.get("checkpoints")
        requested = len(plan) if isinstance(plan, list) else 0
        invalid = any(problem.get("severity") == "invalid"
                      for problem in request_problems)
        checkpoints = []
        validation = {
            "schema": "gfx-checkpoint-validation/v1",
            "status": "invalid" if invalid else "incomplete",
            "requested": requested,
            "returned": len(replay.get("checkpoints") or []),
            "matched": 0,
            "diagnostic": 0,
            "divergent": 0,
            "invalid": 0,
            "problems": [
                {"kind": problem.get("kind"),
                 "detail": problem.get("detail"),
                 "severity": problem.get("severity")}
                for problem in request_problems
            ],
            "problems_count": len(request_problems),
            "request_source": request_source,
            "request_binding": request_binding,
            "note": (
                "the published checkpoint request is not bound to the sealed "
                "frame; qualification fails closed"
                if not invalid else
                "the published checkpoint request disagrees with the "
                "authoritative evidence; qualification fails closed"),
        }
    else:
        checkpoints, validation = reconcile_checkpoints(
            graph, request, replay, frame_dir, metal_dir, capture=capture)
        validation = dict(validation)
        validation["request_source"] = request_source
        validation["request_binding"] = request_binding
        captured_keys = {
            key for key in (_capture_key(item.get("capture"))
                            for item in checkpoints)
            if key is not None
        }
        if len(captured_keys) > 1:
            # Observations from two captures must never be combined into one
            # causal interval, even if a caller assembled them by hand.
            validation["status"] = "invalid"
            validation["problems"] = list(validation.get("problems") or []) + [{
                "kind": "cross_capture_checkpoints",
                "detail": (
                    "checkpoints were reconciled from more than one capture "
                    "identity; a causal interval requires one sealed frame")}]
            validation["problems_count"] = len(validation["problems"])
    kinds = {str(item.get("kind")) for item in validation.get("problems") or []}
    if validation.get("status") == "invalid" and kinds and kinds <= {
            "missing_result"}:
        # A requested checkpoint that never came back is missing evidence, not
        # a renderer difference.
        validation["status"] = "incomplete"
        validation["note"] = (
            "requested checkpoint results are missing; this is incomplete "
            "evidence, not a difference")
    if request_source == "derived":
        validation["request_binding"] = request_binding
        # A request that was not published with the result cannot be bound to
        # the replay.  This is true even when the derived plan is empty: the
        # qualification cannot otherwise prove which request the replay
        # executed.  Individual checkpoint files may still be useful.
        validation["status"] = "incomplete"
        validation["problems"] = list(validation.get("problems") or []) + [
            {"kind": "request_not_bound",
             "detail": "checkpoint request was derived after the replay"}]
        validation["note"] = (
            "the replay did not publish the checkpoint request that produced "
            "its results; qualification fails closed")
    audit = binding_audit(replay)
    fault_record = fault_evidence(
        replay.get("_provenance_samples") or [], faults)
    states = state_coverage(frame_dir, trace=trace, evidence=state_evidence)
    final = final_comparison_report(frame_dir, metal_dir, policy)
    frame_info = trace.get("frame") or {}
    frame_id = (seal or {}).get("capture_frame_id")
    present_index = (seal or {}).get("present_index",
                                     trace.get("present_index"))
    scene_index = (seal or {}).get("scene_index", trace.get("scene_index"))
    divergence = first_divergence(
        graph, checkpoints, final, binding_audit=audit,
        fault=faults or None, checkpoint_validation=validation,
        frame_id=frame_id, state_coverage_report=states,
        event_count=trace_events)
    divergence["present_index"] = present_index
    divergence["scene_index"] = scene_index
    encoded = replay.get("encoded")
    failed = replay.get("failed")
    command_status = replay.get("command_status")
    readback = bool(replay.get("readback"))
    execution_ok = (
        replay.get("status") == "ok"
        and isinstance(encoded, int) and isinstance(replay.get("events"), int)
        and encoded == replay.get("events")
        and failed == 0
        and command_status == 4
        and readback
        and not replay.get("timed_out")
    )
    execution = {
        "status": "ok" if execution_ok else "failed",
        "events": replay.get("events"),
        "encoded": encoded,
        "failed": failed,
        "command_status": command_status,
        "readback_ok": readback,
        "partial": bool(replay.get("partial")),
        "timed_out": bool(replay.get("timed_out")),
        "stage": replay.get("stage"),
        "error": replay.get("error"),
        "failure": replay.get("failure"),
        "failures": replay.get("failures"),
    }
    if replay.get("partial"):
        operation_coverage = "partial"
    elif not execution_ok:
        operation_coverage = "invalid"
    elif (isinstance(replay.get("events"), int)
          and encoded == replay.get("events")
          and failed == 0):
        operation_coverage = "complete"
    else:
        operation_coverage = "partial"

    graph_status = graph.get("status")
    if graph_status == "invalid":
        capture_validity = "invalid"
    elif graph_status == "partial":
        capture_validity = "incomplete"
    else:
        capture_validity = "valid"
    if seal_error:
        capture_validity = "invalid"
    if identity.get("status") == "mismatch":
        capture_validity = "invalid"
    if final.get("status") not in ("match", "divergent"):
        pixels = "unavailable"
    else:
        pixels = final["status"]
    provenance = _provenance_axis(evaluation)
    classification, qualification = classify_diagnosis(
        capture_validity=capture_validity,
        identity=str(identity.get("status")),
        execution=str(execution.get("status")),
        provenance=provenance["status"],
        operation_coverage=operation_coverage,
        state_cov=str(states.get("status") or "invalid"),
        pixels=pixels,
        divergence=str(divergence.get("kind")),
        checkpoint_validation=validation,
        fault_experiment=bool(faults),
        fault_status=(
            "fired" if fault_record and fault_record.get("fired")
            else "not_fired" if fault_record else None),
    )
    report = {
        "schema": DIAGNOSIS_SCHEMA,
        "frame_dir": str(frame_dir),
        "metal_result_dir": str(metal_dir),
        "status": classification,
        "classification": classification,
        "fault_evidence": fault_record,
        "faults": faults or {},
        "frame": {
            "capture_frame_id": frame_id,
            "present_index": present_index,
            "scene_index": scene_index,
            "boundary": (seal or {}).get("boundary"),
            "width": frame_info.get("width"),
            "height": frame_info.get("height"),
            "events": trace_events,
            "manifest_sha256": (seal or {}).get("manifest_sha256"),
            "reference_sha256": frame_info.get("reference_sha256"),
            "sealed": seal is not None,
        },
        "capture": {
            "validity": capture_validity,
            "identity": capture,
            "seal": {
                "status": "verified" if seal is not None and not seal_error
                else ("invalid" if seal_error else "absent"),
                "error": seal_error,
                "path": str(frame_dir / "frame-seal.json"),
            },
            "graph_status": graph_status,
            "graph_reasons": graph.get("reasons") or [],
            "counts": graph.get("counts") or {},
        },
        "identity": identity,
        "execution": execution,
        "provenance": provenance,
        "evaluation": evaluation,
        "history": replay.get("history") or {},
        "operation_coverage": {
            "status": operation_coverage,
            "events": replay.get("events"),
            "encoded": encoded,
            "failed": failed,
            "partial_mode": bool(replay.get("partial")),
        },
        "state_coverage": states,
        "pixels": {
            "status": pixels,
            "final": final,
            "statistics": final.get("statistics"),
        },
        "checkpoints": checkpoints,
        "checkpoint_validation": validation,
        "binding_audit": audit,
        "first_divergence": divergence,
        "qualification": qualification,
        "graph": graph,
        "policy": policy_evidence,
        "artifacts": {
            "replay_json": str(metal_dir / "replay.json"),
            "provenance": str(metal_dir / "replay-provenance.jsonl"),
            "metal_frame": str(metal_dir / "metal-frame.bgra"),
            "checkpoint_request": str(metal_dir / "checkpoint-request.json"),
            "shadow_report": str(metal_dir / "shadow-report.json"),
            "shaders": str(shaders) if shaders is not None else None,
            "policy": str(Path(policy or DEFAULT_POLICY)),
        },
        "faults": faults or {},
    }
    return report


def _prepare_diagnosis_output(out_dir: Path, inputs: list[Path]) -> Path:
    out_dir = out_dir.expanduser().resolve()
    if _protected_output(out_dir):
        raise ReplayError("output_protected",
                          f"refusing to replace protected output {out_dir}")
    frame_dir = inputs[0].expanduser().resolve() if inputs else None
    metal_dir = inputs[1].expanduser().resolve() if len(inputs) > 1 else None
    if frame_dir is not None and out_dir != metal_dir and (
            out_dir == frame_dir or frame_dir in out_dir.parents):
        raise ReplayError(
            "output_overlap",
            f"diagnostic output {out_dir} is inside the captured frame")
    for source in inputs:
        source = source.expanduser().resolve()
        if out_dir == source:
            continue
        if out_dir in source.parents:
            raise ReplayError(
                "output_overlap",
                f"diagnostic output {out_dir} contains input {source}")
    if out_dir.exists() and not out_dir.is_dir():
        raise ReplayError("output_conflict", f"{out_dir} is not a directory")
    out_dir.mkdir(parents=True, exist_ok=True)
    return out_dir


def _default_case_destination(frame_dir: Path, metal_dir: Path,
                              out_dir: Path) -> Path:
    """Choose a deterministic case destination outside every input.

    The default must never be ``<metal_dir>/case``: the Metal result directory
    is a protected package input.  Prefer a sibling of the result directory so
    repeated diagnostics of the same fixture reuse one owned case directory,
    then fall back to another sibling if the workspace shape is unusual.
    """
    protected = [frame_dir.resolve(), metal_dir.resolve(), out_dir.resolve()]

    def usable(candidate: Path) -> bool:
        candidate = candidate.resolve()
        if _protected_output(candidate):
            return False
        return not any(_path_overlap(candidate, item) for item in protected)

    stem = metal_dir.name or "metal"
    for suffix in range(0, 16):
        name = f"{stem}-case" if suffix == 0 else f"{stem}-case-{suffix + 1}"
        candidate = metal_dir.parent / name
        if usable(candidate):
            return candidate
    raise ReplayError(
        "output_overlap",
        "no package destination outside the captured frame and Metal result "
        "directories is available")


def _compact_recommendation(divergence: dict) -> dict | None:
    value = divergence.get("recommended_checkpoint")
    if isinstance(value, dict):
        return dict(value)
    value = _normalise_checkpoint(
        divergence.get("next_checkpoint"), "next_checkpoint")
    if value is not None:
        return value
    return _normalise_checkpoint(
        divergence.get("suggested_checkpoint"), "suggested_checkpoint")


def _compact_source_evidence(evidence: dict | None) -> dict | None:
    if not isinstance(evidence, dict):
        return None
    checkpoint = evidence.get("checkpoint")
    if not isinstance(checkpoint, dict):
        return {
            "status": evidence.get("status"),
            "reason": evidence.get("reason"),
            "event": evidence.get("event"),
            "target": evidence.get("target"),
            "checkpoint": None,
            "gl_sha256": None,
            "metal_sha256": None,
        }
    statistics = checkpoint.get("statistics") or {}
    return {
        "status": evidence.get("status"),
        "reason": evidence.get("reason"),
        "event": evidence.get("event"),
        "target": evidence.get("target"),
        "checkpoint": {
            "event": checkpoint.get("event"),
            "target": checkpoint.get("target"),
            "resource": checkpoint.get("resource"),
            "status": checkpoint.get("status"),
            "authoritative": checkpoint.get("authoritative"),
            "mismatched_pixels": statistics.get("mismatched_pixels"),
            "max_channel_error": statistics.get("max_channel_error"),
            # ``reconcile_checkpoints()`` names the authoritative captured
            # reference digest ``reference_sha256``.  The compact evidence
            # keeps publishing it as ``gl_sha256`` so agents can independently
            # verify the source-side comparison; the Metal digest already has
            # its own field.  A missing digest stays explicitly null.
            "gl_sha256": (checkpoint.get("reference_sha256")
                          or checkpoint.get("gl_sha256")),
            "metal_sha256": checkpoint.get("metal_sha256"),
        },
    }


def _compact_checkpoints(report: dict) -> tuple[list[dict], dict]:
    """Select a bounded, causally relevant checkpoint subset.

    The selection is deterministic and prioritises the first divergent
    checkpoint, the last matching checkpoint, the divergent resource's
    producer/predecessor events, the recommended next checkpoint, and the
    first/last observations.  It never simply returns the first 32 rows.
    """
    items = [item for item in (report.get("checkpoints") or [])
             if isinstance(item, dict)]
    divergence = report.get("first_divergence") or {}
    recommended = _compact_recommendation(divergence) or {}
    selected: list[dict] = []
    reasons: dict[tuple, list[str]] = {}

    def key(item: dict):
        return (item.get("event"), str(item.get("target") or ""))

    def add(item: dict | None, reason: str) -> None:
        if not isinstance(item, dict):
            return
        item_key = key(item)
        if not any(key(existing) == item_key for existing in selected):
            selected.append(item)
        reasons.setdefault(item_key, []).append(reason)

    div_event = divergence.get("event")
    div_target = str(divergence.get("target") or "")
    if div_event is not None:
        add(next((item for item in items
                  if item.get("event") == div_event
                  and str(item.get("target") or "") == div_target), None),
            "first_divergent")
        div_resource = divergence.get("resource")
        matching = [
            item for item in items
            if item.get("status") == "match"
            and isinstance(item.get("event"), int)
            and item["event"] < int(div_event)
            and _checkpoint_matches_resource(item, div_resource, div_target)
        ]
        if matching:
            add(max(matching, key=lambda item: item["event"]), "last_matching")
        producer_event = divergence.get("producer_event")
        source_evidence = divergence.get("copy_source_evidence")
        if producer_event is not None and isinstance(source_evidence, dict):
            source_checkpoint = source_evidence.get("checkpoint")
            if isinstance(source_checkpoint, dict):
                add(next((item for item in items
                          if item.get("event") == source_checkpoint.get("event")
                          and str(item.get("target") or "")
                          == str(source_checkpoint.get("target") or "")), None),
                    "copy_source")
        for prior in divergence.get("prior_producers") or []:
            event = prior.get("event") if isinstance(prior, dict) else None
            if event is not None:
                add(next((item for item in items
                          if item.get("event") == event), None),
                    "resource_predecessor")
        resource = divergence.get("resource")
        if resource:
            related = [item for item in items
                       if item.get("resource") == resource
                       and isinstance(item.get("event"), int)
                       and (div_event is None
                            or item["event"] <= int(div_event))]
            for item in related[-4:]:
                add(item, "resource_predecessor")
    else:
        start_event = (divergence.get("unresolved_interval") or {}).get(
            "start_event")
        if isinstance(start_event, int):
            add(next((item for item in items
                      if item.get("event") == start_event
                      and _checkpoint_matches_resource(item, "fb:0", "0")),
                     None),
                "last_matching")
    if recommended.get("event") is not None:
        add(next((item for item in items
                  if item.get("event") == recommended["event"]), None),
            "recommended_next")
    if items:
        add(items[0], "first_observation")
        add(items[-1], "last_observation")
    selected.sort(key=lambda item: (
        item.get("event") if isinstance(item.get("event"), int) else -1,
        str(item.get("target") or "")))
    bounded = selected[:32]
    projected = []
    for item in bounded:
        statistics = item.get("statistics") or {}
        projected.append({
            "event": item.get("event"),
            "target": item.get("target"),
            "resource": item.get("resource"),
            "status": item.get("status"),
            "authoritative": item.get("authoritative"),
            "mismatched_pixels": statistics.get("mismatched_pixels"),
            "max_channel_error": statistics.get("max_channel_error"),
            "selected_because": reasons.get(key(item), []),
        })
    return projected, {
        "total": len(items),
        "selected": len(projected),
        "bounded": 32,
        "eligible_for_selection": len(selected),
    }


def _sanitize_compact_paths(value):
    """Recursively replace absolute path tokens with their basenames.

    The compact diagnosis is handed to an agent and must not leak host paths.
    Checkpoint problems and native execution errors can embed absolute paths,
    so the whole projection is sanitized, not just the artifact map.
    """
    if isinstance(value, dict):
        return {key: _sanitize_compact_paths(item)
                for key, item in value.items()}
    if isinstance(value, list):
        return [_sanitize_compact_paths(item) for item in value]
    if isinstance(value, str):
        return _ABSOLUTE_PATH_RE.sub(
            lambda match: Path(match.group(0)).name or match.group(0), value)
    return value


_ABSOLUTE_PATH_RE = re.compile(r"(?<![/\w<:>])/(?:[^\s\",',;:]+)")


def compact_diagnosis(report: dict) -> dict:
    """A bounded, machine-readable diagnosis for an autonomous coding agent.

    The full ``diagnostic-report.json`` keeps the dependency graph and raw
    evidence.  This projection carries only the fields needed to choose the
    next diagnostic action, so an agent does not need to parse a multi-megabyte
    report or infer the causal interval from prose.
    """
    qualification = report.get("qualification") or {}
    divergence = report.get("first_divergence") or {}
    checkpoints, selection = _compact_checkpoints(report)
    recommendation = _compact_recommendation(divergence)
    action = divergence.get("recommended_action")
    next_action = (
        (recommendation or {}).get("reason")
        or (action or {}).get("reason")
        or "no further checkpoint is recommended by this diagnosis")
    compact = {
        "schema": "gfx-agent-diagnosis/v1",
        "classification": report.get("classification"),
        "qualified": qualification.get("qualified"),
        "axes": qualification.get("axes"),
        "frame": {
            key: (report.get("frame") or {}).get(key)
            for key in ("capture_frame_id", "present_index", "scene_index",
                        "events", "sealed")
        },
        "execution": {
            key: (report.get("execution") or {}).get(key)
            for key in ("status", "events", "encoded", "failed",
                        "command_status", "stage", "error")
        },
        "first_divergence": {
            **{
                key: divergence.get(key)
                for key in ("kind", "event", "target", "resource", "operation",
                            "entry", "caller_rva", "program", "producer_event",
                            "producer_program", "producer_entry",
                            "producer_caller_rva", "confidence", "root_cause",
                            "reason", "pixel_location", "differing_bounds",
                            "unresolved_interval", "next_checkpoint",
                            "suggested_checkpoint", "recommended_action",
                            "unsupported_state", "limitations",
                            "cross_capture_excluded")
            },
            "copy_source_evidence": _compact_source_evidence(
                divergence.get("copy_source_evidence")),
            "recommended_checkpoint": recommendation,
        },
        "inputs": (divergence.get("inputs") or [])[:16],
        "checkpoints": checkpoints,
        "checkpoint_selection": selection,
        "final": {
            "status": (report.get("pixels") or {}).get("status"),
            "statistics": ((report.get("pixels") or {}).get("final")
                           or {}).get("statistics"),
        },
        "checkpoint_validation": {
            key: (report.get("checkpoint_validation") or {}).get(key)
            for key in ("status", "requested", "returned", "matched",
                        "diagnostic", "divergent", "invalid", "problems")
        },
        "artifacts": {
            key: (Path(value).name
                  if isinstance(value, str) and Path(value).is_absolute()
                  else value)
            for key, value in (report.get("artifacts") or {}).items()
        },
        "next_action": next_action,
    }
    return _sanitize_compact_paths(compact)


def write_compact_diagnosis(out_dir: Path | str, report: dict) -> Path:
    path = Path(out_dir) / "agent-diagnosis.json"
    path.write_text(json.dumps(compact_diagnosis(report), indent=2) + "\n",
                    encoding="utf-8")
    return path


def diagnose_frame(frame_dir: Path | str, metal_result_dir: Path | str,
                   output_dir: Path | str,
                   policy: Path | str | None = None, *,
                   package: bool = False, case_dir: Path | str | None = None,
                   case_id: str | None = None,
                   require_seal: bool = False,
                   shaders: Path | str | None = None,
                   faults: dict | None = None) -> dict:
    """Produce one structured diagnostic report for a completed Metal result.

    The normal path never executes Metal or starts EU4.  ``package`` optionally
    writes a portable, digest-verified case through ``gfx_replay_case``.
    """
    frame_dir = Path(frame_dir).expanduser().resolve()
    metal_dir = Path(metal_result_dir).expanduser().resolve()
    out_dir = _prepare_diagnosis_output(Path(output_dir), [frame_dir, metal_dir])
    report = analyse_frame(frame_dir, metal_dir, policy,
                           require_seal=require_seal, shaders=shaders,
                           faults=faults)
    report = dict(report)
    report_path = out_dir / "diagnostic-report.json"
    report["artifacts"] = dict(report.get("artifacts") or {})
    report["artifacts"]["diagnostic_report"] = str(report_path)
    report_path.write_text(json.dumps(report, indent=2) + "\n",
                           encoding="utf-8")
    compact_path = write_compact_diagnosis(out_dir, report)
    report["artifacts"]["agent_diagnosis"] = str(compact_path)
    if package:
        from automation import gfx_replay_case

        destination = (Path(case_dir) if case_dir else
                       _default_case_destination(frame_dir, metal_dir, out_dir))
        try:
            manifest = gfx_replay_case.package_diagnosis(
                frame_dir, metal_dir, destination, report,
                shaders=shaders,
                case_id=case_id or f"diagnosis-{report.get('frame', {}).get('capture_frame_id') or 'frame'}")
        except gfx_replay_case.CaseError as exc:
            raise ReplayError(exc.kind, exc.detail) from exc
        report["case"] = manifest
        report_path.write_text(json.dumps(report, indent=2) + "\n",
                               encoding="utf-8")
    return report


def full_frame_completion_problems(replay: dict) -> list[str]:
    """Return why a replay result is not a complete full-frame execution.

    This is the full-frame baseline contract: every captured event encoded,
    no failures, a completed Metal command buffer, a real readback and no
    partial/timeout mode.  Missing metadata is a problem, not an implicit pass.
    """
    problems: list[str] = []
    if replay.get("status") != "ok":
        problems.append(f"status is {replay.get('status')!r}")
    if replay.get("stage") != "complete":
        problems.append(f"stage is {replay.get('stage')!r}")
    if replay.get("readback") is not True:
        problems.append("readback is not true")
    if replay.get("partial") is not False:
        problems.append("partial is not false")
    if replay.get("timed_out") is not False:
        problems.append("timed_out is not false")
    if replay.get("failed") != 0:
        problems.append(f"failed is {replay.get('failed')!r}")
    events = replay.get("events")
    encoded = replay.get("encoded")
    if type(events) is not int or type(encoded) is not int:
        problems.append("encoded/events are not integers")
    elif encoded != events:
        problems.append(f"encoded {encoded} != events {events}")
    if replay.get("command_status") != 4:
        problems.append(f"command_status is {replay.get('command_status')!r}")
    return problems


def classify_replay(*, execution_ok: bool, coverage: str, semantic: str,
                    pixels: str, checkpoints: list[dict],
                    checkpoint_validation: dict, faults: dict | None,
                    state_cov: str = "complete",
                    identity: str = "unverified_session",
                    capture_validity: str = "incomplete",
                    provenance: str = "incomplete",
                    ) -> tuple[str, dict]:
    """Pure qualification classification used by replay() and its tests.

    Invalid or unknown required evidence is always an instrumentation failure.
    It can never fall through to a merely nonqualifying or qualified result.
    Qualification additionally requires an explicit positive identity,
    capture validity and resource provenance; a legacy caller that supplies
    none of them can still receive a completed *diagnostic* result but never a
    qualified equivalence claim.
    """
    intermediate_divergent = any(
        item.get("status") == "divergent" and item.get("authoritative")
        for item in checkpoints)
    checkpoint_invalid = (
        (checkpoint_validation or {}).get("status") not in ("ok",)
        or any(item.get("status") == "invalid" for item in checkpoints))
    fault_injection = bool(faults)
    invalid_verdict = (
        not execution_ok
        or coverage not in ("complete", "partial")
        or pixels not in ("match", "divergent")
        or semantic not in ("match", "divergent")
        or state_cov not in ("complete", "partial")
        or checkpoint_invalid
    )
    positive_identity = (
        identity == "verified" and capture_validity == "valid"
        and provenance == "verified")
    qualified = bool(
        not invalid_verdict
        and coverage == "complete" and state_cov == "complete"
        and semantic == "match" and pixels == "match"
        and not intermediate_divergent
        and not fault_injection
        and positive_identity
    )
    if invalid_verdict:
        status = "invalid"
    elif semantic == "divergent":
        status = "semantic_divergence"
    elif pixels == "divergent" or intermediate_divergent:
        status = ("confirmed_rendering_difference" if coverage == "complete"
                  else "partial_rendering_difference")
    elif coverage == "partial" or state_cov == "partial":
        status = "partial"
    elif not qualified:
        # A deliberate fault experiment, for example, is a valid nonqualifying
        # outcome.  It must never be labelled qualified while qualified=false.
        status = "partial"
    else:
        status = "qualified"
    return status, {
        "execution": "ok" if execution_ok else "failed",
        "coverage": coverage,
        "pixels": pixels,
        "semantic": semantic,
        "semantic_scope": "binding_audit",
        "state_coverage": state_cov,
        "checkpoints": "invalid" if checkpoint_invalid else "ok",
        "identity": identity,
        "capture_validity": capture_validity,
        "provenance": provenance,
        "qualified": qualified,
        "intermediate_divergence": intermediate_divergent,
        "fault_injection": fault_injection,
    }


def qualification_exit_code(report: dict) -> int:
    """Stable CLI policy for replay qualification.

    0  qualified (execution, coverage, semantics, checkpoints and pixels pass)
    2  executed but not qualified (partial coverage/divergence)
    3  deliberate fault injection executed; qualification is expected to fail
    1  invalid execution, invalid checkpoint evidence or unusable report
    """
    outcome = report.get("outcome") or {}
    execution = outcome.get("execution")
    if execution != "ok":
        return 1
    if report.get("status") == "invalid":
        return 1
    if outcome.get("checkpoints") == "invalid":
        return 1
    if outcome.get("coverage") not in ("complete", "partial"):
        return 1
    if outcome.get("pixels") not in ("match", "divergent"):
        return 1
    if outcome.get("state_coverage") not in ("complete", "partial"):
        return 1
    if outcome.get("qualified"):
        return 0
    if outcome.get("fault_injection"):
        return 3
    return 2


# Canonical classification -> public outcome mapping.  Every agent-facing
# surface (diagnose, independent replay, DSH bridge, tools plugin) consumes this
# instead of re-deriving a verdict from status strings.
_OUTCOME_BY_CLASSIFICATION = {
    "qualified_match": ("ok", "complete", "equal", "valid", True),
    "qualified_difference": ("ok", "complete", "different", "valid", False),
    "difference_with_partial_state_coverage": (
        "ok", "partial", "different", "valid", False),
    "unsupported_state": ("ok", "partial", "unverified", "valid", False),
    "invalid_provenance": ("invalid", "invalid", None, "invalid", False),
    "gpu_execution_failure": ("failed", "failed", None, "invalid", False),
    "incomplete_capture": ("incomplete", "incomplete", None, "unverified",
                           False),
    "incomplete_diagnostics": ("incomplete", "incomplete", None,
                               "unverified", False),
    "invalid": ("invalid", "invalid", None, "invalid", False),
    "failed": ("failed", "failed", None, "invalid", False),
    "unavailable": ("incomplete", "unavailable", None, "unverified", False),
}


def classification_outcome(report: dict) -> dict:
    """Project one canonical classification into the agent outcome contract.

    The projection never infers qualification from the legacy replay status.
    A completed comparison is a completed diagnostic even when it reports a
    difference; only execution/evidence failures are tool failures.  Fault
    experiments remain explicit and non-qualifying.
    """
    classification = str(report.get("classification")
                         or report.get("status") or "")
    qualification = report.get("qualification") or {}
    axes = qualification.get("axes") or {}
    if classification == "fault_experiment":
        execution, diagnostic, verdict, evidence, qualified = (
            "ok", "partial", None, "valid", False)
        verdict = ("different" if axes.get("pixel_comparison") == "divergent"
                   else "unverified")
        base = str(axes.get("base_classification") or "")
        if base in ("incomplete_capture", "incomplete_diagnostics"):
            execution, diagnostic, verdict, evidence = (
                "incomplete", "incomplete", None, "unverified")
    elif classification in _OUTCOME_BY_CLASSIFICATION:
        execution, diagnostic, verdict, evidence, qualified = (
            _OUTCOME_BY_CLASSIFICATION[classification])
    else:
        # Unknown/future classifications fail closed: they are not a
        # successful comparison and cannot be promoted to equivalence.
        execution, diagnostic, verdict, evidence, qualified = (
            "incomplete", "incomplete", None, "unverified", False)
    return {
        "classification": classification or None,
        "execution": execution,
        "diagnostic": diagnostic,
        "diagnostic_complete": diagnostic == "complete",
        "verdict": verdict,
        "evidence": evidence,
        "qualified": bool(qualified and qualification.get("qualified")),
        "axes": axes or None,
        "renderer_equivalence": bool(
            qualification.get("renderer_equivalence")),
    }



def _parser():
    import argparse

    parser = argparse.ArgumentParser(
        description="Replay one captured OpenGL frame through the production Metal host")
    parser.add_argument("--frame", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--shaders", type=Path, default=None)
    parser.add_argument("--policy", type=Path, default=None)
    parser.add_argument("--disable-render-target-vflip", action="store_true")
    parser.add_argument("--sampler-shift", type=int, default=None)
    parser.add_argument("--sampler-shift-min", type=int, default=7)
    return parser


def _diagnose_parser():
    import argparse

    parser = argparse.ArgumentParser(
        description="Diagnose an already-produced Metal result without re-executing it")
    parser.add_argument("--frame", required=True, type=Path)
    parser.add_argument("--metal", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--policy", type=Path, default=None)
    parser.add_argument("--shaders", type=Path, default=None)
    parser.add_argument("--require-seal", action="store_true")
    parser.add_argument("--package", action="store_true")
    parser.add_argument("--case", type=Path, default=None)
    parser.add_argument("--case-id", default=None)
    return parser


def diagnose_main(argv: list[str] | None = None) -> int:
    args = _diagnose_parser().parse_args(argv)
    try:
        report = diagnose_frame(
            args.frame, args.metal, args.out, args.policy,
            package=args.package, case_dir=args.case, case_id=args.case_id,
            require_seal=args.require_seal, shaders=args.shaders)
    except ReplayError as exc:
        print(json.dumps({"status": "error", "reason": str(exc)}, indent=2))
        return 1
    qualification = report.get("qualification") or {}
    print(json.dumps({
        "schema": report.get("schema"),
        "status": report.get("status"),
        "classification": report.get("classification"),
        "axes": qualification.get("axes"),
        "first_divergence": {
            "kind": (report.get("first_divergence") or {}).get("kind"),
            "event": (report.get("first_divergence") or {}).get("event"),
            "confidence": (report.get("first_divergence") or {}).get("confidence"),
        },
        "pixels": (report.get("pixels") or {}).get("statistics"),
        "state_coverage": (report.get("state_coverage") or {}).get("status"),
        "checkpoint_validation": {
            "status": (report.get("checkpoint_validation") or {}).get("status"),
            "request_source": (report.get("checkpoint_validation") or {}).get(
                "request_source"),
        },
        "report": str(Path(args.out) / "diagnostic-report.json"),
    }, indent=2))
    return diagnosis_exit_code(report)


def main(argv: list[str] | None = None) -> int:
    values = list(argv) if argv is not None else None
    if values is None:
        import sys
        values = sys.argv[1:]
    if values and values[0] == "diagnose":
        return diagnose_main(values[1:])
    args = _parser().parse_args(values)
    faults: dict = {}
    if args.disable_render_target_vflip:
        faults["disable_render_target_vflip"] = True
    if args.sampler_shift is not None:
        faults["sampler_shift"] = args.sampler_shift
        faults["sampler_shift_min"] = args.sampler_shift_min
    try:
        report = replay(args.frame, args.out, shaders=args.shaders,
                        faults=faults, policy=args.policy)
    except ReplayError as exc:
        print(json.dumps({"status": "error", "reason": str(exc)}, indent=2))
        return 1
    print(json.dumps({
        "status": report.get("status"),
        "outcome": report.get("outcome"),
        "qualified": (report.get("outcome") or {}).get("qualified"),
        "graph": (report.get("graph") or {}).get("status"),
        "evaluation": (report.get("evaluation") or {}).get("status"),
        "final": (report.get("final") or {}).get("status"),
        "mismatched_pixels": ((report.get("final") or {}).get("statistics") or {}).get(
            "mismatched_pixels"),
        "checkpoints": [
            {"event": item["event"], "target": item["target"],
             "status": item["status"],
             "mismatched_pixels": (item.get("statistics") or {}).get(
                 "mismatched_pixels")}
            for item in report.get("checkpoints") or []
        ],
        "checkpoint_validation": report.get("checkpoint_validation"),
        "state_coverage": report.get("state_coverage"),
        "binding_audit": {
            "ok": (report.get("binding_audit") or {}).get("ok"),
            "mismatched": (report.get("binding_audit") or {}).get("mismatched_bindings"),
            "earliest_event": (report.get("binding_audit") or {}).get("earliest_event"),
        },
        "first_divergence": report.get("first_divergence"),
        "report": str(Path(args.out) / "replay-report.json"),
    }, indent=2))
    return qualification_exit_code(report)


PRODUCER_RECEIPT_SCHEMA = "gfx-replay-producer-receipt/v1"
def _sha256_file(path: Path) -> str | None:
    if not path.is_file():
        return None
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()




_CONSUMED_REPLAY_ENV = (
    "EU4_FRAME_REPLAY_COPY_FAULT",
    "EU4_FRAME_REPLAY_DEBUG_ATTR_VERTEX",
    "EU4_FRAME_REPLAY_DEBUG_DESCRIPTOR",
    "EU4_FRAME_REPLAY_DEBUG_FRAGMENT",
    "EU4_FRAME_REPLAY_DEBUG_VERTEX",
    "EU4_FRAME_REPLAY_DISABLE_RT_FLIP",
    "EU4_FRAME_REPLAY_FAULT_EVENT",
    "EU4_FRAME_REPLAY_FAULT_FRAME",
    "EU4_FRAME_REPLAY_FLIP_WINDING",
    "EU4_FRAME_REPLAY_LINEAR_ALL",
    "EU4_FRAME_REPLAY_LINEAR_MIN",
    "EU4_FRAME_REPLAY_NEAREST_ALL",
    "EU4_FRAME_REPLAY_NO_CULL",
    "EU4_FRAME_REPLAY_PRECISE_MATH",
    "EU4_FRAME_REPLAY_SAMPLER_SHIFT",
    "EU4_FRAME_REPLAY_SAMPLER_SHIFT_MIN",
)

# These switches are always removed before a replay so a stray ambient
# experiment cannot be inherited by an ordinary run.  The receipt records the
# scrub so the difference between the caller's environment and the child's is
# explicit rather than implied.
_SCRUBBED_REPLAY_ENV = (
    "EU4_FRAME_REPLAY_DISABLE_RT_FLIP",
    "EU4_FRAME_REPLAY_SAMPLER_SHIFT",
)

_BOOLEAN_REPLAY_ENV = (
    "EU4_FRAME_REPLAY_DEBUG_ATTR_VERTEX",
    "EU4_FRAME_REPLAY_DEBUG_VERTEX",
    "EU4_FRAME_REPLAY_DISABLE_RT_FLIP",
    "EU4_FRAME_REPLAY_FLIP_WINDING",
    "EU4_FRAME_REPLAY_LINEAR_ALL",
    "EU4_FRAME_REPLAY_LINEAR_MIN",
    "EU4_FRAME_REPLAY_NEAREST_ALL",
    "EU4_FRAME_REPLAY_NO_CULL",
)

RECIPE_SCHEMA = "gfx-replay-recipe/v2"


def _replay_int(value, default: int = 0) -> int:
    try:
        return int(str(value).strip(), 10)
    except (TypeError, ValueError):
        return default


def _replay_enabled(value) -> bool:
    return isinstance(value, str) and value[:1] == "1"


def _fault_environment(faults: dict | None) -> dict:
    """The environment variables an explicit replay fault injects."""
    faults = dict(faults or {})
    injected: dict[str, str] = {}
    if faults.get("disable_render_target_vflip"):
        injected["EU4_FRAME_REPLAY_DISABLE_RT_FLIP"] = "1"
    if faults.get("sampler_shift") is not None:
        injected["EU4_FRAME_REPLAY_SAMPLER_SHIFT"] = str(
            faults["sampler_shift"])
        if faults.get("sampler_shift_min") is not None:
            injected["EU4_FRAME_REPLAY_SAMPLER_SHIFT_MIN"] = str(
                faults["sampler_shift_min"])
    return injected


def resolve_replay_environment(*, ambient: dict | None = None,
                               faults: dict | None = None) -> dict:
    """The exact environment passed to the replay child.

    The caller's ambient environment is inherited, a fixed set of fault
    switches is scrubbed, and requested faults are injected for this replay
    only.  The resolved mapping is what both the child and the receipt see.
    """
    base = dict(os.environ if ambient is None else ambient)
    env = dict(base)
    for key in _SCRUBBED_REPLAY_ENV:
        env.pop(key, None)
    env.update(_fault_environment(faults))
    return env


def _effective_replay_env(consumed: dict) -> dict:
    """The consumed variables whose values actually change native execution.

    An explicit default (for example ``EU4_FRAME_REPLAY_PRECISE_MATH=0``)
    resolves to the same behaviour as an absent variable.  Only effective
    values belong in the recipe identity; the raw values stay inspectable.
    """
    effective: dict[str, str] = {}
    for key, value in consumed.items():
        if key == "EU4_FRAME_REPLAY_PRECISE_MATH":
            active = value == "1"
        elif key in _BOOLEAN_REPLAY_ENV:
            active = _replay_enabled(value)
        elif key == "EU4_FRAME_REPLAY_DEBUG_FRAGMENT":
            active = _replay_enabled(value) or value in ("texture", "uv")
        elif key == "EU4_FRAME_REPLAY_DEBUG_DESCRIPTOR":
            # Presence alone selects the descriptor diagnostics.
            active = True
        elif key == "EU4_FRAME_REPLAY_COPY_FAULT":
            active = value == "command"
        else:
            # Sampler-shift selectors are resolved contextually below.
            active = False
        if active:
            effective[key] = value
    shift = _replay_int(consumed.get("EU4_FRAME_REPLAY_SAMPLER_SHIFT"), 0)
    if shift:
        effective["EU4_FRAME_REPLAY_SAMPLER_SHIFT"] = consumed[
            "EU4_FRAME_REPLAY_SAMPLER_SHIFT"]
        shift_min = _replay_int(
            consumed.get("EU4_FRAME_REPLAY_SAMPLER_SHIFT_MIN"), 7)
        if (shift_min != 7
                and "EU4_FRAME_REPLAY_SAMPLER_SHIFT_MIN" in consumed):
            effective["EU4_FRAME_REPLAY_SAMPLER_SHIFT_MIN"] = consumed[
                "EU4_FRAME_REPLAY_SAMPLER_SHIFT_MIN"]
        if ("EU4_FRAME_REPLAY_FAULT_FRAME" in consumed
                and _replay_int(
                    consumed.get("EU4_FRAME_REPLAY_FAULT_FRAME"), 0) > 0):
            effective["EU4_FRAME_REPLAY_FAULT_FRAME"] = consumed[
                "EU4_FRAME_REPLAY_FAULT_FRAME"]
        if ("EU4_FRAME_REPLAY_FAULT_EVENT" in consumed
                and _replay_int(
                    consumed.get("EU4_FRAME_REPLAY_FAULT_EVENT"), -1) >= 0):
            effective["EU4_FRAME_REPLAY_FAULT_EVENT"] = consumed[
                "EU4_FRAME_REPLAY_FAULT_EVENT"]
    return effective


def effective_replay_environment(consumed: dict | None) -> dict:
    """Canonical effective projection of a receipt's consumed environment."""
    return _effective_replay_env(dict(consumed or {}))


def recipe_digest(recipe: dict) -> str:
    """Digest of the execution-relevant fields of a recipe.

    Approval and acceptance compare this digest, never a receipt's own
    ``sha256`` field, so editing the recorded inputs invalidates an approved
    recipe.
    """
    payload = {
        "schema": RECIPE_SCHEMA,
        "effective_env": dict(sorted((recipe.get("effective_env") or {}).items())),
        "faults": recipe.get("faults") or {},
        "shader_identity": recipe.get("shader_identity") or {},
        "policy_sha256": recipe.get("policy_sha256"),
    }
    return hashlib.sha256(json.dumps(
        payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def replay_recipe(*, env: dict | None = None,
                  ambient: dict | None = None,
                  shaders: Path | str | None = None,
                  policy: Path | str | None = None,
                  faults: dict | None = None) -> dict:
    """The effective, consumed inputs of one replay invocation.

    A byte hash of the output proves which bytes were saved; it cannot explain
    which ambient override produced them.  ``env`` is the exact mapping passed
    to the replay child (after scrubbing and fault injection), not the
    caller's ambient environment.  The recipe is deliberately limited to
    consumed options: unrelated variables, secrets and host paths are not
    public evidence.
    """
    inherited_ambient = ambient is None and env is None
    resolved = dict(os.environ if env is None else env)
    previous = dict(os.environ) if inherited_ambient else (
        None if ambient is None else dict(ambient))
    consumed = {
        key: resolved[key] for key in _CONSUMED_REPLAY_ENV
        if key in resolved
    }
    effective = _effective_replay_env(consumed)
    faults = dict(faults or {})
    fault_env = _fault_environment(faults)
    injected = sorted(
        key for key in effective
        if key in fault_env and resolved.get(key) == fault_env[key])
    inherited = sorted(
        key for key in effective
        if previous is not None
        and key not in injected
        and previous.get(key) == resolved.get(key))
    # resolve_replay_environment always removes these from the ambient
    # environment before it applies any explicit fault.  Record what was
    # scrubbed even when a fault re-injects the same name with a new value.
    scrubbed = sorted(
        key for key in _SCRUBBED_REPLAY_ENV
        if previous is not None and key in previous)
    shader_identity = {"shaders_json_sha256": "", "artifacts_sha256": "",
                       "artifact_count": 0}
    if shaders is not None:
        try:
            shader_identity = m6_frame.shader_receipt_identity(
                Path(shaders), required=False)
        except (OSError, m6_frame.FrameError):
            shader_identity = {"shaders_json_sha256": "", "artifacts_sha256": "",
                               "artifact_count": 0}
    policy_sha = None
    if policy is not None:
        try:
            policy_sha = _sha256_file(Path(policy))
        except OSError:
            policy_sha = None
    recipe = {
        "schema": RECIPE_SCHEMA,
        "diagnostic": bool(faults) or bool(effective),
        "faults": faults,
        "consumed_env": dict(sorted(consumed.items())),
        "effective_env": dict(sorted(effective.items())),
        "inherited_env": inherited,
        "injected_env": injected,
        "scrubbed_env": scrubbed,
        "shader_identity": shader_identity,
        "policy_sha256": policy_sha,
        "precise_math": consumed.get("EU4_FRAME_REPLAY_PRECISE_MATH"),
        "environment_sha256": hashlib.sha256(json.dumps({
            "effective_env": dict(sorted(effective.items())),
            "faults": faults,
        }, sort_keys=True, separators=(",", ":")).encode()).hexdigest(),
    }
    recipe["sha256"] = recipe_digest(recipe)
    return recipe


def write_producer_receipt(out_dir: Path, frame_dir: Path,
                           producer: dict, result_path: Path, *,
                           shaders: Path | str | None = None,
                           policy: Path | str | None = None,
                           faults: dict | None = None,
                           env: dict | None = None,
                           ambient: dict | None = None) -> dict:
    """Bind a replay result to the exact executable and build that produced it."""
    seal = _optional_json(frame_dir / "frame-seal.json") or {}
    trace = _optional_json(frame_dir / "frame-trace.json") or {}
    capture = capture_identity(seal, trace) or {}
    replay = _optional_json(result_path) or {}
    metal = out_dir / "metal-frame.bgra"
    checkpoint_files = []
    seen_checkpoints: set[str] = set()
    declared_checkpoints = replay.get("checkpoints") or []
    if not isinstance(declared_checkpoints, list):
        raise ReplayError(
            "invalid_checkpoint_binding",
            "replay checkpoints are not a list")
    for item in declared_checkpoints:
        if not isinstance(item, dict):
            raise ReplayError(
                "invalid_checkpoint_binding",
                "a replay checkpoint entry is not an object")
        declared = str(item.get("file") or "")
        name = _safe_member_name(declared)
        if not name:
            # A result that declares a checkpoint must declare a safe basename.
            # Silently omitting it would leave the acceptance path to discover
            # an unbound intermediate after the producer has already signed.
            raise ReplayError(
                "invalid_checkpoint_binding",
                f"checkpoint declares an invalid file name {declared!r}")
        if name in seen_checkpoints:
            raise ReplayError(
                "invalid_checkpoint_binding",
                f"checkpoint file {name!r} is declared more than once")
        seen_checkpoints.add(name)
        path = out_dir / name
        if not path.is_file():
            raise ReplayError(
                "invalid_checkpoint_binding",
                f"declared checkpoint file {name!r} is missing from {out_dir}")
        checkpoint_files.append({
            "file": name,
            "sha256": _sha256_file(path),
        })
    checkpoint_files.sort(key=lambda row: row["file"])
    value = {
        "schema": PRODUCER_RECEIPT_SCHEMA,
        "producer": {
            "executable": producer.get("executable"),
            "executable_sha256": producer.get("executable_sha256"),
            "build_fingerprint": producer.get("build_fingerprint"),
            "build_stamp_sha256": producer.get("build_stamp_sha256"),
            "source_revision": producer.get("source_revision"),
        },
        "capture": capture,
        "recipe": replay_recipe(
            env=env, ambient=ambient, shaders=shaders, policy=policy,
            faults=faults),
        "result": {
            "replay_json_sha256": _sha256_file(result_path),
            "metal_frame_sha256": (_sha256_file(metal)
                                   if metal.is_file() else None),
            "checkpoint_files": checkpoint_files,
            "status": replay.get("status"),
            "stage": replay.get("stage"),
            "readback": replay.get("readback"),
            "events": replay.get("events"),
            "encoded": replay.get("encoded"),
            "failed": replay.get("failed"),
            "command_status": replay.get("command_status"),
        },
    }
    path = out_dir / "producer-receipt.json"
    path.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")
    return value


def replay(
    frame_dir: Path | str,
    out_dir: Path | str,
    *,
    shaders: Path | str | None = None,
    faults: dict | None = None,
    policy: Path | str | None = None,
    timeout: float = 1800.0,
) -> dict:
    """Execute one replay under the externally configured campaign budget.

    The budget check happens before the host is built or any output is
    prepared, so an exhausted campaign cannot spend another GPU execution.
    """
    from automation import campaign_guard

    evidence = {
        "entry_point": "automation.gfx_independent_replay.replay",
        "frame_dir": str(frame_dir),
        "out_dir": str(out_dir),
        "command": [sys.executable, "-m",
                    "automation.gfx_independent_replay",
                    "replay", "--frame", str(frame_dir),
                    "--out", str(out_dir)],
    }
    try:
        # Reserve before the host is built or any output is prepared.  The
        # attempt boundary is shared with nested case/sequence entry points so
        # exactly one reservation covers the physical replay.
        with campaign_guard.attempt(
                "replays", evidence=evidence) as attempt:
            report = _replay_locked(
                frame_dir, out_dir, shaders=shaders, faults=faults,
                policy=policy, timeout=timeout)
            failed = (report.get("status") == "invalid"
                      or (report.get("execution") or {}).get("status")
                      == "failed")
            if failed and attempt is not None:
                detail = " ".join(str(item) for item in (
                    (report.get("execution") or {}).get("error"),
                    (report.get("execution") or {}).get("failure"),
                    report.get("status")) if item)
                attempt.fail(detail, evidence={
                    "status": report.get("status"),
                    "exit_code": report.get("returncode"),
                    "stderr_tail": str(report.get("stderr") or "")[-2000:],
                    "command": report.get("command") or evidence["command"],
                })
            return report
    except campaign_guard.CampaignRefusal as exc:
        raise ReplayError("campaign_refused",
                          f"{exc.kind}: {exc.detail}") from exc


def _replay_locked(
    frame_dir: Path | str,
    out_dir: Path | str,
    *,
    shaders: Path | str | None = None,
    faults: dict | None = None,
    policy: Path | str | None = None,
    timeout: float = 1800.0,
) -> dict:
    """Execute one captured workload through the production Metal replay host."""
    frame_dir = Path(frame_dir).expanduser().resolve()
    out_dir = Path(out_dir).expanduser().resolve()
    shader_dir = _shader_dir(frame_dir, shaders)
    out_dir = prepare_replay_output(out_dir, [frame_dir, shader_dir])
    graph = gfx_frame_graph.build(frame_dir)
    request = gfx_frame_graph.write_checkpoint_request(
        graph, out_dir / "checkpoint-request.json")
    host = m6_frame.build_replay_host()
    try:
        snapshot, producer = m6_frame.snapshot_replay_host(host)
    except m6_frame.FrameError as exc:
        raise ReplayError("producer_identity", str(exc)) from exc
    command = [
        str(snapshot), "--frame", str(frame_dir), "--shaders", str(shader_dir),
        "--out", str(out_dir), "--checkpoints",
        str(out_dir / "checkpoint-request.json"),
    ]
    ambient = dict(os.environ)
    faults = dict(faults or {})
    # Resolve once and pass the same mapping to the child and to the receipt.
    # The receipt must describe the environment that actually ran, not the
    # caller's pre-scrub environment or a sample of known switches.
    env = resolve_replay_environment(ambient=ambient, faults=faults)
    try:
        ran = subprocess.run(
            command, capture_output=True, text=True, check=False, env=env,
            timeout=timeout,
        )
    except BaseException:
        try:
            snapshot.unlink()
        except FileNotFoundError:
            pass
        raise
    result_path = out_dir / "replay.json"
    receipt_error: str | None = None
    try:
        if result_path.is_file():
            try:
                write_producer_receipt(
                    out_dir, frame_dir, producer, result_path,
                    shaders=shader_dir, policy=policy, faults=faults,
                    env=env, ambient=ambient)
            except ReplayError as exc:
                # A malformed/failed checkpoint declaration is a diagnostic
                # result, not a reason to lose the report or leak the host
                # copy.
                receipt_error = f"{exc.kind}: {exc.detail or exc}"
    finally:
        try:
            snapshot.unlink()
        except FileNotFoundError:
            pass
    if receipt_error is not None:
        diagnosis = analyse_frame(
            frame_dir, out_dir, policy, graph=graph, request=request,
            request_source="published", faults=faults)
        report = {
            "schema": SCHEMA,
            "frame_dir": str(frame_dir),
            "out_dir": str(out_dir),
            "status": "invalid",
            "receipt_error": receipt_error,
            "faults": faults,
            "command": command,
            "returncode": ran.returncode,
            "stderr": (ran.stderr or "")[-4000:],
            "graph": graph,
            "diagnosis": diagnosis,
            "classification": diagnosis.get("classification"),
            "qualification": diagnosis.get("qualification"),
            "first_divergence": diagnosis.get("first_divergence"),
            "execution": diagnosis.get("execution"),
        }
        (out_dir / "replay-report.json").write_text(
            json.dumps(report, indent=2) + "\n", encoding="utf-8")
        return report
    if ran.returncode != 0 or not result_path.is_file():
        diagnosis = analyse_frame(
            frame_dir, out_dir, policy, graph=graph, request=request,
            request_source="published", faults=faults)
        report = {
            "schema": SCHEMA,
            "frame_dir": str(frame_dir),
            "out_dir": str(out_dir),
            "status": "invalid",
            "faults": faults,
            "command": command,
            "returncode": ran.returncode,
            "stderr": (ran.stderr or "")[-4000:],
            "graph": graph,
            "diagnosis": diagnosis,
            "classification": diagnosis.get("classification"),
            "qualification": diagnosis.get("qualification"),
            "first_divergence": diagnosis.get("first_divergence"),
            "execution": diagnosis.get("execution"),
        }
        (out_dir / "replay-report.json").write_text(
            json.dumps(report, indent=2) + "\n", encoding="utf-8")
        return report
    diagnosis = analyse_frame(
        frame_dir, out_dir, policy, graph=graph, request=request,
        request_source="published", faults=faults)
    replay_result = _optional_json(result_path) or {}
    evaluation = diagnosis.get("evaluation") or {}
    checkpoints = diagnosis.get("checkpoints") or []
    checkpoint_validation = diagnosis.get("checkpoint_validation") or {}
    audit = diagnosis.get("binding_audit") or {}
    states = diagnosis.get("state_coverage") or {}
    final = (diagnosis.get("pixels") or {}).get("final") or {}
    execution_ok = (diagnosis.get("execution") or {}).get("status") == "ok"
    coverage = str(evaluation.get("status") or "invalid")
    if execution_ok and coverage == "complete" and evaluation.get("seeded_intermediates"):
        coverage = "partial"
    semantic = "match" if audit.get("ok") else "divergent"
    pixels = final.get("status", "invalid")
    status, outcome = classify_replay(
        execution_ok=execution_ok, coverage=coverage, semantic=semantic,
        pixels=pixels, checkpoints=checkpoints,
        checkpoint_validation=checkpoint_validation, faults=faults,
        state_cov=str(states.get("status") or "invalid"),
        identity=str((diagnosis.get("identity") or {}).get("status")
                     or "unverified_session"),
        capture_validity=str(
            (diagnosis.get("capture") or {}).get("validity") or "incomplete"),
        provenance=str((diagnosis.get("provenance") or {}).get("status")
                       or "incomplete"))
    report = {
        "schema": SCHEMA,
        "frame_dir": str(frame_dir),
        "out_dir": str(out_dir),
        "status": status,
        "outcome": outcome,
        "command": command,
        "returncode": ran.returncode,
        "stderr": (ran.stderr or "")[-4000:],
        "classification": diagnosis.get("classification"),
        "qualification": diagnosis.get("qualification"),
        "faults": faults,
        "graph": diagnosis.get("graph") or graph,
        "evaluation": evaluation,
        "checkpoints": checkpoints,
        "checkpoint_validation": checkpoint_validation,
        "binding_audit": audit,
        "state_coverage": states,
        "final": final,
        "first_divergence": diagnosis.get("first_divergence"),
        "diagnosis": diagnosis,
        "replay": {
            key: replay_result.get(key) for key in (
                "status", "events", "encoded", "failed", "command_status",
                "readback", "provenance", "failures",
            )
        },
        "artifacts": {
            "replay_json": str(result_path),
            "provenance": str(out_dir / "replay-provenance.jsonl"),
            "final_readback": str(out_dir / "metal-frame.bgra"),
            "checkpoint_request": str(out_dir / "checkpoint-request.json"),
            "diagnostic_report": str(out_dir / "replay-report.json"),
            "producer_receipt": str(out_dir / "producer-receipt.json"),
        },
    }
    (out_dir / "replay-report.json").write_text(
        json.dumps(report, indent=2) + "\n", encoding="utf-8")
    return report


if __name__ == "__main__":
    raise SystemExit(main())
