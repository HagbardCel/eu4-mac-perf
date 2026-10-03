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


def _phase_c_synthetic_frame(phase_name: str, *, counters: bool) -> dict:
    """Counters windows are fully instrumented; reference matches lean REFERENCE semantics."""
    multiplier = 1.1 if counters else 1.0
    return {
        "phase": model.PHASE_NUMBER[phase_name],
        "update_id": 1,
        "render_id": 1,
        "measurement_epoch": 1,
        "render_executed": 1 if counters else 0,
        "render_attempts": 1 if counters else 0,
        "present_calls": 1 if counters else 0,
        "present_scene_calls": 0,
        "update_wall_ns": int(8e6),
        "update_cpu_ns": int(4e6 * multiplier),
        "render_wall_ns": int(2e6) if counters else 0,
        "render_cpu_ns": int(1e6 * multiplier) if counters else 0,
        "idle_wall_ns": 0,
        "idle_cpu_ns": 0,
        "present_wall_ns": int(1e6) if counters else 0,
        "present_cpu_ns": int(5e5) if counters else 0,
        "wait_ns": 0,
        "wait_cpu_ns": 0,
        "wall_ns": int(12e6),
        "cpu_ns": int(6e6 * multiplier),
        "draws": 10 if counters else 0,
        "indices": 100,
        "triangles": 50,
        "draw_wall_ns_est": 0,
        "draw_cpu_ns_est": 0,
        "draw_timed_samples": 2 if counters else 0,
        "buffer_calls": 3 if counters else 0,
        "buffer_bytes": 0,
        "buffer_storage_bytes": 0,
        "buffer_upload_bytes": 0,
        "texture_calls": 0,
        "texture_bytes": 0,
        "uniform_calls": 4 if counters else 0,
        "uniform_bytes": 0,
        "state_calls": 5 if counters else 0,
        "texture_binds": 0,
        "buffer_binds": 0,
        "program_switches": 0,
        "bucket_calls": 0,
        "append_calls": 0,
        "forwarded_draws": 0,
        "suppressed_draws": 0,
        "flags": 0,
        "sleep_ns": 0,
    }


class PhaseCScheduleTests(unittest.TestCase):
    def test_intrusive_diagnostic_phase_schedule(self):
        names = [item["name"] for item in model.intrusive_diagnostic_phase_schedule()]
        self.assertEqual(names, ["R1", "C1", "R2", "C2", "R3"])
        self.assertEqual(
            [item["mode"] for item in model.intrusive_diagnostic_phase_schedule()],
            ["reference", "profile", "reference", "profile", "reference"],
        )

    def test_intrusive_diagnostic_run_budget_within_deadline(self):
        self.assertLessEqual(gates.intrusive_diagnostic_run_budget(), gates.RUN_DEADLINE_SECONDS)

    def test_compute_live_rc_observer_effect_lean_reference_semantics(self):
        phases = []
        base_ns = 1_000_000_000
        for index, name in enumerate(("R1", "C1", "R2", "C2", "R3")):
            phases.append(
                {
                    "name": name,
                    "duration_s": 20,
                    "start_ns": base_ns + index * 30_000_000_000,
                    "end_ns": base_ns + (index + 1) * 30_000_000_000,
                },
            )
        profile_rows = []
        for phase in phases:
            counters = phase["name"].startswith("C")
            for _ in range(100):
                profile_rows.append(_phase_c_synthetic_frame(phase["name"], counters=counters))
        probe = []
        for phase in phases:
            rate = 120.0 if phase["name"].startswith("R") else 118.0
            probe.append({"monotonic_ns": phase["start_ns"] + 5_000_000_000, "swaps_s": rate})
        historical_path = Path(__file__).resolve().parents[1] / "results/autonomous-reproducibility.json"
        if not historical_path.is_file():
            self.skipTest("autonomous-reproducibility.json missing")

        def fake_power(_raw, phase_list, _anchor, _pid):
            return {
                phase["name"]: {
                    "eu4_cputime_ms_per_s": 400.0 if phase["name"].startswith("R") else 440.0,
                }
                for phase in phase_list
            }

        with mock.patch.object(model, "ROOT", historical_path.parent.parent), mock.patch.object(
            model.diagnostic,
            "summarize_power",
            side_effect=fake_power,
        ):
            effect = model.compute_live_rc_observer_effect(
                phases,
                profile_rows,
                [],
                probe,
                [],
                {"monotonic_ns": 0},
                12345,
            )
        self.assertGreater(effect["aggregate"]["cpu_perturbation_fraction"], 0.05)
        self.assertIn("median_update_cpu_ms", effect["frame_perturbation_fraction"])
        self.assertIn("C1", effect["brackets"])
        self.assertIn("live_update_cpu_delta_us_per_update_intrusive", effect["brackets"]["C1"])
        self.assertAlmostEqual(
            effect["brackets"]["C1"]["reference_phases"],
            ["R1", "R2"],
        )
        attribution = model.intrusive_diagnostic_exclusive_attribution(
            [],
            phases,
            profile_rows,
            effect["phase_summaries"],
        )
        self.assertIn("C1", attribution["exclusive_scope_windows"])
        self.assertIn("exclusive_rank_stable", attribution)

    def test_disabled_forensic_record_check_includes_lowercase_uniform(self):
        counts = {kind: 0 for kind in ("D", "S", "U", "u", "V", "W", "B", "b", "T", "t", "G")}
        self.assertFalse(any(counts.get(kind, 0) for kind in model.FORENSIC_DETAIL_RECORD_KINDS))

    def test_summarize_frame_model_forensic_trace_state_operations(self):
        row = ["S", "1", "4096", "8", "0"] + ["0"] * 8 + ["1", "1", "103", "0", "0", "0", "0"]
        self.assertEqual(len(row), 20)
        parsed = model.summarize_frame_model_forensic_trace([row])
        self.assertEqual(parsed["state_operation_counts"].get("vertex_attrib_pointer"), 1)
        self.assertEqual(parsed["record_counts"].get("S"), 1)

    def test_intrusive_diagnostic_forensic_tail_summary_uses_frame_model_schema(self):
        tail_phase = {
            "name": "TAIL",
            "duration_s": 20,
            "measurement_epoch": 1,
            "start_ns": 0,
            "end_ns": 20_000_000_000,
            "window_generation": 0,
            "clock_offset_ns": 0,
            "alignment_uncertainty_ns": 0,
            "sampled_window_evidence": {"status": "passed", "windows": []},
        }
        frame = _phase_c_synthetic_frame("TAIL", counters=True)
        frame["phase"] = model.PHASE_NUMBER["TAIL"]
        frame["measurement_epoch"] = 1
        frame["render_id"] = 1
        trace = [["S", "1", "8192", "9", "2"] + ["0"] * 8 + ["1", "1", str(model.PHASE_NUMBER["TAIL"]), "0", "0", "0", "0"]]
        self.assertEqual(len(trace[0]), 20)
        summary = model.intrusive_diagnostic_forensic_tail_summary(
            trace,
            tail_phase,
            {"monotonic_ns": 0, "wall_ns": 0},
            [frame],
        )
        self.assertEqual(summary["state_operation_counts"].get("enable_vertex_attrib_array"), 1)
        self.assertEqual(summary.get("structural_capture_status"), "passed")
        self.assertIn("temporal_differences", summary)
        self.assertNotEqual(summary.get("status"), "unavailable")


