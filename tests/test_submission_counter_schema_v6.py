#!/usr/bin/env python3
import json
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

DOMAIN_CENSUS_NAMES = (
    "border_mode0_entries",
    "border_mode1_entries",
    "border_mode_other_entries",
    "border_mode0_visible_entries",
    "border_mode1_visible_entries",
    "border_mode_other_visible_entries",
    "border_mode0_visible_nonzero_triangle_entries",
    "border_mode1_visible_nonzero_triangle_entries",
    "border_mode_other_visible_nonzero_triangle_entries",
)


class SubmissionCounterSchemaV6Tests(unittest.TestCase):
    def test_schema_v6_domain_census_slots(self) -> None:
        schema = json.loads((ROOT / "benchmark" / "submission_counter_schema.json").read_text())
        self.assertEqual(schema["schema_version"], 6)
        names = {c["name"]: c for c in schema["counters"]}
        for name in DOMAIN_CENSUS_NAMES:
            self.assertIn(name, names)
            row = names[name]
            self.assertEqual(row["bank"], "border")
            self.assertTrue(row.get("requires_armed"))
        self.assertEqual(len(schema["counters"]), 80)


if __name__ == "__main__":
    unittest.main()
