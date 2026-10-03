"""Tier-1 offline causal admission policies and replay over frozen trial data."""
from __future__ import annotations

import math
import statistics
from dataclasses import dataclass
from pathlib import PurePosixPath

import frame_model_workload as workload

LEGACY_SEVEN_PAIR_POLICY_VERSION = "reference_counters_3pct_sampled_5pct_v1"
TIER1_CAUSAL_POLICY_VERSION_V1 = "tier1_causal_rel3pct_abs50us_v1"
TIER1_CAUSAL_POLICY_VERSION = "tier1_causal_rel3pct_abs50us_v2"
TIER1_CAUSAL_POLICY_VERSION_V3 = "tier1_causal_steady_state_hybrid_v3"

# Steady-state measurement contract (WP8); required for captures evaluated under v3.
TIER1_V3_POST_ARM_PRIME_FRAMES = 1
TIER1_V3_MEASURED_FRAMES_DEFAULT = 4
TIER1_V3_MEASUREMENT_VARIANT = "four_frame_post_arm_prime_1"

# Hybrid admission: |delta_us| <= min(cap, max(relative_limit * baseline_us_per_frame, floor_us)).
# Floors calibrated from WP8 capture 3c1e0db7 (primed windows); see docs/wp9-tier1-v3-design.md.
TIER1_V3_REFERENCE_RELATIVE_LIMIT = 0.03
TIER1_V3_REFERENCE_ABSOLUTE_FLOOR_US = 6.0
TIER1_V3_COUNTERS_RELATIVE_LIMIT = 0.12
TIER1_V3_COUNTERS_ABSOLUTE_FLOOR_US = 11.0
TIER1_V3_ABSOLUTE_CAP_US_PER_FRAME = 50.0
TIER1_V3_COMPLETION_WALL_RELATIVE_LIMIT = 0.20

EXPECTED_CAUSAL_GATE_NAMES_V3 = (
    "reference_submission_plus_drain_elapsed_ns",
    "reference_cpu_ns",
    "counters_submission_plus_drain_elapsed_ns",
    "counters_cpu_ns",
)

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
HELD_OUT_FIXTURE_PREFIX = "analysis/held-out/"
HELD_OUT_UNQUALIFIED_REASON = "held-out evidence was not frozen before timing"
HELD_OUT_CONTAMINATED_REASON = (
    "held-out fixture was previously measured as exploratory evidence"
)


def _normalized_fixture_path(path: str) -> str | None:
    if not path:
        return None
    normalized = PurePosixPath(path.replace("\\", "/"))
    if ".." in normalized.parts:
        return None
    return normalized.as_posix()


def held_out_admission_qualified(entry: dict | None) -> bool:
    if not entry:
        return False
    recipe = entry.get("recipe") or {}
    if recipe.get("role") != "held_out":
        return False
    if recipe.get("admission_qualified") is not True or recipe.get("fixture_committed") is not True:
        return False
    fixture_path = _normalized_fixture_path(recipe.get("fixture_path") or "")
    if fixture_path != workload.HELD_OUT_FIXTURE_PATH:
        return False
    fixture_sha = recipe.get("fixture_sha256")
    recipe_sha = recipe.get("sha256")
    if not fixture_sha or not recipe_sha or fixture_sha != recipe_sha:
        return False
    if fixture_sha in workload.EXPLORATORY_HELD_OUT_SHA256S:
        return False
    return True


def held_out_disqualification_reason(entry: dict) -> str:
    recipe = entry.get("recipe") or {}
    sha = recipe.get("fixture_sha256") or recipe.get("sha256")
    if sha in workload.EXPLORATORY_HELD_OUT_SHA256S:
        return HELD_OUT_CONTAMINATED_REASON
    return HELD_OUT_UNQUALIFIED_REASON


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

    def __post_init__(self) -> None:
        if self.relative_limit < 0 or self.absolute_us_per_frame < 0:
            raise ValueError("Tier-1 limits must be non-negative")
        if self.frames_per_trial <= 0:
            raise ValueError("frames_per_trial must be positive")
        if self.bootstrap_samples <= 0:
            raise ValueError("bootstrap_samples must be positive")
        low, high = self.bootstrap_ci_low_index, self.bootstrap_ci_high_index
        if not (0 <= low <= high < self.bootstrap_samples):
            raise ValueError("bootstrap CI indices must satisfy 0 <= low <= high < bootstrap_samples")

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


def hybrid_allowed_us_per_frame(
    pairs: list[dict],
    *,
    frames: int,
    relative_limit: float,
    absolute_floor_us: float,
    absolute_cap_us: float = TIER1_V3_ABSOLUTE_CAP_US_PER_FRAME,
) -> float:
    baseline_us = statistics.median(pair["reference"] / (1000.0 * frames) for pair in pairs)
    return min(max(relative_limit * baseline_us, absolute_floor_us), absolute_cap_us)


