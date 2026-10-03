import json
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "benchmark"))
import eu4_benchmark as base  # noqa: E402
import eu4_frame_model as model  # noqa: E402


class LiveMeasurementContractTests(unittest.TestCase):
    def test_intrusive_contract_records_wp11_terminal_qual(self):
        contract = model.live_measurement_contract(model.LIVE_MEASUREMENT_INTRUSIVE_DIAGNOSTIC)
        self.assertTrue(contract["intrusive"])
        self.assertFalse(contract["quantitatively_qualified"])
        self.assertEqual(
            contract["tier1_qualification_terminal"]["evidence_id"],
            model.WP11_TERMINAL_TIER1_V4_EVIDENCE_ID,
        )
        self.assertIn("held_out_qualification_claims", contract["prohibitions"])

    def test_diagnostic_only_flags_mutually_exclusive(self):
        with self.assertRaises(base.BenchmarkError):
            model.run(
                Path("/tmp/eu4-test-out"),
                diagnostic_only=True,
                calibration_only=True,
            )


class PreflightDiagnosticModeTests(unittest.TestCase):
    def _failed_admission_representative(self) -> dict:
        return {
            "offline_causal_admission": {"status": "failed", "training_recipes": []},
            "offline_forensic_suitability": {"status": "unavailable"},
        }

    @mock.patch.object(
        model,
        "_publish_offline_immutable_evidence",
        side_effect=lambda evidence, **_: {
            **evidence,
            "immutable_evidence": {"archive_path": "analysis/evidence/fake-preflight.json"},
        },
    )
    @mock.patch.object(model, "offline_workloads")
    @mock.patch.object(model, "offline_harness")
    @mock.patch.object(model, "auto")
    @mock.patch.object(model, "build")
    @mock.patch.object(model.base, "sha256", return_value=model.EXPECTED)
    def test_qualified_preflight_raises_on_failed_admission(
        self,
        _sha,
        _build,
        auto_mock,
        _harness,
        workloads_mock,
        _publish,
    ):
        auto_mock.preflight.return_value = {"fixture": {"save_sha256": "x"}}
        _build.return_value = {"executable_sha256": "exe"}
        workloads_mock.return_value = self._failed_admission_representative()
        with mock.patch.object(model, "_offline_git_identity_snapshot", return_value={"git_tree_clean": True}), \
             mock.patch.object(model, "_offline_executed_artifact_snapshot", return_value={"test_library_sha256": "a", "workload_harness_sha256": "b"}), \
             mock.patch.object(model, "_require_build_executed_artifacts_match"), \
             mock.patch.object(model, "_require_executed_artifacts_stable"), \
             mock.patch.object(model, "_require_git_identity_stable"):
            with self.assertRaises(base.BenchmarkError) as raised:
                model.preflight(run_gl=True, live_measurement_mode=model.LIVE_MEASUREMENT_QUALIFIED)
            self.assertIn("Offline causal admission failed", str(raised.exception))

    @mock.patch.object(
        model,
        "_publish_offline_immutable_evidence",
        side_effect=lambda evidence, **_: {
            **evidence,
            "immutable_evidence": {"archive_path": "analysis/evidence/fake-preflight.json"},
        },
    )
    @mock.patch.object(model, "offline_workloads")
    @mock.patch.object(model, "offline_harness")
    @mock.patch.object(model, "auto")
    @mock.patch.object(model, "build")
    @mock.patch.object(model.base, "sha256", return_value=model.EXPECTED)
    def test_diagnostic_preflight_returns_diagnostic_ready(
        self,
        _sha,
        _build,
        auto_mock,
        _harness,
        workloads_mock,
        _publish,
    ):
        auto_mock.preflight.return_value = {"fixture": {"save_sha256": "x"}}
        _build.return_value = {"executable_sha256": "exe"}
        workloads_mock.return_value = self._failed_admission_representative()
        with mock.patch.object(model, "_offline_git_identity_snapshot", return_value={"git_tree_clean": True}), \
             mock.patch.object(model, "_offline_executed_artifact_snapshot", return_value={"test_library_sha256": "a", "workload_harness_sha256": "b"}), \
             mock.patch.object(model, "_require_build_executed_artifacts_match"), \
             mock.patch.object(model, "_require_executed_artifacts_stable"), \
             mock.patch.object(model, "_require_git_identity_stable"):
            evidence = model.preflight(
                run_gl=True,
                live_measurement_mode=model.LIVE_MEASUREMENT_INTRUSIVE_DIAGNOSTIC,
            )
        self.assertEqual(evidence["status"], "diagnostic_ready")
        self.assertEqual(
            evidence["measurement_contract"]["mode"],
            model.LIVE_MEASUREMENT_INTRUSIVE_DIAGNOSTIC,
        )


if __name__ == "__main__":
    unittest.main()
