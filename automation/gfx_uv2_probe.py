#!/usr/bin/env python3
"""Numerical comparison for the opt-in event-1 uv2 diagnostic probe.

Both probe descriptors, the divergent-pixel selection and the original-shader
control reference are bound to one authenticated sealed frame and to the same
Metal replay execution that produced the probe.  The report gives raw float32
bits, numeric values, signed differences, ULP differences, coordinates and
orientation; it never substitutes an aggregate similarity score.

    python3 -m automation.gfx_uv2_probe compare \\
        --gl <capture>/uv2-probe --metal <metal>/uv2-probe \\
        --frame <capture>/frame --out <capture>/uv2-probe
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import math
import re
import struct
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from automation import (gfx_event_evidence, gfx_frame_compare,
                        gfx_independent_replay, gfx_observation,
                        gfx_replay_case)  # noqa: E402

SCHEMA = "gfx-uv2-probe/v1"
COMPARISON_SCHEMA = "gfx-uv2-probe-comparison/v1"
NEIGHBORHOOD_MAX = 4
MAX_MASK_PIXELS = 4096
EVENT1 = {"event": 1, "program": 310, "framebuffer": 6, "texture": 433}
STAGES = ("fragment_coverage", "uv2", "texture_samples", "pre_conversion", "rgb8")
FLOAT_STAGES = ("uv2", "texture_samples", "pre_conversion")
CONTROL_PIXELS = 8
SPARSE_FILE = "uv2-fragments.json"
SPARSE_SCHEMA = "gfx-uv2-fragments/v1"
EVENT1_SHADER_SHA256 = {
    "vertex": "2e8842a26b765c70f06594103eff681f8a149a49966c34b25d8648058f7a4b98",
    "pixel": "3ecb11ae2fcd0c07bb9960e41fa7122a6cb7b5d873dcc9f95b9d55b765caf548",
}
SPARSE_METHODS = {"opengl": "original_program_query_pre_depth",
                  "metal": "normal_pipeline_query_post_lequal"}


class ProbeError(RuntimeError):
    pass


class MissingObservation(ProbeError):
    pass


def _sha256(body: bytes) -> str:
    return hashlib.sha256(body).hexdigest()


def _read_json(path: Path) -> dict:
    try:
        value = json.loads(path.read_text())
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ProbeError(f"unreadable JSON {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise ProbeError(f"not a JSON object: {path}")
    return value


def load_probe(directory: Path) -> dict:
    """Loads one descriptor and validates its declared layout and payload."""
    directory = Path(directory)
    descriptor_path = directory / "uv2-probe.json"
    if not descriptor_path.is_file():
        raise ProbeError(f"missing probe descriptor: {descriptor_path}")
    descriptor = _read_json(descriptor_path)
    if descriptor.get("schema") != SCHEMA:
        raise ProbeError(f"unexpected probe schema in {descriptor_path}")
    side = descriptor.get("side")
    if side not in ("opengl", "metal"):
        raise ProbeError(f"unknown probe side {side!r}")
    if descriptor.get("status") != "ok":
        raise ProbeError(
            f"{side} probe did not produce numbers: {descriptor.get('status')} "
            f"({descriptor.get('reason', '')})")
    if descriptor.get("pixel_format") != "RGBA32F":
        raise ProbeError(f"{side} probe is not RGBA32F")
    if descriptor.get("origin") != "lower_left":
        raise ProbeError(f"{side} probe origin is not lower_left")
    width, height = descriptor.get("width"), descriptor.get("height")
    if not isinstance(width, int) or not isinstance(height, int):
        raise ProbeError(f"{side} probe dimensions are invalid")
    if width <= 0 or height <= 0:
        raise ProbeError(f"{side} probe dimensions are invalid")
    if descriptor.get("row_stride") != width * 16:
        raise ProbeError(f"{side} probe row_stride does not match its extent")
    raw_path = directory / str(descriptor.get("raw_file") or "")
    if not raw_path.is_file():
        raise ProbeError(f"missing {side} probe payload: {raw_path}")
    raw = raw_path.read_bytes()
    if len(raw) != width * height * 16 or descriptor.get("raw_bytes") != len(raw):
        raise ProbeError(f"{side} probe payload size is inconsistent")
    digest = _sha256(raw)
    if descriptor.get("raw_sha256") != digest:
        raise ProbeError(f"{side} probe payload digest does not match")
    return {"directory": directory, "descriptor": descriptor, "raw": raw,
            "sha256": digest, "width": width, "height": height, "side": side}

def frame_context(frame_dir: Path, event_index: int) -> tuple[dict, dict, dict]:
    """Loads the authenticated sealed frame and one captured draw event."""
    try:
        # The existing verified loader accepts both a direct sealed frame and
        # an authenticated trusted-evidence copy of one.
        trace, seal = gfx_event_evidence._load_frame(frame_dir)
    except Exception as exc:  # noqa: BLE001 - report as a probe input error
        raise ProbeError(f"frame is not a verified sealed capture: {exc}") from exc
    if seal is None or seal.get("sealed") is not True:
        raise ProbeError("frame seal is not sealed")
    events = trace.get("_events") or []
    if not 0 <= event_index < len(events):
        raise ProbeError(f"event {event_index} is outside the captured frame")
    event = events[event_index]
    if event.get("kind") != "draw":
        raise ProbeError(f"event {event_index} is not a draw")
    return trace, seal, event


def post_observation(trace: dict, event_index: int, resource: str) -> dict:
    prefix = trace.get("_prefix") or {}
    for record in prefix.get("post_events") or []:
        if record.get("event") == event_index and record.get("resource") == resource:
            return record
    raise MissingObservation(
        f"the capture has no post-event observation for event {event_index} "
        f"({resource}); capture with --prefix-end")


def observation_image(frame_dir: Path, record: dict) -> dict:
    color = record.get("color") or {}
    name = color.get("file")
    if not name:
        raise ProbeError("the post-event observation has no colour payload")
    path = Path(frame_dir) / "blobs" / str(name)
    if not path.is_file():
        raise ProbeError(f"missing observation payload: {path}")
    body = path.read_bytes()
    width, height = record.get("width"), record.get("height")
    if not isinstance(width, int) or not isinstance(height, int):
        raise ProbeError("the post-event observation extent is invalid")
    if len(body) != width * height * 4 or color.get("sha256") != _sha256(body):
        raise ProbeError("the post-event observation payload is inconsistent")
    return {"path": path, "sha256": _sha256(body), "width": width,
            "height": height, "origin": "lower_left", "pixel_format": "BGRA8",
            "event": record.get("event"), "timing": record.get("timing"),
            "resource": record.get("resource")}


def _image(path: Path, sha256: str, width: int, height: int, label: str):
    try:
        image = gfx_frame_compare.load_frame(
            path, width, height, origin="lower_left", pixel_format="BGRA8",
            expected_sha256=sha256)
    except gfx_frame_compare.CompareError as exc:
        raise ProbeError(f"{label} image is unusable: {exc}") from exc
    image[:, :, 3] = 255  # RGB8 scope: the alpha byte is not compared.
    return image


def _diff_mask(left, right):
    return gfx_frame_compare.difference_mask(left, right, 0)


def _differences(left, right,
                 limit: int = MAX_MASK_PIXELS) -> list[tuple[int, int]]:
    """Returns divergent pixels in the probe's lower-left convention.

    ``load_frame`` decodes to a top-left array, while the raw uv2 payloads are
    lower-left.  The conversion happens here, once, before sampling.
    """
    import numpy as np

    mask = _diff_mask(left, right)
    count = int(np.count_nonzero(mask))
    if count > limit:
        raise ProbeError(
            f"divergent mask has {count} pixels; the diagnostic budget is "
            f"{limit}; refusing an unbounded report")
    height = int(left.shape[0])
    return [(int(x), height - 1 - int(y)) for y, x in np.argwhere(mask)]


def _f32_bits(raw: bytes, offset: int) -> int:
    return struct.unpack_from("<I", raw, offset)[0]


def _bits_float(bits: int) -> float:
    return struct.unpack("<f", struct.pack("<I", bits))[0]


def _ordered_bits(bits: int) -> int:
    if bits & 0x80000000:
        return -(bits & 0x7FFFFFFF)
    return bits


def _ulp(left: int, right: int) -> int:
    return abs(_ordered_bits(left) - _ordered_bits(right))


def _sample(probe: dict, x: int, y: int) -> list[int]:
    offset = (y * probe["width"] + x) * 16
    return [_f32_bits(probe["raw"], offset + 4 * component)
            for component in range(4)]


def _capture(probe: dict) -> dict:
    descriptor = probe["descriptor"]
    capture = descriptor.get("capture")
    if isinstance(capture, dict):
        return capture
    return {"sealed": descriptor.get("sealed"),
            "session_id": descriptor.get("session_id"),
            "capture_frame_id": descriptor.get("capture_frame_id")}


def capture_binding(gl: dict, metal: dict, seal: dict) -> dict:
    """Requires both probes to name the sealed capture they are compared to."""
    problems = []
    gl_capture = _capture(gl)
    metal_capture = _capture(metal)
    for label, capture in (("opengl", gl_capture), ("metal", metal_capture)):
        if capture.get("sealed") is not True:
            problems.append(f"{label} probe is not bound to a sealed frame")
        if capture.get("session_id") != seal.get("session_id"):
            problems.append(f"{label} session")
        if capture.get("capture_frame_id") != seal.get("capture_frame_id"):
            problems.append(f"{label} capture frame")
    if gl["descriptor"].get("pid") != seal.get("pid"):
        problems.append("opengl pid")
    if metal_capture.get("manifest_sha256") != seal.get("manifest_sha256"):
        problems.append("metal replay did not execute the sealed manifest")
    if metal_capture.get("reference_sha256") != seal.get("reference_sha256"):
        problems.append("metal replay reference differs from the seal")
    return {
        "match": not problems,
        "problems": problems,
        "opengl": gl_capture,
        "metal": metal_capture,
    }


def _draw(record: dict, event_draw: dict) -> dict:
    return {
        "entry": record.get("entry"),
        "mode": record.get("mode"),
        "count": record.get("count"),
        "first": record.get("first", 0),
        "base_vertex": record.get("base_vertex", 0),
        "instances": record.get("instances", record.get("instance_count", 1)),
        "indexed": bool(record.get("indexed")),
        "caller_rva": record.get("caller_rva"),
    }


def _uniform_map(uniforms) -> dict:
    result = {}
    for uniform in uniforms or []:
        name = uniform.get("name")
        if name is None:
            continue
        if uniform.get("kind") == "sampler" or uniform.get("type") == "sampler":
            result[name] = {"kind": "sampler", "unit": uniform.get("unit")}
        else:
            result[name] = {"sha256": uniform.get("sha256")}
    return result


def input_identity(probe: dict, event: dict) -> dict:
    """Checks the probe descriptor against the captured event's exact inputs."""
    descriptor = probe["descriptor"]
    problems = []
    checks = {}
    program = event.get("program") or {}
    target = event.get("target") or {}
    color = target.get("color_attachment") or {}
    probe_target = descriptor.get("target") or {}
    probe_texture = probe_target.get("texture")
    if probe_texture is None:
        probe_texture = (probe_target.get("color_attachment") or {}).get("name")
    checks["program"] = descriptor.get("program") == program.get("name")
    checks["framebuffer"] = probe_target.get("framebuffer") == target.get("framebuffer")
    checks["texture"] = probe_texture == color.get("name")
    event_draw = event.get("draw") or {}
    expected_draw = _draw(event_draw, event_draw)
    actual_draw = _draw(descriptor.get("draw") or {}, event_draw)
    checks["draw"] = actual_draw == expected_draw
    shaders = {stage.get("stage"): stage.get("sha256")
               for stage in program.get("shaders") or []}
    sources = descriptor.get("sources") or {}
    checks["vertex_source"] = sources.get("vertex_sha256") == shaders.get("vertex")
    checks["fragment_source"] = sources.get("fragment_sha256") == shaders.get("pixel")
    checks["uniforms"] = _uniform_map(program.get("uniforms")) == _uniform_map(
        descriptor.get("uniforms"))
    captured_attributes = {a.get("name"): a.get("sha256")
                           for a in event.get("attributes") or []}
    probe_attributes = {a.get("name"): a.get("sha256")
                        for a in descriptor.get("attributes") or []}
    digest_ok = all(a.get("digest_ok", True)
                    for a in descriptor.get("attributes") or [])
    checks["attributes"] = captured_attributes == probe_attributes and digest_ok
    indices = event.get("indices") or {}
    probe_indices = descriptor.get("indices") or {}
    checks["indices"] = (probe_indices.get("sha256") == indices.get("sha256")
                         and probe_indices.get("count") == indices.get("count"))
    for name, passed in checks.items():
        if not passed:
            problems.append(name)
    return {"match": not problems, "problems": problems, "checks": checks}


