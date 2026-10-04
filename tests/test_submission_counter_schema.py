import json
import unittest
from pathlib import Path

from submission_counter_schema import COUNTER_COUNT, COUNTER_SLOTS, HYPOTHESES


ROOT = Path(__file__).resolve().parents[1]
SCHEMA = ROOT / "benchmark" / "submission_counter_schema.json"


class SubmissionCounterSchemaTests(unittest.TestCase):
    def test_codegen_matches_json(self):
        raw = json.loads(SCHEMA.read_text(encoding="utf-8"))
        self.assertEqual(len(raw["counters"]), COUNTER_COUNT)
        for row in raw["counters"]:
            self.assertEqual(COUNTER_SLOTS[row["name"]], row["slot"])
        ids = {h["id"] for h in raw["hypotheses"]}
        self.assertEqual(ids, {h["id"] for h in HYPOTHESES})


if __name__ == "__main__":
    unittest.main()
