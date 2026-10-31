import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "benchmark"))
import eu4_metal_boot as probe  # noqa: E402


@unittest.skipUnless(sys.platform == "darwin", "Metal boot patch runs on macOS")
class MetalBootTests(unittest.TestCase):
    def test_callsite_rewrite_and_restore(self):
        probe.build_library("x86_64")
        probe.run_host("x86_64")

    def test_mode_overrides_inherited_bootstrap(self):
        env = probe.probe_environ({"EU4_METAL_BOOT_MODE": "bootstrap"}, False)
        self.assertEqual(env["EU4_METAL_BOOT_MODE"], "a1")
        env = probe.probe_environ({"EU4_METAL_BOOT_MODE": "nope"}, True)
        self.assertEqual(env["EU4_METAL_BOOT_MODE"], "bootstrap")


if __name__ == "__main__":
    unittest.main()