def control_correspondence(control: dict, reference: dict, width: int,
                           height: int) -> dict:
    """Exact RGB comparison of the diagnostic control with the authentic output."""
    left = _image(control["path"], control["sha256"], width, height, "control")
    right = _image(reference["path"], reference["sha256"], width, height,
                   "reference")
    import numpy as np

    delta = np.abs(left[:, :, :3].astype(np.int16) -
                   right[:, :, :3].astype(np.int16))
    mismatched = delta.max(axis=2)
    count = int(np.count_nonzero(mismatched))
    first = []
    for y, x in np.argwhere(mismatched)[:16]:
        first.append({"x": int(x), "y": height - 1 - int(y),
                      "orientation": "lower_left",
                      "control_rgb": [int(v) for v in left[y, x, :3]],
                      "reference_rgb": [int(v) for v in right[y, x, :3]],
                      "max_channel_error": int(mismatched[y, x])})
    return {
        "status": "compared", "match": count == 0, "scope": "RGB",
        "pixels": width * height, "mismatched_pixels": count,
        "max_channel_error": int(mismatched.max()) if count else 0,
        "first_differences": first,
        "control": {"path": str(control["path"]), "sha256": control["sha256"]},
        "reference": {"path": str(reference["path"]),
                      "sha256": reference["sha256"]},
    }


