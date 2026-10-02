import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "benchmark"))
import eu4_benchmark as base  # noqa: E402
import frame_model_gates as gates  # noqa: E402
import frame_model_tier1_policy as tier1  # noqa: E402
import frame_model_workload as workload  # noqa: E402


class Tier1AdmissionGateTests(unittest.TestCase):
    def test_current_policy_version_is_v2(self):
        self.assertEqual(tier1.TIER1_CAUSAL_POLICY_VERSION, "tier1_causal_rel3pct_abs50us_v2")

    def test_calibration_only_requires_causal_not_forensic(self):
        evidence = gates.GateEvidence()
        evidence.record("format_v3", "passed", "ok")
        evidence.record("offline_causal_admission", "passed", "ok")
        evidence.record("offline_forensic_suitability", "failed", "expected forensic fail")
        evidence.require(gates.required_gates_for_report_kind("calibration_only"))

    def test_calibration_only_blocks_when_causal_fails(self):
        evidence = gates.GateEvidence()
        evidence.record("format_v3", "passed", "ok")
        evidence.record("offline_causal_admission", "failed", "causal fail")
        evidence.record("offline_forensic_suitability", "passed", "ok")
        with self.assertRaises(ValueError):
            evidence.require(gates.required_gates_for_report_kind("calibration_only"))

    def test_forensic_pass_does_not_satisfy_causal(self):
        evidence = gates.GateEvidence()
        evidence.record("format_v3", "passed", "ok")
        evidence.record("offline_forensic_suitability", "passed", "ok")
        with self.assertRaises(ValueError):
            evidence.require(gates.required_gates_for_report_kind("calibration_only"))

    def test_calibration_only_blockers_ignore_forensic_failure(self):
        evidence = gates.GateEvidence()
        evidence.record("format_v3", "passed", "ok")
        evidence.record("offline_causal_admission", "passed", "ok")
        evidence.record("offline_forensic_suitability", "failed", "forensic fail")
        required = gates.required_gates_for_report_kind("calibration_only")
        self.assertEqual(evidence.blockers(required), {})

    def test_causal_blockers_ignore_forensic_failure(self):
        evidence = gates.GateEvidence()
        for name in gates.required_gates_for_report_kind("causal"):
            evidence.record(name, "passed", "ok")
        evidence.record("offline_forensic_suitability", "failed", "forensic fail")
        blockers = evidence.blockers(gates.required_gates_for_report_kind("causal"))
        self.assertNotIn("offline_forensic_suitability", blockers)
        self.assertEqual(blockers, {})

    def test_residual_discovery_required_set_is_satisfiable(self):
        evidence = gates.GateEvidence()
        for name in gates.required_gates_for_report_kind("residual_discovery"):
            evidence.record(name, "passed", "ok")
        evidence.require(gates.required_gates_for_report_kind("residual_discovery"))


