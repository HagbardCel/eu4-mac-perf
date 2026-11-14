"""Normalized logical-frame differential comparison (PR 3).

The two backends legitimately differ in extent (HiDPI) and row origin.  This
module performs exactly one declared normalization per side -- BGRA->RGBA,
row-origin decode, and (when allowed by policy) an integer area downscale to
the logical extent -- then reports full metrics even when the policy passes.
It does not use image similarity to decide alignment.
"""

from __future__ import annotations

import hashlib
import struct
import zlib
from pathlib import Path

try:
    import numpy as np
except Exception:  # noqa: BLE001
    np = None

SCHEMA = "gfx-frame-diff/v1"
POLICIES = ("exact", "tolerant", "diagnostic")
PASS = "pass"
FAIL = "fail"
UNVERIFIED = "unverified"
DIAGNOSTIC = "diagnostic"


class CompareError(RuntimeError):
    def __init__(self, kind: str, detail: str = ""):
        super().__init__(kind if not detail else f"{kind}: {detail}")
        self.kind = kind
        self.detail = detail


def _require_numpy():
    if np is None:
        raise CompareError("unavailable", "numpy is required for frame comparison")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def decode_frame(payload: bytes, width: int, height: int, *, origin: str,
                 pixel_format: str = "BGRA8") -> "np.ndarray":
    """Decode one tightly packed frame into top-left RGBA8."""
    _require_numpy()
    if pixel_format not in ("BGRA8", "RGBA8"):
        raise CompareError("format", f"unsupported pixel format: {pixel_format}")
    if width <= 0 or height <= 0:
        raise CompareError("extent", "frame extent must be positive")
    if len(payload) != width * height * 4:
        raise CompareError("payload", "frame payload does not match its extent")
    image = np.frombuffer(payload, dtype=np.uint8).reshape(height, width, 4).copy()
    if pixel_format == "BGRA8":
        image = image[:, :, [2, 1, 0, 3]]
    if origin == "lower_left":
        image = image[::-1, :, :]
    elif origin != "top_left":
        raise CompareError("origin", f"unsupported origin: {origin}")
    return image


def load_frame(path: Path, width: int, height: int, *, origin: str,
               pixel_format: str = "BGRA8", expected_sha256: str | None = None) -> "np.ndarray":
    path = Path(path)
    try:
        payload = path.read_bytes()
    except OSError as exc:
        raise CompareError("unreadable", str(exc)) from exc
    if expected_sha256 and _sha256(path) != expected_sha256:
        raise CompareError("digest", "frame digest does not match its declaration")
    return decode_frame(payload, width, height, origin=origin,
                        pixel_format=pixel_format)