def _neighborhood(mask: list[tuple[int, int]], width: int, height: int,
                  radius: int) -> list[tuple[int, int]]:
    coordinates = set()
    for x, y in mask:
        for dy in range(-radius, radius + 1):
            for dx in range(-radius, radius + 1):
                nx, ny = x + dx, y + dy
                if 0 <= nx < width and 0 <= ny < height:
                    coordinates.add((nx, ny))
    return sorted(coordinates, key=lambda item: (item[1], item[0]))


def _json_number(value: float):
    """JSON has no NaN/Infinity token; raw bits remain in the bit fields."""
    return value if math.isfinite(value) else None


def _float_components(gl_bits: dict, metal_bits: dict) -> dict:
    gl_values = {name: _bits_float(bits) for name, bits in gl_bits.items()}
    metal_values = {name: _bits_float(bits) for name, bits in metal_bits.items()}
    finite = [name for name in gl_bits if math.isfinite(gl_values[name])
              and math.isfinite(metal_values[name])]
    return {
        "gl_bits": {name: f"0x{bits:08x}" for name, bits in gl_bits.items()},
        "metal_bits": {name: f"0x{bits:08x}" for name, bits in metal_bits.items()},
        "gl_values": {name: _json_number(value) for name, value in gl_values.items()},
        "metal_values": {name: _json_number(value)
                         for name, value in metal_values.items()},
        "signed_differences": {name: (metal_values[name] - gl_values[name]
                                      if name in finite else None) for name in gl_bits},
        "ulp_differences": {name: (_ulp(gl_bits[name], metal_bits[name])
                                   if name in finite else None) for name in gl_bits},
        "bit_equal": {name: gl_bits[name] == metal_bits[name] for name in gl_bits},
    }


def compare_stages(observations: dict, *, attribution: dict,
                   provenance_valid: bool, origin: str = "lower_left") -> dict:
    """Compare verified or explicitly conditional observations for one pixel.

    Callers establish provenance before this projection. The stage walker
    cannot establish attribution from numerical agreement or coverage flags.
    """
    if not provenance_valid or origin != "lower_left":
        return {"status": "invalid_provenance", "stages": {}}
    stages = {}
    verified = attribution.get("verified") is True
    coverage = observations.get("fragment_coverage", {})
    original_fragment = coverage.get("opengl") or {}
    same_fragment = (verified and original_fragment == coverage.get("metal")
                     and original_fragment.get("covered") is True
                     and original_fragment.get("primitive") is not None)
    for stage in STAGES:
        pair = observations.get(stage, {})
        left, right = pair.get("opengl"), pair.get("metal")
        if left is None or right is None:
            stages[stage] = {"status": "missing_evidence",
                             "missing": [side for side in ("opengl", "metal")
                                         if pair.get(side) is None]}
            continue
        if not left or left.keys() != right.keys():
            return {"status": "invalid_provenance", "stages": stages,
                    "reason": f"{stage} components do not correspond"}
        associated = verified if stage == "fragment_coverage" else same_fragment
        observed = stage == "rgb8" or associated
        values = (_float_components(left, right) if stage in FLOAT_STAGES else {
            "gl_values": left, "metal_values": right,
            "signed_differences": {name: int(right[name]) - int(value)
                                   for name, value in left.items()},
        })
        status = "conditional"
        if observed:
            status = "exact" if left == right else "different"
            if stage in FLOAT_STAGES and any(v is None for v in
                                             values["ulp_differences"].values()):
                status = "unsupported_value"
        stages[stage] = {
            "status": status,
            "fragment_correspondence_verified": associated,
            "kind": ({"opengl": "authentic", "metal": "candidate_observed"}
                     if observed else {"opengl": "diagnostic", "metal": "diagnostic"}),
            **values,
        }
    first = next((stage for stage in STAGES if stages[stage]["status"] == "different"),
                 None)
    pending = next((stage for stage in STAGES if stages[stage]["status"] != "exact"), None)
    localized = first is not None and pending == first
    if localized:
        status = "different"
    elif pending is None:
        status = "exact_observed_stage_agreement"
    elif stages[pending]["status"] != "missing_evidence":
        status = "inconclusive"
    else:
        status = "missing_evidence"
    return {"status": status, "first_observed_difference": first,
            "first_differing_stage": first if localized else None,
            "first_unresolved_stage": None if localized else pending,
            "stages": stages}


def fragment_attribution(state: dict | None) -> dict:
    """The isolated probes disable depth/blend and do not prove which fragment
    produced RGB8; attribution stays unverified without direct evidence."""
    if not isinstance(state, dict):
        state = {}
    problems = ["the isolated pass does not prove contributing-fragment identity",
                "fragment-shader discard effects are not evaluated",
                "the probe has no primitive or per-sample identity for overlapping fragments",
                "isolated-pass coverage and attachment/sample behavior can differ",
                "original GL fragment observations are absent from the frame seal",
                "colour-mask and logic-op effects on fragment correspondence "
                "are not evaluated"]
    for key, field, reason in (
            ("blend", "enabled", "blending can combine multiple fragments"),
            ("depth", "test", "depth test and pre-draw depth contents "
             "can reject or supersede a fragment"),
            ("alpha_test", "enabled", "alpha test can discard a fragment"),
            ("stencil", "test", "stencil test can reject a fragment")):
        setting = state.get(key)
        if not isinstance(setting, dict):
            problems.append(f"{key} state missing")
        elif not isinstance(setting.get(field), bool):
            problems.append(f"{key}.{field} missing")
        elif setting[field]:
            problems.append(reason)
    if not state:
        problems.insert(0, "no captured draw state")
    return {"verified": False, "status": "unverified", "problems": problems,
            "not_sufficient": "original-shader RGB equality, coverage alpha"}


