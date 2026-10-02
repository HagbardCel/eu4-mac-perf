"""Tier-1 offline causal admission policies and replay over frozen trial data."""
from __future__ import annotations

import math
from dataclasses import dataclass

import frame_model_workload as workload

LEGACY_SEVEN_PAIR_POLICY_VERSION = "reference_counters_3pct_sampled_5pct_v1"
TIER1_CAUSAL_POLICY_VERSION = "tier1_causal_rel3pct_abs50us_v1"

EXPECTED_CAUSAL_GATE_NAMES = (
    "reference_elapsed_ns",
    "reference_cpu_ns",
    "counters_elapsed_ns",
    "counters_cpu_ns",
)
EXPECTED_FORENSIC_GATE_NAMES = (
    "sampled_elapsed_ns",
    "sampled_cpu_ns",
    "ablation_accounting_elapsed_ns",
    "ablation_accounting_cpu_ns",
    "ablation_writer_elapsed_ns",
    "ablation_writer_cpu_ns",
    "ablation_preparation_elapsed_ns",
    "ablation_preparation_cpu_ns",
)

# Frozen a priori error budget — see docs/tier1-causal-policy.md
TIER1_RELATIVE_LIMIT = 0.03
TIER1_ABSOLUTE_US_PER_FRAME = 50.0
TIER1_BOOTSTRAP_SEED = 1729
TIER1_BOOTSTRAP_SAMPLES = 2000
TIER1_BOOTSTRAP_CI_LOW_INDEX = 50
TIER1_BOOTSTRAP_CI_HIGH_INDEX = 1950
TIER1_FRAMES_PER_TRIAL = 4
TIER1_PAIR_COUNT = workload.TIER1_PAIR_COUNT


def validate_pairs(pairs) -> tuple[bool, str | None]:
    if not isinstance(pairs, list):
        return False, "pairs missing"
    if len(pairs) != TIER1_PAIR_COUNT:
        return False, f"expected {TIER1_PAIR_COUNT} valid pairs, found {len(pairs)}"
    for index, pair in enumerate(pairs):
        if not isinstance(pair, dict):
            return False, f"pair {index} invalid"
        for key in ("reference", "instrumented"):
            value = pair.get(key)
            if not isinstance(value, (int, float)) or not math.isfinite(value):
                return False, f"pair {index} {key} invalid"
        if pair["reference"] <= 0:
            return False, f"pair {index} reference must be > 0"
        if pair["instrumented"] < 0:
            return False, f"pair {index} instrumented must be >= 0"
    return True, None


@dataclass(frozen=True)
class Tier1CausalPolicy:
    version: str
    relative_limit: float
    absolute_us_per_frame: float
    bootstrap_seed: int = TIER1_BOOTSTRAP_SEED
    bootstrap_samples: int = TIER1_BOOTSTRAP_SAMPLES
    bootstrap_ci_low_index: int = TIER1_BOOTSTRAP_CI_LOW_INDEX
    bootstrap_ci_high_index: int = TIER1_BOOTSTRAP_CI_HIGH_INDEX
    frames_per_trial: int = TIER1_FRAMES_PER_TRIAL

    def gate_passes(self, gate: dict) -> bool:
        for key in (
            "median_fraction",
            "confidence_interval_95",
            "overhead_us_per_frame",
            "overhead_us_per_frame_ci95",
        ):
            if key not in gate:
                return False
        interval = gate["confidence_interval_95"]
        if not isinstance(interval, (list, tuple)) or len(interval) != 2:
            return False
        median = abs(float(gate["median_fraction"]))
        if max(median, abs(float(interval[0])), abs(float(interval[1]))) > self.relative_limit:
            return False
        overhead = abs(float(gate["overhead_us_per_frame"]))
        ci = gate["overhead_us_per_frame_ci95"]
        if not isinstance(ci, (list, tuple)) or len(ci) != 2:
            return False
        if max(overhead, abs(float(ci[0])), abs(float(ci[1]))) > self.absolute_us_per_frame:
            return False
        return True

    def summarize_gate(self, gate: dict) -> tuple[str, dict, str | None]:
        """Return status, evaluated gate dict, and optional unavailable reason."""
        pairs = gate.get("pairs")
        valid, reason = validate_pairs(pairs)
        if not valid:
            return "unavailable", dict(gate), reason
        evaluated = workload.paired_summary(
            pairs,
            self.relative_limit,
            self.frames_per_trial,
            bootstrap_seed=self.bootstrap_seed,
            bootstrap_samples=self.bootstrap_samples,
            ci_low_index=self.bootstrap_ci_low_index,
            ci_high_index=self.bootstrap_ci_high_index,
        )
        if self.gate_passes(evaluated):
            return "passed", evaluated, None
        return "failed", evaluated, None


TIER1_CAUSAL_POLICY = Tier1CausalPolicy(
    TIER1_CAUSAL_POLICY_VERSION,
    TIER1_RELATIVE_LIMIT,
    TIER1_ABSOLUTE_US_PER_FRAME,
)


def _merge_status(*statuses: str) -> str:
    if "failed" in statuses:
        return "failed"
    if "unavailable" in statuses:
        return "unavailable"
    if statuses and all(status == "passed" for status in statuses):
        return "passed"
    return "unavailable"


