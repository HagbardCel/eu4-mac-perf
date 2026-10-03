#!/usr/bin/env python3
"""Pareto success criteria for Gfx submission optimizations (profiler-off validation)."""

from __future__ import annotations

import statistics
from typing import Any

NON_INFERIOR_FRACTION = 0.02
PRIMARY_IMPROVEMENT_FRACTION = 0.05


def _non_inferior(baseline: float, candidate: float) -> bool:
    if baseline <= 0:
        return True
    return candidate >= baseline * (1 - NON_INFERIOR_FRACTION)


def evaluate_submission_pareto_gate(
    baseline: dict[str, float],
    candidate: dict[str, float],
) -> dict[str, Any]:
    """Compare one candidate phase summary against baseline (higher swaps/s is good)."""
    cpu_ok = candidate.get("eu4_cpu_ms_per_s", 0) <= baseline.get("eu4_cpu_ms_per_s", 0) * (
        1 - PRIMARY_IMPROVEMENT_FRACTION
    )
    power_ok = candidate.get("combined_w", 0) <= baseline.get("combined_w", 0) * (
        1 - PRIMARY_IMPROVEMENT_FRACTION
    )
    swaps_ok = candidate.get("swaps_per_s", 0) >= baseline.get("swaps_per_s", 0) * (
        1 + PRIMARY_IMPROVEMENT_FRACTION
    )
    swaps_non_inferior = _non_inferior(baseline.get("swaps_per_s", 0), candidate.get("swaps_per_s", 0))
    cpu_non_inferior = _non_inferior(candidate.get("eu4_cpu_ms_per_s", 0), baseline.get("eu4_cpu_ms_per_s", 0))
    power_non_inferior = _non_inferior(candidate.get("combined_w", 0), baseline.get("combined_w", 0))

    paths = []
    if power_ok and swaps_non_inferior:
        paths.append("A_combined_power")
    if cpu_ok and swaps_non_inferior:
        paths.append("B_eu4_cpu")
    if swaps_ok and cpu_non_inferior and power_non_inferior:
        paths.append("C_swaps")

    return {
        "passed": bool(paths),
        "paths": paths,
        "baseline": baseline,
        "candidate": candidate,
        "cpu_ms_per_swap_baseline": baseline.get("cpu_ms_per_swap"),
        "cpu_ms_per_swap_candidate": candidate.get("cpu_ms_per_swap"),
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
    return {
        "swaps_per_s": float(swaps),
        "eu4_cpu_ms_per_s": float(cpu),
        "combined_w": float(combined),
        "cpu_ms_per_swap": float(cpu / swaps) if swaps > 0 else 0.0,
        "joules_per_swap": float(summary.get("joules_per_swap") or 0.0),
    }


def bracket_median_effect(phases: list[dict]) -> dict[str, Any]:
    """Median candidate minus median reference across bracket pairs."""
    refs = [p for p in phases if p.get("role") == "reference"]
    cands = [p for p in phases if p.get("role") == "candidate"]
    if not refs or not cands:
        return {"status": "incomplete"}
    ref_metrics = [summarize_phase_metrics(p["summary"]) for p in refs]
    cand_metrics = [summarize_phase_metrics(p["summary"]) for p in cands]
    merged_ref = {key: statistics.median([m[key] for m in ref_metrics]) for key in ref_metrics[0]}
    merged_cand = {key: statistics.median([m[key] for m in cand_metrics]) for key in cand_metrics[0]}
    return evaluate_submission_pareto_gate(merged_ref, merged_cand)
