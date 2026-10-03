import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "benchmark"))
import eu4_benchmark as base  # noqa: E402
import eu4_frame_model as model  # noqa: E402
import frame_model_gates as gates  # noqa: E402


class LiveMeasurementContractTests(unittest.TestCase):
    def test_intrusive_contract_records_wp11_terminal_qual(self):
        contract = model.live_measurement_contract(model.LIVE_MEASUREMENT_INTRUSIVE_DIAGNOSTIC)
        self.assertTrue(contract["intrusive"])
        self.assertFalse(contract["quantitatively_qualified"])
        self.assertEqual(
            contract["tier1_qualification_terminal"]["evidence_id"],
            model.WP11_TERMINAL_TIER1_V4_EVIDENCE_ID,
        )


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
