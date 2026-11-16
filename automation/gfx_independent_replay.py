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
import os
import shutil
import subprocess
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


def _normalize_format(value) -> str:
    if value is None:
        return ""
    if isinstance(value, int):
        return {
            0x8058: "RGBA8", 0x8051: "RGB8", 0x8814: "RGBA32F",
            0x8815: "RGB32F", 0x881A: "RGBA16F", 0x881B: "RGB16F",
            0x805B: "RGBA16", 0x8C43: "SRGB8_ALPHA8",
        }.get(value, str(value))
    return str(value).upper()


def _checkpoint_key(item: dict) -> tuple[int, str]:
    return (_as_int(item.get("event"), -1), str(item.get("target")))


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
        "producer_events": list(item.get("producer_events") or []),
        "metal_file": str(item.get("_metal_path") or ""),
        "reference_file": str(item.get("_reference_path") or ""),
        "authoritative": True,
        "status": "invalid",
        "reason": reason,
        "requested": requested,
    }


def reconcile_checkpoints(graph: dict, request: dict, replay: dict,
                          frame_dir: Path, out_dir: Path
                          ) -> tuple[list[dict], dict]:
    """Reconcile every requested checkpoint with exactly one replay result.

    Missing, duplicate, unexpected, unreadable or identity-mismatched evidence
    is an instrumentation/qualification failure.  A later matching final frame
    cannot hide it.
    """
    plan = list(request.get("checkpoints") or [])
    actual = list(replay.get("checkpoints") or [])
    problems: list[dict] = []

    requested: dict[tuple[int, str], dict] = {}
    for item in plan:
        key = _checkpoint_key(item)
        if key in requested:
            problems.append({"kind": "duplicate_request", "event": key[0],
                             "target": key[1]})
            continue
        target = key[1]
        expected_resource = "fb:0" if target == "0" else f"tex:{target}"
        if str(item.get("resource") or expected_resource) != expected_resource:
            problems.append({
                "kind": "substituted_request", "event": key[0],
                "target": key[1],
                "expected_resource": expected_resource,
                "actual_resource": item.get("resource")})
            continue
        requested[key] = item

    returned: dict[tuple[int, str], list[dict]] = {}
    for item in actual:
        key = _checkpoint_key(item)
        returned.setdefault(key, []).append(item)

    for key in returned:
        if key not in requested:
            problems.append({"kind": "unexpected_result", "event": key[0],
                             "target": key[1]})

    results: list[dict] = []
    for key, wanted in requested.items():
        event, target = key
        rows = returned.get(key, [])
        if len(rows) == 0:
            problems.append({"kind": "missing_result", "event": event,
                             "target": target})
            results.append(_invalid_checkpoint(
                wanted, graph, "requested checkpoint missing from replay response",
                requested=True))
            continue
        if len(rows) > 1:
            problems.append({"kind": "duplicate_result", "event": event,
                             "target": target, "count": len(rows)})
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
        if captured_format:
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
        statistics = m6_compare.compare_images(
            reference, candidate, width, height)
        entry = {
            "event": event,
            "target": target,
            "resource": resource_id,
            "width": width,
            "height": height,
            "format": internal or None,
            "producer_events": list(wanted.get("producer_events") or []),
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
                )
            },
            "worst_regions": statistics["regions"][:4],
            "metal_sha256": _sha(metal_path),
            "reference_sha256": reference_sha,
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


_FINAL_STATISTICS = (
    "pixels", "mismatched_pixels", "mismatched_fraction",
    "alpha_different_pixels", "max_channel_error", "mean_abs_error",
    "mean_max_channel_error",
)


