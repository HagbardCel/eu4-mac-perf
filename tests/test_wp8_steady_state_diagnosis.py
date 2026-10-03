import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "benchmark"))
import eu4_frame_model as model  # noqa: E402

_FROZEN_ARTIFACTS = {
    "test_library_sha256": "frozen-test-library",
    "workload_harness_sha256": "frozen-workload-harness",
}


class Wp8SteadyStateDiagnosisTests(unittest.TestCase):
    def test_completion_absolute_overhead_scales_with_measured_frames(self):
        pairs = [{"instrumented": 5000, "reference": 1000}]
        four = model._completion_diagnostic_paired_summary(pairs, frames=4)
        forty = model._completion_diagnostic_paired_summary(pairs, frames=40)
        self.assertAlmostEqual(four["overhead_us_per_frame"], 1.0)
        self.assertAlmostEqual(forty["overhead_us_per_frame"], 0.1)
        self.assertAlmostEqual(four["overhead_us_per_frame"] / forty["overhead_us_per_frame"], 10.0)

    def test_steady_state_variant_order_rotates(self):
        names = [name for name, _ in model.STEADY_STATE_MEASUREMENT_VARIANTS]
        self.assertEqual(
            [name for name, _ in model._steady_state_variant_order_for_trial(0)],
            names,
        )
        self.assertEqual(
            [name for name, _ in model._steady_state_variant_order_for_trial(1)],
            names[1:] + names[:1],
        )
        self.assertEqual(
            [name for name, _ in model._steady_state_variant_order_for_trial(2)],
            names[2:] + names[:2],
        )

    def test_require_harness_measurement_contract_rejects_mismatch(self):
        with self.assertRaises(model.base.BenchmarkError):
            model._require_harness_measurement_contract(
                {"post_arm_prime_frames": 0, "measured_frames": 4},
                post_arm_prime_frames=1,
                measured_frames=4,
            )

    def test_policy_versions_for_steady_state_diagnosis(self):
        versions = model._offline_policy_versions({"steady_state_tier1_diagnosis": True})
        self.assertEqual(
            versions["steady_state_tier1_diagnosis"],
            model.WP8_STEADY_STATE_TIER1_DIAGNOSIS_PURPOSE,
        )
        self.assertIn("tier1_causal_gate", versions)
        self.assertNotIn("diagnostic_matrix", versions)

    def test_offline_workload_stage_sets_post_arm_prime_env(self):
        calls: list[dict] = []

        def capture_run(cmd, env, **kwargs):
            calls.append(env)
            stdout = (
                "elapsed_ns=100 cpu_ns=50 draws=4 valid=1 pixel_alpha=255 "
                "preparation_wall_ns=1 preparation_cpu_ns=1 post_arm_prime_frames=1 measured_frames=4 "
                "submission_elapsed_ns=100 submission_cpu_ns=50 post_window_drain_elapsed_ns=10 "
                "post_window_drain_cpu_ns=5 submission_plus_drain_elapsed_ns=110 "
                "submission_plus_drain_cpu_ns=55\n"
            )
            return mock.Mock(returncode=0, stdout=stdout, stderr="")

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            recipe = {"name": "mesh", "path": str(root / "recipe.bin"), "draws": 1}
            (root / "recipe.bin").write_bytes(b"\x00")
            with mock.patch.object(model.subprocess, "run", side_effect=capture_run), mock.patch.object(
            model,
            "_validate_lean_reference_trace",
        ), mock.patch.object(model, "frame_rows", return_value=[{}] * 5), mock.patch.object(
            model,
            "read_rows",
            return_value=[["H", "3"], ["Z", "0", "7", "0"]],
        ):
                model._offline_workload_stage(
                    root,
                    recipe,
                    0,
                    "reference",
                    completion_timing=True,
                    post_arm_prime_frames=1,
                    measured_frames=4,
                    telemetry_suffix="four_frame_post_arm_prime_1",
                )
            self.assertEqual(calls[0]["EU4_TEST_POST_ARM_PRIME_FRAMES"], "1")
            self.assertEqual(calls[0]["EU4_TEST_COMPLETION_TIMING"], "1")

    def test_steady_state_recipe_runs_all_variants_and_stages(self):
        stage_calls: list[tuple[str, str, int]] = []

        def fake_stage(root, recipe, trial, stage, **kwargs):
            stage_calls.append((kwargs.get("telemetry_suffix", ""), stage, kwargs.get("post_arm_prime_frames", 0)))
            metrics = {
                "elapsed_ns": 1000,
                "cpu_ns": 500,
                "submission_elapsed_ns": 1000,
                "submission_cpu_ns": 500,
                "post_window_drain_elapsed_ns": 100,
                "post_window_drain_cpu_ns": 50,
                "submission_plus_drain_elapsed_ns": 1100,
                "submission_plus_drain_cpu_ns": 550,
                "post_arm_prime_frames": kwargs.get("post_arm_prime_frames", 0),
                "measured_frames": kwargs.get("measured_frames", 4),
            }
            return metrics, None

        recipes = [
            {"name": name, "role": "training", "path": Path("/tmp")}
            for name in ("mesh", "borders", "text_ui")
        ]
        with mock.patch.object(model.workload, "recipes", return_value=recipes), mock.patch.object(
            model,
            "_offline_workload_stage",
            side_effect=fake_stage,
        ):
            result = model.steady_state_tier1_diagnosis_workloads(
                executed_artifacts_start=_FROZEN_ARTIFACTS,
            )
        variant_names = {name for name, _ in model.STEADY_STATE_MEASUREMENT_VARIANTS}
        suffixes = {call[0] for call in stage_calls}
        self.assertEqual(suffixes, variant_names)
        stages = {call[1] for call in stage_calls}
        self.assertEqual(stages, {"bare", "reference", "counters"})
        expected_calls = 7 * len(recipes) * len(model.STEADY_STATE_MEASUREMENT_VARIANTS) * 3
        self.assertEqual(len(stage_calls), expected_calls)
        self.assertTrue(result["steady_state_tier1_diagnosis"])
        self.assertEqual(len(result["recipes"]), 3)
        self.assertIn("variant_comparisons", result["recipes"][0])
        trial0 = result["recipes"][0]["trials"][0]
        self.assertIn("variant_order", trial0)
        self.assertEqual(len(trial0["variant_order"]), len(model.STEADY_STATE_MEASUREMENT_VARIANTS))


if __name__ == "__main__":
    unittest.main()