class Tier1FailClosedTests(unittest.TestCase):
    def test_exploratory_fixture_sha_is_rejected(self):
        contaminated = (
            "284365e71435d8b6744440c5f5a028803527f5f1848f56365b85b5957c579188"
        )
        self.assertIn(contaminated, workload.EXPLORATORY_HELD_OUT_SHA256S)
        with self.assertRaises(base.BenchmarkError):
            workload._reject_contaminated_fixture_sha(contaminated)

    def test_partial_causal_gates_are_unavailable(self):
        entry = {
            "recipe": {"name": "mesh"},
            "gates": {"reference_elapsed_ns": {"median_fraction": 0.0, "status": "passed"}},
        }
        result = tier1.evaluate_recipe_causal(entry)
        self.assertEqual(result["status"], "unavailable")

    def test_missing_pairs_are_unavailable_not_pass(self):
        entry = {
            "recipe": {"name": "mesh"},
            "gates": {name: {} for name in tier1.EXPECTED_CAUSAL_GATE_NAMES},
        }
        result = tier1.evaluate_recipe_causal(entry)
        self.assertEqual(result["status"], "unavailable")

    def test_single_pair_is_unavailable(self):
        entry = {
            "recipe": {"name": "mesh"},
            "gates": {
                name: {"pairs": [{"reference": 1000, "instrumented": 1001}]}
                for name in tier1.EXPECTED_CAUSAL_GATE_NAMES
            },
        }
        result = tier1.evaluate_recipe_causal(entry)
        self.assertEqual(result["status"], "unavailable")

    def test_stored_summary_without_pairs_is_unavailable(self):
        entry = {
            "recipe": {"name": "mesh"},
            "gates": {
                name: {
                    "median_fraction": 0.0,
                    "confidence_interval_95": [0.0, 0.0],
                    "overhead_us_per_frame": 0.0,
                    "overhead_us_per_frame_ci95": [0.0, 0.0],
                }
                for name in tier1.EXPECTED_CAUSAL_GATE_NAMES
            },
        }
        result = tier1.evaluate_recipe_causal(entry)
        self.assertEqual(result["status"], "unavailable")

    def test_admission_without_held_out_is_unavailable(self):
        training = [{"recipe": {"name": "mesh"}, "gates": self._passing_gates()}]
        summary = tier1.summarize_admission(training, None, require_held_out=True)
        self.assertEqual(summary["status"], "unavailable")
        self.assertEqual(summary["held_out_recipe"]["status"], "unavailable")

    def test_forensic_without_gates_is_unavailable(self):
        result = tier1.evaluate_recipe_forensic({"recipe": {"name": "mesh"}, "gates": {}})
        self.assertEqual(result["status"], "unavailable")

    def test_partial_forensic_gate_set_is_unavailable(self):
        entry = {
            "recipe": {"name": "mesh"},
            "gates": {"sampled_elapsed_ns": {"status": "passed"}},
        }
        result = tier1.evaluate_recipe_forensic(entry)
        self.assertEqual(result["status"], "unavailable")

    def test_bootstrap_parameters_are_wired(self):
        pairs = [{"reference": 100 + index, "instrumented": 101 + index} for index in range(7)]
        policy = tier1.Tier1CausalPolicy(
            tier1.TIER1_CAUSAL_POLICY_VERSION,
            0.03,
            50.0,
            bootstrap_seed=7,
            bootstrap_samples=1,
            bootstrap_ci_low_index=0,
            bootstrap_ci_high_index=0,
            frames_per_trial=4,
        )
        status, evaluated, reason = policy.summarize_gate({"pairs": pairs})
        self.assertIsNone(reason)
        expected = workload.paired_summary(
            pairs,
            0.03,
            4,
            bootstrap_seed=7,
            bootstrap_samples=1,
            ci_low_index=0,
            ci_high_index=0,
        )
        self.assertEqual(evaluated["confidence_interval_95"], expected["confidence_interval_95"])
        self.assertEqual(evaluated["bootstrap_seed"], 7)
        self.assertEqual(evaluated["bootstrap_samples"], 1)

    def test_invalid_bootstrap_indices_rejected(self):
        with self.assertRaises(ValueError):
            tier1.Tier1CausalPolicy(
                tier1.TIER1_CAUSAL_POLICY_VERSION,
                0.03,
                50.0,
                bootstrap_samples=100,
                bootstrap_ci_high_index=1950,
            )

    @staticmethod
    def _passing_gates():
        gates_out = {}
        for name in tier1.EXPECTED_CAUSAL_GATE_NAMES:
            summary = workload.paired_summary([{"reference": 100, "instrumented": 101}] * 7, 0.03, 4)
            gates_out[name] = summary
        return gates_out


