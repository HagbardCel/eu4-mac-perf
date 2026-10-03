"""Offline observer-bias calibration for intrusive diagnostic attribution (Phase B)."""
from __future__ import annotations

import math
import statistics
from typing import Callable, Mapping, Sequence

OBSERVER_BIAS_CALIBRATION_VERSION = "observer_bias_calibration_v1"
OBSERVER_BIAS_SCALE_FACTORS: tuple[int, ...] = (1, 2, 4)
OBSERVER_BIAS_BASE_FRAMES = 4
OBSERVER_BIAS_BASE_DRAW_LOOPS = 4000
OBSERVER_BIAS_REPETITIONS = 5
OBSERVER_BIAS_RECIPE_NAME = "mesh"

ROLE_AGGREGATE_CONTROL = "aggregate_control"
ROLE_DECOMPOSITION = "decomposition_diagnostic"
ROLE_INSTRUMENTATION = "instrumentation_diagnostic"

MeasureCpu = Callable[[int], tuple[int, int]]


def telemetry_suffix(primitive_id: str, scale: int, repetition: int, side: str) -> str:
    return f"obs-{primitive_id}-s{scale}-r{repetition}-{side}"


def scaled_frames(scale: int, base_frames: int = OBSERVER_BIAS_BASE_FRAMES) -> int:
    if scale not in OBSERVER_BIAS_SCALE_FACTORS:
        raise ValueError(f"unsupported scale factor {scale}")
    return base_frames * scale


def scaled_draw_loops(scale: int, base_loops: int = OBSERVER_BIAS_BASE_DRAW_LOOPS) -> int:
    if scale not in OBSERVER_BIAS_SCALE_FACTORS:
        raise ValueError(f"unsupported scale factor {scale}")
    return base_loops * scale


def published_frame_operations(scale: int, base_frames: int = OBSERVER_BIAS_BASE_FRAMES) -> int:
    return scaled_frames(scale, base_frames)


def count_scope_pairs_from_trace(trace: Sequence[Sequence[str]] | None) -> int:
    if not trace:
        return 0
    total = 0
    for row in trace:
        if not row or row[0] != "Q" or len(row) < 6:
            continue
        try:
            total += int(row[5])
        except ValueError:
            continue
    return total


def count_gpu_timestamp_segments_from_trace(trace: Sequence[Sequence[str]] | None) -> int:
    if not trace:
        return 0
    for row in trace:
        if row and row[0] == "M" and len(row) >= 4:
            try:
                return int(row[1])
            except ValueError:
                return 0
    return 0


def count_timed_gl_samples_from_trace(trace: Sequence[Sequence[str]] | None) -> int:
    if not trace:
        return 0
    kinds = ("D", "S")
    return sum(1 for row in trace if row and row[0] in kinds)


def fit_slope_zero_intercept(points: Sequence[tuple[float, float]]) -> dict:
    """OLS through origin: cpu_delta_ns ≈ slope_ns_per_op × operations."""
    filtered = [(ops, delta) for ops, delta in points if ops > 0]
    if len(filtered) < 2:
        return {
            "slope_ns_per_op": None,
            "slope_se_ns_per_op": None,
            "residual_std_ns": None,
            "points_used": len(filtered),
            "status": "insufficient_points",
        }
    numerator = sum(ops * delta for ops, delta in filtered)
    denominator = sum(ops * ops for ops, _ in filtered)
    if denominator <= 0:
        return {
            "slope_ns_per_op": None,
            "slope_se_ns_per_op": None,
            "residual_std_ns": None,
            "points_used": len(filtered),
            "status": "degenerate",
        }
    slope = numerator / denominator
    residuals = [delta - slope * ops for ops, delta in filtered]
    dof = max(len(filtered) - 1, 1)
    residual_std = math.sqrt(sum(r * r for r in residuals) / dof)
    slope_se = residual_std / math.sqrt(denominator)
    return {
        "slope_ns_per_op": slope,
        "slope_se_ns_per_op": slope_se,
        "residual_std_ns": residual_std,
        "points_used": len(filtered),
        "status": "ok",
    }


