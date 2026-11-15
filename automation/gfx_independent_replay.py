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
DEFAULT_POLICY = Path(__file__).resolve().parents[1] / "analysis" / "m6" / "comparison-policy.json"
ROOT = Path(__file__).resolve().parents[1]
OUTPUT_MARKER = ".gfx-replay-output.json"


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
    return (int(item.get("event") if item.get("event") is not None else -1),
            str(item.get("target")))


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
        "width": int(item.get("reference_width")
                     or item.get("width") or 0),
        "height": int(item.get("reference_height")
                      or item.get("height") or 0),
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
        requested_width = int(wanted.get("reference_width")
                              or wanted.get("width") or 0)
        requested_height = int(wanted.get("reference_height")
                               or wanted.get("height") or 0)
        actual_width = int(item.get("width") or 0)
        actual_height = int(item.get("height") or 0)
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
            missing_unit = int(row.get("fault_missing_engine_unit") or -1)
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
                    "engine_unit": int(row.get("selected_engine_unit") or -1),
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


def final_comparison(frame_dir: Path, out_dir: Path,
                     policy_path: Path | str | None = None) -> dict:
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
        "statistics": {
            key: statistics[key] for key in (
                "pixels", "mismatched_pixels", "mismatched_fraction",
                "alpha_different_pixels", "max_channel_error",
                "mean_abs_error", "mean_max_channel_error",
            )
        },
        "worst_regions": statistics["regions"][:4],
        "policy": str(policy_path or DEFAULT_POLICY),
    }