def downscale_area(image: "np.ndarray", scale: int) -> "np.ndarray":
    _require_numpy()
    if type(scale) is not int or scale < 1:
        raise CompareError("scale", "scale must be a positive integer")
    if scale == 1:
        return image
    height, width, channels = image.shape
    if height % scale or width % scale:
        raise CompareError("scale", "extent is not divisible by the declared scale")
    reduced = image.reshape(height // scale, scale, width // scale, scale, channels)
    return np.rint(reduced.mean(axis=(1, 3))).astype(np.uint8)


def normalize_candidate(reference: "np.ndarray", candidate: "np.ndarray",
                        *, policy: dict, candidate_meta: dict) -> tuple["np.ndarray", dict]:
    """Bring the candidate to the reference's logical extent, with provenance."""
    _require_numpy()
    ref_h, ref_w = reference.shape[:2]
    cand_h, cand_w = candidate.shape[:2]
    normalization = {
        "reference_extent": [ref_w, ref_h],
        "candidate_extent": [cand_w, cand_h],
        "scale": 1,
        "method": "none",
        "allowed": True,
        "origin": {
            "reference": "top_left_after_decode",
            "candidate": "top_left_after_decode",
        },
    }
    if (cand_w, cand_h) == (ref_w, ref_h):
        return candidate, normalization
    if cand_w % ref_w or cand_h % ref_h or cand_w // ref_w != cand_h // ref_h:
        normalization["allowed"] = False
        raise CompareError("extent", "candidate is not an integer multiple of the logical extent")
    scale = cand_w // ref_w
    if scale not in (2, 3, 4):
        normalization["allowed"] = False
        raise CompareError("extent", f"unsupported candidate scale: {scale}")
    if not policy.get("allow_scale_normalization", True):
        normalization["allowed"] = False
        raise CompareError(
            "scale", "policy does not allow scale normalization; candidate must be logical"
        )
    normalization["scale"] = scale
    normalization["method"] = f"area_average_downscale_x{scale}"
    return downscale_area(candidate, scale), normalization


def _region(image: "np.ndarray", rect: list[int]) -> "np.ndarray":
    y0, y1, x0, x1 = rect
    return image[y0:y1, x0:x1, :]


def _gray(image: "np.ndarray") -> "np.ndarray":
    return image[:, :, :3].astype(np.float32).mean(axis=2)


def _edge(image: "np.ndarray") -> "np.ndarray":
    gray = _gray(image)
    dy = np.abs(np.diff(gray, axis=0))
    dx = np.abs(np.diff(gray, axis=1))
    height = min(dy.shape[0], dx.shape[0])
    width = min(dy.shape[1], dx.shape[1])
    return (dy[:height, :width] + dx[:height, :width]) / 2.0


def _correlation(left: "np.ndarray", right: "np.ndarray") -> float | None:
    a = left.reshape(-1).astype(np.float32)
    b = right.reshape(-1).astype(np.float32)
    if a.size == 0 or b.size != a.size:
        return None
    a = a - a.mean()
    b = b - b.mean()
    denominator = float(np.sqrt((a * a).sum() * (b * b).sum()))
    if denominator == 0.0:
        return None
    return float((a * b).sum() / denominator)


def _region_stats(reference: "np.ndarray", candidate: "np.ndarray",
                  rect: list[int], tolerance: int, *, mirror_check: bool) -> dict:
    _require_numpy()
    ref = _region(reference, rect)
    cand = _region(candidate, rect)
    delta = np.abs(ref.astype(np.int16) - cand.astype(np.int16))
    per_pixel = delta.max(axis=2)
    mismatched = int((per_pixel > tolerance).sum())
    pixels = int(per_pixel.size)
    normal_mae = float(delta.mean()) if pixels else 0.0
    result = {
        "rect": rect,
        "pixels": pixels,
        "mismatched_pixels": mismatched,
        "mismatched_fraction": round(mismatched / pixels, 6) if pixels else 0.0,
        "max_channel_error": int(per_pixel.max()) if pixels else 0,
        "mean_abs_error": round(normal_mae, 6),
        "structural_correlation": None,
        "mirror": {"checked": False},
    }
    ref_edge = _edge(ref)
    cand_edge = _edge(cand)
    correlation = _correlation(ref_edge, cand_edge)
    if correlation is not None:
        result["structural_correlation"] = round(correlation, 6)
    if mirror_check and pixels:
        flipped = cand[:, ::-1, :]
        flipped_delta = np.abs(ref.astype(np.int16) - flipped.astype(np.int16))
        flipped_per_pixel = flipped_delta.max(axis=2)
        flipped_mae = float(flipped_delta.mean())
        flipped_fraction = float((flipped_per_pixel > tolerance).mean())
        mirrored = (
            normal_mae > 0.0
            and flipped_mae < normal_mae * 0.75
            and flipped_fraction + 0.05 < (mismatched / pixels if pixels else 1.0)
        )
        result["mirror"] = {
            "checked": True,
            "normal_mean_abs_error": round(normal_mae, 6),
            "flipped_mean_abs_error": round(flipped_mae, 6),
            "flipped_mismatched_fraction": round(flipped_fraction, 6),
            "mirrored": bool(mirrored),
        }
    return result


def compare_frames(
    reference: "np.ndarray",
    candidate: "np.ndarray",
    *,
    policy: dict,
    regions: dict | None = None,
    candidate_meta: dict | None = None,
) -> dict:
    """Compare two decoded (top-left RGBA) frames and return full metrics."""
    _require_numpy()
    policy_name = policy.get("policy", "exact")
    if policy_name not in POLICIES:
        raise CompareError("policy", f"unknown comparison policy: {policy_name}")
    tolerance = int(policy.get("pixel_tolerance", policy.get("max_channel_error", 0)))
    max_fraction = float(policy.get("max_mismatched_fraction", 0.0))
    max_mean = float(policy.get("max_mean_abs_error", 0.0))
    if not (0 <= tolerance <= 255):
        raise CompareError("policy", "pixel tolerance must be 0..255")
    if reference.shape != candidate.shape:
        raise CompareError("extent", "normalized frames must share an extent")
    _, normalization = normalize_candidate(
        reference, candidate, policy=policy, candidate_meta=candidate_meta or {}
    )
    delta = np.abs(reference.astype(np.int16) - candidate.astype(np.int16))
    per_pixel = delta.max(axis=2)
    pixels = int(per_pixel.size)
    mismatched_mask = per_pixel > tolerance
    mismatched = int(mismatched_mask.sum())
    fraction = mismatched / pixels if pixels else 0.0
    max_error = int(per_pixel.max()) if pixels else 0
    mean_error = float(delta.mean()) if pixels else 0.0
    per_channel = {}
    for index, name in enumerate(("red", "green", "blue", "alpha")):
        channel = delta[:, :, index]
        per_channel[name] = {
            "max": int(channel.max()) if channel.size else 0,
            "mean": round(float(channel.mean()), 6) if channel.size else 0.0,
            "mismatched_pixels": int((channel > tolerance).sum()),
        }
    alpha_mismatched = int((delta[:, :, 3] > tolerance).sum())
    if mismatched:
        rows = np.where(mismatched_mask.any(axis=1))[0]
        columns = np.where(mismatched_mask.any(axis=0))[0]
        bbox = [int(rows[0]), int(rows[-1]) + 1, int(columns[0]), int(columns[-1]) + 1]
    else:
        bbox = None

    region_results = {}
    mirrored_regions = []
    for name, definition in (regions or {}).items():
        rect = list(definition.get("rect", [0, reference.shape[0], 0, reference.shape[1]]))
        if not (0 <= rect[0] < rect[1] <= reference.shape[0]
                and 0 <= rect[2] < rect[3] <= reference.shape[1]):
            raise CompareError("region", f"region {name} is outside the frame")
        stats = _region_stats(reference, candidate, rect, tolerance,
                              mirror_check=definition.get("mirror_check", True))
        stats["policy"] = definition.get("policy", "tolerant")
        region_results[name] = stats
        if stats["mirror"].get("mirrored"):
            mirrored_regions.append(name)

    would_fail = (
        max_error > tolerance
        or fraction > max_fraction
        or mean_error > max_mean
        or bool(mirrored_regions)
    )
    reasons = []
    if max_error > tolerance:
        reasons.append(f"max_channel_error {max_error} > {tolerance}")
    if fraction > max_fraction:
        reasons.append(f"mismatched_fraction {fraction:.6f} > {max_fraction}")
    if mean_error > max_mean:
        reasons.append(f"mean_abs_error {mean_error:.6f} > {max_mean}")
    if mirrored_regions:
        reasons.append("horizontal mirroring suspected in: " + ", ".join(mirrored_regions))
    if policy_name == "diagnostic":
        status = DIAGNOSTIC
    elif would_fail:
        status = FAIL
    else:
        status = PASS
    if policy_name == "exact" and normalization["scale"] > 1:
        status = UNVERIFIED
        reasons.append(
            "exact policy cannot treat a scale-normalized candidate as bit-identical"
        )
    worst = None
    if region_results:
        worst = max(
            region_results,
            key=lambda name: (region_results[name]["mismatched_fraction"],
                              region_results[name]["mean_abs_error"]),
        )
    return {
        "schema": SCHEMA,
        "status": status,
        "policy": policy_name,
        "would_fail": would_fail,
        "reasons": reasons,
        "normalization": normalization,
        "statistics": {
            "pixels": pixels,
            "mismatched_pixels": mismatched,
            "mismatched_fraction": round(fraction, 6),
            "max_channel_error": max_error,
            "mean_abs_error": round(mean_error, 6),
            "per_channel": per_channel,
            "alpha_mismatched_pixels": alpha_mismatched,
            "difference_bbox": bbox,
            "tolerance": tolerance,
            "max_mismatched_fraction": max_fraction,
            "max_mean_abs_error": max_mean,
        },
        "regions": region_results,
        "mirrored_regions": mirrored_regions,
        "worst_region": worst,
        "policy_source": {
            "pixel_tolerance": tolerance,
            "allow_scale_normalization": policy.get("allow_scale_normalization", True),
        },
    }


def difference_image(reference: "np.ndarray", candidate: "np.ndarray",
                     *, amplification: int = 4) -> "np.ndarray":
    _require_numpy()
    if amplification < 1:
        raise CompareError("amplification", "must be >= 1")
    delta = np.abs(reference.astype(np.int16) - candidate.astype(np.int16)).astype(np.uint16)
    delta = np.clip(delta * amplification, 0, 255).astype(np.uint8)
    delta[:, :, 3] = 255
    return delta


def _png_chunk(kind: bytes, payload: bytes) -> bytes:
    return (struct.pack(">I", len(payload)) + kind + payload
            + struct.pack(">I", zlib.crc32(kind + payload) & 0xFFFFFFFF))


def write_png(path: Path, rgba: "np.ndarray") -> None:
    """Minimal RGBA PNG writer (no external imaging dependency)."""
    _require_numpy()
    height, width = rgba.shape[:2]
    raw = b"".join(b"\x00" + rgba[row].tobytes() for row in range(height))
    value = (
        b"\x89PNG\r\n\x1a\n"
        + _png_chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0))
        + _png_chunk(b"IDAT", zlib.compress(raw, 6))
        + _png_chunk(b"IEND", b"")
    )
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(value)


