#!/usr/bin/env python3
"""Pareto success criteria for Gfx submission optimizations (profiler-off validation)."""

from __future__ import annotations

import json
import math
import statistics
from pathlib import Path
from typing import Any

import eu4_benchmark as base

NON_INFERIOR_FRACTION = 0.02
PRIMARY_IMPROVEMENT_FRACTION = 0.05

ROOT = Path(__file__).resolve().parents[1]
SCENE_REFERENCE_MANIFEST = ROOT / "benchmark/submission_scene_reference.json"


def higher_is_non_inferior(baseline: float, candidate: float) -> bool:
    if not _finite_positive(baseline):
        return False
    if not math.isfinite(candidate):
        return False
    return candidate >= baseline * (1 - NON_INFERIOR_FRACTION)


def lower_is_non_inferior(baseline: float, candidate: float) -> bool:
    if not _finite_positive(baseline):
        return False
    if not math.isfinite(candidate):
        return False
    return candidate <= baseline * (1 + NON_INFERIOR_FRACTION)


def _finite_positive(value: float | int | None) -> bool:
    if value is None:
        return False
    try:
        number = float(value)
    except (TypeError, ValueError):
        return False
    return math.isfinite(number) and number > 0


def evaluate_submission_pareto_gate(
    baseline: dict[str, float],
    candidate: dict[str, float],
) -> dict[str, Any]:
    """Compare one candidate phase summary against a bracket baseline."""
    if not all(
        _finite_positive(baseline.get(key)) and _finite_positive(candidate.get(key))
        for key in ("swaps_per_s", "eu4_cpu_ms_per_s", "combined_w")
    ):
        return {
            "passed": False,
            "paths": [],
            "baseline": baseline,
            "candidate": candidate,
            "policy": {
                "non_inferior_fraction": NON_INFERIOR_FRACTION,
                "primary_improvement_fraction": PRIMARY_IMPROVEMENT_FRACTION,
            },
            "status": "invalid_metrics",
        }

    cpu_ok = candidate["eu4_cpu_ms_per_s"] <= baseline["eu4_cpu_ms_per_s"] * (
        1 - PRIMARY_IMPROVEMENT_FRACTION
    )
    power_ok = candidate["combined_w"] <= baseline["combined_w"] * (1 - PRIMARY_IMPROVEMENT_FRACTION)
    swaps_ok = candidate["swaps_per_s"] >= baseline["swaps_per_s"] * (1 + PRIMARY_IMPROVEMENT_FRACTION)
    swaps_non_inferior = higher_is_non_inferior(baseline["swaps_per_s"], candidate["swaps_per_s"])
    cpu_non_inferior = lower_is_non_inferior(baseline["eu4_cpu_ms_per_s"], candidate["eu4_cpu_ms_per_s"])
    power_non_inferior = lower_is_non_inferior(baseline["combined_w"], candidate["combined_w"])

    paths = []
    if power_ok and swaps_non_inferior:
        paths.append("A_combined_power")
    if cpu_ok and swaps_non_inferior:
        paths.append("B_eu4_cpu")
    if swaps_ok and cpu_non_inferior and power_non_inferior:
        paths.append("C_swaps")

    swaps = candidate["swaps_per_s"]
    return {
        "passed": bool(paths),
        "paths": paths,
        "baseline": baseline,
        "candidate": candidate,
        "eu4_cpu_ms_per_swap_baseline": baseline["eu4_cpu_ms_per_s"] / baseline["swaps_per_s"],
        "eu4_cpu_ms_per_swap_candidate": candidate["eu4_cpu_ms_per_s"] / swaps,
        "joules_per_swap_baseline": baseline.get("joules_per_swap"),
        "joules_per_swap_candidate": candidate.get("joules_per_swap"),
        "policy": {
            "non_inferior_fraction": NON_INFERIOR_FRACTION,
            "primary_improvement_fraction": PRIMARY_IMPROVEMENT_FRACTION,
        },
    }


def summarize_phase_metrics(summary: dict) -> dict[str, float]:
    swaps = summary.get("median_swaps_s") or summary.get("swaps_per_s") or summary.get("swap_rate") or 0.0
    cpu = summary.get("eu4_cpu_ms_per_s") or summary.get("eu4_cputime_ms_per_s") or 0.0
    combined = summary.get("combined_w") or summary.get("cpu_gpu_w") or 0.0
    swaps_f = float(swaps)
    cpu_f = float(cpu)
    return {
        "swaps_per_s": swaps_f,
        "eu4_cpu_ms_per_s": cpu_f,
        "combined_w": float(combined),
        "eu4_cpu_ms_per_swap": float(cpu_f / swaps_f) if swaps_f > 0 else 0.0,
        "joules_per_swap": float(summary.get("joules_per_swap") or 0.0),
    }


def resolve_repository_scene_reference() -> Path:
    if not SCENE_REFERENCE_MANIFEST.is_file():
        raise FileNotFoundError("missing benchmark/submission_scene_reference.json")
    manifest = json.loads(SCENE_REFERENCE_MANIFEST.read_text(encoding="utf-8"))
    image = ROOT / manifest["reference_image"]
    expected = manifest.get("sha256")
    if expected and base.sha256(image) != expected:
        raise ValueError("repository scene reference sha256 mismatch")
    if not image.is_file():
        raise FileNotFoundError(image)
    return image