def compare(gl: dict, metal: dict, *, identity: dict, control: dict,
            mask: list[tuple[int, int]], neighborhood: int,
            authority: str, state: dict | None = None) -> dict:
    width, height = gl["width"], gl["height"]
    if (width, height) != (metal["width"], metal["height"]):
        raise ProbeError("the OpenGL and Metal probe extents differ")
    mask_set = set(mask)
    components = ("x", "y", "z", "w")
    entries = []
    attribution = fragment_attribution(state)
    for x, y in _neighborhood(mask, width, height, neighborhood):
        left = dict(zip(components, _sample(gl, x, y)))
        right = dict(zip(components, _sample(metal, x, y)))
        values = _float_components(left, right)
        covered = {"gl": left["w"] == 0x3F800000, "metal": right["w"] == 0x3F800000}
        entry = {"x": x, "y": y, "orientation": "lower_left",
                 "mask_pixel": (x, y) in mask_set, "covered": covered,
                 **values, "all_components_bit_equal": left == right}
        entries.append(entry)
    covered = [e for e in entries if e["mask_pixel"]
               and e["covered"]["gl"] and e["covered"]["metal"]]
    coverage = len(mask)
    complete = coverage > 0 and len(covered) == coverage
    observed_mismatch = None
    if covered:
        observed_mismatch = any(
            not (e["bit_equal"]["x"] and e["bit_equal"]["y"]) for e in covered)
    all_equal = (not observed_mismatch) if complete else None
    evidence_checks = {
        "capture_authority": authority == "authentic",
        "capture_binding": identity["capture"]["match"],
        "input_identity": identity["gl"]["match"] and identity["metal"]["match"],
        "original_shader_control": control.get("match") is True,
        "coverage_complete": complete,
    }
    evidence_ok = all(evidence_checks.values())
    conclusive = evidence_ok and complete
    if conclusive:
        status = ("conclusive_unequal" if observed_mismatch
                  else "conclusive_equal")
    elif coverage == 0:
        status = "inconclusive_no_mask"
    elif not covered:
        status = "inconclusive_uncovered"
    elif not complete:
        status = "inconclusive_partial_coverage"
    else:
        status = "inconclusive_evidence"
    ulps = [e["ulp_differences"][c] for e in covered for c in ("x", "y")
            if e["ulp_differences"][c] is not None]
    signed_abs = [abs(e["signed_differences"][c]) for e in covered
                  for c in ("x", "y")
                  if e["signed_differences"][c] is not None]
    summary = {
        "mask_pixels": coverage,
        "mask_pixels_covered_by_both": len(covered),
        "coverage_complete": complete,
        "status": status,
        "numerical_comparison_conclusive": conclusive,
        "observed_mismatch": observed_mismatch,
        "all_selected_pixels_equal": all_equal,
        "xy_bit_for_bit_equal": all_equal if conclusive else None,
        "max_ulp_difference_xy": max(ulps) if ulps else None,
        "max_abs_signed_difference_xy": max(signed_abs) if signed_abs else None,
    }
    return {
        "schema": COMPARISON_SCHEMA,
        "created_utc": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "inputs": {
            "gl": {"directory": str(gl["directory"]), "sha256": gl["sha256"]},
            "metal": {"directory": str(metal["directory"]),
                      "sha256": metal["sha256"]},
        },
        "identity": identity, "control": control,
        "fragment_attribution": attribution,
        "summary": summary, "pixels": entries,
        "conclusion": {
            "authority": authority,
            "evidence_checks": evidence_checks,
            "numerical_comparison_conclusive": conclusive,
            "authenticated_original_gl_observation": False,
            "fragment_attribution_verified": attribution["verified"],
            "first_unresolved_stage": "fragment_coverage",
            "classification": ("conditional_numerical_comparison"
                               if conclusive
                               else "unverified_diagnostic_observation"),
            "uv2_results_equal": all_equal if conclusive else None,
            "original_shader_control_match": control.get("match") is True,
            "xy_bit_for_bit_equal": all_equal if conclusive else None,
        },
    }

def run(args: argparse.Namespace) -> dict:
    gl = load_probe(Path(args.gl))
    metal = load_probe(Path(args.metal))
    if gl["side"] != "opengl" or metal["side"] != "metal":
        raise ProbeError("probe side does not match --gl or --metal")
    event_index = gl["descriptor"].get("event")
    if metal["descriptor"].get("event") != event_index:
        raise ProbeError("the OpenGL and Metal probes name different events")
    frame_dir = Path(args.frame)
    trace, seal, event = frame_context(frame_dir, event_index)
    authority = gfx_observation.authority(trace, seal)
    target = gl["descriptor"].get("target") or {}
    texture = target.get("texture")
    if texture is None:
        texture = (target.get("color_attachment") or {}).get("name")
    if not isinstance(texture, int):
        raise ProbeError("the probe descriptor does not name its colour texture")
    resource = f"tex:{texture}"
    reference = observation_image(
        frame_dir, post_observation(trace, event_index, resource))
    if (reference["width"], reference["height"]) != (gl["width"], gl["height"]):
        raise ProbeError("the sealed observation and probe extents differ")
    normal_target = metal["descriptor"].get("normal_target") or {}
    color_path = Path(normal_target["path"]) if normal_target.get("path") else (
        Path(args.metal).parent / str(normal_target.get("file") or ""))
    if not normal_target.get("file") or not color_path.is_file():
        raise ProbeError("the Metal probe has no event-boundary target readback")
    metal_mask = {
        "path": color_path, "sha256": normal_target.get("sha256") or "",
        "width": normal_target.get("width"), "height": normal_target.get("height")}
    if (metal_mask["width"], metal_mask["height"]) != (gl["width"], gl["height"]):
        raise ProbeError("the Metal event-target readback extent differs")
    left = _image(reference["path"], reference["sha256"], gl["width"],
                  gl["height"], "sealed observation")
    right = _image(metal_mask["path"], metal_mask["sha256"], gl["width"],
                   gl["height"], "Metal event target")
    mask = _differences(left, right)
    control_file = (gl["descriptor"].get("control") or {}).get("file")
    if not control_file:
        raise ProbeError("the OpenGL probe has no original-shader control")
    control = control_correspondence(
        {"path": Path(args.gl) / str(control_file),
         "sha256": (gl["descriptor"].get("control") or {}).get("sha256") or ""},
        reference, gl["width"], gl["height"])
    identity = {
        "gl": input_identity(gl, event),
        "metal": input_identity(metal, event),
        "capture": capture_binding(gl, metal, seal),
    }
    report = compare(gl, metal, identity=identity, control=control, mask=mask,
                     neighborhood=int(args.neighborhood), authority=authority,
                     state=event.get("state"))
    sidecar_checks = {
        "capture": identity["capture"]["match"],
        "pid": gl["descriptor"].get("pid") == seal.get("pid"),
        "event": gl["descriptor"].get("event") == event_index,
        "draw": identity["gl"]["checks"].get("draw") is True,
    }
    report["provenance"] = {
        "frame": {"authority": authority, "sealed": seal.get("sealed"),
                  "trusted_evidence": trace.get("_trusted_evidence") is not None},
        "gl_sidecar": {
            "kind": "descriptor_self_asserted", "authenticated": False,
            "identity_checks": sidecar_checks,
            "reason": "the seal does not attest the uv2 sidecar bytes",
        },
        "metal_sidecar": {"kind": "replay_derived", "authenticated": False},
    }
    report["conclusion"]["authenticated_original_gl_observation"] = False
    report["conclusion"]["sidecar_identity_checks"] = sidecar_checks
    report["provenance"]["frame"].update(
        {"path": str(frame_dir), "event": event_index})
    out = Path(args.out) if args.out else gl["directory"]
    out.mkdir(parents=True, exist_ok=True)
    (out / "comparison.json").write_text(
        json.dumps(report, indent=2, allow_nan=False) + "\n")
    return report