class Tier1ReplayTests(unittest.TestCase):
    @staticmethod
    def _load_preflight(path: Path) -> dict:
        data = json.loads(path.read_text())
        if "preflight" in data:
            return data["preflight"]
        return data

    def test_replay_legacy_archive_causal_fails(self):
        path = Path(__file__).resolve().parents[1] / (
            "analysis/evidence/frame-model-offline-legacy-373f3ff-20261001T204509Z.json"
        )
        if not path.is_file():
            self.skipTest("legacy archive missing")
        preflight = self._load_preflight(path)
        replay = tier1.replay_archive_preflight(preflight, require_held_out=False)
        self.assertEqual(replay["offline_causal_admission"]["status"], "failed")
        self.assertEqual(replay["offline_forensic_suitability"]["status"], "failed")

    def test_replay_50643ce_archive_preserves_failure(self):
        matches = list(
            (Path(__file__).resolve().parents[1] / "analysis/evidence").glob(
                "frame-model-offline-50643ce-*.json",
            ),
        )
        if not matches:
            self.skipTest("50643ce archive missing")
        preflight = self._load_preflight(matches[0])
        replay = tier1.replay_archive_preflight(preflight, require_held_out=False)
        self.assertEqual(replay["offline_causal_admission"]["status"], "failed")

    def test_replay_defaults_require_held_out(self):
        training = [{"recipe": {"name": "mesh"}, "gates": Tier1FailClosedTests._passing_gates()}]
        preflight = {"representative_workloads": {"recipes": training}}
        replay = tier1.replay_archive_preflight(preflight)
        self.assertEqual(replay["validation_scope"], "full_admission")
        self.assertEqual(replay["offline_causal_admission"]["status"], "unavailable")

    def test_replay_recomputes_statistics_from_pairs(self):
        pairs = [{"reference": 100, "instrumented": 130}] * 7
        summary = workload.paired_summary(pairs, tier1.TIER1_RELATIVE_LIMIT, 4)
        entry = {
            "recipe": {"name": "mesh", "role": "training"},
            "gates": {
                name: {
                    **summary,
                    "median_fraction": 0.0,
                    "confidence_interval_95": [0.0, 0.0],
                    "pairs": pairs,
                }
                for name in tier1.EXPECTED_CAUSAL_GATE_NAMES
            },
        }
        preflight = {"representative_workloads": {"recipes": [entry]}}
        replay = tier1.replay_archive_preflight(preflight, require_held_out=False)
        self.assertEqual(replay["offline_causal_admission"]["status"], "failed")

    def test_ab00679_exploratory_held_out_is_not_qualified(self):
        path = Path(__file__).resolve().parents[1] / (
            "analysis/evidence/frame-model-offline-ab00679-20261002T182324.368965Z-4f253c80.json"
        )
        if not path.is_file():
            self.skipTest("ab00679 archive missing")
        preflight = self._load_preflight(path)
        replay = tier1.replay_archive_preflight(preflight)
        held = replay["offline_causal_admission"]["held_out_recipe"]
        self.assertEqual(held["status"], "unavailable")
        self.assertEqual(held["reason"], tier1.HELD_OUT_CONTAMINATED_REASON)
        self.assertEqual(held["qualification"], "exploratory")
        self.assertEqual(replay["offline_causal_admission"]["status"], "failed")

    def test_exploratory_held_out_passing_gates_do_not_qualify_admission(self):
        gates = Tier1FailClosedTests._passing_gates()
        recipes = [
            {"recipe": {"name": "mesh", "role": "training"}, "gates": gates},
            {"recipe": {"name": "terrain_surrogate", "role": "held_out"}, "gates": gates},
        ]
        preflight = {"representative_workloads": {"recipes": recipes}}
        replay = tier1.replay_archive_preflight(preflight)
        self.assertEqual(replay["offline_causal_admission"]["status"], "unavailable")
        self.assertEqual(
            replay["offline_causal_admission"]["held_out_recipe"]["status"],
            "unavailable",
        )

    def test_ab00679_sha_cannot_be_laundered_with_provenance(self):
        contaminated = next(iter(workload.EXPLORATORY_HELD_OUT_SHA256S))
        gates = Tier1FailClosedTests._passing_gates()
        held = {
            "recipe": {
                "name": "terrain_surrogate",
                "role": "held_out",
                "sha256": contaminated,
                "admission_qualified": True,
                "fixture_committed": True,
                "fixture_path": workload.HELD_OUT_FIXTURE_PATH,
                "fixture_sha256": contaminated,
            },
            "gates": gates,
        }
        self.assertFalse(tier1.held_out_admission_qualified(held))
        preflight = {"representative_workloads": {"recipes": [held]}}
        replay = tier1.replay_archive_preflight(preflight)
        held_out = replay["offline_causal_admission"]["held_out_recipe"]
        self.assertEqual(held_out["status"], "unavailable")
        self.assertEqual(held_out["reason"], tier1.HELD_OUT_CONTAMINATED_REASON)

    def test_mismatched_fixture_and_recipe_sha_not_qualified(self):
        gates = Tier1FailClosedTests._passing_gates()
        held = {
            "recipe": {
                "name": "terrain_surrogate",
                "role": "held_out",
                "sha256": "a" * 64,
                "admission_qualified": True,
                "fixture_committed": True,
                "fixture_path": workload.HELD_OUT_FIXTURE_PATH,
                "fixture_sha256": "b" * 64,
            },
            "gates": gates,
        }
        self.assertFalse(tier1.held_out_admission_qualified(held))


if __name__ == "__main__":
    unittest.main()