class RunDiagnosticOnlyTests(unittest.TestCase):
    @mock.patch.object(model, "_run_intrusive_diagnostic_phase_c")
    def test_run_diagnostic_only_dispatches_phase_c(self, phase_c_mock):
        out = Path("/tmp/eu4-phase-c-dispatch")
        phase_c_mock.return_value = out
        self.assertEqual(model.run(out, diagnostic_only=True), out)
        phase_c_mock.assert_called_once_with(out)


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
    def test_analyze_runs_for_complete_with_attribution_gap(self):
        with tempfile.TemporaryDirectory() as temporary:
            run_dir = Path(temporary)
            manifest = {
                "report_kind": "intrusive_diagnostic",
                "format_version": model.FORMAT_VERSION,
                "status": model.DIAGNOSTIC_STATUS_COMPLETE_WITH_GAPS,
                "measurement_contract": model.live_measurement_contract(
                    model.LIVE_MEASUREMENT_INTRUSIVE_DIAGNOSTIC,
                ),
                "gates": {},
                "calibration": {"aggregate": {}},
            }
            (run_dir / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
            (run_dir / "telemetry.csv").write_text("H,3\n", encoding="utf-8")
            output = model.analyze(run_dir)
            self.assertTrue((run_dir / "report.json").is_file())
            self.assertTrue(output.get("attribution_gap"))

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


class PreflightCliOutputTests(unittest.TestCase):
    def test_console_summary_lists_powermetrics_blocker(self):
        summary = model.preflight_console_summary(
            {
                "status": "diagnostic_ready",
                "preflight_kind": "intrusive_diagnostic_contract_v1",
                "live_prerequisites": {
                    "run_ready": False,
                    "venice_scene_registered": True,
                    "run_blockers": ["Install powermetrics first"],
                    "powermetrics_helper": {"status": "missing"},
                },
            },
            evidence_path=Path("/tmp/evidence.json"),
        )
        self.assertIn("evidence_json: /tmp/evidence.json", summary)
        self.assertIn("powermetrics_helper: missing", summary)
        self.assertIn("Install powermetrics first", summary)

    @mock.patch.object(model.auto, "powermetrics_helper_status", return_value={"status": "ready"})
    def test_live_prerequisites_ready_when_helper_and_scene(self, _pm_mock):
        with tempfile.TemporaryDirectory() as temporary:
            scene = Path(temporary) / "venice_scene.png"
            manifest = Path(temporary) / "venice_scene.json"
            scene.write_bytes(b"x")
            manifest.write_text("{}", encoding="utf-8")
            with mock.patch.object(model.auto, "SCENE", scene), mock.patch.object(
                model.auto,
                "SCENE_MANIFEST",
                manifest,
            ):
                prereq = model._intrusive_diagnostic_live_prerequisites()
        self.assertTrue(prereq["run_ready"])
        self.assertEqual(prereq["run_blockers"], [])


if __name__ == "__main__":
    unittest.main()
