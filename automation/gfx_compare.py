#!/usr/bin/env python3
"""Backend-independent differential comparison for captured draws.

    python3 -m automation.gfx_compare compare \
        --reference <dir-or-json> --candidate <dir-or-json> \
        --policy <policy.json> --out <report-dir>

The comparator verifies evidence identity before it looks at pixels: both
inputs must describe the same logical draw, declare compatible target geometry
and pixel layout, prove GPU completion, and ship a complete payload whose
digest matches its declaration. Only established representation differences
(an explicitly recorded vertical origin flip) are normalized; nothing is
rescaled, cropped, blurred or thresholded into agreement.

Verdicts are `pass`, `fail`, `invalid` or `unavailable`. A missing payload,
identity or GPU proof is never `pass`.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import struct
import sys
import zlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

EXIT_PASS = 0
EXIT_FAIL = 1
EXIT_INVALID = 2
EXIT_UNAVAILABLE = 3

POLICY_SCHEMA = "gfx-compare-policy/v1"
REPORT_SCHEMA = "gfx-compare-report/v1"
CAPTURE_SCHEMA = "gfx-capture/v1"

SUPPORTED_BACKENDS = ("opengl", "metal", "cpu")
PIXEL_FORMATS = ("BGRA8", "RGBA8")


class InputError(RuntimeError):
    def __init__(self, kind: str, detail: str):
        super().__init__(detail)
        self.kind = kind
        self.detail = detail


@dataclass
class Capture:
    backend: str
    provenance: dict
    draw: dict
    target: dict
    gpu: dict
    capture: dict
    state: dict
    presentation: dict | None
    origin: str
    pixel_format: str
    width: int
    height: int
    row_stride: int
    payload: bytes
    raw_path: Path
    declared_sha256: str
    source_schema: str
    source_path: Path
    extra: dict = field(default_factory=dict)


def _read_json(path: Path) -> dict:
    try:
        value = json.loads(path.read_text())
    except FileNotFoundError as exc:
        raise InputError("missing", f"{path} does not exist") from exc
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise InputError("invalid", f"{path} is not readable JSON: {exc}") from exc
    if not isinstance(value, dict):
        raise InputError("invalid", f"{path} is not a JSON object")
    return value


def load_policy(path: Path) -> dict:
    policy = _read_json(path)
    if policy.get("schema") != POLICY_SCHEMA:
        raise InputError("invalid", f"policy schema is not {POLICY_SCHEMA}")
    for key in (
        "reference_backend",
        "candidate_backends",
        "max_channel_error",
        "max_mismatched_fraction",
        "max_mean_abs_error",
    ):
        if key not in policy:
            raise InputError("invalid", f"policy is missing {key}")
    if policy["reference_backend"] not in SUPPORTED_BACKENDS:
        raise InputError("invalid", "policy reference backend is unsupported")
    backends = policy["candidate_backends"]
    if not isinstance(backends, list) or not backends:
        raise InputError("invalid", "policy candidate_backends must be a list")
    if any(item not in SUPPORTED_BACKENDS for item in backends):
        raise InputError("invalid", "policy names an unsupported candidate backend")
    value = policy["max_channel_error"]
    if not isinstance(value, int) or value < 0 or value > 255:
        raise InputError("invalid", "policy max_channel_error must be an integer 0..255")
    value = policy["max_mean_abs_error"]
    if not isinstance(value, (int, float)) or not 0.0 <= float(value) <= 255.0:
        raise InputError("invalid", "policy max_mean_abs_error must be 0..255")
    fraction = policy["max_mismatched_fraction"]
    if not isinstance(fraction, (int, float)) or not 0.0 <= float(fraction) <= 1.0:
        raise InputError("invalid", "policy max_mismatched_fraction must be 0..1")
    allowed = policy.get("allow_origin_normalization") or []
    if any(item not in ("vertical_flip",) for item in allowed):
        raise InputError("invalid", "policy allows an unknown normalization")
    policy.setdefault("require_same_event", True)
    policy.setdefault("require_same_extent", True)
    policy.setdefault("require_same_pixel_format", True)
    policy.setdefault("require_gpu_completed", True)
    policy.setdefault("allow_origin_normalization", allowed)
    policy.setdefault("require_presentation", False)
    policy.setdefault("require_vertex_inputs", False)
    return policy


def resolve_json(path: Path, names: Iterable[str]) -> Path:
    if path.is_file():
        return path
    if path.is_dir():
        found = [path / name for name in names if (path / name).is_file()]
        if len(found) == 1:
            return found[0]
        if len(found) > 1:
            raise InputError(
                "invalid",
                f"{path} contains several candidate descriptors: "
                + ", ".join(item.name for item in found),
            )
        raise InputError(
            "missing",
            f"{path} has none of the expected descriptors: {', '.join(names)}",
        )
    raise InputError("missing", f"{path} does not exist")


def _load_payload(base: Path, file_name: str, declared_size: int,
                  declared_sha: str) -> tuple[bytes, Path]:
    if not isinstance(file_name, str) or not file_name:
        raise InputError("invalid", "capture does not name a raw payload file")
    path = base / file_name
    try:
        data = path.read_bytes()
    except FileNotFoundError as exc:
        raise InputError("missing", f"raw payload {path} is absent") from exc
    except OSError as exc:
        raise InputError("unavailable", f"raw payload {path} is unreadable: {exc}") from exc
    if declared_size != len(data):
        raise InputError(
            "invalid",
            f"raw payload is {len(data)} bytes, declaration says {declared_size}",
        )
    digest = hashlib.sha256(data).hexdigest()
    if not isinstance(declared_sha, str) or digest != declared_sha:
        raise InputError("invalid", "raw payload sha256 does not match its declaration")
    return data, path


def _common(payload: bytes, base: Path, pformat: str, origin: str,
            width: int, height: int, row_stride: int) -> None:
    if pformat not in PIXEL_FORMATS:
        raise InputError("invalid", f"unsupported pixel format {pformat!r}")
    if origin not in ("lower_left", "top_left"):
        raise InputError("invalid", f"unknown image origin {origin!r}")
    if not isinstance(width, int) or not isinstance(height, int) or width <= 0 or height <= 0:
        raise InputError("invalid", "target extent is invalid")
    if row_stride != width * 4:
        raise InputError("invalid", "row stride does not match width*4")
    expected = width * height * 4
    if len(payload) != expected:
        raise InputError(
            "invalid",
            f"payload is {len(payload)} bytes, {width}x{height}x4 requires {expected}",
        )


def from_gl_reference(path: Path) -> Capture:
    data = _read_json(path)
    if data.get("schema") != "gfx-gl-reference/v1":
        raise InputError("invalid", "reference is not a gfx-gl-reference/v1 descriptor")
    target = data.get("target") or {}
    capture = data.get("capture") or {}
    provenance = data.get("provenance") or {}
    draw = dict(data.get("draw") or {})
    gpu = data.get("gpu") or {}
    vertex_path = path.parent / "gl-reference-vertex.json"
    if vertex_path.is_file():
        vertex = _read_json(vertex_path)
        if vertex.get("schema") != "gfx-gl-vertex/v1":
            raise InputError("invalid", "gl-reference-vertex.json has an unexpected schema")
        raw = path.parent / str(vertex.get("raw_file") or "")
        if not raw.is_file():
            raise InputError("missing", "GL vertex payload is absent")
        payload = raw.read_bytes()
        if len(payload) != vertex.get("bytes"):
            raise InputError("invalid", "GL vertex payload size does not match")
        if hashlib.sha256(payload).hexdigest() != vertex.get("sha256"):
            raise InputError("invalid", "GL vertex payload sha256 does not match")
        draw["vertex_hex"] = payload.hex()
        draw["vertex_sha256"] = vertex.get("sha256")
        draw["vertex_source_record"] = vertex.get("source_record")
    payload, raw_path = _load_payload(
        path.parent,
        capture.get("raw_file"),
        capture.get("raw_bytes"),
        capture.get("raw_sha256"),
    )
    width, height = target.get("width"), target.get("height")
    stride = target.get("row_stride")
    pformat = target.get("pixel_format") or capture.get("pixel_format")
    origin = target.get("origin")
    _common(payload, path.parent, pformat, origin, width, height, stride)
    return Capture(
        backend="opengl",
        provenance=provenance,
        draw=draw,
        target=target,
        gpu={
            "completed": gpu.get("read_pixels") is True and gpu.get("gl_error") == 0,
            "evidence": "glFinish+glReadPixels",
            "detail": gpu,
        },
        capture=capture,
        state=data.get("state") or {},
        presentation=None,
        origin=origin,
        pixel_format=pformat,
        width=width,
        height=height,
        row_stride=stride,
        payload=payload,
        raw_path=raw_path,
        declared_sha256=capture.get("raw_sha256"),
        source_schema="gfx-gl-reference/v1",
        source_path=path,
        extra={
            "pre_draw": data.get("pre_draw"),
            "captured_utc": data.get("captured_utc"),
            "shader": data.get("shader"),
            "caps": data.get("caps"),
        },
    )


def from_metal_logical(path: Path) -> Capture:
    data = _read_json(path)
    if data.get("schema") != "gfx-metal-logical/v1":
        raise InputError("invalid", "candidate is not a gfx-metal-logical/v1 descriptor")
    target = data.get("target") or {}
    capture = data.get("capture") or {}
    provenance = data.get("provenance") or {}
    draw = data.get("draw") or {}
    gpu = data.get("gpu") or {}
    payload, raw_path = _load_payload(
        path.parent,
        capture.get("raw_file"),
        capture.get("raw_bytes"),
        capture.get("raw_sha256"),
    )
    width, height = target.get("width"), target.get("height")
    stride = target.get("row_stride")
    pformat = target.get("pixel_format") or capture.get("pixel_format")
    origin = target.get("origin")
    _common(payload, path.parent, pformat, origin, width, height, stride)
    return Capture(
        backend="metal",
        provenance=provenance,
        draw=draw,
        target=target,
        gpu={
            "completed": gpu.get("completed") is True,
            "evidence": gpu.get("evidence") or "command buffer completion",
            "detail": gpu,
        },
        capture=capture,
        state=data.get("state") or {},
        presentation=data.get("presentation"),
        origin=origin,
        pixel_format=pformat,
        width=width,
        height=height,
        row_stride=stride,
        payload=payload,
        raw_path=raw_path,
        declared_sha256=capture.get("raw_sha256"),
        source_schema="gfx-metal-logical/v1",
        source_path=path,
        extra={"shader": data.get("shader")},
    )


def from_metal_checkpoint(path: Path) -> Capture:
    data = _read_json(path)
    if data.get("schema") != "gfx-checkpoint/v1":
        raise InputError("invalid", "candidate is not a gfx-checkpoint/v1 descriptor")
    target = data.get("target") or {}
    capture = data.get("capture") or {}
    gpu = data.get("gpu") or {}
    # The M3 checkpoint never declares an image origin. On Apple GPUs the
    # texture bytes are top-left first, but assuming that would be exactly the
    # kind of unrecorded normalization this comparator refuses.
    if "origin" not in target:
        raise InputError(
            "invalid",
            "gfx-checkpoint/v1 does not declare an image origin; use a descriptor "
            "that records it rather than assuming one",
        )
    payload, raw_path = _load_payload(
        path.parent,
        capture.get("raw_file"),
        capture.get("raw_bytes"),
        capture.get("raw_sha256"),
    )
    width, height = target.get("width"), target.get("height")
    stride = target.get("row_stride")
    pformat = target.get("pixel_format")
    origin = target.get("origin")
    _common(payload, path.parent, pformat, origin, width, height, stride)
    draw = data.get("draw") or {}
    return Capture(
        backend="metal",
        provenance={
            "executable": data.get("executable"),
            "executable_sha256": data.get("executable_sha256"),
            "library_sha256": data.get("library_sha256"),
            "session": data.get("session"),
            "pid": (data.get("caller") or {}).get("pid"),
        },
        draw={
            "symbol": data.get("symbol") or "_GfxDraw",
            "caller_rva": (data.get("caller") or {}).get("rva"),
            "mode": draw.get("primitive"),
            "effective_count": draw.get("count"),
            "shader_identity": draw.get("shader_identity"),
        },
        target=target,
        gpu={
            "completed": gpu.get("completed") is True,
            "evidence": "MTLCommandBufferStatusCompleted",
            "detail": gpu,
        },
        capture=capture,
        state=data.get("observed_state") or {},
        presentation=None,
        origin=origin,
        pixel_format=pformat,
        width=width,
        height=height,
        row_stride=stride,
        payload=payload,
        raw_path=raw_path,
        declared_sha256=capture.get("raw_sha256"),
        source_schema="gfx-checkpoint/v1",
        source_path=path,
    )


ADAPTERS = {
    "reference": (("gl-reference.json",), from_gl_reference),
    "candidate": (
        ("metal-logical.json", "checkpoint-after-draw-1.json", "checkpoint-after-present-1.json"),
        None,
    ),
}


def load_capture(path: Path, role: str) -> Capture:
    if role == "reference":
        names, adapter = ADAPTERS["reference"]
        resolved = resolve_json(path, names)
        return adapter(resolved)
    if path.is_file():
        resolved = path
    else:
        names, _ = ADAPTERS["candidate"]
        resolved = resolve_json(path, names)
    data = _read_json(resolved)
    schema = data.get("schema")
    if schema == "gfx-metal-logical/v1":
        return from_metal_logical(resolved)
    if schema == "gfx-checkpoint/v1":
        return from_metal_checkpoint(resolved)
    raise InputError("invalid", f"unknown candidate descriptor schema {schema!r}")


def _canonical_rgba(capture: Capture) -> bytes:
    """Decode a declared payload into top-left RGBA using its recorded origin.

    The origin is metadata, not a transformation chosen to make a comparison
    pass: a lower-left capture is row-reversed, a top-left capture is not.
    """
    data = capture.payload
    fmt = capture.pixel_format
    width, height, stride = capture.width, capture.height, capture.row_stride
    rows = []
    for y in range(height):
        row = data[y * stride:(y + 1) * stride]
        out = bytearray(width * 4)
        if fmt == "RGBA8":
            out[:] = row
        else:  # BGRA8
            for x in range(width):
                i = x * 4
                out[i] = row[i + 2]
                out[i + 1] = row[i + 1]
                out[i + 2] = row[i]
                out[i + 3] = row[i + 3]
        rows.append(bytes(out))
    if capture.origin == "lower_left":
        rows.reverse()
    return b"".join(rows)


def _png_rgb(path: Path, width: int, height: int, rgba: bytes) -> None:
    rows = []
    for y in range(height):
        row = rgba[y * width * 4:(y + 1) * width * 4]
        rows.append(b"\x00" + bytes(row[i] for i in range(len(row)) if i % 4 != 3))
    raw = b"".join(rows)

    def chunk(kind: bytes, payload: bytes) -> bytes:
        return (
            struct.pack(">I", len(payload))
            + kind
            + payload
            + struct.pack(">I", zlib.crc32(kind + payload) & 0xFFFFFFFF)
        )

    png = (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(raw, 9))
        + chunk(b"IEND", b"")
    )
    path.write_bytes(png)


def _stats(reference: bytes, candidate: bytes, width: int, height: int,
           tolerance: int) -> dict:
    per_channel_max = [0, 0, 0, 0]
    per_channel_sum = [0, 0, 0, 0]
    mismatched = 0
    alpha_mismatched = 0
    worst = 0
    worst_index = -1
    grid = [[0] * 8 for _ in range(8)]
    total = width * height
    for index in range(total):
        base = index * 4
        deltas = [
            abs(reference[base + c] - candidate[base + c]) for c in range(4)
        ]
        local_max = max(deltas)
        for channel in range(4):
            delta = deltas[channel]
            if delta > per_channel_max[channel]:
                per_channel_max[channel] = delta
            per_channel_sum[channel] += delta
        if local_max > worst:
            worst = local_max
            worst_index = index
        if local_max > tolerance:
            mismatched += 1
            x = index % width
            y = index // width
            grid[min(7, y * 8 // height)][min(7, x * 8 // width)] += 1
        if deltas[3] > tolerance:
            alpha_mismatched += 1
    coverage = [
        {"row": row, "column": column, "pixels": grid[row][column]}
        for row in range(8)
        for column in range(8)
        if grid[row][column]
    ]
    return {
        "pixels": total,
        "max_channel_error": worst,
        "mean_abs_error": (
            sum(per_channel_sum) / (total * 4) if total else 0.0
        ),
        "per_channel_max_error": {
            "r": per_channel_max[0],
            "g": per_channel_max[1],
            "b": per_channel_max[2],
            "a": per_channel_max[3],
        },
        "per_channel_mean_error": {
            "r": per_channel_sum[0] / total if total else 0.0,
            "g": per_channel_sum[1] / total if total else 0.0,
            "b": per_channel_sum[2] / total if total else 0.0,
            "a": per_channel_sum[3] / total if total else 0.0,
        },
        "mismatched_pixels": mismatched,
        "mismatched_fraction": mismatched / total if total else 0.0,
        "alpha_mismatched_pixels": alpha_mismatched,
        "worst_pixel": None
        if worst_index < 0
        else {"x": worst_index % width, "y": worst_index // width, "error": worst},
        "failure_grid": coverage,
    }


def _difference_rgba(reference: bytes, candidate: bytes, width: int, height: int,
                     tolerance: int) -> bytes:
    out = bytearray(width * height * 4)
    for index in range(width * height):
        base = index * 4
        deltas = [abs(reference[base + c] - candidate[base + c]) for c in range(4)]
        if max(deltas[:3]) <= tolerance and deltas[3] <= tolerance:
            out[base:base + 4] = bytes((0, 0, 0, 255))
            continue
        amplified = [min(255, value * 4) for value in deltas[:3]]
        if amplified == [0, 0, 0] and deltas[3]:
            amplified = [255, 0, 255]
        out[base:base + 4] = bytes((*amplified, 255))
    return bytes(out)


def _expected_mismatch(policy: dict, capture: Capture, role: str) -> list[str]:
    expected = policy.get("expected") or {}
    if not isinstance(expected, dict):
        return ["policy expected block is malformed"]
    problems: list[str] = []
    sha = (capture.provenance or {}).get("executable_sha256")
    pinned = expected.get("executable_sha256")
    if pinned is not None and sha != pinned:
        problems.append(f"{role} executable sha256 is {sha!r}, pinned {pinned!r}")
    if role == "reference":
        program = (capture.state or {}).get("program")
        expected_program = (expected.get("reference_gl_program") or {}).get("program")
        if expected_program is not None and program != expected_program:
            problems.append("reference GL program does not match the pinned identity")
        shader = (capture.extra.get("shader") or {}) if capture.extra else {}
        expected_glsl = expected.get("reference_glsl_sha256")
        if expected_glsl is not None and shader.get("glsl_sha256") != expected_glsl:
            problems.append("reference GLSL digest does not match the pinned identity")
        expected_caps = expected.get("reference_caps_byte4")
        caps = (capture.extra.get("caps") or {}) if capture.extra else {}
        if expected_caps is not None and caps.get("byte4") != expected_caps:
            problems.append("reference GL capability byte does not match the pin")
    if role == "candidate":
        expected_msl = expected.get("candidate_msl_sha256")
        shader = (capture.extra.get("shader") or {}) if capture.extra else {}
        if expected_msl is not None and shader.get("msl_sha256") != expected_msl:
            problems.append("candidate MSL digest does not match the pinned identity")
        expected_identity = expected.get("candidate_shader_identity")
        if expected_identity is not None and \
                capture.draw.get("shader_identity") != expected_identity:
            problems.append("candidate shader identity does not match the pin")
    return problems


def compare(reference: Capture, candidate: Capture, policy: dict) -> dict:
    reasons: list[str] = []
    structural: list[str] = []
    normalization: list[str] = []

    if reference.backend != policy["reference_backend"]:
        structural.append(
            f"reference backend {reference.backend!r} is not "
            f"{policy['reference_backend']!r}"
        )
    if candidate.backend not in policy["candidate_backends"]:
        structural.append(f"candidate backend {candidate.backend!r} is not admitted")
    for capture, role in ((reference, "reference"), (candidate, "candidate")):
        sha = (capture.provenance or {}).get("executable_sha256")
        if not isinstance(sha, str) or len(sha) != 64:
            structural.append(f"{role} executable identity is missing")
        if not (capture.provenance or {}).get("executable"):
            structural.append(f"{role} executable path is missing")
    structural.extend(_expected_mismatch(policy, reference, "reference"))
    structural.extend(_expected_mismatch(policy, candidate, "candidate"))

    if policy["require_same_event"]:
        for key in ("symbol", "caller_rva", "mode", "effective_count"):
            left = reference.draw.get(key)
            right = candidate.draw.get(key)
            if left is None or right is None:
                structural.append(f"draw {key} is missing from one side")
            elif left != right:
                structural.append(f"draw {key} differs: reference {left!r}, candidate {right!r}")
    if policy["require_same_extent"]:
        if (reference.width, reference.height) != (candidate.width, candidate.height):
            structural.append(
                f"extent differs: reference {reference.width}x{reference.height}, "
                f"candidate {candidate.width}x{candidate.height}"
            )
    if policy["require_same_pixel_format"]:
        if reference.pixel_format != candidate.pixel_format:
            structural.append(
                f"pixel format differs: reference {reference.pixel_format}, "
                f"candidate {candidate.pixel_format}"
            )
    if policy["require_gpu_completed"]:
        for capture, role in ((reference, "reference"), (candidate, "candidate")):
            if capture.gpu.get("completed") is not True:
                structural.append(f"{role} GPU completion is not established")
    if policy.get("require_vertex_inputs"):
        left = (reference.draw.get("vertex_hex") or "").lower()
        right = (candidate.draw.get("vertex_hex") or "").lower()
        if not left or not right:
            structural.append("draw vertex inputs are missing from one side")
        elif left != right:
            structural.append("draw vertex inputs differ (unmatched draw inputs)")
    if policy.get("require_presentation"):
        presentation = candidate.presentation or {}
        if presentation.get("shown") is not True:
            structural.append("candidate has no successful presentation evidence")
    if reference.origin != candidate.origin:
        allowed = policy.get("allow_origin_normalization") or []
        if set((reference.origin, candidate.origin)) == {"lower_left", "top_left"} and \
                "vertical_flip" in allowed:
            normalization.append("vertical_flip")
        else:
            structural.append(
                f"origins differ ({reference.origin} vs {candidate.origin}) and no "
                "documented normalization admits it"
            )

    if structural:
        return {
            "schema": REPORT_SCHEMA,
            "status": "invalid",
            "reasons": structural,
            "normalization": normalization,
            "policy": policy.get("name"),
            "reference": _summary(reference),
            "candidate": _summary(candidate),
        }

    reference_rgba = _canonical_rgba(reference)
    candidate_rgba = _canonical_rgba(candidate)
    tolerance = int(policy["max_channel_error"])
    stats = _stats(reference_rgba, candidate_rgba, reference.width, reference.height, tolerance)
    if stats["max_channel_error"] > int(policy["max_channel_error"]):
        reasons.append(
            f"max channel error {stats['max_channel_error']} exceeds "
            f"{policy['max_channel_error']}"
        )
    if stats["mismatched_fraction"] > float(policy["max_mismatched_fraction"]):
        reasons.append(
            f"mismatched fraction {stats['mismatched_fraction']:.8f} exceeds "
            f"{policy['max_mismatched_fraction']}"
        )
    if stats["mean_abs_error"] > float(policy["max_mean_abs_error"]):
        reasons.append(
            f"mean absolute error {stats['mean_abs_error']:.8f} exceeds "
            f"{policy['max_mean_abs_error']}"
        )
    status = "pass" if not reasons else "fail"
    return {
        "schema": REPORT_SCHEMA,
        "status": status,
        "reasons": reasons,
        "normalization": normalization,
        "policy": policy.get("name"),
        "reference": _summary(reference),
        "candidate": _summary(candidate),
        "samples": stats,
    }


def _summary(capture: Capture) -> dict:
    return {
        "backend": capture.backend,
        "schema": capture.source_schema,
        "descriptor": str(capture.source_path),
        "raw_file": str(capture.raw_path),
        "raw_sha256": capture.declared_sha256,
        "extent": [capture.width, capture.height],
        "pixel_format": capture.pixel_format,
        "origin": capture.origin,
        "draw": capture.draw,
        "provenance": capture.provenance,
        "gpu": capture.gpu,
        "presentation": capture.presentation,
    }


def _write_report(out: Path, report: dict, reference: Capture | None,
                  candidate: Capture | None, tolerance: int = 0) -> None:
    out.mkdir(parents=True, exist_ok=True)
    if reference is not None and candidate is not None and report["status"] in ("pass", "fail"):
        reference_rgba = _canonical_rgba(reference)
        candidate_rgba = _canonical_rgba(candidate)
        _png_rgb(out / "reference.png", reference.width, reference.height, reference_rgba)
        _png_rgb(out / "candidate.png", candidate.width, candidate.height, candidate_rgba)
        difference = _difference_rgba(
            reference_rgba,
            candidate_rgba,
            reference.width,
            reference.height,
            tolerance,
        )
        _png_rgb(out / "difference.png", reference.width, reference.height, difference)
    (out / "report.json").write_text(json.dumps(report, indent=2) + "\n")


def command_compare(args: argparse.Namespace) -> int:
    try:
        policy = load_policy(Path(args.policy))
    except InputError as exc:
        report = {"schema": REPORT_SCHEMA, "status": "invalid", "reasons": [exc.detail]}
        _write_report(Path(args.out), report, None, None)
        print(json.dumps(report, indent=2))
        return EXIT_INVALID
    reference = candidate = None
    try:
        reference = load_capture(Path(args.reference), "reference")
        candidate = load_capture(Path(args.candidate), "candidate")
    except InputError as exc:
        status = "unavailable" if exc.kind in ("missing", "unavailable") else "invalid"
        report = {
            "schema": REPORT_SCHEMA,
            "status": status,
            "reasons": [exc.detail],
            "policy": policy.get("name"),
        }
        _write_report(Path(args.out), report, reference, candidate)
        print(json.dumps(report, indent=2))
        return EXIT_UNAVAILABLE if status == "unavailable" else EXIT_INVALID
    report = compare(reference, candidate, policy)
    # Diagnostic rendering must use the same tolerance the verdict used.
    _write_report(
        Path(args.out), report, reference, candidate, int(policy["max_channel_error"])
    )
    print(json.dumps(report, indent=2))
    return {
        "pass": EXIT_PASS,
        "fail": EXIT_FAIL,
        "invalid": EXIT_INVALID,
        "unavailable": EXIT_UNAVAILABLE,
    }[report["status"]]


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    compare_parser = sub.add_parser(
        "compare", description="compare an authenticated reference with a candidate"
    )
    compare_parser.add_argument("--reference", required=True)
    compare_parser.add_argument("--candidate", required=True)
    compare_parser.add_argument("--policy", required=True)
    compare_parser.add_argument("--out", required=True)
    compare_parser.set_defaults(func=command_compare)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
