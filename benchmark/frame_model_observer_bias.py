"""Offline observer-bias calibration for intrusive diagnostic attribution (Phase B)."""
from __future__ import annotations

import math
import statistics
from typing import Callable, Iterable, Mapping, Sequence

OBSERVER_BIAS_CALIBRATION_VERSION = "observer_bias_calibration_v1"
OBSERVER_BIAS_SCALE_FACTORS: tuple[int, ...] = (0, 1, 2, 4)
OBSERVER_BIAS_BASE_FRAMES = 4
OBSERVER_BIAS_BASE_DRAW_LOOPS = 4000
OBSERVER_BIAS_RECIPE_NAME = "mesh"

MeasureCpu = Callable[[int], tuple[int, int]]


def scaled_frames(scale: int, base_frames: int = OBSERVER_BIAS_BASE_FRAMES) -> int:
    if scale not in OBSERVER_BIAS_SCALE_FACTORS:
        raise ValueError(f"unsupported scale factor {scale}")
    return base_frames if scale == 0 else base_frames * scale


def scaled_draw_loops(scale: int, base_loops: int = OBSERVER_BIAS_BASE_DRAW_LOOPS) -> int:
    if scale not in OBSERVER_BIAS_SCALE_FACTORS:
        raise ValueError(f"unsupported scale factor {scale}")
    return base_loops if scale == 0 else base_loops * scale


def operation_count_for_frames(scale: int, draws_per_frame: int, base_frames: int = OBSERVER_BIAS_BASE_FRAMES) -> int:
    """Synthetic workload operations scale with measured frames × recipe draws."""
    return scale * base_frames * draws_per_frame


def fit_slope_zero_intercept(points: Sequence[tuple[float, float]]) -> dict:
    """OLS through origin: cpu_delta_ns ≈ slope_ns_per_op × operations."""
    filtered = [(ops, delta) for ops, delta in points if ops > 0]
    if len(filtered) < 2:
        return {
            "slope_ns_per_op": None,
            "residual_std_ns": None,
            "points_used": len(filtered),
            "status": "insufficient_points",
        }
    numerator = sum(ops * delta for ops, delta in filtered)
    denominator = sum(ops * ops for ops, _ in filtered)
    if denominator <= 0:
        return {
            "slope_ns_per_op": None,
            "residual_std_ns": None,
            "points_used": len(filtered),
            "status": "degenerate",
        }
    slope = numerator / denominator
    residuals = [delta - slope * ops for ops, delta in filtered]
    residual_std = statistics.pstdev(residuals) if len(residuals) > 1 else 0.0
    return {
        "slope_ns_per_op": slope,
        "residual_std_ns": residual_std,
        "points_used": len(filtered),
        "status": "ok",
    }


def summarize_primitive_sweep(
    primitive_id: str,
    *,
    unit: str,
    scale_rows: Sequence[dict],
) -> dict:
    fit_points = [
        (float(row["operations"]), float(row["cpu_delta_ns"]))
        for row in scale_rows
        if row.get("scale", 0) > 0
    ]
    fit = fit_slope_zero_intercept(fit_points)
    return {
        "primitive_id": primitive_id,
        "unit": unit,
        "scale_rows": list(scale_rows),
        "fit": fit,
    }


def run_differential_sweep(
    primitive_id: str,
    *,
    unit: str,
    scale_factors: Sequence[int] = OBSERVER_BIAS_SCALE_FACTORS,
    measure_low_high: MeasureCpu,
    operations_at_scale: Callable[[int], int],
) -> dict:
    """Measure external harness CPU at each scale; delta = high − low."""
    rows: list[dict] = []
    for scale in scale_factors:
        low_cpu, high_cpu = measure_low_high(scale)
        operations = operations_at_scale(scale)
        delta = 0 if scale == 0 else high_cpu - low_cpu
        rows.append(
            {
                "scale": scale,
                "operations": operations,
                "cpu_low_ns": low_cpu,
                "cpu_high_ns": high_cpu if scale > 0 else low_cpu,
                "cpu_delta_ns": delta,
            },
        )
    return summarize_primitive_sweep(primitive_id, unit=unit, scale_rows=rows)