def final_comparison_report(frame_dir: Path, out_dir: Path,
                            policy_path: Path | str | None = None) -> dict:
    """Full-frame differential through the shared comparison engine.

    The complete ``m6_compare`` report (statistics, worst region and draw
    attribution) is retained under ``detail`` so the diagnostic does not
    duplicate the comparison engine or discard region evidence.
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
    value = {
        "status": "match" if report.get("status") == "pass" else "divergent",
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
    policy = json.loads(Path(policy_path or DEFAULT_POLICY).read_text())
    thresholds = policy.get("thresholds") or {}
    max_channel = int(thresholds.get("max_channel_error", 0))
    max_fraction = float(thresholds.get("mismatched_fraction", 0.0))
    max_mean = float(thresholds.get("mean_abs_error", 0.0))
    mean_total = sum(float(value) for value in statistics["mean_abs_error"])
    passed = (
        statistics["max_channel_error"] <= max_channel
        and statistics["mismatched_fraction"] <= max_fraction
        and mean_total <= max_mean
    )
    return {
        "status": "match" if passed else "divergent",
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
        "reason": (
            "checkpoint the last captured event's target; when it is not the "
            "final framebuffer, a checkpoint at or after the last default-"
            "framebuffer write is required to separate the final pass"),
        "final_statistics": final.get("statistics"),
    }


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
        matching = [
            int(item["event"]) for item in checkpoints
            if item.get("status") == "match"
            and item.get("event") is not None
            and int(item["event"]) < int(event or 0)
        ]
        previous = matching[-1] if matching else None
        event_kind = str(context.get("kind") or "")
        event_is_transfer = event_kind in ("resolve", "copy")
        limitations: list[str] = []
        next_checkpoint = None
        if producer and str(producer.get("kind")) in ("resolve", "copy"):
            source = str(producer.get("source") or "")
            source_fbo = producer.get("source_framebuffer")
            source_resource = ("fb:0" if source in ("", "0")
                               or source_fbo == 0 else f"tex:{source}")
            if not resource.get("captured_versions"):
                limitations.append(
                    "the copy source has no independent captured checkpoint; "
                    "the difference may originate before the copy")
            next_checkpoint = {
                "event": producer.get("event"),
                "target": ("0" if source_resource == "fb:0"
                           else source_resource.split(":", 1)[-1]),
                "resource": source_resource,
                "reason": (
                    "checkpoint the copy source immediately before the "
                    "transfer to separate source contents from copy execution"),
            }
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
                "start_event": previous,
                "end_event": event,
                "note": (
                    "the first confirmed divergent checkpoint; earlier "
                    "operations in this interval were not checkpointed"),
            },
            "next_checkpoint": next_checkpoint,
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
        return {
            "schema": "gfx-first-divergence/v1",
            "frame_id": frame_id,
            "kind": "final_only",
            "event": None,
            "last_event": last_event if last_event >= 0 else event_count,
            "statistics": final.get("statistics"),
            "semantic": first_semantic,
            "intermediate": None,
            "suggested_checkpoint": _suggested_checkpoint(graph, final),
            "unresolved_interval": {
                "start_event": max(
                    [int(item["event"]) for item in checkpoints
                     if item.get("status") == "match"
                     and item.get("event") is not None] or [0]),
                "end_event": last_event if last_event >= 0 else event_count,
                "note": (
                    "the final image differs but no authoritative intermediate "
                    "checkpoint did; the responsible pass is unresolved"),
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
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def _load_policy(policy_path: Path | str | None) -> dict:
    path = Path(policy_path or DEFAULT_POLICY)
    value = _optional_json(path)
    return value if value is not None else {}


def _capture_error_class(message: str) -> str:
    """Separate incomplete capture from evidence that actively disagrees."""
    lowered = str(message).lower()
    integrity = ("digest", "sha256", "size does not match", "does not match",
                 "blob", "belongs to another")
    return "invalid" if any(token in lowered for token in integrity) else "incomplete"


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
        published_producers = item.get("producer_events") or []
        if (not isinstance(published_producers, list)
                or any(not isinstance(value, int)
                       for value in published_producers)
                or published_producers != (wanted.get("producer_events") or [])):
            problems.append(_checkpoint_problem(
                "substituted_request",
                f"checkpoint ({key[0]}, {key[1]}) producer history disagrees "
                "with the validated capture graph",
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
    request_sha = _sha(path)
    count = len(request.get("checkpoints") or [])
    problems: list[dict] = []
    bindings: list[str] = []

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


def _checkpoint_request_state(graph: dict, metal_dir: Path,
                              shadow: dict | None = None,
                              seal: dict | None = None
                              ) -> tuple[dict, str, list[dict], dict]:
    """Load and validate the checkpoint request bound to one Metal result.

    An unbound or disagreeing request is reported with an explicit severity;
    it is never silently replaced by a derived plan, and a later valid pixel
    match cannot repair it.
    """
    metal_dir = Path(metal_dir)
    published = _optional_json(metal_dir / "checkpoint-request.json")
    if published is None:
        return gfx_frame_graph.checkpoint_request(graph), "derived", [], {
            "status": "derived",
            "bindings": [],
            "sha256": None,
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
                       checkpoint_validation: dict) -> tuple[str, dict]:
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
    elif identity == "mismatch":
        classification = "invalid_provenance"
    elif identity in ("missing", "incomplete", "unverified_session"):
        classification = "incomplete_diagnostics"
    elif capture_validity == "invalid" or provenance == "invalid":
        classification = "invalid_provenance"
    elif checkpoint_status == "invalid":
        classification = "invalid_provenance"
    elif execution == "missing" or pixels == "unavailable":
        classification = "incomplete_diagnostics"
    elif checkpoint_status == "incomplete" or divergence == "invalid_checkpoint":
        classification = "incomplete_diagnostics"
    elif capture_validity == "incomplete":
        classification = "incomplete_capture"
    elif operation_coverage in ("partial", "invalid"):
        classification = "incomplete_capture"
    elif provenance == "incomplete":
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
    qualified_match = classification == "qualified_match"
    return classification, {
        "schema": "m7-qualification-contract/v1",
        "axes": axes,
        "classification": classification,
        "qualified": qualified_match,
        "diagnostic_qualified": classification in (
            "qualified_match", "qualified_difference"),
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
    policy_value = _load_policy(policy)
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
            _checkpoint_request_state(graph, metal_dir, shadow, seal))
    else:
        request_source = request_source or "provided"
        request_problems = []
    trace_events = len(trace.get("_events") or [])
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
            graph, request, replay, frame_dir, metal_dir)
        validation = dict(validation)
        validation["request_source"] = request_source
        validation["request_binding"] = request_binding
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
    states = state_coverage(frame_dir, trace=trace, evidence=state_evidence)
    final = final_comparison_report(frame_dir, metal_dir, policy)
    frame_info = trace.get("frame") or {}
    frame_id = (seal or {}).get("capture_frame_id")
    divergence = first_divergence(
        graph, checkpoints, final, binding_audit=audit,
        fault=faults or None, checkpoint_validation=validation,
        frame_id=frame_id, state_coverage_report=states,
        event_count=trace_events)
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
    )
    report = {
        "schema": DIAGNOSIS_SCHEMA,
        "frame_dir": str(frame_dir),
        "metal_result_dir": str(metal_dir),
        "status": classification,
        "classification": classification,
        "frame": {
            "capture_frame_id": frame_id,
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


def classify_replay(*, execution_ok: bool, coverage: str, semantic: str,
                    pixels: str, checkpoints: list[dict],
                    checkpoint_validation: dict, faults: dict | None,
                    state_cov: str = "complete"
                    ) -> tuple[str, dict]:
    """Pure qualification classification used by replay() and its tests.

    Invalid or unknown required evidence is always an instrumentation failure.
    It can never fall through to a merely nonqualifying or qualified result.
    """
    intermediate_divergent = any(
        item.get("status") == "divergent" and item.get("authoritative")
        for item in checkpoints)
    checkpoint_invalid = (
        (checkpoint_validation or {}).get("status") != "ok"
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
    qualified = bool(
        not invalid_verdict
        and coverage == "complete" and state_cov == "complete"
        and semantic == "match" and pixels == "match"
        and not intermediate_divergent
        and not fault_injection
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


def replay(
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
    command = [
        str(host), "--frame", str(frame_dir), "--shaders", str(shader_dir),
        "--out", str(out_dir), "--checkpoints",
        str(out_dir / "checkpoint-request.json"),
    ]
    env = dict(os.environ)
    env.pop("EU4_FRAME_REPLAY_DISABLE_RT_FLIP", None)
    env.pop("EU4_FRAME_REPLAY_SAMPLER_SHIFT", None)
    faults = dict(faults or {})
    if faults.get("disable_render_target_vflip"):
        env["EU4_FRAME_REPLAY_DISABLE_RT_FLIP"] = "1"
    if faults.get("sampler_shift") is not None:
        env["EU4_FRAME_REPLAY_SAMPLER_SHIFT"] = str(faults["sampler_shift"])
        if faults.get("sampler_shift_min") is not None:
            env["EU4_FRAME_REPLAY_SAMPLER_SHIFT_MIN"] = str(
                faults["sampler_shift_min"])
    ran = subprocess.run(
        command, capture_output=True, text=True, check=False, env=env,
        timeout=timeout,
    )
    result_path = out_dir / "replay.json"
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
        state_cov=str(states.get("status") or "invalid"))
    report = {
        "schema": SCHEMA,
        "frame_dir": str(frame_dir),
        "out_dir": str(out_dir),
        "status": status,
        "outcome": outcome,
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
        },
    }
    (out_dir / "replay-report.json").write_text(
        json.dumps(report, indent=2) + "\n", encoding="utf-8")
    return report


if __name__ == "__main__":
    raise SystemExit(main())
