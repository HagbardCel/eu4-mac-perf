#!/usr/bin/env python3
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "benchmark"))
import submission_border_validation as bv


class BorderRoiValidityTests(unittest.TestCase):
    def test_validity_passes_clean_snapshot(self) -> None:
        snap = {
            "border_loop_decode_failures": 0,
            "border_roi_epoch_invalid": 0,
            "border_thread_mismatch_count": 0,
            "border_roi_freeze_partial_walk": 0,
            "border_roi_freeze_semantic_suppress": 0,
            "border_roi_freeze_implementable_suppress": 0,
        }
        self.assertEqual(bv.border_roi_validity_gate(snap)["status"], "passed")

    def test_consistency_ordering(self) -> None:
        snap = {
            "border_implementable_eliminations": 10,
            "border_semantic_eliminations": 12,
            "border_structural_eliminations": 15,
            "border_semantic_decisions": 2,
            "border_implementable_decisions": 2,
            "border_semantic_evaluations": 2,
            "border_implementable_evaluations": 2,
        }
        for k in bv.SEMANTIC_RUN_LEN_HISTOGRAM_KEYS:
            snap[k] = 0
        for k in bv.IMPLEMENTABLE_RUN_LEN_HISTOGRAM_KEYS:
            snap[k] = 0
        snap["border_semantic_run_len_2"] = 1
        snap["border_semantic_run_len_3_4"] = 1
        snap["border_implementable_run_len_2"] = 1
        snap["border_implementable_run_len_3_4"] = 1
        self.assertEqual(bv.border_roi_consistency_gate(snap)["status"], "passed")


if __name__ == "__main__":
    unittest.main()
