#!/usr/bin/env python3
import unittest

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "benchmark"))
import submission_border_validation as bv


class BorderRoiEngagementTests(unittest.TestCase):
    def test_passes_observer_ladder(self) -> None:
        snap = {
            "border_loop_head_entries": 10,
            "border_semantic_decisions": 2,
            "border_implementable_decisions": 2,
            "candidate_swaps": 5,
            "border_multidraw_calls": 0,
            "border_draw_calls_eliminated": 0,
        }
        out = bv.border_roi_engagement_validation(snap)
        self.assertEqual(out["status"], "passed")

    def test_fails_on_mutation(self) -> None:
        snap = {
            "border_loop_head_entries": 10,
            "border_semantic_decisions": 2,
            "border_implementable_decisions": 2,
            "candidate_swaps": 5,
            "border_multidraw_calls": 1,
            "border_draw_calls_eliminated": 0,
        }
        self.assertEqual(bv.border_roi_engagement_validation(snap)["status"], "failed")


if __name__ == "__main__":
    unittest.main()
