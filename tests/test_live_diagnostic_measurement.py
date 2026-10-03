import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "benchmark"))
import eu4_benchmark as base  # noqa: E402
import eu4_frame_model as model  # noqa: E402
import frame_model_gates as gates  # noqa: E402


class LiveMeasurementContractTests(unittest.TestCase):
    def test_intrusive_contract_records_wp11_terminal_qual(self):
        contract = model.live_measurement_contract(model.LIVE_MEASUREMENT_INTRUSIVE_DIAGNOSTIC)
        self.assertTrue(contract["intrusive"])
        self.assertNotIn("quantitatively_qualified", contract)
        finalized = model.finalize_measurement_contract(contract, admission_passed=False)
        self.assertFalse(finalized["quantitative_claims_allowed"])
        self.assertEqual(
            contract["tier1_qualification_terminal"]["evidence_id"],
            model.WP11_TERMINAL_TIER1_V4_EVIDENCE_ID,
        )

    def test_qualified_contract_allows_claims_only_when_admission_passed(self):
        base = model.live_measurement_contract(model.LIVE_MEASUREMENT_QUALIFIED)
        self.assertTrue(model.finalize_measurement_contract(base, admission_passed=True)["quantitative_claims_allowed"])
        self.assertFalse(model.finalize_measurement_contract(base, admission_passed=False)["quantitative_claims_allowed"])


class ResolveLivePreflightOutcomeTests(unittest.TestCase):
    def test_qualified_mode_blocks_on_failed_admission(self):
        outcome = model.resolve_live_preflight_outcome("failed", model.LIVE_MEASUREMENT_QUALIFIED)
        self.assertEqual(outcome["status"], "blocked")
        self.assertTrue(outcome["block_on_failed_admission"])

    def test_intrusive_mode_allows_failed_admission(self):
        outcome = model.resolve_live_preflight_outcome("failed", model.LIVE_MEASUREMENT_INTRUSIVE_DIAGNOSTIC)
        self.assertEqual(outcome["status"], "diagnostic_ready")
        self.assertFalse(outcome["block_on_failed_admission"])


class IntrusiveDiagnosticGatePolicyTests(unittest.TestCase):
    def test_required_gates_exclude_tier1_admission(self):
        required = gates.required_gates_for_report_kind("intrusive_diagnostic")
        self.assertIn("diagnostic_authorization", required)
        self.assertNotIn("offline_causal_admission", required)
        self.assertNotIn("interventions", required)

    def test_diagnostic_release_allows_failed_offline_admission(self):
        evidence = gates.GateEvidence()
        evidence.record("format_v3", "passed", "ok")
        evidence.record("integrity", "passed", "ok")
        evidence.record("origin_integrity", "passed", "ok")
        evidence.record("cadence", "passed", "ok")
        evidence.record("diagnostic_authorization", "passed", "explicit intrusive mode")
        evidence.record("live_observer_effect", "passed", "recorded", cpu_perturbation_fraction=0.08)
        evidence.record("offline_causal_admission", "failed", "expected under WP11")
        evidence.require(gates.required_gates_for_report_kind("intrusive_diagnostic"))


class RunDiagnosticOnlyTests(unittest.TestCase):
    def test_run_diagnostic_only_refuses_until_phase_c(self):
        with self.assertRaises(base.BenchmarkError) as raised:
            model.run(Path("/tmp/eu4-out"), diagnostic_only=True)
        self.assertIn("Phase C", str(raised.exception))


class AssertQualifiedIntrusionTests(unittest.TestCase):
    def test_qualified_mode_raises_on_large_cpu_perturbation(self):
        with self.assertRaises(base.BenchmarkError):
            model.assert_qualified_live_intrusion_within_limit(
                frame_perturbation={"median_update_cpu_ms": 0.01},
                cpu_perturbation=0.10,
                swap_perturbation=0.0,
                measured_swap_rate=120.0,
                historical_swap_rate=120.0,
            )


