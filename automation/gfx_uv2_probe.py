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
import struct
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from automation import (gfx_event_evidence, gfx_frame_compare,
                        gfx_observation)  # noqa: E402

SCHEMA = "gfx-uv2-probe/v1"
COMPARISON_SCHEMA = "gfx-uv2-probe-comparison/v1"
NEIGHBORHOOD_MAX = 4
MAX_MASK_PIXELS = 4096


class ProbeError(RuntimeError):
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
    raise ProbeError(
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
    observed = {
        "event": event.get("index"),
        "program": program.get("name"),
        "framebuffer": target.get("framebuffer"),
        "texture": color.get("name"),
        "internal_format": color.get("internal_format"),
        "draw": event.get("draw"),
    }
    checks["program"] = descriptor.get("program") == observed["program"]
    checks["framebuffer"] = probe_target.get("framebuffer") == observed["framebuffer"]
    checks["texture"] = probe_texture == observed["texture"]
    expected_draw = _draw(observed["draw"] or {}, observed["draw"] or {})
    actual_draw = _draw(descriptor.get("draw") or {}, observed["draw"] or {})
    checks["draw"] = actual_draw == expected_draw
    shaders = {stage.get("stage"): stage.get("sha256")
               for stage in program.get("shaders") or []}
    sources = descriptor.get("sources") or {}
    checks["vertex_source"] = sources.get("vertex_sha256") == shaders.get("vertex")
    checks["fragment_source"] = sources.get("fragment_sha256") == shaders.get("pixel")
    captured_uniforms = {}
    for uniform in program.get("uniforms") or []:
        name = uniform.get("name")
        if name is None:
            continue
        if uniform.get("type") == "sampler":
            captured_uniforms[name] = {"kind": "sampler", "unit": uniform.get("unit")}
        else:
            captured_uniforms[name] = {"sha256": uniform.get("sha256")}
    probe_uniforms = {}
    for uniform in descriptor.get("uniforms") or []:
        name = uniform.get("name")
        if name is None:
            continue
        if uniform.get("kind") == "sampler":
            probe_uniforms[name] = {"kind": "sampler", "unit": uniform.get("unit")}
        else:
            probe_uniforms[name] = {"sha256": uniform.get("sha256")}
    checks["uniforms"] = captured_uniforms == probe_uniforms
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
    return {"match": not problems, "problems": problems, "checks": checks,
            "observed": observed}


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


def compare(gl: dict, metal: dict, *, identity: dict, control: dict,
            mask: list[tuple[int, int]], neighborhood: int,
            authority: str) -> dict:
    width, height = gl["width"], gl["height"]
    if (width, height) != (metal["width"], metal["height"]):
        raise ProbeError("the OpenGL and Metal probe extents differ")
    mask_set = set(mask)
    components = ["x", "y", "z", "w"]
    entries = []
    for x, y in _neighborhood(mask, width, height, neighborhood):
        gl_bits = _sample(gl, x, y)
        metal_bits = _sample(metal, x, y)
        gl_values = [_bits_float(bits) for bits in gl_bits]
        metal_values = [_bits_float(bits) for bits in metal_bits]
        bit_equal = {name: gl_bits[i] == metal_bits[i]
                     for i, name in enumerate(components)}
        signed = {}
        for i, name in enumerate(components):
            difference = metal_values[i] - gl_values[i]
            signed[name] = (_json_number(difference)
                            if math.isfinite(gl_values[i])
                            and math.isfinite(metal_values[i]) else None)
        entries.append({
            "x": x, "y": y, "orientation": "lower_left",
            "mask_pixel": (x, y) in mask_set,
            "gl_bits": {name: f"0x{gl_bits[i]:08x}"
                        for i, name in enumerate(components)},
            "metal_bits": {name: f"0x{metal_bits[i]:08x}"
                           for i, name in enumerate(components)},
            "gl_values": {name: _json_number(gl_values[i])
                          for i, name in enumerate(components)},
            "metal_values": {name: _json_number(metal_values[i])
                             for i, name in enumerate(components)},
            "signed_differences": signed,
            "ulp_differences": {name: _ulp(gl_bits[i], metal_bits[i])
                                for i, name in enumerate(components)},
            "bit_equal": bit_equal,
            "all_components_bit_equal": all(bit_equal.values()),
            "covered": {"gl": gl_bits[3] == 0x3F800000,
                        "metal": metal_bits[3] == 0x3F800000},
        })
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
    ulps = [e["ulp_differences"][c] for e in covered for c in ("x", "y")]
    signed_abs = [abs(e["signed_differences"][c]) for e in covered
                  for c in ("x", "y")
                  if e["signed_differences"][c] is not None]
    summary = {
        "mask_pixels": coverage,
        "mask_pixels_covered_by_both": len(covered),
        "coverage_complete": complete,
        "status": status,
        "conclusive": conclusive,
        "observed_mismatch": observed_mismatch,
        "all_selected_pixels_equal": all_equal,
        "xy_bit_for_bit_equal": all_equal if conclusive else None,
        "max_ulp_difference_xy": max(ulps) if covered else None,
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
        "mask": {"pixels": [{"x": x, "y": y} for x, y in mask]},
        "summary": summary, "pixels": entries,
        "conclusion": {
            "authority": authority,
            "evidence_checks": evidence_checks,
            "conclusive": conclusive,
            "uv2_results_equal": all_equal if conclusive else None,
            "original_shader_control_match": control.get("match") is True,
            "xy_bit_for_bit_equal": all_equal if conclusive else None,
        },
    }

def run(args: argparse.Namespace) -> dict:
    gl = load_probe(Path(args.gl))
    metal = load_probe(Path(args.metal))
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
                     neighborhood=int(args.neighborhood), authority=authority)
    report["conclusion"]["trusted_evidence"] = (
        trace.get("_trusted_evidence") is not None)
    report["inputs"]["frame"] = {
        "path": str(frame_dir), "event": event_index,
        "session_id": seal.get("session_id"),
        "capture_frame_id": seal.get("capture_frame_id"),
        "manifest_sha256": seal.get("manifest_sha256"),
        "reference_sha256": seal.get("reference_sha256")}
    report["mask"]["observation"] = {
        "path": str(reference["path"]), "sha256": reference["sha256"],
        "event": reference["event"], "timing": reference["timing"],
        "resource": reference["resource"]}
    report["mask"]["metal_event_target"] = {
        "path": str(color_path), "sha256": metal_mask["sha256"]}
    out = Path(args.out) if args.out else gl["directory"]
    out.mkdir(parents=True, exist_ok=True)
    (out / "comparison.json").write_text(
        json.dumps(report, indent=2, allow_nan=False) + "\n")
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
    args = parser.parse_args(argv)
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
