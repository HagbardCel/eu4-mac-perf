import importlib.util
import json
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BENCH = ROOT / "benchmark"
SCHEMA = BENCH / "submission_counter_schema.json"
CODEGEN = BENCH / "codegen_submission_counters.py"


def _load_codegen():
    spec = importlib.util.spec_from_file_location("codegen_submission_counters", CODEGEN)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


class SubmissionCounterSchemaTests(unittest.TestCase):
    def test_codegen_matches_json_byte_for_byte(self):
        raw = json.loads(SCHEMA.read_text(encoding="utf-8"))
        codegen = _load_codegen()
        expected_h = codegen.generate_h(raw)
        expected_py = codegen.generate_py(raw)
        self.assertEqual((BENCH / "submission_counter_schema.h").read_text(encoding="utf-8"), expected_h)
        self.assertEqual((BENCH / "submission_counter_schema.py").read_text(encoding="utf-8"), expected_py)
        spec = importlib.util.spec_from_file_location("submission_counter_schema", BENCH / "submission_counter_schema.py")
        mod = importlib.util.module_from_spec(spec)
        assert spec.loader is not None
        spec.loader.exec_module(mod)
        self.assertEqual(len(raw["counters"]), mod.COUNTER_COUNT)
        for row in raw["counters"]:
            self.assertEqual(mod.COUNTER_SLOTS[row["name"]], row["slot"])
        self.assertIn("SAFETY_HYPOTHESIS_MASK_DEFAULT = 0x0", expected_py)


if __name__ == "__main__":
    unittest.main()
