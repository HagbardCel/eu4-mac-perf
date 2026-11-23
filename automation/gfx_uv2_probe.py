#!/usr/bin/env python3
"""Numerical comparison for the opt-in event-1 uv2 diagnostic probe.

Consumes the two diagnostic descriptors produced by the original-OpenGL capture
(``gfx-uv2-probe/v1``, side ``opengl``) and the Metal replay (side ``metal``),
the captured frame they were produced from, and the existing divergent-pixel
mask.  It reports raw float32 bits, numeric values, signed differences, ULP
differences, pixel coordinates and orientation for every compared pixel.  It
does not produce a similarity score.

The probe values are diagnostic evidence only.  A difference is attributable to
the original OpenGL numerical behaviour only when the descriptor proves the
same draw inputs and the original-shader control reproduces the authentic
post-event colour output; both checks are reported explicitly.

    python3 -m automation.gfx_uv2_probe compare \
        --gl <capture>/uv2-probe \
        --metal <capture>/metal/uv2-probe \
        --frame <capture>/frame \
        --mask-gl <seal-blob> --mask-metal <seal-metal-event1> \
        --out <capture>/uv2-probe
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import struct
import sys
from pathlib import Path

SCHEMA = "gfx-uv2-probe/v1"
COMPARISON_SCHEMA = "gfx-uv2-probe-comparison/v1"
MAX_MASK_PIXELS = 4096


class ProbeError(RuntimeError):
    pass


def _sha256_bytes(body: bytes) -> str:
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
    """Loads one diagnostic descriptor and validates its declared layout."""
    directory = Path(directory)
    descriptor_path = directory / "uv2-probe.json"
    if not descriptor_path.is_file():
        raise ProbeError(f"missing probe descriptor: {descriptor_path}")
    descriptor = _read_json(descriptor_path)
    if descriptor.get("schema") != SCHEMA:
        raise ProbeError(f"unexpected probe schema in {descriptor_path}")
    side = descriptor.get("side")
    if side not in ("opengl", "metal"):
        raise ProbeError(f"unknown probe side {side!r} in {descriptor_path}")
    if descriptor.get("status") != "ok":
        raise ProbeError(
            f"{side} probe did not produce numbers: "
            f"{descriptor.get('status')} ({descriptor.get('reason', '')})"
        )
    if descriptor.get("pixel_format") != "RGBA32F":
        raise ProbeError(f"{side} probe is not RGBA32F")
    if descriptor.get("origin") != "lower_left":
        raise ProbeError(f"{side} probe origin is not lower_left")
    width = descriptor.get("width")
    height = descriptor.get("height")
    if not isinstance(width, int) or not isinstance(height, int) or width <= 0 or height <= 0:
        raise ProbeError(f"{side} probe dimensions are invalid")
    expected_stride = width * 16
    if descriptor.get("row_stride") != expected_stride:
        raise ProbeError(
            f"{side} probe row_stride {descriptor.get('row_stride')} != "
            f"{expected_stride}"
        )
    raw_path = directory / str(descriptor.get("raw_file") or "")
    if not raw_path.is_file():
        raise ProbeError(f"missing {side} probe payload: {raw_path}")
    raw = raw_path.read_bytes()
    expected_bytes = width * height * 16
    if len(raw) != expected_bytes:
        raise ProbeError(
            f"{side} probe payload {len(raw)} bytes != {expected_bytes}"
        )
    if descriptor.get("raw_bytes") != len(raw):
        raise ProbeError(f"{side} probe declares a different payload size")
    digest = _sha256_bytes(raw)
    if descriptor.get("raw_sha256") != digest:
        raise ProbeError(f"{side} probe payload digest does not match")
    return {
        "directory": directory,
        "descriptor": descriptor,
        "path": raw_path,
        "raw": raw,
        "sha256": digest,
        "width": width,
        "height": height,
        "side": side,
    }


def _f32_bits(raw: bytes, offset: int) -> int:
    return struct.unpack_from("<I", raw, offset)[0]


def _bits_float(bits: int) -> float:
    return struct.unpack("<f", struct.pack("<I", bits))[0]


def _ordered_bits(bits: int) -> int:
    """Maps float32 bits to a monotonic integer for ULP differences."""
    if bits & 0x80000000:
        return -(bits & 0x7FFFFFFF)
    return bits


def _ulp(a_bits: int, b_bits: int) -> int:
    return abs(_ordered_bits(a_bits) - _ordered_bits(b_bits))


def _sample(probe: dict, x: int, y: int) -> list[int]:
    offset = (y * probe["width"] + x) * 16
    return [_f32_bits(probe["raw"], offset + 4 * component) for component in range(4)]


def _load_bgra(path: Path, width: int, height: int, label: str) -> dict:
    path = Path(path)
    try:
        body = path.read_bytes()
    except OSError as exc:
        raise ProbeError(f"unreadable {label} image {path}: {exc}") from exc
    if len(body) != width * height * 4:
        raise ProbeError(
            f"{label} image {len(body)} bytes != {width}x{height}x4"
        )
    return {
        "path": str(path),
        "sha256": _sha256_bytes(body),
        "width": width,
        "height": height,
        "pixel_format": "BGRA8",
        "origin": "lower_left",
        "body": body,
    }


def divergent_pixels(left: dict, right: dict, *, scope: str = "RGB") -> list[dict]:
    """Exact per-pixel colour differences between two BGRA8 images."""
    if left["width"] != right["width"] or left["height"] != right["height"]:
        raise ProbeError("mask images have different extents")
    width = left["width"]
    height = left["height"]
    channels = 3 if scope == "RGB" else 4
    pixels = []
    a = left["body"]
    b = right["body"]
    for index in range(width * height):
        offset = index * 4
        differences = [
            abs(a[offset + channel] - b[offset + channel])
            for channel in range(channels)
        ]
        if max(differences) != 0:
            pixels.append(
                {
                    "x": index % width,
                    "y": index // width,
                    "gl_bgra": list(a[offset:offset + 4]),
                    "metal_bgra": list(b[offset:offset + 4]),
                    "channel_differences": differences,
                    "max_channel_error": max(differences),
                }
            )
            if len(pixels) > MAX_MASK_PIXELS:
                raise ProbeError(
                    f"mask exceeds {MAX_MASK_PIXELS} divergent pixels; "
                    "refusing an unbounded comparison"
                )
    return pixels


def _reference_color(frame_dir: Path, event: int, texture: int) -> dict | None:
    """Returns the authentic post-event colour observation from the capture."""
    observations = Path(frame_dir) / "prefix-observations.jsonl"
    if not observations.is_file():
        return None
    resource = f"tex:{texture}"
    for line in observations.read_text().splitlines():
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        if (
            record.get("event") == event
            and record.get("timing") == "post_event"
            and record.get("resource") == resource
        ):
            color = record.get("color") or {}
            name = color.get("file")
            if not name:
                return None
            path = Path(frame_dir) / "blobs" / str(name)
            if not path.is_file():
                return None
            return {
                "path": str(path),
                "sha256": color.get("sha256") or "",
                "width": record.get("width"),
                "height": record.get("height"),
                "pixel_format": record.get("pixel_format") or "",
                "origin": record.get("origin") or "",
                "body": path.read_bytes(),
            }
    return None


def control_correspondence(control: dict, reference: dict) -> dict:
    """Exact RGB comparison of the diagnostic control with the authentic output."""
    if control["width"] != reference["width"] or control["height"] != reference["height"]:
        return {
            "status": "mismatched_extent",
            "match": False,
            "control": {"path": control["path"], "sha256": control["sha256"]},
            "reference": {"path": reference["path"], "sha256": reference["sha256"]},
        }
    a = control["body"]
    b = reference["body"]
    mismatched = 0
    max_error = 0
    first = []
    for index in range(control["width"] * control["height"]):
        offset = index * 4
        differences = [abs(a[offset + c] - b[offset + c]) for c in range(3)]
        error = max(differences)
        if error != 0:
            mismatched += 1
            if len(first) < 16:
                first.append(
                    {
                        "x": index % control["width"],
                        "y": index // control["width"],
                        "control_bgra": list(a[offset:offset + 4]),
                        "reference_bgra": list(b[offset:offset + 4]),
                        "max_channel_error": error,
                    }
                )
        max_error = max(max_error, error)
    return {
        "status": "compared",
        "match": mismatched == 0,
        "scope": "RGB",
        "pixels": control["width"] * control["height"],
        "mismatched_pixels": mismatched,
        "max_channel_error": max_error,
        "first_differences": first,
        "control": {
            "path": control["path"],
            "sha256": control["sha256"],
            "pixel_format": control["pixel_format"],
            "origin": control["origin"],
        },
        "reference": {
            "path": reference["path"],
            "sha256": reference["sha256"],
            "pixel_format": reference["pixel_format"],
            "origin": reference["origin"],
        },
    }


def _load_event(frame_dir: Path, event: int) -> dict:
    events_path = Path(frame_dir) / "frame-events.jsonl"
    if not events_path.is_file():
        raise ProbeError(f"missing captured event stream: {events_path}")
    for line in events_path.read_text().splitlines():
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        if record.get("kind") == "draw" and record.get("index") == event:
            return record
    raise ProbeError(f"event {event} is not a captured draw in {frame_dir}")


def input_identity(probe: dict, event: dict) -> dict:
    """Checks the probe descriptor against the captured event's exact inputs."""
    descriptor = probe["descriptor"]
    checks = {}
    problems = []
    program = event.get("program") or {}
    target = event.get("target") or {}
    color = target.get("color_attachment") or {}
    observed = {
        "event": event.get("index"),
        "program": program.get("name"),
        "framebuffer": target.get("framebuffer"),
        "texture": color.get("name"),
        "internal_format": color.get("internal_format"),
        "draw": event.get("draw"),
    }
    checks["program"] = descriptor.get("program") == observed["program"]
    if not checks["program"]:
        problems.append("program")
    probe_target = descriptor.get("target") or {}
    probe_framebuffer = probe_target.get("framebuffer")
    probe_texture = probe_target.get("texture")
    if probe_texture is None:
        probe_texture = (probe_target.get("color_attachment") or {}).get("name")
    checks["framebuffer"] = probe_framebuffer == observed["framebuffer"]
    if not checks["framebuffer"]:
        problems.append("framebuffer")
    checks["texture"] = probe_texture == observed["texture"]
    if not checks["texture"]:
        problems.append("texture")
    draw = event.get("draw") or {}
    probe_draw = descriptor.get("draw") or {}
    checks["draw"] = (
        probe_draw.get("count") == draw.get("count")
        and probe_draw.get("mode") == draw.get("mode")
        and bool(probe_draw.get("indexed")) == bool(draw.get("indexed"))
    )
    if not checks["draw"]:
        problems.append("draw")

    shader_by_stage = {
        stage.get("stage"): stage.get("sha256")
        for stage in program.get("shaders") or []
    }
    sources = descriptor.get("sources") or {}
    checks["vertex_source"] = (
        sources.get("vertex_sha256") == shader_by_stage.get("vertex")
    )
    if not checks["vertex_source"]:
        problems.append("vertex_source")
    checks["fragment_source"] = (
        sources.get("fragment_sha256") == shader_by_stage.get("pixel")
    )
    if not checks["fragment_source"]:
        problems.append("fragment_source")

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
    if not checks["uniforms"]:
        problems.append("uniforms")

    captured_attributes = {
        attribute.get("name"): attribute.get("sha256")
        for attribute in event.get("attributes") or []
    }
    probe_attributes = {
        attribute.get("name"): attribute.get("sha256")
        for attribute in descriptor.get("attributes") or []
    }
    checks["attributes"] = (
        captured_attributes == probe_attributes
        and all(
            attribute.get("digest_ok", True)
            for attribute in descriptor.get("attributes") or []
        )
    )
    if not checks["attributes"]:
        problems.append("attributes")

    indices = event.get("indices") or {}
    probe_indices = descriptor.get("indices") or {}
    checks["indices"] = (
        probe_indices.get("sha256") == indices.get("sha256")
        and probe_indices.get("count") == indices.get("count")
    )
    if not checks["indices"]:
        problems.append("indices")
    return {
        "match": not problems,
        "problems": problems,
        "checks": checks,
        "observed": observed,
    }


