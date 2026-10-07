#!/usr/bin/env python3
import json
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class SubmissionCounterSchemaV5Tests(unittest.TestCase):
    def test_border_roi_slots_require_armed(self) -> None:
        schema = json.loads((ROOT / "benchmark" / "submission_counter_schema.json").read_text())
        self.assertEqual(schema["schema_version"], 6)
        for row in schema["counters"]:
            if row.get("bank") == "border":
                self.assertTrue(row.get("requires_armed"), msg=row["name"])
        names = {c["name"] for c in schema["counters"]}
        self.assertIn("border_loop_head_entries", names)
        self.assertIn("border_roi_nonzero_suppression_at_frame_boundary", names)
        self.assertIn("border_semantic_evaluations", names)
        self.assertIn("border_roi_freeze_partial_walk", names)
        self.assertIn("border_loop_decode_failures", names)


if __name__ == "__main__":
    unittest.main()
