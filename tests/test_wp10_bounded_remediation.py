import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "benchmark"))
import eu4_frame_model as model  # noqa: E402
import frame_model_workload as workload  # noqa: E402

_FROZEN_ARTIFACTS = {
    "test_library_sha256": "frozen-test-library",
    "workload_harness_sha256": "frozen-workload-harness",
}


class Wp10BoundedRemediationTests(unittest.TestCase):
    def test_wp10_cpu_overhead_uses_raw_totals_not_double_scaled(self):
        trials = [
            {
                "stages": {
                    "reference": {"cpu_ns": 40_000, "frames": 4},
                    "counters": {"cpu_ns": 80_000, "frames": 4},
                },
            },
        ]
        pairs = model._wp10_cpu_overhead_pairs(trials, "counters")
        summary = workload.paired_summary(pairs, 0.03, model.WP10_MESH_MEASURED_FRAMES)
        self.assertAlmostEqual(summary["overhead_us_per_frame"], 10.0)

    def test_text_wall_block_schedule_counts(self):
        total_40 = sum(sum(1 for f in block if f == 40) for block in model.WP10_TEXT_WALL_BLOCK_SCHEDULE)
        total_400 = sum(sum(1 for f in block if f == 400) for block in model.WP10_TEXT_WALL_BLOCK_SCHEDULE)
        self.assertEqual(total_40, 21)
        self.assertEqual(total_400, 7)
        self.assertEqual(len(model.WP10_TEXT_WALL_BLOCK_SCHEDULE), 7)

    def test_policy_versions_for_bounded_remediation(self):
        versions = model._offline_policy_versions({"bounded_remediation": True})
        self.assertEqual(versions["wp10_bounded_remediation"], model.WP10_BOUNDED_REMEDIATION_PURPOSE)
        self.assertEqual(versions["engineering_success_counters_lite_max_us_per_frame"], 5.0)

    def test_mesh_workloads_run_four_stages_per_trial(self):
        stage_calls: list[str] = []

        def fake_stage(root, recipe, trial, stage, **kwargs):
            stage_calls.append(stage)
            metrics = {
                "elapsed_ns": 4000,
                "cpu_ns": 4000,
                "frames": kwargs.get("measured_frames", 4),
                "submission_elapsed_ns": 4000,
                "submission_cpu_ns": 2000,
                "post_window_drain_elapsed_ns": 100,
                "post_window_drain_cpu_ns": 50,
                "submission_plus_drain_elapsed_ns": 4100,
                "submission_plus_drain_cpu_ns": 2050,
            }
            return metrics, None

        recipes = [
            {"name": "mesh", "role": "training", "path": Path("/tmp/mesh"), "draws": 1},
            {"name": "text_ui", "role": "training", "path": Path("/tmp/text"), "draws": 1},
        ]

        def recipes_side_effect(root, include_held_out=False):
            return recipes

        with mock.patch.object(model.workload, "recipes", side_effect=recipes_side_effect), mock.patch.object(
            model,
            "_offline_workload_stage",
            side_effect=fake_stage,
        ):
            result = model.bounded_remediation_workloads(executed_artifacts_start=_FROZEN_ARTIFACTS)

        self.assertEqual(len(result["mesh_counters_cpu"]["trials"]), 7)
        for trial in result["mesh_counters_cpu"]["trials"]:
            self.assertEqual(set(trial["stages"]), set(model.WP10_MESH_CPU_STAGES))
        self.assertIn("counters_lite_deferred_flush", stage_calls)
        engineering = result["mesh_counters_cpu"]["engineering_success"]
        self.assertEqual(engineering["target_counters_lite_max_us_per_frame"], 5.0)
        self.assertIn("a73ea57b", engineering["criterion"])
        self.assertTrue(result["bounded_remediation"])
        schedule = result["text_ui_completion_wall"]["execution_schedule"]
        self.assertEqual(schedule["block_count"], 7)
        self.assertEqual(len(result["text_ui_completion_wall"]["interleaved_trials"]), 28)
        forty = next(v for v in result["text_ui_completion_wall"]["variants"] if v["name"] == "forty_frame_21_trials")
        four_hundred = next(
            v for v in result["text_ui_completion_wall"]["variants"] if v["name"] == "four_hundred_frame_7_trials"
        )
        self.assertEqual(len(forty["trials"]), 21)
        self.assertEqual(len(four_hundred["trials"]), 7)

    def test_offline_artifact_hashes_include_wp10_recipes(self):
        representative = {
            "bounded_remediation": True,
            "mesh_counters_cpu": {"recipe": {"name": "mesh", "sha256": "mesh-hash"}},
            "text_ui_completion_wall": {"recipe": {"name": "text_ui", "sha256": "text-hash"}},
        }
        hashes = model._offline_artifact_hashes(representative)
        self.assertEqual(
            hashes["recipe_sha256"],
            {"mesh": "mesh-hash", "text_ui": "text-hash"},
        )

    def test_counters_lite_harness_toggles_are_private_env(self):
        private = model._offline_workload_private_env()
        self.assertIn("EU4_TEST_COUNTERS_LITE", private)
        self.assertIn("EU4_TEST_COUNTERS_DEFERRED_FLUSH", private)


if __name__ == "__main__":
    unittest.main()