def compare(
    gl: dict,
    metal: dict,
    *,
    gl_identity: dict,
    metal_identity: dict,
    control: dict,
    mask_pixels: list[dict],
    neighborhood: int,
) -> dict:
    width = gl["width"]
    height = gl["height"]
    if (width, height) != (metal["width"], metal["height"]):
        raise ProbeError("the OpenGL and Metal probe extents differ")
    if mask_pixels:
        if any(
            not 0 <= pixel["x"] < width or not 0 <= pixel["y"] < height
            for pixel in mask_pixels
        ):
            raise ProbeError("a mask pixel is outside the probe extent")
    coordinates = set()
    for pixel in mask_pixels:
        for dy in range(-neighborhood, neighborhood + 1):
            for dx in range(-neighborhood, neighborhood + 1):
                x = pixel["x"] + dx
                y = pixel["y"] + dy
                if 0 <= x < width and 0 <= y < height:
                    coordinates.add((x, y))
    mask_set = {(pixel["x"], pixel["y"]) for pixel in mask_pixels}
    entries = []
    for x, y in sorted(coordinates, key=lambda item: (item[1], item[0])):
        gl_bits = _sample(gl, x, y)
        metal_bits = _sample(metal, x, y)
        gl_values = [_bits_float(bits) for bits in gl_bits]
        metal_values = [_bits_float(bits) for bits in metal_bits]
        component_names = ["x", "y", "z", "w"]
        gl_bits_hex = {name: f"0x{bits:08x}" for name, bits in zip(component_names, gl_bits)}
        metal_bits_hex = {
            name: f"0x{bits:08x}" for name, bits in zip(component_names, metal_bits)
        }
        signed = {
            name: metal_values[index] - gl_values[index]
            for index, name in enumerate(component_names)
        }
        ulps = {
            name: _ulp(gl_bits[index], metal_bits[index])
            for index, name in enumerate(component_names)
        }
        bit_equal = {
            name: gl_bits[index] == metal_bits[index]
            for index, name in enumerate(component_names)
        }
        entries.append(
            {
                "x": x,
                "y": y,
                "orientation": "lower_left",
                "mask_pixel": (x, y) in mask_set,
                "gl_bits": gl_bits_hex,
                "metal_bits": metal_bits_hex,
                "gl_values": {
                    name: gl_values[index] for index, name in enumerate(component_names)
                },
                "metal_values": {
                    name: metal_values[index]
                    for index, name in enumerate(component_names)
                },
                "signed_differences": signed,
                "ulp_differences": ulps,
                "bit_equal": bit_equal,
                "all_components_bit_equal": all(bit_equal.values()),
                "covered": {
                    "gl": gl_bits[3] == 0x3F800000,
                    "metal": metal_bits[3] == 0x3F800000,
                },
            }
        )
    mask_entries = [entry for entry in entries if entry["mask_pixel"]]
    compared = [entry for entry in mask_entries if entry["covered"]["gl"] and entry["covered"]["metal"]]
    per_component_equal = {
        name: sum(1 for entry in compared if entry["bit_equal"][name])
        for name in ["x", "y", "z", "w"]
    }
    ulp_values = [
        entry["ulp_differences"][name] for entry in compared for name in ["x", "y"]
    ]
    signed_values = [
        abs(entry["signed_differences"][name])
        for entry in compared
        for name in ["x", "y"]
    ]
    summary = {
        "mask_pixels": len(mask_pixels),
        "reported_pixels": len(entries),
        "mask_pixels_covered_by_both": len(compared),
        "mask_pixels_uncovered": len(mask_entries) - len(compared),
        "bit_for_bit_equal_xy": all(
            entry["bit_equal"]["x"] and entry["bit_equal"]["y"] for entry in compared
        ),
        "component_bit_equal_counts": per_component_equal,
        "max_ulp_difference_xy": max(ulp_values) if ulp_values else None,
        "max_abs_signed_difference_xy": max(signed_values) if signed_values else None,
    }
    return {
        "schema": COMPARISON_SCHEMA,
        "created_utc": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "inputs": {
            "gl": {
                "directory": str(gl["directory"]),
                "sha256": gl["sha256"],
                "descriptor": gl["descriptor"],
            },
            "metal": {
                "directory": str(metal["directory"]),
                "sha256": metal["sha256"],
                "descriptor": metal["descriptor"],
            },
        },
        "identity": {"gl": gl_identity, "metal": metal_identity},
        "control": control,
        "summary": summary,
        "pixels": entries,
        "conclusion": {
            "kind": "diagnostic_evidence",
            "input_identity_match": gl_identity["match"] and metal_identity["match"],
            "original_shader_control_match": control.get("match") is True,
            "xy_bit_for_bit_equal": summary["bit_for_bit_equal_xy"],
            "note": (
                "Diagnostic only. A uv2 difference is evidence about the observed "
                "draw inputs; it is not a replacement for the sealed reference."
            ),
        },
        "limits": [
            "The OpenGL diagnostic vertex stage is recompiled into a new program; "
            "the driver may compile it differently from the original program.",
            "The original-shader control validates the diagnostic pipeline "
            "(state, inputs, readback), not the probe program's instructions.",
            "The Metal probe reuses the normal pass's compiled vertex function.",
        ],
    }


