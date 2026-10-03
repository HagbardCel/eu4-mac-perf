import copy
import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "benchmark"))
import frame_model_tier1_policy as tier1  # noqa: E402

WP8_ARCHIVE = (
    Path(__file__).resolve().parents[1]
    / "analysis/evidence/frame-model-offline-f62aa28-20261003T123219.715675Z-3c1e0db7.json"
)


def _expand_steady_state_diagnosis_to_v4_trials(diagnosis: dict) -> dict:
    block = copy.deepcopy(diagnosis)
    for recipe_entry in block.get("recipes") or []:
        base_trials = recipe_entry.get("trials") or []
        expanded: list[dict] = []
        for trial_id in range(tier1.TIER1_V4_TRIAL_COUNT):
            source = copy.deepcopy(base_trials[trial_id % len(base_trials)])
            source["trial"] = trial_id
            source["order"] = tier1._expected_stage_order_for_trial(trial_id)
            source["variant_order"] = tier1._expected_variant_order_for_trial(trial_id)
            expanded.append(source)
        recipe_entry["trials"] = expanded
    block["paired_trial_count"] = tier1.TIER1_V4_TRIAL_COUNT
    return block


def _v4_preflight_with_equal_stage_metrics() -> dict:
    if not WP8_ARCHIVE.is_file():
        raise unittest.SkipTest("WP8 archive missing")
    preflight = json.loads(WP8_ARCHIVE.read_text())["preflight"]
    diagnosis = _expand_steady_state_diagnosis_to_v4_trials(
        preflight["steady_state_tier1_diagnosis"],
    )
    equal_ns = 4_000_000
    for recipe_entry in diagnosis.get("recipes") or []:
        for trial in recipe_entry.get("trials") or []:
            for variant_name in (
                tier1.TIER1_V4_CPU_MEASUREMENT_VARIANT,
                tier1.TIER1_V4_WALL_MEASUREMENT_VARIANT,
            ):
                block = trial["variants"][variant_name]
                for stage_name in tier1.TIER1_V3_CAUSAL_STAGES:
                    stage = block["stages"][stage_name]
                    stage["cpu_ns"] = equal_ns
                    stage["submission_plus_drain_elapsed_ns"] = equal_ns
    return {"steady_state_tier1_diagnosis": diagnosis}


class Tier1V4TrialCountTests(unittest.TestCase):
    def test_wp8_seven_pair_capture_is_unavailable_under_v4(self):
        if not WP8_ARCHIVE.is_file():
            self.skipTest("WP8 archive missing")
        preflight = json.loads(WP8_ARCHIVE.read_text())["preflight"]
        replay = tier1.evaluate_wp8_steady_state_training_v4(preflight)
        self.assertEqual(replay["status"], "unavailable")
        self.assertEqual(replay["paired_trial_count"], tier1.TIER1_V4_TRIAL_COUNT)
        mesh = next(item for item in replay["training_recipes"] if item["recipe"] == "mesh")
        self.assertEqual(mesh["status"], "unavailable")
        self.assertIn("21 trials", mesh["reason"])

    def test_v4_policy_version_string(self):
        self.assertEqual(
            tier1.TIER1_CAUSAL_POLICY_V4.version,
            tier1.TIER1_CAUSAL_POLICY_VERSION_V4,
        )
        self.assertEqual(tier1.TIER1_CAUSAL_POLICY_V4.pair_count, 21)
        self.assertEqual(tier1.TIER1_CAUSAL_POLICY_V3.pair_count, 7)

    def test_v4_evaluates_twenty_one_pair_gates_to_passed_not_unavailable(self):
        preflight = _v4_preflight_with_equal_stage_metrics()
        replay = tier1.evaluate_wp8_steady_state_training_v4(preflight)
        self.assertEqual(replay["status"], "passed")
        mesh = next(item for item in replay["training_recipes"] if item["recipe"] == "mesh")
        self.assertEqual(mesh["status"], "passed")
        for gate_name in tier1.EXPECTED_V3_ADMISSION_GATE_NAMES:
            self.assertEqual(mesh["results"][gate_name], "passed", msg=gate_name)
        cpu_gates = tier1.derive_wp8_steady_state_gate_pairs(
            next(
                entry
                for entry in preflight["steady_state_tier1_diagnosis"]["recipes"]
                if entry["recipe"]["name"] == "mesh"
            ),
            variant=tier1.TIER1_V4_CPU_MEASUREMENT_VARIANT,
            axes=tier1._V3_CPU_AXES,
        )
        self.assertEqual(len(cpu_gates["counters_cpu_ns"]["pairs"]), 21)


if __name__ == "__main__":
    unittest.main()
