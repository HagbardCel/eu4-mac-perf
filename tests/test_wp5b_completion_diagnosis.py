import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "benchmark"))
import eu4_frame_model as model  # noqa: E402


class Wp5bCompletionDiagnosisTests(unittest.TestCase):
    def test_completion_recipe_evidence_omits_ephemeral_path(self):
        recipe = {
            "name": "mesh",
            "role": "training",
            "sha256": "deadbeef",
            "path": "/tmp/eu4-completion-xyz/mesh.recipe",
        }
        stage_metrics = {key: 100 for key in model.COMPLETION_TIMING_METRICS}
        with tempfile.TemporaryDirectory() as directory, mock.patch.object(
            model, "_offline_workload_stage", return_value=(stage_metrics, None),
        ):
            entry = model._offline_completion_recipe_evidence(Path(directory), recipe)
        self.assertNotIn("path", entry["recipe"])
        self.assertEqual(entry["recipe"]["sha256"], "deadbeef")

    def test_build_metadata_uses_completion_workloads(self):
        identity = {
            "git_commit": "c" * 40,
            "git_commit_short": "c" * 7,
            "git_tree_clean": True,
            "controller_sha256": "ctrl",
            "key_source_hashes": {},
        }
        build_info = {"executable_sha256": "game", "library_sha256": "lib", "native_source_hashes": {}}
        executed = {
            "profiler_dylib_sha256": "lib",
            "test_library_sha256": "test-lib",
            "workload_harness_sha256": "harness-bin",
            "draw_manifest_sha256": "manifest",
        }
        source = model._offline_workload_source_hashes()
        preflight = {
            "purpose": model.WP5B_COMPLETION_DIAGNOSIS_PURPOSE,
            "completion_workloads": {
                "completion_timing": True,
                "recipes": [
                    {
                        "recipe": {"name": "mesh", "sha256": "mesh-hash", "role": "training"},
                        "trials": [],
                    },
                    {
                        "recipe": {"name": "borders", "sha256": "borders-hash", "role": "training"},
                        "trials": [],
                    },
                ],
                **source,
            },
        }
        metadata = model.build_offline_evidence_metadata(
            preflight,
            "20261003T120000.000000Z-abc12345",
            identity_start=identity,
            identity_end=identity,
            build_info=build_info,
            executed_artifact_start=executed,
            executed_artifact_end=executed,
        )
        hashes = metadata["artifact_hashes"]
        self.assertEqual(hashes["recipe_sha256"]["mesh"], "mesh-hash")
        self.assertEqual(hashes["harness_source_sha256"], source["harness_source_sha256"])
        self.assertEqual(hashes["profiler_source_sha256"], source["profiler_source_sha256"])
        self.assertIn("completion_diagnosis", metadata["policy_versions"])
        self.assertNotIn("diagnostic_matrix", metadata["policy_versions"])

    def test_parse_workload_harness_metrics_reads_completion_fields(self):
        stdout = (
            "elapsed_ns=1000 cpu_ns=24 draws=40 valid=1 pixel_alpha=255 "
            "preparation_wall_ns=5000 preparation_cpu_ns=100 "
            "submission_elapsed_ns=1000 submission_cpu_ns=24 "
            "post_window_drain_elapsed_ns=200 post_window_drain_cpu_ns=5 "
            "submission_plus_drain_elapsed_ns=1200 submission_plus_drain_cpu_ns=29"
        )
        metrics = model.parse_workload_harness_metrics(stdout)
        self.assertEqual(metrics["submission_plus_drain_elapsed_ns"], 1200)
        model._require_completion_timing_metrics(metrics)

    def test_require_completion_timing_checks_drain_sums(self):
        metrics = model.parse_workload_harness_metrics(
            "elapsed_ns=10 cpu_ns=4 submission_elapsed_ns=10 submission_cpu_ns=4 "
            "post_window_drain_elapsed_ns=3 post_window_drain_cpu_ns=1 "
            "submission_plus_drain_elapsed_ns=12 submission_plus_drain_cpu_ns=4",
        )
        with self.assertRaises(model.base.BenchmarkError):
            model._require_completion_timing_metrics(metrics)

    def test_require_completion_timing_rejects_mismatched_submission(self):
        metrics = model.parse_workload_harness_metrics(
            "elapsed_ns=1 cpu_ns=1 submission_elapsed_ns=2 submission_cpu_ns=1 "
            "post_window_drain_elapsed_ns=0 post_window_drain_cpu_ns=0 "
            "submission_plus_drain_elapsed_ns=2 submission_plus_drain_cpu_ns=1",
        )
        with self.assertRaises(model.base.BenchmarkError):
            model._require_completion_timing_metrics(metrics)

    def test_completion_stage_comparisons_shape(self):
        trials = [
            {
                "stages": {
                    "bare": {axis: 1000 + i for axis in model.COMPLETION_TIMING_METRICS},
                    "reference": {axis: 1100 + i for axis in model.COMPLETION_TIMING_METRICS},
                    "counters": {axis: 1050 + i for axis in model.COMPLETION_TIMING_METRICS},
                },
            }
            for i in range(7)
        ]
        comparisons = model._completion_stage_comparisons(trials)
        self.assertIn("reference_submission_plus_drain_elapsed_ns", comparisons)
        self.assertIn("counters_post_window_drain_cpu_ns", comparisons)
        sample = comparisons["reference_elapsed_ns"]
        self.assertEqual(sample["status"], "diagnostic")
        self.assertFalse(sample["acceptance_gate"])
        self.assertNotIn("limit", sample)
        self.assertEqual(sample["reference_band_fraction"], 0.03)


if __name__ == "__main__":
    unittest.main()