def summarize_primitive_measurements(
    primitive_id: str,
    *,
    role: str,
    unit: str,
    low_stage: str,
    high_stage: str,
    observations: Sequence[dict],
) -> dict:
    fit_points = [
        (float(row["operations"]), float(row["cpu_delta_ns"]))
        for row in observations
        if row.get("operations", 0) > 0
    ]
    fit = fit_slope_zero_intercept(fit_points)
    return {
        "primitive_id": primitive_id,
        "role": role,
        "unit": unit,
        "low_stage": low_stage,
        "high_stage": high_stage,
        "observations": list(observations),
        "fit": fit,
    }


def build_bias_model(
    primitives: Sequence[dict],
    *,
    consistency: Mapping[str, object] | None = None,
) -> dict:
    additive: dict[str, dict] = {}
    aggregates: dict[str, dict] = {}
    for entry in primitives:
        fit = entry.get("fit") or {}
        slope = fit.get("slope_ns_per_op")
        if slope is None or not math.isfinite(slope):
            continue
        payload = {
            "slope_ns_per_op": slope,
            "slope_se_ns_per_op": fit.get("slope_se_ns_per_op"),
            "unit": entry.get("unit"),
            "role": entry.get("role"),
            "low_stage": entry.get("low_stage"),
            "high_stage": entry.get("high_stage"),
        }
        if entry.get("role") == ROLE_AGGREGATE_CONTROL:
            aggregates[entry["primitive_id"]] = payload
        elif entry.get("role") in {ROLE_DECOMPOSITION, ROLE_INSTRUMENTATION}:
            additive[entry["primitive_id"]] = payload
    return {
        "calibration_version": OBSERVER_BIAS_CALIBRATION_VERSION,
        "scale_factors": list(OBSERVER_BIAS_SCALE_FACTORS),
        "repetitions": OBSERVER_BIAS_REPETITIONS,
        "primitives": list(primitives),
        "additive_slopes": additive,
        "aggregate_slopes": aggregates,
        "consistency": dict(consistency or {}),
    }


def estimate_bias_ns(
    operation_counts: Mapping[str, float],
    model: Mapping[str, object],
    *,
    allow_aggregates: bool = False,
) -> dict[str, float]:
    slopes = model.get("additive_slopes") or {}
    if allow_aggregates:
        slopes = {**slopes, **(model.get("aggregate_slopes") or {})}
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
    """Subtract only non-overlapping decomposition/instrumentation slopes (never aggregates)."""
    components = estimate_bias_ns(operation_counts, model, allow_aggregates=False)
    total_bias = sum(components.values())
    adjusted = float(inclusive_cpu_ns) - total_bias
    return {
        "inclusive_cpu_ns": float(inclusive_cpu_ns),
        "estimated_bias_ns": total_bias,
        "bias_adjusted_cpu_ns": adjusted,
        "components_ns": components,
        "calibration_version": model.get("calibration_version"),
        "layer": "decomposition_only",
    }


def bias_aware_interval(
    point_estimate_ns: float,
    *,
    slope_ns_per_op: float | None,
    slope_se_ns_per_op: float | None,
    operation_count: float,
    z: float = 1.96,
) -> dict:
    bias = (slope_ns_per_op or 0.0) * operation_count
    margin = z * (slope_se_ns_per_op or 0.0) * operation_count
    low = point_estimate_ns - bias - margin
    high = point_estimate_ns - bias + margin
    return {
        "point_ns": point_estimate_ns,
        "bias_subtracted_ns": bias,
        "low_ns": low,
        "high_ns": high,
        "margin_ns": margin,
    }


