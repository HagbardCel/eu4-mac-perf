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
TIER1_V3_CPU_MEASURED_FRAMES = 4
TIER1_V3_CPU_MEASUREMENT_VARIANT = "four_frame_post_arm_prime_1"
TIER1_V3_WALL_MEASURED_FRAMES = 40
TIER1_V3_WALL_MEASUREMENT_VARIANT = "forty_frame_post_arm_prime_1"
# Back-compat alias for CPU admission variant.
TIER1_V3_MEASUREMENT_VARIANT = TIER1_V3_CPU_MEASUREMENT_VARIANT
TIER1_V3_MEASURED_FRAMES_DEFAULT = TIER1_V3_CPU_MEASURED_FRAMES

# Hybrid admission: |delta_us| <= min(cap, max(relative_limit * baseline_us_per_frame, floor_us)).
# Floors calibrated from WP8 capture 3c1e0db7 (primed windows); see docs/wp9-tier1-v3-design.md.
TIER1_V3_REFERENCE_RELATIVE_LIMIT = 0.03
TIER1_V3_REFERENCE_ABSOLUTE_FLOOR_US = 6.0
TIER1_V3_COUNTERS_RELATIVE_LIMIT = 0.12
TIER1_V3_COUNTERS_ABSOLUTE_FLOOR_US = 11.0
TIER1_V3_ABSOLUTE_CAP_US_PER_FRAME = 50.0
TIER1_V3_COMPLETION_WALL_ADMISSION_RELATIVE_LIMIT = 0.05

TIER1_V3_TRAINING_RECIPE_NAMES = ("mesh", "borders", "text_ui")
TIER1_V3_CAUSAL_STAGES = frozenset({"bare", "reference", "counters"})
TIER1_V3_TRIAL_COUNT = workload.TIER1_PAIR_COUNT
TIER1_V3_TRIAL_IDS = tuple(range(TIER1_V3_TRIAL_COUNT))
STEADY_STATE_VARIANT_NAMES = (
    "four_frame_baseline",
    "four_frame_post_arm_prime_1",
    "forty_frame_post_arm_prime_1",
)

EXPECTED_V3_ADMISSION_GATE_NAMES = (
    "reference_cpu_ns",
    "counters_cpu_ns",
    "reference_submission_plus_drain_elapsed_ns",
    "counters_submission_plus_drain_elapsed_ns",
)
# Back-compat aliases.
EXPECTED_CAUSAL_GATE_NAMES_V3 = ("reference_cpu_ns", "counters_cpu_ns")
EXPECTED_V3_COMPLETION_WALL_GATE_NAMES = (
    "reference_submission_plus_drain_elapsed_ns",
    "counters_submission_plus_drain_elapsed_ns",
)

