import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "benchmark"))
import eu4_frame_model as model  # noqa: E402


class ProfilerOverheadDiagnosisTests(unittest.TestCase):
    def test_diagnostic_matrix_stage_features_cover_all_stages(self):
        self.assertEqual(len(model.DIAGNOSTIC_MATRIX_STAGES), 6)
        for stage in model.DIAGNOSTIC_MATRIX_STAGES:
            self.assertIn(stage, model.DIAGNOSTIC_MATRIX_FEATURES)
            self.assertEqual(len(model.DIAGNOSTIC_MATRIX_FEATURES[stage]), 3)

    def test_offline_workloads_can_exclude_held_out(self):
        frozen = {
            "profiler_dylib_sha256": "profiler",
            "test_library_sha256": "frozen-test",
            "workload_harness_sha256": "frozen-workload",
            "draw_manifest_sha256": "manifest",
        }
        with mock.patch.object(model, "workload") as workload_mock:
            workload_mock.recipes.return_value = []
            model.offline_workloads(
                executed_artifacts_start=frozen,
                include_held_out=False,
            )
        workload_mock.recipes.assert_called_once()
        self.assertFalse(workload_mock.recipes.call_args.kwargs["include_held_out"])

    def test_profiler_overhead_diagnosis_skips_rolling_pointer(self):
        representative = {
            "status": "failed",
            "offline_causal_admission": {"status": "failed"},
            "offline_forensic_suitability": {"status": "failed"},
            "recipes": [],
            "test_library_sha256": "t",
            "workload_harness_sha256": "w",
        }
        identity = {"git_tree_clean": True, "git_commit": "abc"}
        artifacts = {"profiler_dylib_sha256": "p", "test_library_sha256": "t"}
        with mock.patch.object(model, "_offline_git_identity_snapshot", return_value=identity), mock.patch.object(
            model, "build", return_value={"library_sha256": "p"},
        ), mock.patch.object(
            model, "_offline_executed_artifact_snapshot", return_value=artifacts,
        ), mock.patch.object(model, "_require_build_executed_artifacts_match"), mock.patch.object(
            model, "offline_workloads", return_value=representative,
        ) as workloads, mock.patch.object(
            model, "_require_executed_artifacts_stable",
        ), mock.patch.object(
            model, "_require_git_identity_stable",
        ), mock.patch.object(
            model, "_publish_offline_immutable_evidence", return_value={"immutable_evidence": {}},
        ) as publish:
            model.profiler_overhead_diagnosis()
        workloads.assert_called_once()
        self.assertFalse(workloads.call_args.kwargs["include_held_out"])
        publish.assert_called_once()
        self.assertFalse(publish.call_args.kwargs["update_rolling_pointer"])


if __name__ == "__main__":
    unittest.main()