def compare_payloads(
    reference_payload: bytes,
    reference_meta: dict,
    candidate_payload: bytes,
    candidate_meta: dict,
    *,
    policy: dict,
    regions: dict | None = None,
) -> dict:
    """Decode, normalize and compare two raw observations."""
    ref = decode_frame(
        reference_payload, int(reference_meta["width"]), int(reference_meta["height"]),
        origin=str(reference_meta.get("origin", "top_left")),
        pixel_format=str(reference_meta.get("pixel_format", "BGRA8")),
    )
    cand = decode_frame(
        candidate_payload, int(candidate_meta["width"]), int(candidate_meta["height"]),
        origin=str(candidate_meta.get("origin", "top_left")),
        pixel_format=str(candidate_meta.get("pixel_format", "BGRA8")),
    )
    cand, normalization = normalize_candidate(
        ref, cand, policy=policy, candidate_meta=candidate_meta
    )
    result = compare_frames(ref, cand, policy=policy, regions=regions,
                            candidate_meta=candidate_meta)
    if (policy.get("policy") == "exact" and normalization["scale"] > 1
            and result["status"] == PASS):
        result["status"] = UNVERIFIED
        result["reasons"].append(
            "exact policy cannot treat a scale-normalized candidate as bit-identical"
        )
    result["normalization"] = normalization
    result["reference"] = {
        "width": reference_meta.get("width"),
        "height": reference_meta.get("height"),
        "origin": reference_meta.get("origin"),
        "sha256": reference_meta.get("sha256"),
    }
    result["candidate"] = {
        "width": candidate_meta.get("width"),
        "height": candidate_meta.get("height"),
        "origin": candidate_meta.get("origin"),
        "sha256": candidate_meta.get("sha256"),
    }
    return result