_V3_GATE_PAIRINGS = (
    ("reference", "bare"),
    ("counters", "reference"),
)
_V3_CPU_AXES = ("cpu_ns",)
_V3_WALL_AXES = ("submission_plus_drain_elapsed_ns",)
def classify_completion_wall_equivalence_ci(
    ci_low: float,
    ci_high: float,
    *,
    limit: float,
) -> tuple[str, str | None]:
    """Tri-state admission for a signed fraction CI against [-limit, +limit]."""
    low, high = float(ci_low), float(ci_high)
    if low > high:
        low, high = high, low
    band_low, band_high = -limit, limit
    if low >= band_low and high <= band_high:
        return "passed", None
    if low > band_high:
        return "failed", "completion-wall 95% CI wholly above admission relative band"
    if high < band_low:
        return "failed", "completion-wall 95% CI wholly below admission relative band"
    return (
        "unavailable",
        "completion-wall 95% CI overlaps admission band but cannot establish equivalence",
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
    reference_relative_limit: float = TIER1_V3_REFERENCE_RELATIVE_LIMIT
    reference_absolute_floor_us: float = TIER1_V3_REFERENCE_ABSOLUTE_FLOOR_US
    counters_relative_limit: float = TIER1_V3_COUNTERS_RELATIVE_LIMIT
    counters_absolute_floor_us: float = TIER1_V3_COUNTERS_ABSOLUTE_FLOOR_US
    cpu_measured_frames: int = TIER1_V3_CPU_MEASURED_FRAMES
    wall_measured_frames: int = TIER1_V3_WALL_MEASURED_FRAMES
    completion_wall_admission_relative_limit: float = TIER1_V3_COMPLETION_WALL_ADMISSION_RELATIVE_LIMIT
    absolute_cap_us_per_frame: float = TIER1_V3_ABSOLUTE_CAP_US_PER_FRAME
    bootstrap_seed: int = TIER1_BOOTSTRAP_SEED
    bootstrap_samples: int = TIER1_BOOTSTRAP_SAMPLES
    bootstrap_ci_low_index: int = TIER1_BOOTSTRAP_CI_LOW_INDEX
    bootstrap_ci_high_index: int = TIER1_BOOTSTRAP_CI_HIGH_INDEX

    @property
    def measured_frames(self) -> int:
        return self.cpu_measured_frames

    def __post_init__(self) -> None:
        if self.post_arm_prime_frames < 0 or self.cpu_measured_frames <= 0 or self.wall_measured_frames <= 0:
            raise ValueError("invalid steady-state measurement contract")
        if self.absolute_cap_us_per_frame < 0:
            raise ValueError("absolute_cap_us_per_frame must be non-negative")

    def summarize_completion_wall_gate(
        self,
        gate: dict,
        *,
        frames: int,
    ) -> tuple[str, dict, str | None]:
        pairs = gate.get("pairs")
        valid, reason = validate_pairs(pairs)
        if not valid:
            return "unavailable", dict(gate), reason
        evaluated = workload.paired_summary(
            pairs,
            self.completion_wall_admission_relative_limit,
            frames,
            bootstrap_seed=self.bootstrap_seed,
            bootstrap_samples=self.bootstrap_samples,
            ci_low_index=self.bootstrap_ci_low_index,
            ci_high_index=self.bootstrap_ci_high_index,
        )
        evaluated["completion_wall_admission_relative_limit"] = (
            self.completion_wall_admission_relative_limit
        )
        evaluated["measurement_variant"] = TIER1_V3_WALL_MEASUREMENT_VARIANT
        interval = evaluated["confidence_interval_95"]
        status, reason = classify_completion_wall_equivalence_ci(
            interval[0],
            interval[1],
            limit=self.completion_wall_admission_relative_limit,
        )
        return status, evaluated, reason

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


def _expected_stage_order_for_trial(trial: int) -> list[str]:
    stages = ["bare", "reference", "counters"]
    return list(reversed(stages)) if trial % 2 else stages


def _expected_variant_order_for_trial(trial: int) -> list[str]:
    rotation = trial % len(STEADY_STATE_VARIANT_NAMES)
    return list(STEADY_STATE_VARIANT_NAMES[rotation:]) + list(STEADY_STATE_VARIANT_NAMES[:rotation])


def _sorted_wp8_trials(recipe_entry: dict) -> list[dict]:
    trials = recipe_entry.get("trials") or []
    return sorted(trials, key=lambda item: int(item["trial"]))


def _parse_trial_id(trial: dict, *, recipe_name: str) -> int | None:
    raw = trial.get("trial")
    if raw is None:
        return None
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


def validate_wp8_steady_state_recipe_experiment_layout(recipe_entry: dict) -> str | None:
    recipe_name = (recipe_entry.get("recipe") or {}).get("name", "?")
    trials = recipe_entry.get("trials") or []
    if len(trials) != TIER1_V3_TRIAL_COUNT:
        return f"expected {TIER1_V3_TRIAL_COUNT} trials, got {len(trials)}"
    trial_ids: list[int] = []
    for trial in trials:
        trial_id = _parse_trial_id(trial, recipe_name=recipe_name)
        if trial_id is None:
            return f"recipe {recipe_name} invalid or missing trial id"
        trial_ids.append(trial_id)
    if sorted(trial_ids) != list(TIER1_V3_TRIAL_IDS):
        return f"recipe {recipe_name} trial ids must be {list(TIER1_V3_TRIAL_IDS)}, got {trial_ids}"
    if len(set(trial_ids)) != TIER1_V3_TRIAL_COUNT:
        return f"recipe {recipe_name} duplicate trial ids"
    for trial in trials:
        trial_id = _parse_trial_id(trial, recipe_name=recipe_name)
        if trial_id is None:
            return f"recipe {recipe_name} invalid or missing trial id"
        if trial.get("order") != _expected_stage_order_for_trial(trial_id):
            return (
                f"recipe {recipe_name} trial {trial_id} stage order "
                f"{trial.get('order')} != expected {_expected_stage_order_for_trial(trial_id)}"
            )
        if trial.get("variant_order") != _expected_variant_order_for_trial(trial_id):
            return (
                f"recipe {recipe_name} trial {trial_id} variant_order "
                f"{trial.get('variant_order')} != expected {_expected_variant_order_for_trial(trial_id)}"
            )
    return None


def validate_wp8_steady_state_training_recipes(
    recipes: list[dict],
) -> str | None:
    if len(recipes) != len(TIER1_V3_TRAINING_RECIPE_NAMES):
        return (
            f"expected exactly {len(TIER1_V3_TRAINING_RECIPE_NAMES)} training recipes, "
            f"got {len(recipes)}"
        )
    seen: set[str] = set()
    for entry in recipes:
        recipe = entry.get("recipe") or {}
        name = recipe.get("name")
        role = recipe.get("role")
        if role != "training":
            return f"recipe {name!r} has role {role!r}; only training recipes allowed"
        if name not in TIER1_V3_TRAINING_RECIPE_NAMES:
            return f"unexpected recipe name {name!r}"
        if name in seen:
            return f"duplicate training recipe {name!r}"
        seen.add(name)
    missing = set(TIER1_V3_TRAINING_RECIPE_NAMES) - seen
    if missing:
        return f"missing training recipes: {', '.join(sorted(missing))}"
    return None


def validate_wp8_steady_state_recipe_measurement_contract(
    recipe_entry: dict,
    *,
    variant: str,
    post_arm_prime_frames: int,
    measured_frames: int,
) -> str | None:
    layout_reason = validate_wp8_steady_state_recipe_experiment_layout(recipe_entry)
    if layout_reason:
        return layout_reason
    variants_meta = recipe_entry.get("measurement_variants") or []
    meta = next((item for item in variants_meta if item.get("name") == variant), None)
    if meta is None:
        return f"measurement_variants missing {variant}"
    try:
        meta_prime = int(meta.get("post_arm_prime_frames", -1))
        meta_measured = int(meta.get("measured_frames", -1))
    except (TypeError, ValueError):
        return f"variant {variant} invalid measurement_variants metadata"
    if meta_prime != post_arm_prime_frames:
        return (
            f"variant {variant} post_arm_prime_frames={meta.get('post_arm_prime_frames')} "
            f"(expected {post_arm_prime_frames})"
        )
    if meta_measured != measured_frames:
        return (
            f"variant {variant} measured_frames={meta.get('measured_frames')} "
            f"(expected {measured_frames})"
        )
    expected_published = post_arm_prime_frames + measured_frames
    recipe_name = (recipe_entry.get("recipe") or {}).get("name", "?")
    for trial in _sorted_wp8_trials(recipe_entry):
        trial_id = int(trial["trial"])
        block = (trial.get("variants") or {}).get(variant)
        if block is None:
            return f"recipe {recipe_name} trial {trial_id} missing variant {variant}"
        measurement = block.get("measurement") or {}
        try:
            meas_prime = int(measurement.get("post_arm_prime_frames", -1))
            meas_frames = int(measurement.get("measured_frames", -1))
        except (TypeError, ValueError):
            return f"recipe {recipe_name} trial {trial_id} invalid variant measurement metadata"
        if meas_prime != post_arm_prime_frames:
            return (
                f"recipe {recipe_name} trial {trial_id} variant measurement "
                f"post_arm_prime_frames mismatch"
            )
        if meas_frames != measured_frames:
            return (
                f"recipe {recipe_name} trial {trial_id} variant measurement "
                f"measured_frames mismatch"
            )
        stages = block.get("stages") or {}
        if set(stages) != TIER1_V3_CAUSAL_STAGES:
            return (
                f"recipe {recipe_name} trial {trial_id} expected stages "
                f"{sorted(TIER1_V3_CAUSAL_STAGES)}, got {sorted(stages)}"
            )
        required_metrics = (
            _V3_CPU_AXES if variant != TIER1_V3_WALL_MEASUREMENT_VARIANT else _V3_WALL_AXES
        )
        for stage_name in TIER1_V3_CAUSAL_STAGES:
            stage = stages[stage_name]
            for metric in required_metrics:
                if metric not in stage:
                    return (
                        f"recipe {recipe_name} trial {trial_id} stage {stage_name} "
                        f"missing metric {metric}"
                    )
            try:
                prime_reported = int(stage.get("post_arm_prime_frames", -1))
                measured_reported = int(stage.get("measured_frames", -1))
            except (TypeError, ValueError):
                return f"recipe {recipe_name} trial {trial_id} stage {stage_name} invalid harness counts"
            if prime_reported != post_arm_prime_frames:
                return (
                    f"recipe {recipe_name} trial {trial_id} stage {stage_name} "
                    f"post_arm_prime_frames={stage.get('post_arm_prime_frames')}"
                )
            if measured_reported != measured_frames:
                return (
                    f"recipe {recipe_name} trial {trial_id} stage {stage_name} "
                    f"measured_frames={stage.get('measured_frames')}"
                )
        reference = stages["reference"]
        counters = stages["counters"]
        try:
            ref_frames = int(reference.get("frames", -1))
            ref_published = int(reference.get("published_frames", -1))
            ctr_frames = int(counters.get("frames", -1))
            ctr_published = int(counters.get("published_frames", -1))
        except (TypeError, ValueError):
            return f"recipe {recipe_name} trial {trial_id} invalid frame metadata"
        if ref_frames != measured_frames:
            return f"recipe {recipe_name} trial {trial_id} reference frames mismatch"
        if ref_published != expected_published:
            return f"recipe {recipe_name} trial {trial_id} reference published_frames mismatch"
        if ctr_frames != measured_frames:
            return f"recipe {recipe_name} trial {trial_id} counters frames mismatch"
        if ctr_published != expected_published:
            return f"recipe {recipe_name} trial {trial_id} counters published_frames mismatch"
    return None


def derive_wp8_steady_state_gate_pairs(
    recipe_entry: dict,
    *,
    variant: str,
    axes: tuple[str, ...],
) -> dict[str, dict]:
    gates: dict[str, dict] = {}
    trials = _sorted_wp8_trials(recipe_entry)
    for stage, reference in _V3_GATE_PAIRINGS:
        for axis in axes:
            gate_name = f"{stage}_{axis}"
            gates[gate_name] = {
                "pairs": [
                    {
                        "instrumented": trial["variants"][variant]["stages"][stage][axis],
                        "reference": trial["variants"][variant]["stages"][reference][axis],
                    }
                    for trial in trials
                ],
            }
    return gates


def evaluate_recipe_causal_v3(
    recipe_entry: dict,
    policy: Tier1CausalPolicyV3 = TIER1_CAUSAL_POLICY_V3,
) -> dict:
    recipe_name = (recipe_entry.get("recipe") or {}).get("name")
    try:
        return _evaluate_recipe_causal_v3_impl(recipe_entry, policy, recipe_name)
    except (KeyError, TypeError, ValueError) as error:
        return _recipe_v3_unavailable(
            recipe_name,
            policy,
            f"malformed steady-state evidence: {error}",
        )


def _evaluate_recipe_causal_v3_impl(
    recipe_entry: dict,
    policy: Tier1CausalPolicyV3,
    recipe_name: str | None,
) -> dict:
    cpu_contract = validate_wp8_steady_state_recipe_measurement_contract(
        recipe_entry,
        variant=TIER1_V3_CPU_MEASUREMENT_VARIANT,
        post_arm_prime_frames=policy.post_arm_prime_frames,
        measured_frames=policy.cpu_measured_frames,
    )
    if cpu_contract:
        return _recipe_v3_unavailable(recipe_name, policy, cpu_contract)
    wall_contract = validate_wp8_steady_state_recipe_measurement_contract(
        recipe_entry,
        variant=TIER1_V3_WALL_MEASUREMENT_VARIANT,
        post_arm_prime_frames=policy.post_arm_prime_frames,
        measured_frames=policy.wall_measured_frames,
    )
    if wall_contract:
        return _recipe_v3_unavailable(recipe_name, policy, wall_contract)

    cpu_gates = derive_wp8_steady_state_gate_pairs(
        recipe_entry,
        variant=TIER1_V3_CPU_MEASUREMENT_VARIANT,
        axes=_V3_CPU_AXES,
    )
    wall_gates = derive_wp8_steady_state_gate_pairs(
        recipe_entry,
        variant=TIER1_V3_WALL_MEASUREMENT_VARIANT,
        axes=_V3_WALL_AXES,
    )
    evaluated_gates: dict[str, dict] = {}
    results: dict[str, str] = {}
    gate_reasons: dict[str, str] = {}

    for name in EXPECTED_CAUSAL_GATE_NAMES_V3:
        status, evaluated, reason = policy.summarize_gate(
            cpu_gates[name],
            gate_name=name,
            frames=policy.cpu_measured_frames,
        )
        evaluated_gates[name] = evaluated
        results[name] = status if reason is None else "unavailable"
        if reason:
            gate_reasons[name] = reason

    for name in EXPECTED_V3_COMPLETION_WALL_GATE_NAMES:
        status, evaluated, reason = policy.summarize_completion_wall_gate(
            wall_gates[name],
            frames=policy.wall_measured_frames,
        )
        evaluated_gates[name] = evaluated
        results[name] = status
        if reason:
            gate_reasons[name] = reason

    return {
        "recipe": recipe_name,
        "policy_version": policy.version,
        "cpu_measurement_variant": TIER1_V3_CPU_MEASUREMENT_VARIANT,
        "completion_wall_measurement_variant": TIER1_V3_WALL_MEASUREMENT_VARIANT,
        "cpu_measured_frames": policy.cpu_measured_frames,
        "completion_wall_measured_frames": policy.wall_measured_frames,
        "post_arm_prime_frames": policy.post_arm_prime_frames,
        "gates": evaluated_gates,
        "results": results,
        "gate_reasons": gate_reasons,
        "status": _merge_status(*results.values()),
    }


def _recipe_v3_unavailable(recipe_name: str | None, policy: Tier1CausalPolicyV3, reason: str) -> dict:
    return {
        "recipe": recipe_name,
        "policy_version": policy.version,
        "cpu_measurement_variant": TIER1_V3_CPU_MEASUREMENT_VARIANT,
        "completion_wall_measurement_variant": TIER1_V3_WALL_MEASUREMENT_VARIANT,
        "status": "unavailable",
        "reason": reason,
        "gates": {},
        "results": {},
        "gate_reasons": {},
    }


def evaluate_wp8_steady_state_training_v3(
    preflight: dict,
    policy: Tier1CausalPolicyV3 = TIER1_CAUSAL_POLICY_V3,
) -> dict:
    block = preflight.get("steady_state_tier1_diagnosis") or {}
    recipes = block.get("recipes") or []
    recipe_set_reason = validate_wp8_steady_state_training_recipes(recipes)
    if recipe_set_reason:
        return {
            "status": "unavailable",
            "reason": recipe_set_reason,
            "policy_version": policy.version,
            "cpu_measurement_variant": TIER1_V3_CPU_MEASUREMENT_VARIANT,
            "completion_wall_measurement_variant": TIER1_V3_WALL_MEASUREMENT_VARIANT,
            "post_arm_prime_frames": policy.post_arm_prime_frames,
            "cpu_measured_frames": policy.cpu_measured_frames,
            "completion_wall_measured_frames": policy.wall_measured_frames,
            "training_recipes": [],
        }
    by_name = {(entry.get("recipe") or {}).get("name"): entry for entry in recipes}
    evaluations = [evaluate_recipe_causal_v3(by_name[name], policy) for name in TIER1_V3_TRAINING_RECIPE_NAMES]
    return {
        "status": _merge_status(*(item["status"] for item in evaluations)),
        "policy_version": policy.version,
        "cpu_measurement_variant": TIER1_V3_CPU_MEASUREMENT_VARIANT,
        "completion_wall_measurement_variant": TIER1_V3_WALL_MEASUREMENT_VARIANT,
        "post_arm_prime_frames": policy.post_arm_prime_frames,
        "cpu_measured_frames": policy.cpu_measured_frames,
        "completion_wall_measured_frames": policy.wall_measured_frames,
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
