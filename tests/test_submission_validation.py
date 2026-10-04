import unittest
from unittest import mock

from submission_validation import (
    bracket_normalized_ababa_gate,
    evaluate_submission_pareto_gate,
    higher_is_non_inferior,
    lower_is_non_inferior,
    summarize_phase_metrics,
)


def _phase(name: str, role: str, summary: dict, **extra) -> dict:
    return {
        "name": name,
        "role": role,
        "summary": summary,
        "screenshot": __file__,
        "control_validation": {"status": "passed"},
        **extra,
    }


class SubmissionValidationTests(unittest.TestCase):
    def test_power_win_requires_swaps_non_inferior(self):
        baseline = {"eu4_cpu_ms_per_s": 1100, "combined_w": 10.0, "swaps_per_s": 54.0}
        candidate = {"eu4_cpu_ms_per_s": 1090, "combined_w": 9.4, "swaps_per_s": 53.5}
        result = evaluate_submission_pareto_gate(baseline, candidate)
        self.assertIn("A_combined_power", result["paths"])
        self.assertTrue(result["passed"])

    def test_swap_loss_fails_power_path(self):
        baseline = {"eu4_cpu_ms_per_s": 1100, "combined_w": 10.0, "swaps_per_s": 54.0}
        candidate = {"eu4_cpu_ms_per_s": 1000, "combined_w": 9.0, "swaps_per_s": 40.0}
        result = evaluate_submission_pareto_gate(baseline, candidate)
        self.assertFalse(result["passed"])

    def test_zero_metrics_fail_closed(self):
        baseline = {"eu4_cpu_ms_per_s": 1100, "combined_w": 10.0, "swaps_per_s": 54.0}
        candidate = {"eu4_cpu_ms_per_s": 0, "combined_w": 9.0, "swaps_per_s": 54.0}
        result = evaluate_submission_pareto_gate(baseline, candidate)
        self.assertFalse(result["passed"])
        self.assertEqual(result["status"], "invalid_metrics")

    def test_bracket_normalized_rejects_monotonic_drift_false_win(self):
        import statistics

        base = {"eu4_cpu_ms_per_s": 1000.0, "combined_w": 10.0, "swaps_per_s": 50.0}
        phases = [
            _phase("a1", "reference", base),
            _phase("b1", "candidate", {**base, "combined_w": 8.5}),
            _phase("a2", "reference", {**base, "combined_w": 9.0}),
            _phase("b2", "candidate", {**base, "combined_w": 8.4}),
            _phase("a3", "reference", {**base, "combined_w": 6.0}),
        ]
        metrics = [summarize_phase_metrics(p["summary"]) for p in phases]
        refs = [metrics[0], metrics[2], metrics[4]]
        cands = [metrics[1], metrics[3]]
        merged_ref = {key: statistics.median([m[key] for m in refs]) for key in refs[0]}
        merged_cand = {key: statistics.median([m[key] for m in cands]) for key in cands[0]}
        global_gate = evaluate_submission_pareto_gate(merged_ref, merged_cand)
        self.assertTrue(global_gate["passed"])
        with mock.patch("submission_validation.scene_gate", return_value={"passed": True}):
            result = bracket_normalized_ababa_gate(phases, expected_candidate_capability_id=1)
        self.assertFalse(result["bracket_gates"]["b2"]["passed"])
        self.assertTrue(result["bracket_gates"]["b1"]["passed"])

    def test_non_inferior_helpers(self):
        self.assertTrue(higher_is_non_inferior(100.0, 99.0))
        self.assertTrue(lower_is_non_inferior(100.0, 101.0))
        self.assertFalse(lower_is_non_inferior(0.0, 1.0))

    def test_summarize_phase_metrics_cpu_ms_per_swap(self):
        metrics = summarize_phase_metrics({"median_swaps_s": 50.0, "eu4_cpu_ms_per_s": 1000.0, "combined_w": 8.0})
        self.assertEqual(metrics["eu4_cpu_ms_per_swap"], 20.0)
