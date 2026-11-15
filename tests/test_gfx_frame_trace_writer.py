"""Fail-closed frame-trace writer and scene-selection regressions.

The native scenario host links the production trace module and uses its
documented TEST_ONLY readback path.  No OpenGL context, GPU or EU4 process is
involved.
"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BENCH = ROOT / "benchmark"


@unittest.skipUnless(sys.platform == "darwin", "native trace host is macOS")
class FrameTraceWriterTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        cls.binary = Path(cls.temporary.name) / "gfx_frame_trace_scenarios"
        built = subprocess.run(
            [
                "clang",
                "-O1",
                "-Wall",
                "-Wextra",
                "-I",
                str(BENCH),
                "-o",
                str(cls.binary),
                str(ROOT / "tests" / "gfx_frame_trace_scenarios.c"),
                str(BENCH / "eu4_gfx_frame_trace.c"),
                "-framework",
                "OpenGL",
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        if built.returncode:
            raise unittest.SkipTest(built.stderr.strip() or "trace host build failed")

    @classmethod
    def tearDownClass(cls):
        cls.temporary.cleanup()

    def _run(self, scenario: str, directory: Path):
        command = [str(self.binary), scenario, str(directory)]
        result = subprocess.run(command, capture_output=True, text=True, check=False)
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)
        return result

    def _manifest(self, directory: Path) -> dict:
        return json.loads((directory / "frame-trace.json").read_text())

    def _events(self, directory: Path) -> list[dict]:
        path = directory / "frame-events.jsonl"
        return [json.loads(line) for line in path.read_text().splitlines() if line]

    def test_valid_capture_with_draws_and_copies_is_complete(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary) / "capture"
            self._run("copy-valid", directory)
            manifest = self._manifest(directory)
            events = self._events(directory)
            self.assertTrue(manifest["complete"], manifest)
            self.assertTrue(manifest["stream_validated"])
            self.assertFalse(manifest["truncated"])
            self.assertEqual(manifest["events"], len(events))
            self.assertEqual([event["kind"] for event in events],
                             ["draw", "resolve", "resolve"])
            copy = events[1]
            self.assertEqual(copy["operation"], "glCopyTexSubImage2D")
            self.assertEqual(copy["read_buffer"], 0x0405)
            self.assertTrue(copy["source_resolved"])
            self.assertTrue(copy["destination_resolved"])
            self.assertEqual(copy["source_rect"], [1, 1, 2, 2])
            self.assertEqual(copy["destination_offset"], [1, 1])

    def test_copy_exceeding_capacity_invalidates_frame(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary) / "capture"
            self._run("copy-capacity", directory)
            manifest = self._manifest(directory)
            self.assertFalse(manifest["complete"], manifest)
            self.assertTrue(manifest["truncated"])
            self.assertEqual(manifest["reason"], "draw_cap")
            self.assertEqual(manifest["events"], 1)

    def test_event_stream_flush_failure_invalidates_frame(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary) / "capture"
            self._run("flush-fail", directory)
            manifest = self._manifest(directory)
            self.assertFalse(manifest["complete"], manifest)
            self.assertEqual(manifest["reason"], "event_flush_failed")
            self.assertFalse(manifest["stream_validated"])

    def test_malformed_event_invalidates_frame(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary) / "capture"
            self._run("malformed", directory)
            manifest = self._manifest(directory)
            self.assertFalse(manifest["complete"], manifest)
            self.assertEqual(manifest["reason"], "malformed_event")

    def test_event_count_mismatch_invalidates_frame(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary) / "capture"
            self._run("count-mismatch", directory)
            manifest = self._manifest(directory)
            self.assertFalse(manifest["complete"], manifest)
            self.assertEqual(manifest["reason"], "event_count_mismatch")
            self.assertEqual(manifest["events"], 1)

    def test_scene_occurrence_selects_the_requested_scene_boundaries(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary) / "capture"
            self._run("scene-sequence", directory)
            manifest = self._manifest(directory)
            events = self._events(directory)
            self.assertTrue(manifest["complete"], manifest)
            self.assertEqual(manifest["reason"], "scene_present")
            # Scene 5 is the second matching scene; capture starts at the
            # scene-6 boundary and ends at its corresponding next boundary.
            self.assertEqual(manifest["present_index"], 6)
            self.assertEqual(manifest["selection"]["occurrence"], 2)
            self.assertEqual(manifest["selection"]["boundary"], "scene")
            self.assertEqual(len(events), 3)
            self.assertTrue(all(event["draw"]["caller_rva"] == "0x1000"
                                for event in events))
            timeline = [
                json.loads(line)
                for line in (directory / "timeline.jsonl").read_text().splitlines()
                if line
            ]
            scene_exits = [item for item in timeline
                           if item["kind"] == "scene_exit"]
            selected = [item["scene"] for item in scene_exits
                        if item["selected"]]
            self.assertEqual(selected, [5])
            self.assertEqual([item["scene"] for item in scene_exits
                              if item["enough"]], [3, 5])

    def test_scene_absolute_frame_selection_is_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary) / "capture"
            result = self._run("scene-at-frame", directory)
            self.assertIn("configure_rejected=-1", result.stdout)


if __name__ == "__main__":
    unittest.main()
