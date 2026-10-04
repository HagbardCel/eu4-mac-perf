import importlib.util
import json
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BENCH = ROOT / "benchmark"
SCHEMA = BENCH / "submission_counter_schema.json"
CODEGEN = BENCH / "codegen_submission_counters.py"


class SubmissionCounterSchemaTests(unittest.TestCase):
    def test_codegen_matches_json_byte_for_byte(self):
        raw = json.loads(SCHEMA.read_text(encoding="utf-8"))
        result = subprocess.run(
            [sys.executable, str(CODEGEN)],
            cwd=str(BENCH),
            capture_output=True,
            text=True,
            check=True,
        )
        self.assertEqual(result.returncode, 0, msg=result.stderr)
        expected_h = (BENCH / "submission_counter_schema.h").read_bytes()
        expected_py = (BENCH / "submission_counter_schema.py").read_bytes()
        spec = importlib.util.spec_from_file_location("submission_counter_schema", BENCH / "submission_counter_schema.py")
        mod = importlib.util.module_from_spec(spec)
        assert spec.loader is not None
        spec.loader.exec_module(mod)
        self.assertEqual(len(raw["counters"]), mod.COUNTER_COUNT)
        for row in raw["counters"]:
            self.assertEqual(mod.COUNTER_SLOTS[row["name"]], row["slot"])
        ids = {h["id"] for h in raw["hypotheses"]}
        self.assertEqual(ids, {h["id"] for h in mod.HYPOTHESES})
        self.assertIn(b"EU4_COUNTER_CANDIDATE_SWAPS", expected_h)
        self.assertIn(b"SAFETY_HYPOTHESIS_MASK_DEFAULT = 0x0", expected_py)


if __name__ == "__main__":
    unittest.main()
