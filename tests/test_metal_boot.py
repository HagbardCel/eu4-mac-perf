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
        code, signaled = probe.classify_diagnostic_exit(True, 2)
        self.assertEqual((code, signaled), (2, False))
        code, signaled = probe.classify_diagnostic_exit(False, None)
        self.assertEqual(signaled, True)
        live = Path(__file__).resolve().parents[1] / "results/20261101T220023Z-metal-init"
        init = probe.session_event(live / "init.json", 0, legacy=True)
        stop = probe.session_event(live / "stop.json", 0, legacy=True)
        self.assertTrue((live / "init.json").is_file())
        self.assertTrue((live / "stop.json").is_file())
        self.assertIsNotNone(init)
        self.assertIsNotNone(stop)
        recorded = probe.metal_init_outcome(
            init.get("success") == 1, stop.get("phase"), "stop", 2,
            init.get("thread"), stop.get("thread"), False,
            stop.get("reason"), stop.get("image"), stop.get("rva"),
        )
        self.assertEqual(recorded["status"], "complete")
        self.assertEqual(recorded["live_gfx_init"], "returned")
        self.assertFalse(recorded["raises"])
        image = stop.get("image")
        complete = probe.metal_init_outcome(
            True, "after_init", "stop", 2, 7, 7, False, "gl", image, "0x15e9e20"
        )
        self.assertEqual(complete["status"], "complete")
        self.assertEqual(complete["live_gfx_init"], "returned")
        self.assertFalse(complete["raises"])
        for reason in ("unsupported", "reentry", "unresolved_forward"):
            rejected = probe.metal_init_outcome(
                True, "after_init", "stop", 2, 7, 7, False, reason, image, "0x15e9e20"
            )
            self.assertEqual(rejected["status"], "post_init_failure")
            self.assertTrue(rejected["raises"])
        inside = probe.metal_init_outcome(
            True, "after_init", "stop", 2, 7, 7, False, "gl", image, "0x15e9d11"
        )
        self.assertEqual(inside["status"], "post_init_failure")
        for rva in ("0x15ea480", "0x15ea49b", "0x15ea4c0", "0x15ea4f2"):
            replaced = probe.metal_init_outcome(
                True, "after_init", "stop", 2, 7, 7, False, "gl", image, rva
            )
            self.assertEqual(replaced["status"], "post_init_failure")
            self.assertTrue(replaced["raises"])
        for bad in (None, "nope", -1, 0):
            malformed = probe.metal_init_outcome(
                True, "after_init", "stop", 2, 7, 7, False, "gl", image, bad
            )
            self.assertEqual(malformed["status"], "post_init_failure")
            self.assertTrue(malformed["raises"])
        other_thread = probe.metal_init_outcome(True, "after_init", "stop", 2, 7, 8, False)
        self.assertTrue(other_thread["raises"])
        self.assertEqual(other_thread["live_gfx_init"], "postconditions")
        signaled_stop = probe.metal_init_outcome(True, "after_init", "stop", -15, 7, 7, True)
        self.assertTrue(signaled_stop["raises"])
        crash = probe.metal_init_outcome(True, None, "exit", -11, 7, None, False)
        self.assertEqual(crash["status"], "post_init_failure")
        self.assertTrue(crash["raises"])
        clean = probe.metal_init_outcome(True, None, "exit", 0, 7, None, False)
        self.assertEqual(clean["status"], "clean_exit")
        self.assertFalse(clean["raises"])
        deadline = probe.metal_init_outcome(True, None, "deadline", -15, 7, None, True)
        self.assertEqual(deadline["status"], "deadline")
        self.assertEqual(deadline["live_gfx_init"], "postconditions")
        self.assertFalse(deadline["raises"])
        directory = Path(__file__).resolve().parent / ".." / "benchmark" / ".build" / "event-test"
        directory.mkdir(parents=True, exist_ok=True)
        truncated = directory / "init.json"
        truncated.write_text("{")
        self.assertIsNone(probe.read_event(truncated))
        partial = directory / "init.json.partial"
        partial.write_text('{"success": 1}\n')
        self.assertIsNone(probe.read_event(partial))

    def test_session_event_requires_launched_pid(self):
        directory = Path(__file__).resolve().parents[1] / "benchmark" / ".build" / "pid-test"
        if directory.exists():
            for child in directory.iterdir():
                child.unlink()
        directory.mkdir(parents=True, exist_ok=True)
        for name in ("init.json", "stop.json"):
            path = directory / name
            path.write_text('{"pid": 7}\n')
            self.assertEqual(probe.session_event(path, 7)["pid"], 7)
            self.assertTrue(path.is_file())
            path.write_text('{"pid": 8}\n')
            self.assertIsNone(probe.session_event(path, 7))
            self.assertFalse(path.exists())
            path.write_text('{"phase": "after_init"}\n')
            self.assertIsNone(probe.session_event(path, 7))
            self.assertFalse(path.exists())
            self.assertIsNotNone(probe.session_event(directory / f"{name}.untrusted-1", 7, legacy=True))
            path.write_text('{"pid": "7"}\n')
            self.assertIsNone(probe.session_event(path, 7))
            path.write_text('{"pid": true}\n')
            self.assertIsNone(probe.session_event(path, 1))
        legacy = directory / "legacy.json"
        legacy.write_text('{"success": 1}\n')
        self.assertEqual(probe.session_event(legacy, 7, legacy=True)["success"], 1)
        self.assertTrue(legacy.is_file())
        wrong = directory / "wrong.json"
        wrong.write_text('{"pid": 9}\n')
        self.assertIsNone(probe.session_event(wrong, 7, legacy=True))
        self.assertFalse(wrong.exists())

    def test_metal_init_excludes_bootstrap(self):
        with contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit):
                probe.main(["--bootstrap", "--metal-init", "unused"])


if __name__ == "__main__":
    unittest.main()
