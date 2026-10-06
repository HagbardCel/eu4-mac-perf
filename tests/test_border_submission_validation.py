#!/usr/bin/env python3
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "benchmark"))

import submission_border_validation as bv


class BorderSubmissionValidationTests(unittest.TestCase):
    def test_reference_phase_requires_draws_without_multidraw(self) -> None:
        start = {"border_candidate_draws": 0, "border_multidraw_calls": 0, "border_draw_calls_eliminated": 0}
        end = {"border_candidate_draws": 100, "border_multidraw_calls": 0, "border_draw_calls_eliminated": 0}
        out = bv.border_phase_validation(
            "reference", start, end, border_mutate=False, border_minimal=False, legacy_gl_engagement=False
        )
        self.assertEqual(out["status"], "skipped")
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

    def test_phase_roi_authoritative_gates_pass(self) -> None:
        snap = {
            "border_loop_head_entries": 10,
            "border_semantic_evaluations": 2,
            "border_implementable_evaluations": 2,
            "candidate_swaps": 5,
            "border_multidraw_calls": 0,
            "border_draw_calls_eliminated": 0,
            "border_runtime_context_checked": 1,
            "border_runtime_context_multidraw_supported": 1,
            "border_implementable_eliminations": 5,
            "border_semantic_eliminations": 8,
            "border_structural_eliminations": 10,
            "border_semantic_decisions": 1,
            "border_implementable_decisions": 1,
        }
        for k in bv.SEMANTIC_RUN_LEN_HISTOGRAM_KEYS:
            snap[k] = 0
        for k in bv.IMPLEMENTABLE_RUN_LEN_HISTOGRAM_KEYS:
            snap[k] = 0
        snap["border_semantic_run_len_2"] = 1
        snap["border_implementable_run_len_2"] = 1
        out = bv.border_phase_roi_authoritative_gates(snap)
        self.assertEqual(out["status"], "passed")


if __name__ == "__main__":
    unittest.main()