def scene_gate(reference: Path, observed: Path) -> dict[str, Any]:
    from autonomous_runner import scene_difference
    from PIL import Image

    alignment = scene_difference(reference, observed)
    with Image.open(reference) as image:
        width, height = image.size
    passed = (
        alignment["mean_rgb_delta"] <= 15
        and abs(alignment["shift_x_px"]) <= 0.04 * width
        and abs(alignment["shift_y_px"]) <= 0.09 * height
    )
    return {"passed": passed, "alignment": alignment}


def _bracket_ratio(b_value: float, baseline: float, *, higher_is_better: bool) -> float | None:
    if not _finite_positive(baseline) or not _finite_positive(b_value):
        return None
    ratio = b_value / baseline
    if higher_is_better:
        return ratio
    return baseline / b_value if b_value > 0 else None


def bracket_normalized_ababa_gate(
    phases: list[dict],
    *,
    expected_candidate_capability_id: int = 0,
) -> dict[str, Any]:
    """Bracket-normalized A-B-A-B-A validation with per-phase scene and counter gates."""
    by_name = {p.get("name"): p for p in phases}
    required = ("a1", "b1", "a2", "b2", "a3")
    if not all(name in by_name for name in required):
        return {"status": "incomplete", "reason": "missing phases"}

    reference = resolve_repository_scene_reference()
    phase_reports: dict[str, Any] = {}
    all_valid = True
    for name in required:
        phase = by_name[name]
        metrics = summarize_phase_metrics(phase.get("summary") or {})
        finite_ok = all(_finite_positive(metrics.get(key)) for key in ("swaps_per_s", "eu4_cpu_ms_per_s", "combined_w"))
        screenshot = phase.get("screenshot")
        scene_result = None
        if screenshot:
            shot_path = Path(screenshot)
            if not shot_path.is_file():
                shot_path = Path(phase.get("run_dir", ".")) / screenshot
            if shot_path.is_file():
                scene_result = scene_gate(reference, shot_path)
        scene_ok = bool(scene_result and scene_result["passed"])
        control = phase.get("control_validation") or {}
        control_ok = control.get("status") == "passed"
        if phase.get("role") == "candidate" and expected_candidate_capability_id == 0:
            control_ok = control.get("status") in {"passed", "unsupported_candidate"}
        phase_ok = finite_ok and scene_ok and control_ok
        if not phase_ok:
            all_valid = False
        phase_reports[name] = {
            "metrics": metrics,
            "finite_metrics": finite_ok,
            "scene": scene_result,
            "control_validation": control,
            "phase_valid": phase_ok,
        }

    if not all_valid:
        return {
            "status": "failed",
            "reason": "one or more phases failed metric, scene, or control gates",
            "phases": phase_reports,
        }

    m = {name: phase_reports[name]["metrics"] for name in required}
    b1_gate = evaluate_submission_pareto_gate(
        {k: statistics.median([m["a1"][k], m["a2"][k]]) for k in m["a1"]},
        m["b1"],
    )
    b2_gate = evaluate_submission_pareto_gate(
        {k: statistics.median([m["a2"][k], m["a3"][k]]) for k in m["a2"]},
        m["b2"],
    )

    cpu_effects = [
        _bracket_ratio(m["b1"]["eu4_cpu_ms_per_s"], statistics.median([m["a1"]["eu4_cpu_ms_per_s"], m["a2"]["eu4_cpu_ms_per_s"]]), higher_is_better=False),
        _bracket_ratio(m["b2"]["eu4_cpu_ms_per_s"], statistics.median([m["a2"]["eu4_cpu_ms_per_s"], m["a3"]["eu4_cpu_ms_per_s"]]), higher_is_better=False),
    ]
    power_effects = [
        _bracket_ratio(m["b1"]["combined_w"], statistics.median([m["a1"]["combined_w"], m["a2"]["combined_w"]]), higher_is_better=False),
        _bracket_ratio(m["b2"]["combined_w"], statistics.median([m["a2"]["combined_w"], m["a3"]["combined_w"]]), higher_is_better=False),
    ]
    swap_effects = [
        _bracket_ratio(m["b1"]["swaps_per_s"], statistics.median([m["a1"]["swaps_per_s"], m["a2"]["swaps_per_s"]]), higher_is_better=True),
        _bracket_ratio(m["b2"]["swaps_per_s"], statistics.median([m["a2"]["swaps_per_s"], m["a3"]["swaps_per_s"]]), higher_is_better=True),
    ]

    def _aggregate(values: list[float | None]) -> float | None:
        clean = [v for v in values if v is not None and math.isfinite(v)]
        return statistics.median(clean) if clean else None

    aggregate = {
        "cpu_improvement_ratio": _aggregate(cpu_effects),
        "power_improvement_ratio": _aggregate(power_effects),
        "swap_improvement_ratio": _aggregate(swap_effects),
    }

    passed = b1_gate["passed"] or b2_gate["passed"]
    if expected_candidate_capability_id == 0:
        passed = False

    return {
        "status": "passed" if passed else "failed",
        "bracket_gates": {"b1": b1_gate, "b2": b2_gate},
        "normalized_effects": {
            "cpu": cpu_effects,
            "power": power_effects,
            "swaps": swap_effects,
            "aggregate_median": aggregate,
        },
        "phases": phase_reports,
        "expected_candidate_capability_id": expected_candidate_capability_id,
        "policy": {
            "non_inferior_fraction": NON_INFERIOR_FRACTION,
            "primary_improvement_fraction": PRIMARY_IMPROVEMENT_FRACTION,
            "comparison": "bracket_normalized",
        },
    }


# Backwards-compatible alias for callers not yet updated.
def bracket_median_effect(phases: list[dict], **kwargs: Any) -> dict[str, Any]:
    return bracket_normalized_ababa_gate(phases, **kwargs)