@dataclass(frozen=True)
class Tier1CausalPolicyV3:
    version: str
    post_arm_prime_frames: int = TIER1_V3_POST_ARM_PRIME_FRAMES
    measured_frames: int = TIER1_V3_MEASURED_FRAMES_DEFAULT
    reference_relative_limit: float = TIER1_V3_REFERENCE_RELATIVE_LIMIT
    reference_absolute_floor_us: float = TIER1_V3_REFERENCE_ABSOLUTE_FLOOR_US
    counters_relative_limit: float = TIER1_V3_COUNTERS_RELATIVE_LIMIT
    counters_absolute_floor_us: float = TIER1_V3_COUNTERS_ABSOLUTE_FLOOR_US
    completion_wall_relative_limit: float = TIER1_V3_COMPLETION_WALL_RELATIVE_LIMIT
    absolute_cap_us_per_frame: float = TIER1_V3_ABSOLUTE_CAP_US_PER_FRAME
    bootstrap_seed: int = TIER1_BOOTSTRAP_SEED
    bootstrap_samples: int = TIER1_BOOTSTRAP_SAMPLES
    bootstrap_ci_low_index: int = TIER1_BOOTSTRAP_CI_LOW_INDEX
    bootstrap_ci_high_index: int = TIER1_BOOTSTRAP_CI_HIGH_INDEX

    def __post_init__(self) -> None:
        if self.post_arm_prime_frames < 0 or self.measured_frames <= 0:
            raise ValueError("invalid steady-state measurement contract")
        if self.absolute_cap_us_per_frame < 0:
            raise ValueError("absolute_cap_us_per_frame must be non-negative")

    def _hybrid_limits_for_gate(self, gate_name: str) -> tuple[float, float]:
        if gate_name.startswith("reference_"):
            return self.reference_relative_limit, self.reference_absolute_floor_us
        if gate_name.startswith("counters_"):
            return self.counters_relative_limit, self.counters_absolute_floor_us
        raise ValueError(f"unknown v3 gate {gate_name}")

    def _relative_ci_passes(self, gate: dict, *, relative_limit: float, frames: int) -> bool:
        pairs = gate.get("pairs")
        valid, _ = validate_pairs(pairs)
        if not valid:
            return False
        evaluated = workload.paired_summary(
            pairs,
            relative_limit,
            frames,
            bootstrap_seed=self.bootstrap_seed,
            bootstrap_samples=self.bootstrap_samples,
            ci_low_index=self.bootstrap_ci_low_index,
            ci_high_index=self.bootstrap_ci_high_index,
        )
        interval = evaluated["confidence_interval_95"]
        median = abs(float(evaluated["median_fraction"]))
        return max(median, abs(float(interval[0])), abs(float(interval[1]))) <= relative_limit

    def hybrid_gate_passes(self, gate: dict, *, gate_name: str, frames: int) -> bool:
        if gate_name.endswith("_elapsed_ns"):
            return self._relative_ci_passes(
                gate,
                relative_limit=self.completion_wall_relative_limit,
                frames=frames,
            )
        pairs = gate.get("pairs")
        valid, _ = validate_pairs(pairs)
        if not valid:
            return False
        relative_limit, floor_us = self._hybrid_limits_for_gate(gate_name)
        allowed = hybrid_allowed_us_per_frame(
            pairs,
            frames=frames,
            relative_limit=relative_limit,
            absolute_floor_us=floor_us,
            absolute_cap_us=self.absolute_cap_us_per_frame,
        )
        evaluated = workload.paired_summary(
            pairs,
            relative_limit,
            frames,
            bootstrap_seed=self.bootstrap_seed,
            bootstrap_samples=self.bootstrap_samples,
            ci_low_index=self.bootstrap_ci_low_index,
            ci_high_index=self.bootstrap_ci_high_index,
        )
        overhead = abs(float(evaluated["overhead_us_per_frame"]))
        ci = evaluated["overhead_us_per_frame_ci95"]
        if not isinstance(ci, (list, tuple)) or len(ci) != 2:
            return False
        return max(overhead, abs(float(ci[0])), abs(float(ci[1]))) <= allowed

    def summarize_gate(self, gate: dict, *, gate_name: str, frames: int) -> tuple[str, dict, str | None]:
        pairs = gate.get("pairs")
        valid, reason = validate_pairs(pairs)
        if not valid:
            return "unavailable", dict(gate), reason
        if gate_name.endswith("_elapsed_ns"):
            evaluated = workload.paired_summary(
                pairs,
                self.completion_wall_relative_limit,
                frames,
                bootstrap_seed=self.bootstrap_seed,
                bootstrap_samples=self.bootstrap_samples,
                ci_low_index=self.bootstrap_ci_low_index,
                ci_high_index=self.bootstrap_ci_high_index,
            )
            evaluated["completion_wall_relative_limit"] = self.completion_wall_relative_limit
            if self.hybrid_gate_passes(gate, gate_name=gate_name, frames=frames):
                return "passed", evaluated, None
            return "failed", evaluated, None
        relative_limit, floor_us = self._hybrid_limits_for_gate(gate_name)
        evaluated = workload.paired_summary(
            pairs,
            relative_limit,
            frames,
            bootstrap_seed=self.bootstrap_seed,
            bootstrap_samples=self.bootstrap_samples,
            ci_low_index=self.bootstrap_ci_low_index,
            ci_high_index=self.bootstrap_ci_high_index,
        )
        allowed = hybrid_allowed_us_per_frame(
            pairs,
            frames=frames,
            relative_limit=relative_limit,
            absolute_floor_us=floor_us,
            absolute_cap_us=self.absolute_cap_us_per_frame,
        )
        evaluated["hybrid_allowed_us_per_frame"] = allowed
        evaluated["hybrid_absolute_floor_us"] = floor_us
        evaluated["hybrid_relative_limit"] = relative_limit
        if self.hybrid_gate_passes(gate, gate_name=gate_name, frames=frames):
            return "passed", evaluated, None
        return "failed", evaluated, None