def first_divergence(graph: dict, checkpoints: list[dict],
                     final: dict, *, binding_audit: dict | None = None,
                     fault: dict | None = None,
                     checkpoint_validation: dict | None = None) -> dict:
    """Earliest confirmed semantic, intermediate or final divergence."""
    validation = checkpoint_validation or {}
    if validation.get("status") == "invalid":
        return {
            "schema": "gfx-first-divergence/v1",
            "kind": "invalid_checkpoint",
            "event": None,
            "statistics": None,
            "semantic": None,
            "intermediate": None,
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
    final_event = None
    candidates = [
        ("semantic", semantic_event if not audit.get("ok") else None),
        ("intermediate", intermediate_event),
        ("final", final_event),
    ]
    candidates = [item for item in candidates if item[1] is not None]
    earliest = min(candidates, key=lambda item: item[1]) if candidates else None
    if earliest and earliest[1] == semantic_event:
        return {
            "schema": "gfx-first-divergence/v1",
            "kind": "semantic",
            "event": semantic_event,
            "semantic": first_semantic,
            "intermediate": (divergent[0] if divergent else None),
            "final": final.get("status"),
            "fault": fault,
            "reason": (
                "the earliest confirmed difference is a binding/state mismatch; "
                "its pixel effect may be overdrawn later"),
        }
    if divergent:
        first = divergent[0]
        resource = (graph.get("resources") or {}).get(first["resource"]) or {}
        producers = [item for item in resource.get("producers") or []
                     if item.get("event", 0) < (first.get("event") or 0)]
        upstream = [
            {"event": row["event"],
             "target": row["target"],
             "status": next((item["status"] for item in checkpoints
                             if item.get("event") == row["event"]
                             and str(item.get("target")) == row["target"]),
                            "not_checkpointed")}
            for row in graph.get("events") or []
            if row.get("event") is not None and row.get("event") < first["event"]
            and row.get("samples") and row.get("target") == first["resource"]
        ]
        return {
            "schema": "gfx-first-divergence/v1",
            "kind": "intermediate",
            "event": first["event"],
            "target": first["target"],
            "resource": first["resource"],
            "producer_event": producers[-1]["event"] if producers else None,
            "producer": producers[-1] if producers else None,
            "statistics": first.get("statistics"),
            "upstream_checkpoints": upstream,
            "semantic": first_semantic,
            "final": final.get("status"),
            "reason": (
                "the earliest authoritative intermediate checkpoint differs; "
                "its inputs are not necessarily the cause"),
            "fault": fault,
        }
    if final.get("status") == "divergent":
        return {
            "schema": "gfx-first-divergence/v1",
            "kind": "final_only",
            "event": None,
            "statistics": final.get("statistics"),
            "semantic": first_semantic,
            "intermediate": None,
            "reason": (
                "no authoritative intermediate checkpoint diverged before the "
                "final output; increase checkpoint detail around the last pass"),
            "fault": fault,
        }
    diagnostic = [item for item in checkpoints
                  if item.get("status") == "divergent_diagnostic"]
    return {
        "schema": "gfx-first-divergence/v1",
        "kind": "none",
        "event": None,
        "diagnostic_checkpoints": [
            {"event": item["event"], "target": item["target"],
             "statistics": item.get("statistics")} for item in diagnostic
        ],
        "semantic": first_semantic,
        "intermediate": None,
        "reason": "all authoritative checkpoints and the final output match",
        "fault": fault,
    }


def state_coverage(frame_dir: Path) -> dict:
    """Classify captured render state that the Metal host implements.

    The binding audit establishes shader-resource equivalence only.  GL state
    that the replay intentionally does not implement is reported here rather
    than folded into an exact semantic verdict.
    """
    unsupported: set[str] = set()
    try:
        trace = m6_frame.load_trace(Path(frame_dir))
    except m6_frame.FrameError as exc:
        return {
            "schema": "gfx-state-coverage/v1",
            "status": "invalid",
            "supported": [],
            "unsupported": [],
            "reason": str(exc),
        }
    for event in trace.get("_events") or []:
        state = event.get("state") or {}
        if not isinstance(state, dict):
            continue
        if state.get("dither") is True:
            unsupported.add("gl_dither")
        if state.get("multisample") is True:
            unsupported.add("multisample")
    return {
        "schema": "gfx-state-coverage/v1",
        "status": "partial" if unsupported else "complete",
        "supported": ["shader_bindings", "render_targets", "samplers",
                      "viewport", "scissor", "blend", "depth", "color_mask"],
        "unsupported": sorted(unsupported),
        "scope": "captured draw state observed by the replay host",
    }


def classify_replay(*, execution_ok: bool, coverage: str, semantic: str,
                    pixels: str, checkpoints: list[dict],
                    checkpoint_validation: dict, faults: dict | None,
                    state_cov: str = "complete"
                    ) -> tuple[str, dict]:
    """Pure qualification classification used by replay() and its tests."""
    intermediate_divergent = any(
        item.get("status") == "divergent" and item.get("authoritative")
        for item in checkpoints)
    checkpoint_invalid = checkpoint_validation.get("status") != "ok"
    fault_injection = bool(faults)
    qualified = bool(
        execution_ok and coverage == "complete" and state_cov == "complete"
        and semantic == "match" and pixels == "match"
        and not intermediate_divergent and not checkpoint_invalid
        and not fault_injection
    )
    if not execution_ok or coverage == "invalid" or checkpoint_invalid:
        status = "invalid"
    elif semantic == "divergent":
        status = "semantic_divergence"
    elif pixels == "divergent" or intermediate_divergent:
        status = ("confirmed_rendering_difference" if coverage == "complete"
                  else "partial_rendering_difference")
    elif coverage == "partial" or state_cov != "complete":
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
    if outcome.get("checkpoints") == "invalid":
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


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
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
        return {
            "schema": SCHEMA,
            "frame_dir": str(frame_dir),
            "out_dir": str(out_dir),
            "status": "invalid",
            "faults": faults,
            "returncode": ran.returncode,
            "stderr": (ran.stderr or "")[-4000:],
            "graph": graph,
        }
    replay_result = json.loads(result_path.read_text())
    replay_result["_provenance_samples"] = _read_jsonl(
        out_dir / "replay-provenance.jsonl")
    replay_result["_checkpoint_plan"] = request.get("checkpoints") or []
    evaluation = gfx_frame_graph.evaluate_replay(graph, replay_result)
    checkpoints, checkpoint_validation = reconcile_checkpoints(
        graph, request, replay_result, frame_dir, out_dir)
    audit = binding_audit(replay_result)
    states = state_coverage(frame_dir)
    final = final_comparison(frame_dir, out_dir, policy)
    divergence = first_divergence(
        graph, checkpoints, final, binding_audit=audit,
        fault=faults or None, checkpoint_validation=checkpoint_validation)
    execution_ok = (
        replay_result.get("status") == "ok"
        and replay_result.get("encoded") == replay_result.get("events")
        and replay_result.get("failed") == 0
        and replay_result.get("command_status") == 4
        and bool(replay_result.get("readback"))
    )
    coverage = evaluation["status"]
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
        "faults": faults,
        "graph": graph,
        "evaluation": evaluation,
        "checkpoints": checkpoints,
        "checkpoint_validation": checkpoint_validation,
        "binding_audit": audit,
        "state_coverage": states,
        "final": final,
        "first_divergence": divergence,
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
        },
    }
    (out_dir / "replay-report.json").write_text(
        json.dumps(report, indent=2) + "\n", encoding="utf-8")
    return report


if __name__ == "__main__":
    raise SystemExit(main())
