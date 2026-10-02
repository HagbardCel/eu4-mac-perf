import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "benchmark"))
import eu4_benchmark as base  # noqa: E402
import eu4_frame_model as model  # noqa: E402


class ProfilerOverheadDiagnosisTests(unittest.TestCase):
    def test_diagnostic_matrix_stage_features_cover_all_stages(self):
        self.assertEqual(len(model.DIAGNOSTIC_MATRIX_STAGES), 6)
        for stage in model.DIAGNOSTIC_MATRIX_STAGES:
            self.assertIn(stage, model.DIAGNOSTIC_MATRIX_FEATURES)
            self.assertEqual(len(model.DIAGNOSTIC_MATRIX_FEATURES[stage]), 3)

    def test_diagnostic_trial_stages_lead_with_diag_counters(self):
        self.assertEqual(model.DIAGNOSTIC_MATRIX_TRIAL_STAGES[0], model.DIAGNOSTIC_MATRIX_COUNTERS_STAGE)
        self.assertEqual(model.DIAGNOSTIC_MATRIX_TRIAL_STAGES[1], model.DIAGNOSTIC_MATRIX_BASELINE)
        self.assertEqual(len(model.DIAGNOSTIC_MATRIX_TRIAL_STAGES), 7)

    def test_offline_workload_telemetry_paths_are_unique_per_trial(self):
        paths = model.offline_workload_telemetry_paths_for_recipe_trial("mesh", 0)
        self.assertEqual(len(paths), len(set(paths)))
        self.assertIn("mesh-0-counters.csv", paths)
        self.assertIn("mesh-0-diag_counters.csv", paths)
        self.assertNotEqual("mesh-0-counters.csv", "mesh-0-diag_counters.csv")

    def test_validate_diagnostic_stage_features_rejects_inactive_gpu(self):
        with self.assertRaises(base.BenchmarkError):
            model._validate_diagnostic_stage_features(
                "diag_B_gpu",
                (1, 0, 0),
                [0, 0, 0],
                0,
                {},
            )

    def test_validate_diagnostic_stage_features_rejects_inactive_forensic(self):
        with self.assertRaises(base.BenchmarkError):
            model._validate_diagnostic_stage_features(
                "diag_C_detail",
                (0, 1, 0),
                [0, 0, 0],
                0,
                {kind: 0 for kind in model.FORENSIC_DETAIL_RECORD_KINDS},
            )

    def test_validate_diagnostic_stage_features_accepts_active_forensic(self):
        counts = {kind: 0 for kind in model.FORENSIC_DETAIL_RECORD_KINDS}
        counts["D"] = 3
        model._validate_diagnostic_stage_features("diag_C_detail", (0, 1, 0), [0, 0, 0], 3, counts)

    def test_offline_workloads_training_only_stamps_validation_scope(self):
        frozen = {
            "profiler_dylib_sha256": "profiler",
            "test_library_sha256": "frozen-test",
            "workload_harness_sha256": "frozen-workload",
            "draw_manifest_sha256": "manifest",
        }
        admission = {"status": "failed", "training_recipes": [], "held_out_recipe": {}}
        with mock.patch.object(model, "workload") as workload_mock, mock.patch.object(
            model.tier1,
            "summarize_admission",
            return_value=admission,
        ), mock.patch.object(model.tier1, "summarize_forensic", return_value={"status": "failed", "recipes": []}):
            workload_mock.recipes.return_value = []
            result = model.offline_workloads(
                executed_artifacts_start=frozen,
                include_held_out=False,
            )
        self.assertEqual(result["validation_scope"], model.TRAINING_ONLY_VALIDATION_SCOPE)
        self.assertEqual(
            result["offline_causal_admission"]["validation_scope"],
            model.TRAINING_ONLY_VALIDATION_SCOPE,
        )

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

    def test_profiler_overhead_diagnosis_skips_rolling_pointer_and_scopes_training(self):
        representative = {
            "status": "failed",
            "validation_scope": model.TRAINING_ONLY_VALIDATION_SCOPE,
            "offline_causal_admission": {
                "status": "failed",
                "validation_scope": model.TRAINING_ONLY_VALIDATION_SCOPE,
            },
            "offline_forensic_suitability": {"status": "failed"},
            "recipes": [],
            "test_library_sha256": "t",
            "workload_harness_sha256": "w",
        }
        identity = {"git_tree_clean": True, "git_commit": "abc"}
        artifacts = {"profiler_dylib_sha256": "p", "test_library_sha256": "t"}
        published = {"immutable_evidence": {}, "validation_scope": model.TRAINING_ONLY_VALIDATION_SCOPE}

        def capture_evidence(evidence, **kwargs):
            self.assertEqual(evidence["validation_scope"], model.TRAINING_ONLY_VALIDATION_SCOPE)
            return published

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
            model, "_publish_offline_immutable_evidence", side_effect=capture_evidence,
        ) as publish:
            result = model.profiler_overhead_diagnosis()
        workloads.assert_called_once()
        self.assertFalse(workloads.call_args.kwargs["include_held_out"])
        publish.assert_called_once()
        self.assertFalse(publish.call_args.kwargs["update_rolling_pointer"])
        self.assertEqual(result["validation_scope"], model.TRAINING_ONLY_VALIDATION_SCOPE)


    def test_summarize_wp1_reads_preflight_payload_on_disk(self):
        import summarize_wp1_diagnosis as wp1

        commit = "deadbeef" * 5
        archive = {
            "evidence_id": "20261002T220000.000000Z-abc12345",
            "recorded_at_utc": "20261002T220000.000000Z-abc12345",
            "git_commit": commit,
            "git_provenance_verified": True,
            "git_tree_clean": True,
            "identity_start": {"git_commit": commit, "git_tree_clean": True},
            "identity_end": {"git_commit": commit, "git_tree_clean": True},
            "executed_artifact_start": {"test_library_sha256": "a"},
            "executed_artifact_end": {"test_library_sha256": "a"},
            "policy_versions": {"diagnostic_matrix": wp1.EXPECTED_DIAGNOSTIC_POLICY},
            "preflight": {
                "purpose": wp1.EXPECTED_PURPOSE,
                "status": wp1.EXPECTED_STATUS,
                "validation_scope": wp1.EXPECTED_SCOPE,
                "representative_workloads": {
                    "recipes": [
                        {"recipe": {"name": "mesh", "role": "training"}},
                        {"recipe": {"name": "borders", "role": "training"}},
                        {"recipe": {"name": "text_ui", "role": "training"}},
                    ],
                },
            },
        }
        with tempfile.TemporaryDirectory(dir=wp1.ROOT) as temporary:
            temp_path = Path(temporary) / "wp1-test-archive.json"
            temp_path.write_text(json.dumps(archive), encoding="utf-8")
            entry = wp1.validate_wp1_archive(json.loads(temp_path.read_text()), temp_path)
            self.assertEqual(entry["evidence_id"], "20261002T220000.000000Z-abc12345")
            self.assertEqual(entry["git_commit"], commit)


if __name__ == "__main__":
    unittest.main()