TIER1_CAUSAL_POLICY_V3 = Tier1CausalPolicyV3(TIER1_CAUSAL_POLICY_VERSION_V3)


def gates_from_wp8_steady_state_recipe(
    recipe_entry: dict,
    *,
    variant: str = TIER1_V3_MEASUREMENT_VARIANT,
) -> dict[str, dict]:
    comparisons = recipe_entry.get("variant_comparisons") or {}
    if variant not in comparisons:
        raise ValueError(f"variant {variant} missing from steady-state recipe")
    block = comparisons[variant]
    missing = [name for name in EXPECTED_CAUSAL_GATE_NAMES_V3 if name not in block]
    if missing:
        raise ValueError(f"variant {variant} missing gates: {', '.join(missing)}")
    return {name: block[name] for name in EXPECTED_CAUSAL_GATE_NAMES_V3}


def evaluate_recipe_causal_v3(
    recipe_entry: dict,
    policy: Tier1CausalPolicyV3 = TIER1_CAUSAL_POLICY_V3,
    *,
    variant: str = TIER1_V3_MEASUREMENT_VARIANT,
    measured_frames: int | None = None,
) -> dict:
    try:
        gates = gates_from_wp8_steady_state_recipe(recipe_entry, variant=variant)
    except ValueError as error:
        return {
            "recipe": (recipe_entry.get("recipe") or {}).get("name"),
            "policy_version": policy.version,
            "measurement_variant": variant,
            "status": "unavailable",
            "reason": str(error),
            "gates": {},
            "results": {},
        }
    frames = measured_frames
    if frames is None:
        variant_meta = next(
            (
                item
                for item in (recipe_entry.get("measurement_variants") or [])
                if item.get("name") == variant
            ),
            None,
        )
        frames = int((variant_meta or {}).get("measured_frames") or policy.measured_frames)
    evaluated_gates: dict[str, dict] = {}
    results: dict[str, str] = {}
    for name in EXPECTED_CAUSAL_GATE_NAMES_V3:
        status, evaluated, reason = policy.summarize_gate(gates[name], gate_name=name, frames=frames)
        evaluated_gates[name] = evaluated
        results[name] = status if reason is None else "unavailable"
    return {
        "recipe": (recipe_entry.get("recipe") or {}).get("name"),
        "policy_version": policy.version,
        "measurement_variant": variant,
        "measured_frames": frames,
        "post_arm_prime_frames": policy.post_arm_prime_frames,
        "gates": evaluated_gates,
        "results": results,
        "status": _merge_status(*results.values()),
    }


def evaluate_wp8_steady_state_training_v3(
    preflight: dict,
    policy: Tier1CausalPolicyV3 = TIER1_CAUSAL_POLICY_V3,
    *,
    variant: str = TIER1_V3_MEASUREMENT_VARIANT,
) -> dict:
    block = preflight.get("steady_state_tier1_diagnosis") or {}
    recipes = block.get("recipes") or []
    evaluations = [
        evaluate_recipe_causal_v3(entry, policy, variant=variant) for entry in recipes
    ]
    return {
        "status": _merge_status(*(item["status"] for item in evaluations)) if evaluations else "unavailable",
        "policy_version": policy.version,
        "measurement_variant": variant,
        "post_arm_prime_frames": policy.post_arm_prime_frames,
        "training_recipes": evaluations,
    }


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
            "qualification": "absent",
            "policy_version": policy.version,
            "recipe": None,
            "gates": {},
            "results": {},
        }
    elif require_held_out and not held_out_admission_qualified(held_out):
        recipe_meta = held_out.get("recipe") or {}
        held_eval = {
            "status": "unavailable",
            "reason": held_out_disqualification_reason(held_out),
            "qualification": "exploratory",
            "policy_version": policy.version,
            "recipe": recipe_meta.get("name"),
            "gates": {},
            "results": {},
        }
    else:
        held_eval = evaluate_recipe_causal(held_out, policy)
        held_eval = {
            **held_eval,
            "qualification": "qualified" if held_out_admission_qualified(held_out) else "exploratory",
        }
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
