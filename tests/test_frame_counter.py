import sys
import tempfile
import unittest
import argparse
import json
from pathlib import Path
from unittest.mock import patch


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "benchmark"))
import eu4_frame_counter as counter  # noqa: E402


class FrameCounterTests(unittest.TestCase):
    def test_quick_confirmation_waits_for_first_complete_bucket(self):
        with tempfile.TemporaryDirectory() as temporary:
            log = Path(temporary) / "swaps.csv"
            log.write_text("interval_ns,swap_calls\n")

            class FakeGame:
                def poll(self):
                    return None

            def finish_bucket(_seconds):
                with log.open("a") as output:
                    output.write("1000000000,80\n")

            with patch.object(counter.time, "sleep", side_effect=finish_bucket):
                counter.wait_for_first_bucket(log, FakeGame())

    def test_uses_only_complete_buckets_after_capture_start(self):
        with tempfile.TemporaryDirectory() as temporary:
            log = Path(temporary) / "swaps.csv"
            log.write_text("interval_ns,swap_calls\n1000000000,40\n")
            offset = log.stat().st_size
            with log.open("a") as output:
                output.write("1000000000,10\n1000000000,120\n500000000,60\n")
            rows = counter.new_rows(log, offset)
            self.assertEqual([row["swaps_per_second"] for row in rows], [120, 120])

    def test_multithreaded_status_requires_successful_opt_in(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "status.csv"
            path.write_text("query_before,before,enable_error,query_after,after\n0,0,0,0,1\n")
            self.assertEqual(counter.multithreaded_status(path)[0]["after"], 1)
            path.write_text("query_before,before,enable_error,query_after,after\n0,0,10017,0,0\n")
            with self.assertRaisesRegex(counter.base.BenchmarkError, "not enabled"):
                counter.multithreaded_status(path)

    def test_guarded_direct_launch_records_only_paused_fullscreen_window(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            game = root / "eu4.app/Contents/MacOS/eu4"
            game.parent.mkdir(parents=True)
            game.write_bytes(b"gog binary")
            save = root / "Venice.eu4"
            save.write_bytes(b"disposable save")
            launcher = root / "launcher-settings.json"
            launcher.write_text(json.dumps({"exePath": "./eu4.app/Contents/MacOS/eu4",
                                            "exeArgs": ["-enabletelemetry", "-gdpr-compliant"]}))
            calls = []
            sleep_calls = []

            class FakeGame:
                pid = 42

                def poll(self):
                    return None

            def fake_popen(argv, **kwargs):
                calls.append(argv)
                Path(kwargs["env"]["EU4_FRAME_LOG"]).write_text(
                    "interval_ns,swap_calls\n1000000000,80\n")
                Path(kwargs["env"]["EU4_GL_STATUS_LOG"]).write_text(
                    "query_before,before,enable_error,query_after,after\n0,0,0,0,1\n")
                return FakeGame()

            def fake_sleep(_seconds):
                sleep_calls.append(_seconds)
                if len(sleep_calls) == 2:
                    log = next((root / "results").glob("*/frame-swaps.csv"))
                    with log.open("a") as output:
                        output.write("1000000000,90\n1000000000,120\n"
                                     "1000000000,120\n1000000000,120\n")

            args = argparse.Namespace(seconds=5, lead_in=0, output=str(root / "results"),
                                      multithreaded_gl=True)
            with patch.object(counter.base, "GOG_EXE", game), \
                 patch.object(counter.base, "GOG_LAUNCHER", launcher), \
                 patch.object(counter.base, "DEFAULT_SAVE", save), \
                 patch.object(counter.base, "gog_identity", return_value={"executable_sha256": "gog"}), \
                 patch.object(counter.base, "running_game_pid", side_effect=lambda scenario: None if scenario == "idle" else 42), \
                 patch.object(counter.base, "command", return_value="x86_64\n"), \
                 patch.object(counter.base, "display_mode", return_value={"verified": True, "refresh_hz": 120}), \
                 patch.object(counter.base, "game_settings", return_value={"fullScreen": "yes", "borderless": "no", "game_resolution": "3456x2234"}), \
                 patch.object(counter.base, "power_state", return_value={"mode": "normal"}), \
                 patch.object(counter.base, "mod_settings", return_value={}), \
                 patch.object(counter.base, "prompt", side_effect=["yes", ""]), \
                 patch.object(counter.base, "cue"), \
                 patch.object(counter, "build_library"), \
                 patch.object(counter.subprocess, "Popen", side_effect=fake_popen), \
                 patch.object(counter.time, "sleep", side_effect=fake_sleep):
                counter.measure(args)

            metadata = json.loads(next((root / "results").glob("*/metadata.json")).read_text())
            self.assertEqual(metadata["status"], "complete")
            self.assertEqual(metadata["median_swaps_per_second"], 120)
            self.assertTrue(metadata["multithreaded_gl_requested"])
            self.assertEqual(metadata["multithreaded_gl_contexts"][0]["after"], 1)
            self.assertEqual(calls[0], ["./eu4", "-enabletelemetry", "-gdpr-compliant"])


if __name__ == "__main__":
    unittest.main()