def _parse_mask_json(path: Path) -> list[dict]:
    document = _read_json(path)
    pixels = document.get("pixels") if isinstance(document, dict) else document
    if not isinstance(pixels, list):
        raise ProbeError("mask JSON must contain a pixel list")
    result = []
    for item in pixels:
        if isinstance(item, dict):
            x, y = item.get("x"), item.get("y")
        elif isinstance(item, (list, tuple)) and len(item) == 2:
            x, y = item
        else:
            raise ProbeError("mask JSON pixel is not {x,y} or [x,y]")
        if not isinstance(x, int) or not isinstance(y, int):
            raise ProbeError("mask JSON pixel coordinates are not integers")
        result.append({"x": x, "y": y})
    return result


def run(args: argparse.Namespace) -> dict:
    gl = load_probe(Path(args.gl))
    metal = load_probe(Path(args.metal))
    frame_dir = Path(args.frame)
    event = gl["descriptor"].get("event")
    if metal["descriptor"].get("event") != event:
        raise ProbeError("the OpenGL and Metal probes name different events")
    target = gl["descriptor"].get("target") or {}
    texture = target.get("texture")
    if texture is None:
        texture = (target.get("color_attachment") or {}).get("name")
    if not isinstance(texture, int):
        raise ProbeError("the probe descriptor does not name its colour texture")
    captured_event = _load_event(frame_dir, event)
    gl_identity = input_identity(gl, captured_event)
    metal_identity = input_identity(metal, captured_event)

    if args.mask_json:
        mask_pixels = _parse_mask_json(Path(args.mask_json))
        mask_provenance = {"source": "json", "path": str(args.mask_json)}
    else:
        if not args.mask_gl or not args.mask_metal:
            raise ProbeError(
                "provide --mask-gl and --mask-metal (or --mask-json) naming "
                "the existing event-1 GL and Metal colour images"
            )
        mask_gl = _load_bgra(Path(args.mask_gl), gl["width"], gl["height"], "GL mask")
        mask_metal = _load_bgra(
            Path(args.mask_metal), gl["width"], gl["height"], "Metal mask"
        )
        mask_pixels = divergent_pixels(mask_gl, mask_metal)
        mask_provenance = {
            "source": "images",
            "gl": {"path": mask_gl["path"], "sha256": mask_gl["sha256"]},
            "metal": {"path": mask_metal["path"], "sha256": mask_metal["sha256"]},
            "scope": "RGB",
            "origin": "lower_left",
        }

    reference = _reference_color(frame_dir, event, int(texture))
    if reference is None:
        control = {
            "status": "unavailable",
            "match": False,
            "reason": (
                "the capture has no post-event colour observation for this "
                "event; re-capture with --prefix-end"
            ),
        }
    else:
        control_body = gl["directory"] / str(
            (gl["descriptor"].get("control") or {}).get("file") or ""
        )
        control_image = _load_bgra(control_body, gl["width"], gl["height"], "control")
        control = control_correspondence(control_image, reference)

    report = compare(
        gl,
        metal,
        gl_identity=gl_identity,
        metal_identity=metal_identity,
        control=control,
        mask_pixels=mask_pixels,
        neighborhood=int(args.neighborhood),
    )
    report["inputs"]["frame"] = {"path": str(frame_dir), "event": event}
    report["inputs"]["mask"] = mask_provenance
    out = Path(args.out) if args.out else gl["directory"]
    out.mkdir(parents=True, exist_ok=True)
    (out / "comparison.json").write_text(json.dumps(report, indent=2) + "\n")
    return report


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    parser = argparse.ArgumentParser(prog="gfx_uv2_probe")
    sub = parser.add_subparsers(dest="command", required=True)
    command = sub.add_parser("compare", help="compare GL and Metal uv2 probes")
    command.add_argument("--gl", required=True)
    command.add_argument("--metal", required=True)
    command.add_argument("--frame", required=True)
    command.add_argument("--mask-gl", default=None)
    command.add_argument("--mask-metal", default=None)
    command.add_argument("--mask-json", default=None)
    command.add_argument("--neighborhood", type=int, default=1)
    command.add_argument("--out", default=None)
    args = parser.parse_args(argv)
    if args.neighborhood < 0 or args.neighborhood > 4:
        parser.error("--neighborhood must be between 0 and 4")
    try:
        report = run(args)
    except ProbeError as exc:
        print(json.dumps({"status": "error", "reason": str(exc)}, indent=2))
        return 1
    print(
        json.dumps(
            {
                "status": "ok",
                "summary": report["summary"],
                "conclusion": report["conclusion"],
                "out": str(
                    Path(args.out or args.gl) / "comparison.json"
                ),
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
