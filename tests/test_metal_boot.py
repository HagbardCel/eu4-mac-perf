import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "benchmark"))
import eu4_metal_boot as probe  # noqa: E402


@unittest.skipUnless(sys.platform == "darwin", "Metal boot patch runs on macOS")
class MetalBootTests(unittest.TestCase):
    def test_callsite_rewrite_and_restore(self):
        probe.run_host("x86_64")


if __name__ == "__main__":
    unittest.main()
