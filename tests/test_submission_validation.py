import unittest

from submission_validation import evaluate_submission_pareto_gate


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