def _attachment(target: dict, plane: str) -> tuple | None:
    attachment = target.get(f"{plane}_attachment") or {}
    if attachment.get("name") is None:
        return None
    return (target.get("framebuffer"), attachment.get("type"),
            attachment["name"], attachment.get("level"))


def _event1_images(frame_dir: Path, trace: dict, event: dict) -> tuple[dict, dict]:
    target = event.get("target") or {}
    color = _attachment(target, "color")
    if (event.get("index") != 1 or (event.get("program") or {}).get("name") != 310
            or color != (6, "texture", 433, 0)):
        raise ProbeError("the sealed draw does not match the event-1 target")
    post = post_observation(trace, 1, "tex:433")
    scope = gfx_observation.format_scope(post)
    if (_attachment(post.get("target") or {}, "color") != color
            or post.get("origin") != "lower_left"
            or not scope["supported"] or scope["compared_channels"] != "RGB"):
        raise ProbeError("the seal does not name the event-1 RGB8 draw and attachment")
    reference = observation_image(frame_dir, post)
    clear = trace["_events"][0]
    prior_target = clear.get("target") or {}
    before = next((item for item in (trace.get("_prefix") or {}).get("post_events", [])
                   if item.get("event") == 0 and item.get("resource") == "tex:433"), {})
    before_target = before.get("target") or {}
    if (clear.get("kind") != "clear" or before.get("kind") != "clear"
            or before.get("timing") != "post_event" or before.get("origin") != "lower_left"
            or _attachment(prior_target, "color") != color
            or _attachment(before_target, "color") != color
            or any(before.get(k) != post[k] for k in ("width", "height"))):
        return reference, {}
    scope = gfx_observation.format_scope(before)
    predecessor = {}
    if (scope["supported"] and scope["compared_channels"] == "RGB"
            and (before.get("color") or {}).get("file")):
        predecessor["color"] = observation_image(frame_dir, before)
    depth = _attachment(target, "depth")
    plane = before.get("depth") or {}
    if (depth is not None and _attachment(prior_target, "depth") == depth
            and _attachment(before_target, "depth") == depth
            and plane.get("status") == "captured" and plane.get("format") == "f32"
            and plane.get("bytes") == reference["width"] * reference["height"] * 4
            and all(plane.get(k, reference[k]) == reference[k] for k in ("width", "height"))
            and plane.get("file") and plane.get("sha256")):
        path = frame_dir / "blobs" / plane["file"]
        if (path.is_file() and path.stat().st_size == plane["bytes"]
                and gfx_independent_replay._sha256_file(path) == plane["sha256"]):
            predecessor["depth"] = plane
    return reference, predecessor


def sparse_pixels(text: str) -> list[tuple[int, int]]:
    pairs = text.split(";")
    if len(pairs) > 64 or any(not re.fullmatch(r"[0-9]{1,4},[0-9]{1,4}", p)
                              for p in pairs):
        raise ProbeError("expected 1–64 lower-left x,y pairs separated by semicolons")
    pixels = [tuple(map(int, pair.split(","))) for pair in pairs]
    if len(set(pixels)) != len(pixels) or any(max(p) > 8191 for p in pixels):
        raise ProbeError("sparse coordinates must be unique and in 0–8191")
    return pixels


def load_sparse(directory: Path, binding: dict, side: str, seal: dict,
                width: int, height: int, index_sha: str | None) -> tuple[dict, dict]:
    """Authenticate the observation envelope through its existing producer."""
    if not isinstance(binding, dict) or binding.get("file") != SPARSE_FILE:
        raise ProbeError(f"{side} sparse observation file identity is missing")
    body = (directory / SPARSE_FILE).read_bytes()
    if len(body) > 64 * 1024 or _sha256(body) != binding.get("sha256"):
        raise ProbeError(f"{side} sparse observation digest mismatch")
    value = json.loads(body)
    if (not isinstance(value, dict) or value.get("schema") != SPARSE_SCHEMA
            or value.get("side") != side or value.get("origin") != "lower_left"
            or any(type(value.get(k)) is not int or value[k] != v
                   for k, v in {**EVENT1, "level": 0}.items())):
        raise ProbeError(f"{side} sparse observation identity or orientation mismatch")
    capture = value.get("capture")
    keys = ("session_id", "capture_frame_id", "pid")
    if side == "metal":
        keys += ("manifest_sha256", "reference_sha256")
    if not isinstance(capture, dict) or any(capture.get(k) != seal.get(k) for k in keys):
        raise ProbeError(f"{side} sparse observation belongs to a different seal")
    selection = value.get("selection")
    if (not isinstance(selection, list) or not 1 <= len(selection) <= 64
            or any(not isinstance(p, list) or len(p) != 2
                   or any(type(v) is not int for v in p)
                   or not (0 <= p[0] <= 8191 and 0 <= p[1] <= 8191) for p in selection)
            or len({tuple(p) for p in selection}) != len(selection)):
        raise ProbeError(f"{side} sparse observation has an invalid coordinate selection")
    pixels = value.get("pixels")
    if not isinstance(pixels, list):
        raise ProbeError(f"{side} sparse observation has no pixel records")
    if value.get("status") != "observed":
        if pixels or not isinstance(value.get("reason"), str):
            raise ProbeError(f"{side} sparse failure has invalid evidence")
        return value, {}
    if (not isinstance(index_sha, str) or not re.fullmatch(r"[a-f0-9]{64}", index_sha)
            or value.get("index_sha256") != index_sha):
        raise ProbeError(f"{side} sparse observation names a different index stream")
    if (value.get("method") != SPARSE_METHODS[side]
            or any(value.get(k) != v for k, v in (("width", width), ("height", height)))
            or type(value.get("samples")) is not int or value["samples"] != 1
            or len(pixels) != len(selection)):
        raise ProbeError(f"{side} sparse observation has an unsupported method or layout")

    def uint(n, maximum):
        return type(n) is int and 0 <= n <= maximum

    def vector(row, key, length, maximum):
        v = row.get(key)
        return isinstance(v, list) and len(v) == length and all(uint(n, maximum) for n in v)

    rows = {}
    for (x, y), row in zip(selection, pixels):
        if (not isinstance(row, dict) or (row.get("x"), row.get("y")) != (x, y)
                or not uint(row.get("x"), width - 1) or not uint(row.get("y"), height - 1)
                or any(not uint(row.get(k), 0xffffffff) for k in ("sample", "hits"))
                or type(row.get("primitive")) is not int or not -1 <= row["primitive"] < 65535
                or type(row.get("survived")) is not bool
                or not vector(row, "control_rgb", 3, 255)
                or not vector(row, "uv2_bits", 2, 0xffffffff)):
            raise ProbeError(f"{side} sparse sample record is invalid")
        if side == "opengl" and (
                not vector(row, "pre_rgb", 3, 255)
                or any(not uint(row.get(k), 0xffffffff) for k in
                       ("pre_depth_bits", "control_depth_bits", "hits_without_depth"))):
            raise ProbeError("OpenGL sparse sample has invalid pre-draw/depth evidence")
        if side == "metal" and not uint(row.get("selected_hits"), 0xffffffff):
            raise ProbeError("Metal sparse sample has no selected-primitive visibility result")
        rows[(x, y)] = row
    return value, rows


