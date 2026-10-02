import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "benchmark"))
import eu4_benchmark as base  # noqa: E402
import frame_model_workload as workload  # noqa: E402


class HeldOutFixtureReplayTests(unittest.TestCase):
    def test_projection_reproduces_frozen_recipe_sha256(self):
        if not workload.HELD_OUT_SOURCE_PROJECTION.is_file():
            self.skipTest("held-out source projection not committed")
        payload, meta = workload.build_held_out_fixture_material_from_projection()
        self.assertEqual(meta["sha256"], workload.FROZEN_HELD_OUT_RECIPE_SHA256)
        self.assertEqual(payload, workload.HELD_OUT_FIXTURE.read_bytes())

    def test_exploratory_sha_is_rejected(self):
        contaminated = "284365e71435d8b6744440c5f5a028803527f5f1848f56365b85b5957c579188"
        with self.assertRaises(base.BenchmarkError):
            workload._reject_contaminated_fixture_sha(contaminated)


if __name__ == "__main__":
    unittest.main()
