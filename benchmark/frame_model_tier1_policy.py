"""Tier-1 offline causal admission policies and replay over frozen trial data."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

LEGACY_SEVEN_PAIR_POLICY_VERSION = "reference_counters_3pct_sampled_5pct_v1"
TIER1_CAUSAL_POLICY_VERSION = "tier1_causal_rel3pct_abs50us_v1"

CAUSAL_GATE_SUFFIXES = (
    "reference_elapsed_ns",
    "reference_cpu_ns",
    "counters_elapsed_ns",
    "counters_cpu_ns",
)
FORENSIC_GATE_SUFFIXES = (
    "sampled_elapsed_ns",
    "sampled_cpu_ns",
)

# A priori error budget (not tuned to mesh/borders/text_ui outcomes).
TIER1_RELATIVE_LIMIT = 0.03
TIER1_ABSOLUTE_US_PER_FRAME = 50.0


@dataclass(frozen=True)
class Tier1CausalPolicy:
    version: str
    relative_limit: float
    absolute_us_per_frame: float

    def gate_passes(self, gate: dict) -> bool:
        median = abs(gate.get("median_fraction", 0.0))
        interval = gate.get("confidence_interval_95") or [median, median]
        if max(median, abs(interval[0]), abs(interval[1])) > self.relative_limit:
            return False
        overhead = abs(gate.get("overhead_us_per_frame", 0.0))
        ci = gate.get("overhead_us_per_frame_ci95") or [overhead, overhead]
        if max(overhead, abs(ci[0]), abs(ci[1])) > self.absolute_us_per_frame:
            return False
        return True


TIER1_CAUSAL_POLICY = Tier1CausalPolicy(
    TIER1_CAUSAL_POLICY_VERSION,
    TIER1_RELATIVE_LIMIT,
    TIER1_ABSOLUTE_US_PER_FRAME,
)


def causal_gate_names(gates: dict) -> list[str]:
    return [name for name in gates if any(name.endswith(suffix) for suffix in CAUSAL_GATE_SUFFIXES)]


def forensic_gate_names(gates: dict) -> list[str]:
    names = [name for name in gates if any(name.endswith(suffix) for suffix in FORENSIC_GATE_SUFFIXES)]
    for component, axes in (gates.get("_ablations") or {}).items():
        for axis, gate in axes.items():
            names.append(f"ablation_{component}_{axis}")
    return names


def _ablation_gates(recipe_entry: dict) -> dict[str, dict]:
    flat: dict[str, dict] = {}
    for component, axes in (recipe_entry.get("ablations") or {}).items():
        for axis, gate in axes.items():
            flat[f"ablation_{component}_{axis}"] = gate
    return flat


def evaluate_recipe_causal(recipe_entry: dict, policy: Tier1CausalPolicy = TIER1_CAUSAL_POLICY) -> dict:
    gates = recipe_entry.get("gates") or {}
    causal = {name: gates[name] for name in causal_gate_names(gates)}
    results = {name: policy.gate_passes(gate) for name, gate in causal.items()}
    return {
        "recipe": (recipe_entry.get("recipe") or {}).get("name"),
        "policy_version": policy.version,
        "gates": causal,
        "results": results,
        "status": "passed" if results and all(results.values()) else "failed",
    }


def evaluate_recipe_forensic(recipe_entry: dict) -> dict:
    gates = recipe_entry.get("gates") or {}
    sampled = {name: gates[name] for name in gates if name.startswith("sampled_")}
    ablations = _ablation_gates(recipe_entry)
    all_gates = {**sampled, **ablations}
    legacy_limit_pass = all(g.get("status") == "passed" for g in all_gates.values()) if all_gates else True
    return {
        "recipe": (recipe_entry.get("recipe") or {}).get("name"),
        "policy_version": LEGACY_SEVEN_PAIR_POLICY_VERSION,
        "gates": all_gates,
        "diagnostic_matrix": recipe_entry.get("diagnostic_matrix"),
        "status": "passed" if legacy_limit_pass else "failed",
    }


def summarize_admission(
    training: list[dict],
    held_out: dict | None,
    *,
    policy: Tier1CausalPolicy = TIER1_CAUSAL_POLICY,
) -> dict:
    training_eval = [evaluate_recipe_causal(entry, policy) for entry in training]
    held_eval = evaluate_recipe_causal(held_out, policy) if held_out else None
    statuses = [item["status"] for item in training_eval]
    if held_eval:
        statuses.append(held_eval["status"])
    overall = "passed" if statuses and all(status == "passed" for status in statuses) else "failed"
    return {
        "status": overall,
        "policy_version": policy.version,
        "relative_limit": policy.relative_limit,
        "absolute_us_per_frame": policy.absolute_us_per_frame,
        "training_recipes": training_eval,
        "held_out_recipe": held_eval,
    }


def summarize_forensic(recipes: list[dict]) -> dict:
    evaluations = [evaluate_recipe_forensic(entry) for entry in recipes]
    status = "passed" if evaluations and all(item["status"] == "passed" for item in evaluations) else "failed"
    return {
        "status": status,
        "policy_version": LEGACY_SEVEN_PAIR_POLICY_VERSION,
        "recipes": evaluations,
    }


def replay_archive_preflight(preflight: dict, *, policy: Tier1CausalPolicy = TIER1_CAUSAL_POLICY) -> dict:
    """Re-evaluate embedded trials under a Tier-1 policy without re-running harnesses."""
    representative = preflight.get("representative_workloads") or {}
    recipes = representative.get("recipes") or []
    training = [entry for entry in recipes if (entry.get("recipe") or {}).get("role") != "held_out"]
    held = next((entry for entry in recipes if (entry.get("recipe") or {}).get("role") == "held_out"), None)
    return {
        "offline_causal_admission": summarize_admission(training, held, policy=policy),
        "offline_forensic_suitability": summarize_forensic(recipes),
    }