def _ablation_gates(recipe_entry: dict) -> dict[str, dict]:
    flat: dict[str, dict] = {}
    for component, axes in (recipe_entry.get("ablations") or {}).items():
        for axis, gate in axes.items():
            flat[f"ablation_{component}_{axis}"] = gate
    return flat


def evaluate_recipe_causal(recipe_entry: dict, policy: Tier1CausalPolicy = TIER1_CAUSAL_POLICY) -> dict:
    gates = recipe_entry.get("gates") or {}
    missing = [name for name in EXPECTED_CAUSAL_GATE_NAMES if name not in gates]
    if missing:
        return {
            "recipe": (recipe_entry.get("recipe") or {}).get("name"),
            "policy_version": policy.version,
            "status": "unavailable",
            "reason": f"missing causal gates: {', '.join(missing)}",
            "gates": {},
            "results": {},
        }
    evaluated_gates: dict[str, dict] = {}
    results: dict[str, str] = {}
    for name in EXPECTED_CAUSAL_GATE_NAMES:
        status, evaluated, reason = policy.summarize_gate(gates[name])
        evaluated_gates[name] = evaluated
        results[name] = status if reason is None else "unavailable"
    return {
        "recipe": (recipe_entry.get("recipe") or {}).get("name"),
        "policy_version": policy.version,
        "gates": evaluated_gates,
        "results": results,
        "status": _merge_status(*results.values()),
    }


def evaluate_recipe_forensic(recipe_entry: dict) -> dict:
    gates = recipe_entry.get("gates") or {}
    sampled = {name: gates[name] for name in gates if name.startswith("sampled_")}
    ablations = _ablation_gates(recipe_entry)
    all_gates = {**sampled, **ablations}
    missing = [name for name in EXPECTED_FORENSIC_GATE_NAMES if name not in all_gates]
    if missing:
        return {
            "recipe": (recipe_entry.get("recipe") or {}).get("name"),
            "policy_version": LEGACY_SEVEN_PAIR_POLICY_VERSION,
            "status": "unavailable",
            "reason": f"missing forensic gates: {', '.join(missing)}",
            "gates": all_gates,
        }
    if not all_gates:
        return {
            "recipe": (recipe_entry.get("recipe") or {}).get("name"),
            "policy_version": LEGACY_SEVEN_PAIR_POLICY_VERSION,
            "status": "unavailable",
            "reason": "no forensic gates present",
            "gates": {},
        }
    incomplete = [
        name
        for name, gate in all_gates.items()
        if gate.get("status") not in ("passed", "failed")
    ]
    if incomplete:
        return {
            "recipe": (recipe_entry.get("recipe") or {}).get("name"),
            "policy_version": LEGACY_SEVEN_PAIR_POLICY_VERSION,
            "status": "unavailable",
            "reason": f"incomplete forensic gate status: {', '.join(incomplete)}",
            "gates": all_gates,
        }
    legacy_limit_pass = all(gate.get("status") == "passed" for gate in all_gates.values())
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
    require_held_out: bool = True,
) -> dict:
    training_eval = [evaluate_recipe_causal(entry, policy) for entry in training]
    if held_out is None:
        held_eval = {
            "status": "unavailable",
            "reason": "held-out validation absent",
            "policy_version": policy.version,
            "recipe": None,
            "gates": {},
            "results": {},
        }
    else:
        held_eval = evaluate_recipe_causal(held_out, policy)
    statuses = [item["status"] for item in training_eval]
    if require_held_out:
        statuses.append(held_eval["status"])
    return {
        "status": _merge_status(*statuses) if statuses else "unavailable",
        "policy_version": policy.version,
        "relative_limit": policy.relative_limit,
        "absolute_us_per_frame": policy.absolute_us_per_frame,
        "bootstrap_seed": policy.bootstrap_seed,
        "bootstrap_samples": policy.bootstrap_samples,
        "bootstrap_ci_indices": [policy.bootstrap_ci_low_index, policy.bootstrap_ci_high_index],
        "training_recipes": training_eval,
        "held_out_recipe": held_eval,
    }


def summarize_forensic(recipes: list[dict]) -> dict:
    evaluations = [evaluate_recipe_forensic(entry) for entry in recipes]
    if not evaluations:
        return {
            "status": "unavailable",
            "reason": "no training recipes for forensic evaluation",
            "policy_version": LEGACY_SEVEN_PAIR_POLICY_VERSION,
            "recipes": [],
        }
    return {
        "status": _merge_status(*(item["status"] for item in evaluations)),
        "policy_version": LEGACY_SEVEN_PAIR_POLICY_VERSION,
        "recipes": evaluations,
    }


def replay_archive_preflight(
    preflight: dict,
    *,
    policy: Tier1CausalPolicy = TIER1_CAUSAL_POLICY,
    require_held_out: bool = True,
) -> dict:
    """Re-evaluate embedded trials under a Tier-1 policy without re-running harnesses."""
    representative = preflight.get("representative_workloads") or {}
    recipes = representative.get("recipes") or []
    training = [entry for entry in recipes if (entry.get("recipe") or {}).get("role") != "held_out"]
    held = next((entry for entry in recipes if (entry.get("recipe") or {}).get("role") == "held_out"), None)
    return {
        "validation_scope": "full_admission" if require_held_out else "training_only",
        "offline_causal_admission": summarize_admission(
            training, held, policy=policy, require_held_out=require_held_out,
        ),
        "offline_forensic_suitability": summarize_forensic(training),
    }