def sparse_replay_inputs(frame_dir: Path, selection: str) -> tuple[str, list[dict]]:
    trace, seal, event = frame_context(frame_dir, 1)
    if gfx_observation.authority(trace, seal) != "authentic":
        raise ProbeError("sparse replay requires authentic GL evidence")
    reference, before = _event1_images(frame_dir, trace, event)
    document, rows = load_sparse(frame_dir, trace.get("uv2_fragments"), "opengl",
                                 seal, reference["width"], reference["height"],
                                 (event.get("indices") or {}).get("sha256"))
    pixels = sparse_pixels(selection)
    if [list(pixel) for pixel in pixels] != document["selection"]:
        raise ProbeError("replay pixels differ from the sealed selection")
    primitives = ",".join(str(rows.get(pixel, {}).get("primitive", -1)) for pixel in pixels)
    checkpoints = [{
        "event": 1, "target": "433", "resource": "tex:433", "timing": timing,
        "file": name, "reference_sha256": item["sha256"],
    } for timing, name, item in (
        ("post_event", "uv2-color.bgra", reference),
        ("pre_event", "uv2-pre-color.bgra", before.get("color")),
    ) if item is not None]
    return primitives, checkpoints


def _sparse_state(event: dict) -> int:
    """Return the triangle count for the reviewed replace-write path."""
    draw, state = event.get("draw") or {}, event.get("state") or {}
    count = draw.get("count")
    sources = {stage["stage"]: stage.get("sha256")
               for stage in (event.get("program") or {}).get("shaders", [])}
    if (type(count) is not int or not 0 < count <= 65535 * 3 or count % 3
            or draw.get("indexed") is not True
            or any(draw.get(k) != v for k, v in
                   {"mode": 4, "first": 0, "instance_count": 1, "base_vertex": 0}.items())
            or state.get("color_mask") != [1, 1, 1, 1]
            or (state.get("depth") or {}).get("test") is not True
            or (state.get("depth") or {}).get("write") is not True
            or (state.get("depth") or {}).get("func") != "0x203"
            or (state.get("polygon") or {}).get("mode") != ["0x1b02", "0x1b02"]
            or (state.get("polygon") or {}).get("offset_fill") is not False
            or any((state.get(k) or {}).get(field) is not False for k, field in
                   (("blend", "enabled"), ("logic_op", "enabled"),
                    ("scissor", "enabled"), ("alpha_test", "enabled"), ("stencil", "test")))
            or sources != EVENT1_SHADER_SHA256):
        return 0
    indices = event.get("indices") or {}
    if (indices.get("first_index") != 0 or indices.get("count") != count
            or indices.get("range_valid") is not True or indices.get("bytes") != 2 * count):
        return 0
    return count // 3


def _side_witness(row: dict | None, *, triangles: int, before: list | None,
                  after: list, depth: tuple[int, int] | None = None,
                  opengl: bool = False) -> str:
    if row is None:
        return "missing_observation"
    if not triangles:
        return "unsupported_write_or_draw_state"
    if row["sample"] != 0:
        return "unsupported_sample"
    if row["hits"] == 0:
        if opengl:
            return ("depth_rejected" if row["hits_without_depth"] > 0
                    else "no_coverage_or_discard")
        return "no_passing_sample"
    if row["hits"] != 1:
        return "overlap"
    if not 0 <= row["primitive"] < triangles:
        return "primitive_unresolved"
    if not opengl and row["selected_hits"] != 1:
        return "gl_primitive_not_observed"
    if not row["survived"]:
        return "instrumented_fragment_did_not_write"
    if before is None or before == after:
        return "normal_color_write_unproven"
    if row["control_rgb"] != after:
        return "instrumented_color_differs"
    if opengl and (depth is None or depth[0] != 0x3f800000
                   or (row["pre_depth_bits"], row["control_depth_bits"]) != depth
                   or row["pre_rgb"] != before):
        return "original_pre_or_post_depth_unbound"
    return "verified_writer"


def _sparse_pair(gl, metal, *, triangles, before, after, depth) -> dict:
    decisions = {
        side: _side_witness(row, triangles=triangles, before=before.get(side),
                            after=after[side], depth=depth, opengl=side == "opengl")
        for side, row in (("opengl", gl), ("metal", metal))
    }
    both_writers = all(value == "verified_writer" for value in decisions.values())
    verified = both_writers and (gl["primitive"], gl["sample"]) == (metal["primitive"],
                                                                    metal["sample"])
    return {
        "verified": verified,
        "status": "verified" if verified else "unresolved",
        "decisions": decisions,
        "different_primitive_or_sample": both_writers and not verified,
        "opengl": gl,
        "metal": metal,
    }


def _sealed_depth(frame_dir: Path, record: dict, event: dict, width: int,
                  height: int) -> bytes | None:
    plane = record.get("depth") or {}
    identity = _attachment(event.get("target") or {}, "depth")
    if (identity is None or _attachment(record.get("target") or {}, "depth") != identity
            or plane.get("status") != "captured" or plane.get("format") != "f32"
            or plane.get("bytes") != width * height * 4 or not plane.get("file")
            or any(plane.get(k) != v for k, v in (("width", width), ("height", height)))):
        return None
    body = (frame_dir / "blobs" / plane["file"]).read_bytes()
    return body if len(body) == plane["bytes"] and _sha256(body) == plane.get("sha256") else None


