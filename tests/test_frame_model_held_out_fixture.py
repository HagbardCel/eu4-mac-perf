import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "benchmark"))
import eu4_benchmark as base  # noqa: E402
import frame_model_workload as workload  # noqa: E402

EXPECTED_HELD_OUT_SHA256 = "dbb0af0e7964777b7ab9f0fecde068692377719bde1670c2a082ecbb26281503"


class HeldOutFixtureReplayTests(unittest.TestCase):
    def _load_projection(self) -> dict:
        path = workload.HELD_OUT_SOURCE_PROJECTION
        self.assertTrue(
            path.is_file(),
            "held-out source projection must be committed",
        )
        return json.loads(path.read_text())

    def test_projection_reproduces_frozen_recipe_via_production_path(self):
        projection = self._load_projection()
        self.assertEqual(projection["schema"], "held-out-source-projection-v1")
        self.assertEqual(projection["selection_rule"], workload.HELD_OUT_SELECTION_RULE_ID)
        self.assertEqual(projection["source_frame"], [1, 6270])
        self.assertEqual(projection["terrain_record_count"], 14)
        self.assertEqual(len(projection["terrain_records"]), 14)
        for row in projection["terrain_records"]:
            self.assertNotIn(
                "return_offset",
                row,
                "projection must use raw trace record fields only",
            )
        self.assertEqual(
            projection["inventory_sha256"],
            base.sha256(workload.INVENTORY),
        )
        sites = {
            row["return_offset"]: row["category"]
            for row in json.loads(workload.INVENTORY.read_text())["direct_sites"]
        }
        payload, meta = workload.build_held_out_fixture_material(
            rows=projection["terrain_records"],
            key=tuple(projection["source_frame"]),
            sites=sites,
        )
        self.assertEqual(meta["draws"], 7)
        self.assertEqual(meta["indices"], 172_032)
        self.assertEqual(meta["sha256"], EXPECTED_HELD_OUT_SHA256)
        self.assertEqual(meta["sha256"], workload.FROZEN_HELD_OUT_RECIPE_SHA256)
        self.assertEqual(payload, workload.HELD_OUT_FIXTURE.read_bytes())


if __name__ == "__main__":
    unittest.main()