def build_bias_model(primitives: Sequence[dict]) -> dict:
    slopes = {}
    for entry in primitives:
        fit = entry.get("fit") or {}
        slope = fit.get("slope_ns_per_op")
        if slope is not None and math.isfinite(slope):
            slopes[entry["primitive_id"]] = {
                "slope_ns_per_op": slope,
                "unit": entry.get("unit"),
                "residual_std_ns": fit.get("residual_std_ns"),
            }
    return {
        "calibration_version": OBSERVER_BIAS_CALIBRATION_VERSION,
        "scale_factors": list(OBSERVER_BIAS_SCALE_FACTORS),
        "primitives": list(primitives),
        "slopes": slopes,
    }


def estimate_bias_ns(operation_counts: Mapping[str, float], model: Mapping[str, object]) -> dict[str, float]:
    slopes = model.get("slopes") or {}
    estimated: dict[str, float] = {}
    for primitive_id, count in operation_counts.items():
        spec = slopes.get(primitive_id)
        if not spec or count <= 0:
            continue
        slope = spec.get("slope_ns_per_op")
        if slope is None:
            continue
        estimated[primitive_id] = float(slope) * float(count)
    return estimated


def bias_adjust_inclusive_cpu(
    inclusive_cpu_ns: int | float,
    operation_counts: Mapping[str, float],
    model: Mapping[str, object],
) -> dict:
    components = estimate_bias_ns(operation_counts, model)
    total_bias = sum(components.values())
    adjusted = float(inclusive_cpu_ns) - total_bias
    return {
        "inclusive_cpu_ns": float(inclusive_cpu_ns),
        "estimated_bias_ns": total_bias,
        "bias_adjusted_cpu_ns": adjusted,
        "components_ns": components,
        "calibration_version": model.get("calibration_version"),
    }


def bias_aware_interval(
    point_estimate_ns: float,
    *,
    slope_ns_per_op: float | None,
    operation_count: float,
    residual_std_ns: float | None,
    z: float = 1.96,
) -> dict:
    """Conservative interval for attribution after subtracting calibrated observer bias."""
    bias = (slope_ns_per_op or 0.0) * operation_count
    margin = z * (residual_std_ns or 0.0) * max(operation_count, 1.0)
    low = point_estimate_ns - bias - margin
    high = point_estimate_ns - bias + margin
    return {
        "point_ns": point_estimate_ns,
        "bias_subtracted_ns": bias,
        "low_ns": low,
        "high_ns": high,
        "margin_ns": margin,
    }


def bias_aware_table_rows(model: Mapping[str, object]) -> list[dict]:
    rows = []
    for entry in model.get("primitives") or []:
        fit = entry.get("fit") or {}
        rows.append(
            {
                "primitive_id": entry.get("primitive_id"),
                "unit": entry.get("unit"),
                "slope_ns_per_op": fit.get("slope_ns_per_op"),
                "residual_std_ns": fit.get("residual_std_ns"),
                "fit_status": fit.get("status"),
            },
        )
    return rows


PRIMITIVE_CATALOG: tuple[dict, ...] = (
    {
        "primitive_id": "gl_interpose_dispatch",
        "label": "GL draw interposer dispatch",
        "unit": "synthetic_draw_loop",
        "description": "Marginal harness CPU with dylib interposition vs bare GL loop.",
    },
    {
        "primitive_id": "reference_frame_hooks",
        "label": "REFERENCE frame hooks",
        "unit": "published_frame",
        "description": "loaded-disabled → lean REFERENCE (update/render/present scaffolding).",
    },
    {
        "primitive_id": "tls_event_accounting",
        "label": "TLS event accounting",
        "unit": "published_frame",
        "description": "minimal-reference → reference (events enabled; publish path armed).",
    },
    {
        "primitive_id": "scope_pair_clocks",
        "label": "scope_begin/end clocks",
        "unit": "published_frame",
        "description": "lean REFERENCE → counters-lite (scopes disabled in lite path).",
    },
    {
        "primitive_id": "per_frame_counter_flush",
        "label": "per-frame counter flush",
        "unit": "published_frame",
        "description": "counters-lite → counters-lite deferred flush.",
    },
    {
        "primitive_id": "sparse_gl_timestamp",
        "label": "sparse GL timestamp sampling",
        "unit": "published_frame",
        "description": "counters without GPU timestamps → counters with GPU timestamps.",
    },
    {
        "primitive_id": "frame_publication_writer",
        "label": "frame publication / writer",
        "unit": "published_frame",
        "description": "ablation writer → sampled forensic window (SPSC + writer path).",
    },
)
