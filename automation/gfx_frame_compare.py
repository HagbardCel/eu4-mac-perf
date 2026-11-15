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
    region_failures = []
    for name, definition in (regions or {}).items():
        rect = list(definition.get("rect", [0, reference.shape[0], 0, reference.shape[1]]))
        if not (0 <= rect[0] < rect[1] <= reference.shape[0]
                and 0 <= rect[2] < rect[3] <= reference.shape[1]):
            raise CompareError("region", f"region {name} is outside the frame")
        stats = _region_stats(reference, candidate, rect, tolerance,
                              mirror_check=definition.get("mirror_check", True))
        region_policy = str(definition.get("policy", "tolerant"))
        stats["policy"] = region_policy
        region_results[name] = stats
        if stats["mirror"].get("mirrored"):
            mirrored_regions.append(name)
        if region_policy == "ignore":
            continue
        region_failure = None
        if region_policy == "structural":
            minimum = float(definition.get("min_structural_correlation", 0.5))
            correlation = stats.get("structural_correlation")
            if correlation is None or correlation < minimum:
                region_failure = (
                    f"{name}: structural correlation {correlation} < {minimum}")
        else:
            region_tolerance = int(definition.get("max_channel_error", tolerance))
            region_fraction = float(definition.get(
                "max_mismatched_fraction", max_fraction))
            region_mean = float(definition.get("max_mean_abs_error", max_mean))
            if stats["max_channel_error"] > region_tolerance:
                region_failure = (
                    f"{name}: max_channel_error {stats['max_channel_error']} "
                    f"> {region_tolerance}")
            elif stats["mismatched_fraction"] > region_fraction:
                region_failure = (
                    f"{name}: mismatched_fraction {stats['mismatched_fraction']} "
                    f"> {region_fraction}")
            elif stats["mean_abs_error"] > region_mean:
                region_failure = (
                    f"{name}: mean_abs_error {stats['mean_abs_error']} > {region_mean}")
        if region_failure:
            region_failures.append(region_failure)

    would_fail = (
        max_error > tolerance
        or fraction > max_fraction
        or mean_error > max_mean
        or bool(mirrored_regions)
        or bool(region_failures)
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
    reasons.extend(region_failures)
    if policy_name == "diagnostic":
        status = DIAGNOSTIC
    elif would_fail:
        status = FAIL
    else:
        status = PASS
    exact_qualified = (
        policy_name == "exact"
        and normalization["scale"] == 1
        and normalization["method"] == "none"
    )
    if policy_name == "exact" and normalization["scale"] > 1:
        if would_fail:
            # A genuine difference remains a difference even though exact
            # pixel equality cannot be qualified through a resample.
            status = FAIL
            reasons.append(
                "exact pixel equality is unqualified after scale normalization"
            )
        else:
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
        "pixel_verdict": "different" if would_fail else "equal",
        "exact_qualified": bool(exact_qualified),
        "region_failures": region_failures,
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


def _apply_normalization_policy(result: dict, normalization: dict,
                                policy: dict) -> dict:
    """Apply exact-policy semantics to a resampled comparison.

    A genuine difference stays a difference; equality after resampling cannot
    be qualified as bit-identical under the exact policy.
    """
    if policy.get("policy") != "exact" or int(normalization.get("scale", 1)) <= 1:
        return result
    result["exact_qualified"] = False
    reason = "exact policy cannot treat a scale-normalized candidate as bit-identical"
    if result.get("status") == FAIL:
        result.setdefault("reasons", []).append(reason)
    elif result.get("status") == PASS:
        result["status"] = UNVERIFIED
        result.setdefault("reasons", []).append(reason)
    return result


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
    _apply_normalization_policy(result, normalization, policy)
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


# ---------------------------------------------------------------------------
# Temporal frame diagnostics
#
# The M7.0 canary had to write a private NumPy script to discover that a
# process's own consecutive frames differ more than the cross-process pair, and
# that most cross-process differing pixels also move within one process.  These
# helpers expose that measurement once, on top of the same decoder, origin
# normalization, scale handling and comparison metrics used everywhere else.

TEMPORAL_SCHEMA = "gfx-temporal-frame-diff/v1"
TEMPORAL_MAX_PAIRS = 12


def difference_mask(reference: "np.ndarray", candidate: "np.ndarray",
                    tolerance: int) -> "np.ndarray":
    """Boolean mask of pixels whose maximum channel error exceeds tolerance."""
    _require_numpy()
    if reference.shape != candidate.shape:
        raise CompareError("extent", "difference masks require a shared extent")
    if not (0 <= int(tolerance) <= 255):
        raise CompareError("policy", "pixel tolerance must be 0..255")
    delta = np.abs(reference.astype(np.int16) - candidate.astype(np.int16))
    return delta.max(axis=2) > int(tolerance)


def _temporal_pair(left: dict, right: dict, relation: str, *,
                   policy: dict, regions: dict | None,
                   normalization: dict | None = None) -> dict:
    result = compare_frames(
        left["image"], right["image"], policy=policy, regions=regions)
    normalization = normalization or {
        "reference_extent": [int(left["image"].shape[1]), int(left["image"].shape[0])],
        "candidate_extent": [int(right["image"].shape[1]), int(right["image"].shape[0])],
        "scale": 1,
        "method": "none",
        "allowed": bool(policy.get("allow_scale_normalization", True)),
        "origin": {"reference": "top_left_after_decode",
                   "candidate": "top_left_after_decode"},
    }
    _apply_normalization_policy(result, normalization, policy)
    statistics = result.get("statistics") or {}
    return {
        "relation": relation,
        "reference": {
            "frame_index": left.get("frame_index"),
            "sha256": left.get("sha256"),
            "identity": left.get("identity") or {},
        },
        "candidate": {
            "frame_index": right.get("frame_index"),
            "sha256": right.get("sha256"),
            "identity": right.get("identity") or {},
        },
        "status": result.get("status"),
        "pixel_verdict": result.get("pixel_verdict"),
        "would_fail": result.get("would_fail"),
        "exact_qualified": result.get("exact_qualified", False),
        "reasons": result.get("reasons") or [],
        "statistics": {
            key: statistics.get(key) for key in (
                "pixels", "mismatched_pixels", "mismatched_fraction",
                "max_channel_error", "mean_abs_error", "difference_bbox",
                "tolerance", "per_channel")
        },
        "regions": result.get("regions") or {},
        "normalization": normalization,
    }


def _mask_overlap(cross: "np.ndarray", temporal: "np.ndarray") -> dict:
    intersection = int(np.logical_and(cross, temporal).sum())
    union = int(np.logical_or(cross, temporal).sum())
    cross_pixels = int(cross.sum())
    temporal_pixels = int(temporal.sum())
    return {
        "cross_pixels": cross_pixels,
        "temporal_pixels": temporal_pixels,
        "intersection_pixels": intersection,
        "cross_also_temporal_fraction": (
            round(intersection / cross_pixels, 6) if cross_pixels else None),
        "temporal_also_cross_fraction": (
            round(intersection / temporal_pixels, 6) if temporal_pixels else None),
        "jaccard": round(intersection / union, 6) if union else None,
    }


def compare_temporal(
    reference_frames: list[dict],
    candidate_frames: list[dict],
    *,
    policy: dict,
    regions: dict | None = None,
    cross_pairs: list[tuple[int, int]] | None = None,
    reference_consecutive: list[tuple[int, int]] | None = None,
    candidate_consecutive: list[tuple[int, int]] | None = None,
    include_overlap: bool = True,
    max_pairs: int = TEMPORAL_MAX_PAIRS,
    artifacts: dict | None = None,
) -> dict:
    """Compare requested consecutive and cross-process frames with completion.

    ``reference_consecutive`` / ``candidate_consecutive`` name the requested
    within-process pairs explicitly; when omitted, adjacent available frames are
    requested.  ``cross_pairs`` names the requested cross-process pairs; when
    omitted, the first shared index is requested.  Every requested comparison is
    reported as ``completed``, ``skipped`` (pair bound) or ``unavailable`` (a
    missing frame or normalization failure), and the top-level status is derived
    from those records rather than from whichever pairs happened to run.

    Normalization follows the scenario policy.  Cross-process pairs are
    normalized to the reference extent once, using the same normalized arrays
    for metrics, masks and artifacts.  Candidate-temporal pairs stay in the
    candidate's own coordinate space.
    """
    _require_numpy()
    if max_pairs < 1:
        raise CompareError("bound", "max_pairs must be >= 1")
    if not reference_frames or not candidate_frames:
        return {
            "schema": TEMPORAL_SCHEMA,
            "status": "incomplete",
            "reason": "both roles need at least one preserved frame",
            "pairs": [],
            "comparisons": [],
            "completion": {
                "requested": 0, "completed": 0, "skipped": 0,
                "unavailable": 0, "cross_process_complete": False,
                "temporal_variability_complete": False,
            },
            "truncated": {"pairs": 0, "max_pairs": max_pairs},
            "overlap": None,
        }

    def ordered(rows: list[dict]) -> list[dict]:
        return sorted(
            (row for row in rows if isinstance(row, dict)
             and isinstance(row.get("image"), np.ndarray)),
            key=lambda row: int(row.get("frame_index") or 0))

    def by_index(rows: list[dict]) -> dict[int, dict]:
        return {int(row["frame_index"]): row for row in rows}

    def consecutive(rows: list[dict]) -> list[tuple[int, int]]:
        pairs: list[tuple[int, int]] = []
        for left, right in zip(rows, rows[1:]):
            if int(right["frame_index"]) == int(left["frame_index"]) + 1:
                pairs.append((int(left["frame_index"]),
                               int(right["frame_index"])))
        return pairs

    reference = ordered(reference_frames)
    candidate = ordered(candidate_frames)
    reference_index = by_index(reference)
    candidate_index = by_index(candidate)
    requested_reference = (reference_consecutive
                           if reference_consecutive is not None
                           else consecutive(reference))
    requested_candidate = (candidate_consecutive
                           if candidate_consecutive is not None
                           else consecutive(candidate))
    tolerance = int(policy.get("pixel_tolerance", policy.get("max_channel_error", 0)))
    allow_scale = bool(policy.get("allow_scale_normalization", True))
    pairs: list[dict] = []
    comparisons: list[dict] = []

    def record(relation: str, ref_index: int | None, cand_index: int | None,
               status: str, reason: str = "") -> None:
        comparisons.append({
            "relation": relation,
            "reference_frame": ref_index,
            "candidate_frame": cand_index,
            "status": status,
            "reason": reason,
        })

    def add_pair(left: dict, right: dict, relation: str, *,
                 normalization: dict | None = None) -> None:
        if len(pairs) >= max_pairs:
            record(relation, left.get("frame_index"), right.get("frame_index"),
                   "skipped", "pair bound reached")
            return
        try:
            pairs.append(_temporal_pair(
                left, right, relation, policy=policy, regions=regions,
                normalization=normalization))
            record(relation, left.get("frame_index"), right.get("frame_index"),
                   "completed")
        except CompareError as exc:
            record(relation, left.get("frame_index"), right.get("frame_index"),
                   "unavailable", f"{exc.kind}: {exc.detail or exc}")

    for requested, relation, index, role_rows in (
            (requested_reference, "reference_temporal", reference_index,
             reference),
            (requested_candidate, "candidate_temporal", candidate_index,
             candidate)):
        for left_index, right_index in requested:
            left = index.get(int(left_index))
            right = index.get(int(right_index))
            if left is None or right is None:
                missing = [value for value, row in (
                    (left_index, left), (right_index, right)) if row is None]
                record(relation, int(left_index), int(right_index),
                       "unavailable",
                       f"preserved frame(s) missing: {missing}")
                continue
            add_pair(left, right, relation)

    cross: list[dict] = []
    requested_cross = cross_pairs
    cross_required = requested_cross is not None
    if requested_cross is None:
        shared = sorted(set(reference_index) & set(candidate_index))
        requested_cross = [(index, index) for index in shared[:1]]
        # The default temporal diagnostic requires a cross-process comparison;
        # no shared index means the request cannot be satisfied.
        cross_required = True
    for ref_index, cand_index in requested_cross or []:
        ref_index, cand_index = int(ref_index), int(cand_index)
        left = reference_index.get(ref_index)
        right = candidate_index.get(cand_index)
        if left is None or right is None:
            missing = [value for value, row in (
                (ref_index, left), (cand_index, right)) if row is None]
            record("cross_process", ref_index, cand_index, "unavailable",
                   f"preserved frame(s) missing: {missing}")
            continue
        normalization = {
            "reference_extent": [int(left["image"].shape[1]),
                                 int(left["image"].shape[0])],
            "candidate_extent": [int(right["image"].shape[1]),
                                 int(right["image"].shape[0])],
            "scale": 1,
            "method": "none",
            "allowed": allow_scale,
            "origin": {"reference": "top_left_after_decode",
                       "candidate": "top_left_after_decode"},
        }
        normalized = right
        if left["image"].shape != right["image"].shape:
            try:
                image, normalization = normalize_candidate(
                    left["image"], right["image"],
                    policy={"allow_scale_normalization": allow_scale},
                    candidate_meta={})
            except CompareError as exc:
                record("cross_process", ref_index, cand_index, "unavailable",
                       f"normalization failed: {exc.kind}: {exc.detail or exc}")
                continue
            normalized = dict(right)
            normalized["image"] = image
            normalized["identity"] = dict(right.get("identity") or {})
            normalized["identity"]["normalization_scale"] = normalization["scale"]
        if len(pairs) >= max_pairs:
            record("cross_process", ref_index, cand_index, "skipped",
                   "pair bound reached")
            continue
        try:
            pair = _temporal_pair(
                left, normalized, "cross_process", policy=policy,
                regions=regions, normalization=normalization)
        except CompareError as exc:
            record("cross_process", ref_index, cand_index, "unavailable",
                   f"{exc.kind}: {exc.detail or exc}")
            continue
        pairs.append(pair)
        record("cross_process", ref_index, cand_index, "completed")
        cross.append({"reference": left, "candidate": normalized})

    overlap = None
    if include_overlap and cross:
        cross_pair = cross[0]
        cross_mask = difference_mask(
            cross_pair["reference"]["image"], cross_pair["candidate"]["image"],
            tolerance)
        temporal_overlaps = []
        for rows, role in ((reference, "reference"), (candidate, "candidate")):
            for left, right in zip(rows, rows[1:]):
                if int(right["frame_index"]) != int(left["frame_index"]) + 1:
                    continue
                mask = difference_mask(left["image"], right["image"], tolerance)
                if mask.shape != cross_mask.shape:
                    temporal_overlaps.append({
                        "relation": f"{role}_temporal",
                        "reference_frame": left.get("frame_index"),
                        "candidate_frame": right.get("frame_index"),
                        "skipped": "extent differs from the cross-process pair",
                    })
                    continue
                entry = _mask_overlap(cross_mask, mask)
                entry.update({
                    "relation": f"{role}_temporal",
                    "reference_frame": left.get("frame_index"),
                    "candidate_frame": right.get("frame_index"),
                })
                temporal_overlaps.append(entry)
        overlap = {
            "cross": {
                "reference_frame": cross_pair["reference"].get("frame_index"),
                "candidate_frame": cross_pair["candidate"].get("frame_index"),
                "mask_pixels": int(cross_mask.sum()),
            },
            "temporal": temporal_overlaps,
            "interpretation": (
                "cross_also_temporal_fraction is the share of cross-process "
                "differing pixels that also change between consecutive frames "
                "of that process; high values mean the cross-process "
                "difference is not separable from temporal variability"),
        }

    artifact_paths = []
    if artifacts:
        directory = Path(artifacts["dir"])
        amplification = int(artifacts.get("amplification", 4))
        directory.mkdir(parents=True, exist_ok=True)
        for pair in pairs:
            relation = pair["relation"]
            reference_frame = int(pair["reference"]["frame_index"])
            candidate_frame = int(pair["candidate"]["frame_index"])
            if relation == "reference_temporal":
                left = reference_index.get(reference_frame)
                right = reference_index.get(candidate_frame)
            elif relation == "candidate_temporal":
                left = candidate_index.get(reference_frame)
                right = candidate_index.get(candidate_frame)
            elif relation == "cross_process":
                left = reference_index.get(reference_frame)
                right = next(
                    (entry["candidate"] for entry in cross
                     if int(entry["candidate"].get("frame_index", -1))
                     == candidate_frame), None)
            else:
                left = right = None
            if left is None or right is None:
                continue
            name = (f"{relation}-{reference_frame}-{candidate_frame}.png")
            image = difference_image(left["image"], right["image"],
                                     amplification=amplification)
            write_png(directory / name, image)
            artifact_paths.append(str(directory / name))

    completed = [item for item in comparisons if item["status"] == "completed"]
    skipped = [item for item in comparisons if item["status"] == "skipped"]
    unavailable = [item for item in comparisons if item["status"] == "unavailable"]
    cross_comparisons = [item for item in comparisons
                         if item["relation"] == "cross_process"]
    cross_complete = bool(cross_comparisons) and all(
        item["status"] == "completed" for item in cross_comparisons)
    temporal_complete = all(
        item["status"] == "completed" for item in comparisons
        if item["relation"] != "cross_process")
    if unavailable:
        status = "incomplete"
        reason = "requested comparison(s) unavailable: " + "; ".join(
            f"{item['relation']} {item['reference_frame']}/"
            f"{item['candidate_frame']}: {item['reason']}"
            for item in unavailable[:4])
    elif any(item["relation"] == "cross_process" for item in skipped):
        status = "incomplete"
        reason = "requested cross-process comparison was skipped by the pair bound"
    elif skipped:
        status = "partial"
        reason = "some requested comparisons were skipped by the pair bound"
    elif not pairs:
        status = "incomplete"
        reason = "no temporal comparison could be completed"
    elif cross_required and not cross_complete:
        status = "incomplete"
        reason = "cross-process comparison did not complete"
    elif not temporal_complete:
        status = "incomplete"
        reason = "requested within-process variability was not fully assessed"
    else:
        status = "completed"
        reason = ""
    return {
        "schema": TEMPORAL_SCHEMA,
        "status": status,
        "reason": reason,
        "policy": {
            "pixel_tolerance": tolerance,
            "max_mismatched_fraction": float(policy.get("max_mismatched_fraction", 0.0)),
            "max_mean_abs_error": float(policy.get("max_mean_abs_error", 0.0)),
            "allow_scale_normalization": allow_scale,
        },
        "pairs": pairs,
        "comparisons": comparisons,
        "completion": {
            "requested": len(comparisons),
            "completed": len(completed),
            "skipped": len(skipped),
            "unavailable": len(unavailable),
            "cross_process_complete": cross_complete,
            "temporal_variability_complete": temporal_complete,
            "cross_process_requested": cross_required,
        },
        "truncated": {
            "pairs": len(skipped),
            "max_pairs": max_pairs,
            "cross_process": any(item["relation"] == "cross_process"
                                 for item in skipped),
        },
        "skipped": skipped,
        "unavailable": unavailable,
        "overlap": overlap,
        "frames": {
            "reference": [int(row["frame_index"]) for row in reference],
            "candidate": [int(row["frame_index"]) for row in candidate],
        },
        "bounded": {"max_pairs": max_pairs, "returned_pairs": len(pairs),
                    "skipped": len(skipped)},
        "artifacts": artifact_paths,
    }
