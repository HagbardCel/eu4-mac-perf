import contextlib
import io
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

    def test_metal_init_waits_for_stop_not_init(self):
        self.assertIsNone(probe.metal_init_should_stop(True, False, False, False))
        self.assertEqual(probe.metal_init_should_stop(True, True, False, False), "stop")
        self.assertEqual(probe.metal_init_should_stop(False, False, True, False), "exit")
        self.assertEqual(probe.metal_init_should_stop(True, False, False, True), "deadline")
        directory = Path(__file__).resolve().parent / ".." / "benchmark" / ".build" / "event-test"
        directory.mkdir(parents=True, exist_ok=True)
        truncated = directory / "init.json"
        truncated.write_text("{")
        self.assertIsNone(probe.read_event(truncated))
        partial = directory / "init.json.partial"
        partial.write_text('{"success": 1}\n')
        self.assertIsNone(probe.read_event(partial))

    def test_metal_init_excludes_bootstrap(self):
        with contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit):
                probe.main(["--bootstrap", "--metal-init", "unused"])


if __name__ == "__main__":
    unittest.main()