def _sparse_localization(report, frame_dir, replay_dir, trace, seal, event,
                         reference, before, replay, checkpoint_files, left, right,
                         mask, matched):
    width, height = reference["width"], reference["height"]
    index_sha = (event.get("indices") or {}).get("sha256")
    gl, gl_rows = load_sparse(frame_dir, trace["uv2_fragments"], "opengl", seal,
                              width, height, index_sha)
    binding = replay.get("uv2_probe") or {}
    if binding.get("file") == SPARSE_FILE:
        if report["candidate"]["compile_mode_provenance"] != "verified":
            raise ProbeError("Metal sparse observations require a current replay recipe")
        metal, metal_rows = load_sparse(replay_dir / "uv2-probe", binding, "metal", seal,
                                       width, height, index_sha)
        if gl["selection"] != metal["selection"]:
            raise ProbeError("the sealed GL and Metal sparse coordinates differ")
    else:
        metal, metal_rows = {"status": "missing", "reason": binding.get("reason")}, {}
    before_rgb = {}
    gl_before = before.get("color")
    if gl_before is not None:
        before_rgb["opengl"] = _image(gl_before["path"], gl_before["sha256"],
                                      width, height, "sealed pre-draw RGB8")
        candidates = [item for item in replay["checkpoints"]
                      if item.get("event") == 1 and item.get("target") == "433"
                      and item.get("resource") == "tex:433" and item.get("timing") == "pre_event"]
        if len(candidates) == 1:
            checkpoint = candidates[0]
            if (checkpoint.get("reference_sha256") != gl_before["sha256"]
                    or checkpoint.get("ok") is not True or checkpoint.get("pixel_format") != "BGRA8"
                    or checkpoint.get("origin", "lower_left") != "lower_left"
                    or any(checkpoint.get(k) != reference[k] for k in ("width", "height"))):
                raise ProbeError("Metal sparse pre-draw checkpoint disagrees with the seal")
            digest = checkpoint_files[checkpoint["file"]]
            before_rgb["metal"] = _image(replay_dir / checkpoint["file"], digest,
                                         width, height, "Metal pre-draw RGB8")
    pre_depth = _sealed_depth(frame_dir, post_observation(trace, 0, "tex:433"),
                              event, width, height) if "depth" in before else None
    post_depth = _sealed_depth(frame_dir, post_observation(trace, 1, "tex:433"),
                               event, width, height)
    triangles = _sparse_state(event)
    residuals, controls = set(mask), set(matched)
    for x, y in gl["selection"]:
        if not (0 <= x < width and 0 <= y < height):
            report["pixels"].append({"x": x, "y": y, "origin": "lower_left",
                                     "status": "inconclusive",
                                     "reason": "coordinate_outside_target"})
            continue
        rgb = {side: list(map(int, image[height - 1 - y, x, :3]))
               for side, image in (("opengl", left), ("metal", right))}
        prior = {side: list(map(int, image[height - 1 - y, x, :3]))
                 for side, image in before_rgb.items()}
        offset = (y * width + x) * 4
        depth = ((_f32_bits(pre_depth, offset), _f32_bits(post_depth, offset))
                 if pre_depth is not None and post_depth is not None else None)
        pair = _sparse_pair(gl_rows.get((x, y)), metal_rows.get((x, y)), triangles=triangles,
                            before=prior, after=rgb, depth=depth)
        stages = {"rgb8": {side: dict(zip("RGB", value)) for side, value in rgb.items()}}
        for side in ("opengl", "metal"):
            row = pair[side]
            if row is not None and row["survived"]:
                stages.setdefault("uv2", {})[side] = dict(zip(("x", "y"), row["uv2_bits"]))
            if pair["decisions"][side] == "verified_writer":
                stages.setdefault("fragment_coverage", {})[side] = {
                    "covered": True, "primitive": row["primitive"], "sample": row["sample"]}
        report["pixels"].append({
            "x": x, "y": y, "origin": "lower_left",
            "selection": ("residual" if (x, y) in residuals else
                          "rgb_match_control" if (x, y) in controls else "saved_coordinate"),
            **compare_stages(stages, attribution=pair, provenance_valid=True),
            "fragment_correspondence": pair,
        })
        if pair["verified"]:
            report["pixels"][-1]["stages"]["uv2"]["kind"] = {
                "opengl": "instrumented_original_shader",
                "metal": "instrumented_replay_shader",
            }
        else:
            report["pixels"][-1]["status"] = "inconclusive"
    verified = sum(row.get("fragment_correspondence", {}).get("verified", False)
                   for row in report["pixels"])
    report["sparse_observations"] = {
        "opengl": {"sha256": trace["uv2_fragments"]["sha256"],
                   "status": gl["status"], "reason": gl.get("reason")},
        "metal": {"sha256": binding.get("sha256"), "status": metal["status"],
                  "reason": metal.get("reason")},
        "selected": len(gl["selection"]), "verified": verified,
        "fresh_residuals_outside_selection": len(residuals - set(map(tuple, gl["selection"]))),
    }
    all_verified = verified == len(gl["selection"])
    report["fragment_attribution"] = {
        "verified": all_verified, "status": "verified" if all_verified else "unresolved"}
    report["fragment_attribution_verified"] = all_verified
    if all_verified:
        report["missing"].remove("contributing_fragment_identity_and_coverage")
        uv2_statuses = {pixel["stages"]["uv2"]["status"] for pixel in report["pixels"]}
        if uv2_statuses == {"exact"}:
            report.update(next_observation="texture_samples", first_unresolved_stage="texture_samples")
        elif uv2_statuses <= {"exact", "different"}:
            report.update(next_observation="vertex_outputs_and_interpolation",
                          first_unresolved_stage=None)
        else:
            report.update(next_observation="uv2", first_unresolved_stage="uv2")
    return "correspondence_verified" if all_verified else "inconclusive"


