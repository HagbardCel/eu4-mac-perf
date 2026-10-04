#!/usr/bin/env python3
import unittest

import submission_border_validation as bv


class BorderSubmissionValidationTests(unittest.TestCase):
    def test_reference_phase_requires_draws_without_multidraw(self) -> None:
        start = {"border_candidate_draws": 0, "border_multidraw_calls": 0, "border_draw_calls_eliminated": 0}
        end = {"border_candidate_draws": 100, "border_multidraw_calls": 0, "border_draw_calls_eliminated": 0}
        out = bv.border_phase_validation("reference", start, end, border_mutate=False, border_minimal=False)
        self.assertEqual(out["status"], "passed")

    def test_observer_b_phase_accepts_mutate_disabled_fallback(self) -> None:
        start = {
            "border_candidate_draws": 0,
            "border_multidraw_calls": 0,
            "border_draw_calls_eliminated": 0,
            "border_fallback_mutate_disabled": 0,
        }
        end = {
            "border_candidate_draws": 50,
            "border_multidraw_calls": 0,
            "border_draw_calls_eliminated": 0,
            "border_fallback_mutate_disabled": 50,
        }
        out = bv.border_phase_validation("candidate", start, end, border_mutate=True, border_minimal=False)
        self.assertEqual(out["status"], "passed")

    def test_experiment_gate_fails_without_a_phase_draws(self) -> None:
        phases = [
            {
                "name": "a1",
                "border_validation": {
                    "status": "failed",
                    "reason": "x",
                    "border_candidate_draws_delta": 0,
                },
            },
            {
                "name": "b1",
                "border_validation": {
                    "status": "passed",
                    "border_candidate_draws_delta": 10,
                },
            },
        ]
        gate = bv.border_experiment_gate(phases)
        self.assertEqual(gate["status"], "failed")


if __name__ == "__main__":
    unittest.main()
