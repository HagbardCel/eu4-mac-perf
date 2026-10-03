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


class Tier1V3CompletionWallTriStateTests(unittest.TestCase):
    def test_tight_in_band_ci_passes(self):
        status, reason = tier1.classify_completion_wall_equivalence_ci(
            0.01,
            0.02,
            limit=0.05,
        )
        self.assertEqual(status, "passed")
        self.assertIsNone(reason)

    def test_tight_positive_out_of_band_ci_fails(self):
        status, reason = tier1.classify_completion_wall_equivalence_ci(
            0.07,
            0.11,
            limit=0.05,
        )
        self.assertEqual(status, "failed")
        self.assertIsNotNone(reason)

    def test_wide_overlapping_ci_is_unavailable(self):
        status, reason = tier1.classify_completion_wall_equivalence_ci(
            -0.20,
            0.03,
            limit=0.05,
        )
        self.assertEqual(status, "unavailable")
        self.assertIsNotNone(reason)

    def test_tight_positive_overhead_gate_fails_not_unavailable(self):
        reference = 1_000_000
        instrumented = 1_100_000
        pairs = [{"reference": reference, "instrumented": instrumented}] * 7
        policy = tier1.TIER1_CAUSAL_POLICY_V3
        status, _, reason = policy.summarize_completion_wall_gate({"pairs": pairs}, frames=40)
        self.assertEqual(status, "failed")
        self.assertIsNotNone(reason)


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

    def test_missing_stage_metric_is_unavailable(self):
        if not WP8_ARCHIVE.is_file():
            self.skipTest("WP8 archive missing")
        preflight = json.loads(WP8_ARCHIVE.read_text())["preflight"]
        block = json.loads(json.dumps(preflight["steady_state_tier1_diagnosis"]))
        trial0 = block["recipes"][0]["trials"][0]
        del trial0["variants"]["four_frame_post_arm_prime_1"]["stages"]["bare"]["cpu_ns"]
        replay = tier1.evaluate_wp8_steady_state_training_v3(
            {"steady_state_tier1_diagnosis": block},
        )
        mesh = next(item for item in replay["training_recipes"] if item["recipe"] == "mesh")
        self.assertEqual(mesh["status"], "unavailable")
        self.assertIn("missing metric", mesh["reason"])

    def test_non_contiguous_trial_ids_are_unavailable(self):
        if not WP8_ARCHIVE.is_file():
            self.skipTest("WP8 archive missing")
        preflight = json.loads(WP8_ARCHIVE.read_text())["preflight"]
        block = json.loads(json.dumps(preflight["steady_state_tier1_diagnosis"]))
        for index, trial in enumerate(block["recipes"][0]["trials"]):
            trial["trial"] = index * 2
        replay = tier1.evaluate_wp8_steady_state_training_v3(
            {"steady_state_tier1_diagnosis": block},
        )
        mesh = next(item for item in replay["training_recipes"] if item["recipe"] == "mesh")
        self.assertEqual(mesh["status"], "unavailable")
        self.assertIn("trial ids", mesh["reason"])

    def test_derived_pairs_match_archived_variant_comparisons(self):
        if not WP8_ARCHIVE.is_file():
            self.skipTest("WP8 archive missing")
        preflight = json.loads(WP8_ARCHIVE.read_text())["preflight"]
        recipe = preflight["steady_state_tier1_diagnosis"]["recipes"][0]
        derived = tier1.derive_wp8_steady_state_gate_pairs(
            recipe,
            variant=tier1.TIER1_V3_CPU_MEASUREMENT_VARIANT,
            axes=tier1._V3_CPU_AXES,
        )
        archived = recipe["variant_comparisons"][tier1.TIER1_V3_CPU_MEASUREMENT_VARIANT]
        for gate_name, gate in derived.items():
            self.assertEqual(
                gate["pairs"],
                archived[gate_name]["pairs"],
                msg=gate_name,
            )


class Tier1V3Wp8ReplayTests(unittest.TestCase):
    def test_wp8_mesh_and_borders_pass_all_admission_gates(self):
        if not WP8_ARCHIVE.is_file():
            self.skipTest("WP8 archive missing")
        preflight = json.loads(WP8_ARCHIVE.read_text())["preflight"]
        replay = tier1.evaluate_wp8_steady_state_training_v3(preflight)
        for recipe_name in ("mesh", "borders"):
            recipe_eval = next(item for item in replay["training_recipes"] if item["recipe"] == recipe_name)
            self.assertEqual(recipe_eval["status"], "passed")
            for gate_name, status in recipe_eval["results"].items():
                self.assertEqual(status, "passed", msg=f"{recipe_name} {gate_name}")

    def test_wp8_text_ui_reference_completion_wall_is_unavailable(self):
        if not WP8_ARCHIVE.is_file():
            self.skipTest("WP8 archive missing")
        preflight = json.loads(WP8_ARCHIVE.read_text())["preflight"]
        replay = tier1.evaluate_wp8_steady_state_training_v3(preflight)
        text_ui = next(item for item in replay["training_recipes"] if item["recipe"] == "text_ui")
        self.assertEqual(text_ui["status"], "unavailable")
        self.assertEqual(
            text_ui["results"]["reference_submission_plus_drain_elapsed_ns"],
            "unavailable",
        )

    def test_gate_embedded_status_matches_results_for_all_admission_gates(self):
        if not WP8_ARCHIVE.is_file():
            self.skipTest("WP8 archive missing")
        preflight = json.loads(WP8_ARCHIVE.read_text())["preflight"]
        replay = tier1.evaluate_wp8_steady_state_training_v3(preflight)
        for recipe_eval in replay["training_recipes"]:
            for gate_name in tier1.EXPECTED_V3_ADMISSION_GATE_NAMES:
                self.assertEqual(
                    recipe_eval["gates"][gate_name]["status"],
                    recipe_eval["results"][gate_name],
                    msg=f"{recipe_eval['recipe']} {gate_name}",
                )
            for gate_name in tier1.EXPECTED_CAUSAL_GATE_NAMES_V3:
                self.assertIn("relative_diagnostic_status", recipe_eval["gates"][gate_name])

    def test_fractional_measured_frames_rejected(self):
        if not WP8_ARCHIVE.is_file():
            self.skipTest("WP8 archive missing")
        preflight = json.loads(WP8_ARCHIVE.read_text())["preflight"]
        block = json.loads(json.dumps(preflight["steady_state_tier1_diagnosis"]))
        meta = block["recipes"][0]["measurement_variants"]
        prime_meta = next(m for m in meta if m["name"] == tier1.TIER1_V3_CPU_MEASUREMENT_VARIANT)
        prime_meta["measured_frames"] = 4.9
        replay = tier1.evaluate_wp8_steady_state_training_v3(
            {"steady_state_tier1_diagnosis": block},
        )
        mesh = next(item for item in replay["training_recipes"] if item["recipe"] == "mesh")
        self.assertEqual(mesh["status"], "unavailable")

    def test_wp8_overall_unavailable_due_to_text_ui_wall_variance(self):
        if not WP8_ARCHIVE.is_file():
            self.skipTest("WP8 archive missing")
        preflight = json.loads(WP8_ARCHIVE.read_text())["preflight"]
        replay = tier1.evaluate_wp8_steady_state_training_v3(preflight)
        self.assertEqual(replay["status"], "unavailable")


if __name__ == "__main__":
    unittest.main()