def _event1_checkpoint(replay_dir: Path, seal: dict, trace: dict,
                       reference: dict, renderer_sha256: str | None) -> tuple[dict, dict, dict, dict]:
    verified = gfx_replay_case.verify_replay_producer_receipt(replay_dir, seal)
    if verified["status"] != "verified":
        raise ProbeError("; ".join(verified["problems"]) or "missing producer receipt")
    receipt = verified["receipt"]
    if any(receipt["capture"].get(k) != seal.get(k) for k in
           ("manifest_sha256", "reference_sha256")):
        raise ProbeError("candidate receipt names a different sealed manifest or reference")
    if receipt["producer"]["executable_sha256"] != renderer_sha256:
        raise ProbeError("candidate renderer does not match the pinned executable digest")
    body = (replay_dir / "replay.json").read_bytes()
    if _sha256(body) != receipt["result"]["replay_json_sha256"]:
        raise ProbeError("replay.json changed during verification")
    replay = json.loads(body)
    if (gfx_independent_replay.full_frame_completion_problems(replay)
            or replay.get("events") != len(trace["_events"])):
        raise ProbeError("candidate replay did not complete the sealed frame")
    matches = [item for item in replay["checkpoints"]
               if item.get("event") == 1 and item.get("resource") == "tex:433"
               and item.get("timing") == "post_event"]
    if len(matches) != 1:
        raise ProbeError("candidate receipt must bind one post-event tex:433 checkpoint")
    checkpoint = matches[0]
    if (checkpoint.get("ok") is not True or checkpoint.get("target") != "433"
            or checkpoint.get("pixel_format") != "BGRA8"
            or checkpoint.get("origin", "lower_left") != "lower_left"
            or checkpoint.get("reference_sha256") != reference["sha256"]
            or any(checkpoint.get(k) != reference[k] for k in ("width", "height"))):
        raise ProbeError("candidate checkpoint does not name the sealed RGB8 observation")
    checkpoint_files = {item["file"]: item["sha256"]
                        for item in receipt["result"]["checkpoint_files"]}
    producer = {**receipt["producer"],
                "compile_mode_provenance": verified["compile_mode_provenance"]}
    image = {"path": replay_dir / checkpoint["file"],
             "sha256": checkpoint_files[checkpoint["file"]]}
    return image, producer, replay, checkpoint_files


def localize(frame_dir: Path, replay_dir: Path | None = None, *,
             renderer_sha256: str | None = None) -> dict:
    """Read the existing event-1 artifacts without executing either renderer."""
    report = {"schema": "gfx-event1-stage-localization/v1", "identity": dict(EVENT1),
              "status": "invalid_provenance", "pixels": []}
    try:
        trace, seal, event = frame_context(frame_dir, 1)
        if gfx_observation.authority(trace, seal) != "authentic":
            raise ProbeError("the source seal does not contain authentic OpenGL evidence")
        report["capture"] = gfx_independent_replay.capture_identity(seal, trace)
        reference, before = _event1_images(frame_dir, trace, event)
        attribution = fragment_attribution(event.get("state"))
        report["fragment_attribution"] = attribution
        report["fragment_attribution_verified"] = attribution["verified"]
        report["next_observation"] = "contributing_fragment_identity_and_coverage"
        report["first_unresolved_stage"] = "fragment_coverage"
        pre_color = before.get("color")
        missing_predecessor = [f"original_pre_{plane}" for plane in ("color", "depth")
                               if plane not in before]
        report["missing"] = [*missing_predecessor, report["next_observation"]]
        report["opengl"] = {
            "post_rgb8": {**reference, "path": str(reference["path"])},
            "pre_color": {**pre_color, "path": str(pre_color["path"])} if pre_color else None,
            "pre_depth": before.get("depth"),
            "predecessor": {"event": 0, "resource": "tex:433",
                            "status": "missing_evidence" if missing_predecessor else "observed",
                            "missing": missing_predecessor},
        }
        if replay_dir is None:
            report["missing"].append("candidate_event1_rgb8")
            return {**report, "status": "missing_evidence"}
        image, producer, replay, checkpoint_files = _event1_checkpoint(
            replay_dir, seal, trace, reference, renderer_sha256)
        width, height = reference["width"], reference["height"]
        left = _image(reference["path"], reference["sha256"], width, height, "sealed RGB8")
        right = _image(image["path"], image["sha256"], width, height, "candidate RGB8")
        mask = _differences(left, right)
        residuals = set(mask)
        matched = []
        if pre_color is not None:
            before_rgb = _image(pre_color["path"], pre_color["sha256"], width, height,
                                "sealed predecessor RGB8")
            changed = _diff_mask(before_rgb, left)
            matched = [(x, y) for x, y in _neighborhood(mask, width, height, 1)
                       if (x, y) not in residuals and changed[height - 1 - y, x]][:CONTROL_PIXELS]
        report["candidate"] = {**producer, **image, "path": str(image["path"])}
        report["mask_pixels"] = len(mask)
        report["matched_control_pixels"] = len(matched)
        if "uv2_fragments" in trace:
            report["status"] = _sparse_localization(
                report, frame_dir, replay_dir, trace, seal, event, reference, before,
                replay, checkpoint_files, left, right, mask, matched)
            return report
        for x, y in mask + matched:
            rgb = {side: dict(zip("RGB", map(int, array[height - 1 - y, x, :3])))
                   for side, array in (("opengl", left), ("metal", right))}
            report["pixels"].append({
                "x": x, "y": y, "origin": "lower_left",
                "selection": "residual" if (x, y) in residuals else "rgb_match_control",
                **compare_stages({"rgb8": rgb}, attribution=attribution,
                                 provenance_valid=True),
            })
        report["status"] = "missing_evidence"
    except MissingObservation as exc:
        report.update(status="missing_evidence", missing=["original_event1_rgb8"],
                      reason=str(exc))
    except (ProbeError, OSError, ValueError) as exc:
        report["reason"] = str(exc)
    return report


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    parser = argparse.ArgumentParser(prog="gfx_uv2_probe")
    sub = parser.add_subparsers(dest="command", required=True)
    command = sub.add_parser("compare", help="compare GL and Metal uv2 probes")
    command.add_argument("--gl", required=True)
    command.add_argument("--metal", required=True)
    command.add_argument("--frame", required=True)
    command.add_argument("--neighborhood", type=int, default=1)
    command.add_argument("--out", default=None)
    offline = sub.add_parser("localize", help="inspect sealed event-1 numerical evidence")
    offline.add_argument("--frame", type=Path, required=True)
    offline.add_argument("--replay", type=Path)
    offline.add_argument("--renderer-sha256",
                         help="expected executable digest to compare with the producer receipt")
    args = parser.parse_args(argv)
    if args.command == "localize":
        if bool(args.replay) != bool(args.renderer_sha256):
            parser.error("--replay and --renderer-sha256 must be supplied together")
        report = localize(args.frame, args.replay, renderer_sha256=args.renderer_sha256)
        print(json.dumps(report, indent=2, allow_nan=False))
        return int(report["status"] == "invalid_provenance")
    if not 0 <= args.neighborhood <= NEIGHBORHOOD_MAX:
        parser.error("--neighborhood is out of range")
    try:
        report = run(args)
    except ProbeError as exc:
        print(json.dumps({"status": "error", "reason": str(exc)}, indent=2))
        return 1
    print(json.dumps({"status": "ok", "summary": report["summary"],
                      "conclusion": report["conclusion"],
                      "out": str(Path(args.out or args.gl) / "comparison.json")},
                     indent=2, allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