class IntrusiveContractPreflightTests(unittest.TestCase):
    @mock.patch.object(model, "_publish_offline_immutable_evidence")
    @mock.patch.object(model, "offline_workloads")
    @mock.patch.object(model, "offline_harness")
    @mock.patch.object(model, "verify_wp11_terminal_tier1_v4_evidence")
    @mock.patch.object(model, "auto")
    @mock.patch.object(model, "build")
    @mock.patch.object(model.base, "sha256", return_value=model.EXPECTED)
    def test_intrusive_contract_preflight_skips_qualification_capture(
        self,
        _sha,
        build_mock,
        auto_mock,
        verify_mock,
        harness_mock,
        workloads_mock,
        publish_mock,
    ):
        build_mock.return_value = {"executable_sha256": "exe"}
        auto_mock.preflight.return_value = {"fixture": {"save_sha256": "x"}}
        verify_mock.return_value = {
            "evidence_id": model.WP11_TERMINAL_TIER1_V4_EVIDENCE_ID,
            "overhead_gate": "failed",
            "tier1_v4_admission": "failed",
        }
        with mock.patch.object(model, "offline_detour_harness", return_value={}), \
             mock.patch.object(model, "offline_render_gate_harness", return_value={}), \
             mock.patch.object(model, "offline_arb_harness", return_value={}), \
             mock.patch.object(model, "offline_producer_harnesses", return_value={}), \
             mock.patch.object(model, "offline_control_harness", return_value={}), \
             mock.patch.object(model, "offline_writer_production_harness", return_value={}), \
             mock.patch.object(model, "offline_draw_alias_harness", return_value={}), \
             mock.patch.object(model, "offline_alias_interpose_harness", return_value={}), \
             mock.patch.object(model, "_offline_git_identity_snapshot", return_value={"git_tree_clean": True}), \
             mock.patch.object(model, "_offline_executed_artifact_snapshot", return_value={"test_library_sha256": "a", "workload_harness_sha256": "b"}), \
             mock.patch.object(model, "_require_build_executed_artifacts_match"):
            result = model.preflight(
                run_gl=True,
                live_measurement_mode=model.LIVE_MEASUREMENT_INTRUSIVE_DIAGNOSTIC,
                intrusive_contract_only=True,
            )
        workloads_mock.assert_not_called()
        harness_mock.assert_not_called()
        publish_mock.assert_not_called()
        self.assertEqual(result["status"], "diagnostic_ready")
        self.assertEqual(result["offline_qualification_capture"], "skipped")
        self.assertFalse(result["measurement_contract"]["quantitative_claims_allowed"])


class AnalyzeIntrusiveDiagnosticTests(unittest.TestCase):
    def test_analyze_intrusive_report_is_not_causal_eligible(self):
        with tempfile.TemporaryDirectory() as temporary:
            run_dir = Path(temporary)
            manifest = {
                "report_kind": "intrusive_diagnostic",
                "format_version": model.FORMAT_VERSION,
                "status": "complete",
                "measurement_contract": model.live_measurement_contract(
                    model.LIVE_MEASUREMENT_INTRUSIVE_DIAGNOSTIC,
                ),
                "gates": {
                    "offline_causal_admission": {"status": "failed", "reason": "WP11"},
                },
            }
            (run_dir / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
            (run_dir / "telemetry.csv").write_text("H,3\n", encoding="utf-8")
            output = model.analyze(run_dir)
            self.assertEqual(output["report_kind"], "intrusive_diagnostic")
            self.assertIn("measurement_contract", output)
            self.assertEqual(output["causal_contrasts"], {})
            self.assertIn("not eligible", output["diagnosis_status"])
            report = json.loads((run_dir / "report.json").read_text(encoding="utf-8"))
            self.assertEqual(report["measurement_contract"]["mode"], model.LIVE_MEASUREMENT_INTRUSIVE_DIAGNOSTIC)


if __name__ == "__main__":
    unittest.main()
