#!/usr/bin/env python3
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "benchmark"))

import eu4_gfx_startup_diff as diff  # noqa: E402
import eu4_gfx_shadow_worker as shadow  # noqa: E402


@unittest.skipUnless(sys.platform == "darwin", "Metal shadow worker runs on macOS")
class StartupDiffMetalTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        shadow.build()

    def test_replay_committed_reference(self):
        reference = json.loads(diff.REFERENCE_PATH.read_text())
        self.assertEqual(reference["init"]["input"]["source"], 0)
        with tempfile.TemporaryDirectory() as tmp:
            out_dir = Path(tmp) / "diff"
            commands, responses, stderr = diff.replay(reference)
            by_op = {}
            for cmd, resp in zip(commands, responses):
                by_op[cmd["op"]] = resp
            comparison = diff.compare(reference, by_op)
            diff.write_run_dir(out_dir, reference, commands, responses, comparison, stderr)
            self.assertTrue((out_dir / "comparison.json").is_file())
            self.assertTrue((out_dir / "report.md").is_file())
            self.assertTrue(comparison["metal_init_ok"], stderr)
            self.assertTrue(comparison["shutdown_ok"], stderr)
            self.assertTrue(comparison["contract_pass"], json.dumps(comparison["counts"]))
            init = by_op["init"]
            self.assertEqual(init["state"]["source"], 1)
            self.assertEqual(init["state"]["width"], reference["init"]["output"]["width"])
            self.assertEqual(init["state"]["height"], reference["init"]["output"]["height"])
            by_field = {row["field"]: row for row in comparison["fields"]}
            self.assertEqual(by_field["settings.return"]["classification"], "abi_unresolved")
            self.assertEqual(
                by_field["settings.msaa_algorithm"]["classification"], "matched"
            )


if __name__ == "__main__":
    unittest.main()
