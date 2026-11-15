"""Fail-closed frame-trace writer and scene-selection regressions.

The native scenario host links the production trace module and uses its
documented TEST_ONLY readback path.  No OpenGL context, GPU or EU4 process is
involved.
"""
from __future__ import annotations

import hashlib
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

    # --- authoritative frame seal -------------------------------------------

    def _seal(self, directory: Path) -> dict:
        return json.loads((directory / "frame-seal.json").read_text())

    def test_seal_notifies_once_with_ordered_operation_accounting(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary) / "capture"
            result = self._run("seal-ok", directory)
            fields = dict(
                item.split("=", 1) for item in result.stdout.split() if "=" in item
            )
            self.assertEqual(fields["callback_calls"], "1")
            self.assertEqual(fields["notifications"], "1")
            self.assertEqual(fields["lock_free"], "1")
            self.assertEqual(fields["files_ready"], "1")
            self.assertEqual(fields["abi_ok"], "1")
            self.assertEqual(fields["ops"], "4")
            self.assertEqual(fields["draws"], "2")
            self.assertEqual(fields["clears"], "1")
            self.assertEqual(fields["copies"], "1")
            self.assertEqual(fields["frame_id"], "1")
            seal = self._seal(directory)
            self.assertTrue(seal["sealed"])
            self.assertEqual(seal["boundary"], "present")
            self.assertEqual(
                [event["kind"] for event in self._events(directory)],
                ["draw", "clear", "draw", "resolve"],
            )
            # The consumer sees the same immutable manifest the trace wrote.
            self.assertEqual(
                seal["manifest_sha256"],
                hashlib.sha256(
                    (directory / "frame-trace.json").read_bytes()
                ).hexdigest(),
            )

    def test_one_shot_mode_never_notifies_a_second_frame(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary) / "capture"
            result = self._run("seal-second-frame", directory)
            self.assertIn("callback_calls=1", result.stdout)
            self.assertIn("notifications=1", result.stdout)
            self.assertIn("frame_id=1", result.stdout)
            self.assertEqual(len(self._events(directory)), 4)
            self.assertTrue(self._seal(directory)["sealed"])

    def test_indexed_draw_path_shares_the_ordered_stream(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary) / "capture"
            result = self._run("seal-indexed", directory)
            fields = dict(
                item.split("=", 1) for item in result.stdout.split() if "=" in item
            )
            self.assertEqual(fields["callback_calls"], "1")
            self.assertEqual(fields["ops"], "5")
            self.assertEqual(fields["draws"], "3")
            self.assertEqual(
                [event["kind"] for event in self._events(directory)],
                ["draw", "draw", "clear", "draw", "resolve"],
            )

    def test_incomplete_frame_publishes_failure_without_notification(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary) / "capture"
            result = self._run("seal-incomplete", directory)
            self.assertIn("callback_calls=0", result.stdout)
            self.assertIn("notifications=0", result.stdout)
            seal = self._seal(directory)
            self.assertFalse(seal["sealed"])
            self.assertEqual(seal["failure_reason"], "draw_cap")
            self.assertFalse(json.loads(
                (directory / "frame-trace.json").read_text()
            )["complete"])

    def test_missing_reference_publishes_failure_without_notification(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary) / "capture"
            result = self._run("seal-no-reference", directory)
            self.assertIn("callback_calls=0", result.stdout)
            seal = self._seal(directory)
            self.assertFalse(seal["sealed"])
            self.assertFalse(seal["readback_ok"])
            self.assertEqual(seal["failure_reason"], "reference_read")

    def test_manifest_digest_failure_is_explicit_and_not_notified(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary) / "capture"
            result = self._run("seal-manifest-hash", directory)
            self.assertIn("callback_calls=0", result.stdout)
            seal = self._seal(directory)
            self.assertFalse(seal["sealed"])
            self.assertEqual(seal["failure_reason"], "manifest_hash_failed")
            manifest = json.loads(
                (directory / "frame-trace.json").read_text()
            )
            self.assertFalse(manifest["complete"])

    def test_invalid_manifest_publishes_failure_without_notification(self):
        for scenario, reason in (
            ("seal-malformed", "malformed_event"),
            ("seal-count-mismatch", "event_count_mismatch"),
        ):
            with self.subTest(scenario=scenario):
                with tempfile.TemporaryDirectory() as temporary:
                    directory = Path(temporary) / "capture"
                    result = self._run(scenario, directory)
                    self.assertIn("callback_calls=0", result.stdout)
                    seal = self._seal(directory)
                    self.assertFalse(seal["sealed"])
                    self.assertFalse(seal["trace_integrity"])
                    self.assertEqual(seal["failure_reason"], reason)

    def test_scene_boundary_seals_without_gfxpresent(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary) / "capture"
            result = self._run("seal-scene", directory)
            self.assertIn("callback_calls=1", result.stdout)
            seal = self._seal(directory)
            self.assertTrue(seal["sealed"])
            self.assertEqual(seal["boundary"], "scene")
            self.assertEqual(seal["scene_index"], 2)
            timeline = [
                json.loads(line)
                for line in (directory / "timeline.jsonl").read_text().splitlines()
                if line
            ]
            self.assertTrue(timeline)
            self.assertFalse(
                any(item["kind"] == "present" for item in timeline)
            )

    def test_missed_scene_entry_is_incomplete_and_never_reads_back(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary) / "capture"
            result = self._run("seal-scene-missed", directory)
            self.assertIn("callback_calls=0", result.stdout)
            seal = self._seal(directory)
            self.assertFalse(seal["sealed"])
            self.assertFalse(seal["readback_ok"])
            self.assertEqual(seal["failure_reason"], "scene_entry_missed")

    def test_reentrant_notification_is_rejected_and_serialized(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary) / "capture"
            result = self._run("seal-reentrant", directory)
            self.assertIn("callback_calls=1", result.stdout)
            self.assertIn("notifications=1", result.stdout)
            self.assertIn("reentrant=1", result.stdout)

    def test_reconfigure_keeps_process_identity_and_one_shot_gate(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary) / "capture"
            result = self._run("seal-reconfigure", directory)
            self.assertIn("callback_calls=1", result.stdout)
            self.assertIn("notifications=1", result.stdout)
            self.assertIn("frame_id=1", result.stdout)
            self.assertEqual(len(self._events(directory)), 4)

    def test_a_late_consumer_receives_the_pending_sealed_frame(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary) / "capture"
            result = self._run("seal-late-consumer", directory)
            self.assertIn("callback_calls=1", result.stdout)
            self.assertIn("notifications=1", result.stdout)
            self.assertTrue(self._seal(directory)["sealed"])

    def test_callback_failure_is_recorded_without_renotification(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary) / "capture"
            result = self._run("seal-callback-fail", directory)
            self.assertIn("callback_calls=1", result.stdout)
            self.assertIn("notifications=1", result.stdout)
            self.assertIn("callback_status=3", result.stdout)
            self.assertTrue(self._seal(directory)["sealed"])

    def test_timeout_is_an_explicit_incomplete_result(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary) / "capture"
            result = self._run("seal-timeout", directory)
            self.assertIn("callback_calls=0", result.stdout)
            self.assertIn("notifications=0", result.stdout)
            self.assertEqual(
                [event["kind"] for event in self._events(directory)], []
            )
            seal = self._seal(directory)
            self.assertEqual(seal["width"], 4)
            self.assertFalse(seal["sealed"])
            self.assertEqual(seal["failure_reason"], "capture_timeout")
            self.assertEqual(seal["gpu_status"], "readback_not_attempted")



if __name__ == "__main__":
    unittest.main()
