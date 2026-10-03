import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "benchmark"))
import frame_model_tier1_policy as tier1  # noqa: E402
import frame_model_workload as workload  # noqa: E402

WP8_ARCHIVE = (
    Path(__file__).resolve().parents[1]
    / "analysis/evidence/frame-model-offline-f62aa28-20261003T123219.715675Z-3c1e0db7.json"
)


class Tier1V3HybridGateTests(unittest.TestCase):
    def test_tiny_denominator_passes_with_floor_not_v2_relative(self):
        pairs = [{"reference": 400_000, "instrumented": 416_000}] * 7
        gate = workload.paired_summary(pairs, 0.03, 4)
        self.assertFalse(tier1.TIER1_CAUSAL_POLICY.gate_passes(gate))
        policy = tier1.TIER1_CAUSAL_POLICY_V3
        self.assertTrue(policy.hybrid_gate_passes(gate, gate_name="reference_cpu_ns", frames=4))

    def test_hybrid_allowed_us_is_capped(self):
        pairs = [{"reference": 100_000_000, "instrumented": 100_003_000}] * 7
        allowed = tier1.hybrid_allowed_us_per_frame(
            pairs,
            frames=4,
            relative_limit=0.03,
            absolute_floor_us=5.0,
        )
        self.assertAlmostEqual(allowed, tier1.TIER1_V3_ABSOLUTE_CAP_US_PER_FRAME)

    def test_counters_hybrid_allows_band_that_v2_relative_rejects(self):
        pairs = [{"reference": 400_000, "instrumented": 420_000}] * 7
        gate = workload.paired_summary(pairs, 0.03, 4)
        self.assertFalse(tier1.TIER1_CAUSAL_POLICY.gate_passes(gate))
        policy = tier1.TIER1_CAUSAL_POLICY_V3
        self.assertTrue(policy.hybrid_gate_passes(gate, gate_name="counters_cpu_ns", frames=4))


class Tier1V3EvidenceContractTests(unittest.TestCase):
    def test_incomplete_recipe_set_is_unavailable(self):
        if not WP8_ARCHIVE.is_file():
            self.skipTest("WP8 archive missing")
        preflight = json.loads(WP8_ARCHIVE.read_text())["preflight"]
        block = preflight["steady_state_tier1_diagnosis"]
        recipes = block["recipes"][:1]
        replay = tier1.evaluate_wp8_steady_state_training_v3(
            {"steady_state_tier1_diagnosis": {"recipes": recipes}},
        )
        self.assertEqual(replay["status"], "unavailable")
        self.assertIn("expected exactly 3", replay["reason"])

    def test_wrong_post_arm_prime_on_stage_is_unavailable(self):
        if not WP8_ARCHIVE.is_file():
            self.skipTest("WP8 archive missing")
        preflight = json.loads(WP8_ARCHIVE.read_text())["preflight"]
        block = json.loads(json.dumps(preflight["steady_state_tier1_diagnosis"]))
        trial0 = block["recipes"][0]["trials"][0]
        variant = trial0["variants"]["four_frame_post_arm_prime_1"]
        variant["stages"]["bare"]["post_arm_prime_frames"] = 0
        replay = tier1.evaluate_wp8_steady_state_training_v3(
            {"steady_state_tier1_diagnosis": block},
        )
        self.assertEqual(replay["status"], "unavailable")
        mesh = next(item for item in replay["training_recipes"] if item["recipe"] == "mesh")
        self.assertEqual(mesh["status"], "unavailable")
        self.assertIn("post_arm_prime_frames", mesh["reason"])


class Tier1V3Wp8ReplayTests(unittest.TestCase):
    def test_wp8_primed_variant_reference_cpu_passes_v3(self):
        if not WP8_ARCHIVE.is_file():
            self.skipTest("WP8 archive missing")
        preflight = json.loads(WP8_ARCHIVE.read_text())["preflight"]
        replay = tier1.evaluate_wp8_steady_state_training_v3(preflight)
        self.assertEqual(replay["policy_version"], tier1.TIER1_CAUSAL_POLICY_VERSION_V3)
        for recipe_eval in replay["training_recipes"]:
            self.assertEqual(
                recipe_eval["results"]["reference_cpu_ns"],
                "passed",
                msg=recipe_eval["recipe"],
            )

    def test_wp8_primed_variant_counters_cpu_passes_v3_hybrid(self):
        if not WP8_ARCHIVE.is_file():
            self.skipTest("WP8 archive missing")
        preflight = json.loads(WP8_ARCHIVE.read_text())["preflight"]
        replay = tier1.evaluate_wp8_steady_state_training_v3(preflight)
        for recipe_eval in replay["training_recipes"]:
            self.assertEqual(
                recipe_eval["results"]["counters_cpu_ns"],
                "passed",
                msg=recipe_eval["recipe"],
            )

    def test_wp8_overall_training_status_passed_under_v3(self):
        if not WP8_ARCHIVE.is_file():
            self.skipTest("WP8 archive missing")
        preflight = json.loads(WP8_ARCHIVE.read_text())["preflight"]
        replay = tier1.evaluate_wp8_steady_state_training_v3(preflight)
        self.assertEqual(replay["status"], "passed")

    def test_wp8_completion_wall_is_diagnostic_not_admission(self):
        if not WP8_ARCHIVE.is_file():
            self.skipTest("WP8 archive missing")
        preflight = json.loads(WP8_ARCHIVE.read_text())["preflight"]
        replay = tier1.evaluate_wp8_steady_state_training_v3(preflight)
        mesh = next(item for item in replay["training_recipes"] if item["recipe"] == "mesh")
        wall = mesh["completion_wall_diagnostic"]["counters_submission_plus_drain_elapsed_ns"]
        self.assertEqual(wall["status"], "diagnostic")
        self.assertFalse(wall["acceptance_gate"])
        self.assertNotIn("counters_submission_plus_drain_elapsed_ns", mesh["results"])


if __name__ == "__main__":
    unittest.main()
