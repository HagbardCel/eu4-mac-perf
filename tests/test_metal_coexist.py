import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "benchmark"))
import eu4_metal_coexist as probe  # noqa: E402


@unittest.skipUnless(sys.platform == "darwin", "Metal probe runs on macOS")
class MetalCoexistTests(unittest.TestCase):
    def test_offscreen_clear_readback_matches(self):
        library = probe.build_library("x86_64")
        with tempfile.TemporaryDirectory() as temporary:
            receipt_path = Path(temporary) / "receipt.json"
            receipt = probe.run_probe(library, receipt_path, "x86_64")
        self.assertEqual(receipt["gpu_coexistence"], "offscreen_coexistence_verified")
        self.assertTrue(receipt["readback_matches"])
        self.assertTrue(receipt["metal_command_completed"])
        self.assertEqual(receipt["window_drawable"], "window_drawable_not_tested")
        self.assertEqual(receipt["format"], "BGRA8Unorm")
        self.assertEqual(receipt["clear_rgba"], [1, 0, 0, 1])
        json.dumps(receipt)


if __name__ == "__main__":
    unittest.main()