def reconcile_counters_decomposition(
    model: Mapping[str, object],
    *,
    operation_counts: Mapping[str, float],
    published_frames: float,
) -> dict:
    """Compare summed decomposition estimates to aggregate counters_incremental tax."""
    aggregate = (model.get("aggregate_slopes") or {}).get("counters_incremental")
    if not aggregate:
        return {"status": "missing_aggregate"}
    decomposed = estimate_bias_ns(operation_counts, model, allow_aggregates=False)
    decomposed_total = sum(decomposed.values())
    aggregate_total = float(aggregate["slope_ns_per_op"]) * published_frames
    if aggregate_total <= 0:
        fraction = None
    else:
        fraction = decomposed_total / aggregate_total
    return {
        "status": "ok",
        "aggregate_counters_incremental_ns": aggregate_total,
        "decomposed_sum_ns": decomposed_total,
        "explained_fraction": fraction,
        "components_ns": decomposed,
        "published_frames": published_frames,
    }


def bias_aware_table_rows(model: Mapping[str, object]) -> list[dict]:
    rows = []
    for entry in model.get("primitives") or []:
        fit = entry.get("fit") or {}
        rows.append(
            {
                "primitive_id": entry.get("primitive_id"),
                "role": entry.get("role"),
                "unit": entry.get("unit"),
                "low_stage": entry.get("low_stage"),
                "high_stage": entry.get("high_stage"),
                "slope_ns_per_op": fit.get("slope_ns_per_op"),
                "slope_se_ns_per_op": fit.get("slope_se_ns_per_op"),
                "fit_status": fit.get("status"),
            },
        )
    return rows


PRIMITIVE_SPECS: tuple[dict, ...] = (
    {
        "primitive_id": "gl_interpose_dispatch",
        "role": ROLE_INSTRUMENTATION,
        "unit": "intercepted_draw_loop",
        "low_stage": "bare_harness",
        "high_stage": "instrumented_harness",
        "label": "GL draw interposer dispatch",
        "description": "Bare synthetic GL loop vs the same loop with dylib interposition.",
    },
    {
        "primitive_id": "reference_activation",
        "role": ROLE_AGGREGATE_CONTROL,
        "unit": "published_frame",
        "low_stage": "loaded-disabled",
        "high_stage": "reference",
        "label": "REFERENCE activation tax",
        "description": "Aggregate loaded-disabled → lean REFERENCE; not additive with decomposition primitives.",
    },
    {
        "primitive_id": "counters_incremental",
        "role": ROLE_AGGREGATE_CONTROL,
        "unit": "published_frame",
        "low_stage": "reference",
        "high_stage": "counters",
        "label": "COUNTERS incremental tax",
        "description": "Aggregate reference → counters profile path; reconciliation target for decomposition sum.",
    },
    {
        "primitive_id": "scope_pair_clocks",
        "role": ROLE_DECOMPOSITION,
        "unit": "scope_pair",
        "low_stage": "counters_lite",
        "high_stage": "counters",
        "label": "scope_begin/end + scope snapshot",
        "description": "Counters-lite (scopes disabled) → full counters (scopes restored).",
    },
    {
        "primitive_id": "per_frame_counter_flush",
        "role": ROLE_DECOMPOSITION,
        "unit": "published_frame",
        "low_stage": "counters_lite_deferred_flush",
        "high_stage": "counters_lite",
        "label": "per-frame counter flush",
        "description": "Deferred flush → immediate flush on counters-lite path (positive flush cost).",
    },
    {
        "primitive_id": "gpu_timestamp_segment",
        "role": ROLE_DECOMPOSITION,
        "unit": "gpu_timestamp_segment",
        "low_stage": "counters",
        "high_stage": "counters",
        "label": "GPU timestamp segment",
        "description": "Counters with EU4_TEST_GPU_TIMESTAMPS=0 → 1; operations from M-line stamp count.",
        "high_env": {"EU4_TEST_GPU_TIMESTAMPS": "1"},
        "low_env": {"EU4_TEST_GPU_TIMESTAMPS": "0"},
    },
    {
        "primitive_id": "timed_gl_sample",
        "role": ROLE_DECOMPOSITION,
        "unit": "timed_gl_sample",
        "low_stage": "counters",
        "high_stage": "sampled",
        "label": "sparse timed GL sample",
        "description": "Counters aggregate timing → sampled forensic draw timing records.",
    },
)

PRIMITIVE_CATALOG = PRIMITIVE_SPECS
