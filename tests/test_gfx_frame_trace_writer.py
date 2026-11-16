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
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from automation import gfx_frame_graph  # noqa: E402
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

    def _run(self, scenario: str, directory: Path, *args):
        command = [str(self.binary), scenario, str(directory), *map(str, args)]
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

    def test_repeated_delete_lifecycle_keeps_both_transitions(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary) / "campaign"
            self._run("continuous-reuse", directory)
            sequence = json.loads(
                (directory / "frame-000001" / "frame-sequence.json").read_text())
            deletes = [entry for entry in sequence["lifecycle"]
                       if entry["kind"] == "delete"]
            self.assertEqual([entry["event"] for entry in deletes], [2, 4])
            self.assertEqual([entry["names"] for entry in deletes],
                             [["443"], ["443"]])
            self.assertEqual(sequence["carried_renderer_outputs"], [])

    def test_renderer_owned_mutation_is_recorded_for_fail_closed_replay(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary) / "campaign"
            self._run("mutation-target", directory)
            sequence = json.loads(
                (directory / "frame-000001" / "frame-sequence.json").read_text())
            mutations = [entry for entry in sequence["lifecycle"]
                         if entry["kind"] == "mutation"]
            self.assertEqual(len(mutations), 1, sequence["lifecycle"])
            self.assertEqual(mutations[0]["names"], ["443"])
            self.assertEqual(mutations[0]["target"], "0xde1")
            self.assertEqual(mutations[0]["operation"], "glTexSubImage2D")

    def test_cube_face_reallocation_uses_the_cube_identity(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary) / "campaign"
            self._run("mutation-cube", directory)
            sequence = json.loads(
                (directory / "frame-000001" / "frame-sequence.json").read_text())
            mutations = [entry for entry in sequence["lifecycle"]
                         if entry["kind"] == "mutation"]
            self.assertEqual(len(mutations), 1, sequence["lifecycle"])
            self.assertEqual(mutations[0]["target"], "0x8513")
            self.assertEqual(mutations[0]["operation"], "glTexImage2D")

    def _lifecycle(self, scenario: str, mode: str) -> list[dict]:
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary) / "campaign"
            self._run(scenario, directory, mode)
            sequence = json.loads(
                (directory / "frame-000001" / "frame-sequence.json").read_text())
            return sequence["lifecycle"]

    def test_delete_recreate_delete_keeps_intra_event_order(self):
        lifecycle = self._lifecycle("lifecycle-order", "delete-mutate-delete")
        self.assertEqual([item["kind"] for item in lifecycle],
                         ["delete", "mutation", "delete"], lifecycle)
        self.assertEqual([item["event"] for item in lifecycle], [1, 1, 1])
        sequences = [item["lifecycle_sequence"] for item in lifecycle]
        self.assertEqual(sequences, sorted(sequences))
        self.assertEqual(len(set(sequences)), len(sequences), lifecycle)
        # The two deletions must remain distinct transitions, not one folded
        # record, even though they share the captured event index.
        self.assertEqual([item["names"] for item in lifecycle],
                         [["443"], ["443"], ["443"]])

    def test_mutation_and_delete_at_one_event_keep_call_order(self):
        for mode, expected in (
                ("mutate-delete", ["mutation", "delete"]),
                ("delete-mutate", ["delete", "mutation"])):
            with self.subTest(mode=mode):
                lifecycle = self._lifecycle("lifecycle-order", mode)
                self.assertEqual([item["kind"] for item in lifecycle],
                                 expected, lifecycle)
                self.assertEqual({item["event"] for item in lifecycle}, {1})
                sequences = [item["lifecycle_sequence"] for item in lifecycle]
                self.assertEqual(sequences, sorted(sequences))
                self.assertEqual(len(set(sequences)), len(sequences))

    def test_two_independent_delete_calls_at_one_event_stay_distinct(self):
        lifecycle = self._lifecycle("lifecycle-order", "two-calls")
        self.assertEqual(len(lifecycle), 2, lifecycle)
        self.assertEqual([item["kind"] for item in lifecycle],
                         ["delete", "delete"])
        self.assertEqual([item["names"] for item in lifecycle],
                         [["443"], ["443"]])
        sequences = [item["lifecycle_sequence"] for item in lifecycle]
        self.assertEqual(len(set(sequences)), 2, lifecycle)

    def test_duplicate_names_in_one_delete_call_are_one_transition(self):
        lifecycle = self._lifecycle("lifecycle-order", "duplicate-call")
        self.assertEqual(len(lifecycle), 1, lifecycle)
        self.assertEqual(lifecycle[0]["kind"], "delete")
        self.assertEqual(lifecycle[0]["names"], ["443"], lifecycle)

    def test_capacity_exact_and_one_beyond(self):
        expected = {
            "targets": ("capacity_limit", "render_targets(64)"),
            "deletes": ("capacity_limit", "delete_records"),
            "mutations": ("capacity_limit", "mutation_records"),
        }
        for kind in expected:
            for count, complete in ((64, True), (65, False)):
                with self.subTest(kind=kind, count=count), \
                        tempfile.TemporaryDirectory() as temporary:
                    directory = Path(temporary) / "campaign"
                    self._run(f"capacity-{kind}", directory, count)
                    manifest = self._manifest(directory / "frame-000001")
                    self.assertEqual(manifest["complete"], complete, manifest)
                    self.assertEqual(manifest["capacity_exceeded"], not complete)
                    if complete:
                        sequence = json.loads(
                            (directory / "frame-000001" /
                             "frame-sequence.json").read_text())
                        if kind == "deletes":
                            names = [name for entry in sequence["lifecycle"]
                                     for name in entry["names"]]
                            self.assertEqual(len(names), 64)
                            self.assertEqual(len(set(names)), 64)
                        if kind == "mutations":
                            mutations = [entry for entry in sequence["lifecycle"]
                                         if entry["kind"] == "mutation"]
                            self.assertEqual(len(mutations), 64)
                    else:
                        self.assertEqual(manifest["reason"], "resource_limit")
                        campaign = json.loads(
                            (directory / "sequence.json").read_text())
                        self.assertEqual(campaign["status"], "incomplete")
                        self.assertEqual(campaign["reason"], "resource_limit")

    def test_carried_capacity_fails_the_campaign_closed(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary) / "campaign"
            self._run("capacity-carried", directory)
            third = self._manifest(directory / "frame-000003")
            self.assertFalse(third["complete"], third)
            self.assertEqual(third["reason"], "resource_limit")
            self.assertTrue(third["capacity_exceeded"], third)
            campaign = json.loads((directory / "sequence.json").read_text())
            self.assertEqual(campaign["status"], "incomplete")
            self.assertEqual(campaign["reason"], "resource_limit")
            self.assertEqual(campaign["frames_completed"], 2)
            graph = gfx_frame_graph.build(directory / "frame-000003")
            self.assertEqual(graph["status"], "invalid", graph.get("reasons"))

    def test_campaign_record_capacity_holds_exactly_64_frames(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary) / "campaign"
            self._run("continuous", directory, 64, 64 * 1024 * 1024)
            campaign = json.loads((directory / "sequence.json").read_text())
            self.assertEqual(campaign["status"], "complete")
            self.assertEqual(campaign["frames_completed"], 64)
            self.assertEqual(len(campaign["frames"]), 64)

    def test_continuous_capture_publishes_ordered_sealed_frames(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary) / "campaign"
            self._run("continuous", directory, 3, 64 * 1024 * 1024)
            campaign = json.loads((directory / "sequence.json").read_text())
            self.assertEqual(campaign["schema"],
                             "gfx-frame-sequence-campaign/v1")
            self.assertEqual(campaign["status"], "complete")
            self.assertEqual(campaign["reason"], "frame_limit")
            self.assertEqual(campaign["frames_completed"], 3)
            self.assertEqual(len(campaign["frames"]), 3)
            self.assertGreater(campaign["bytes_written"], 0)
            previous = None
            lifecycle_seen = []
            for sequence in range(1, 4):
                path = directory / f"frame-{sequence:06d}"
                manifest = self._manifest(path)
                self.assertTrue(manifest["complete"], manifest)
                seal = json.loads((path / "frame-seal.json").read_text())
                self.assertTrue(seal["sealed"])
                self.assertEqual(seal["capture_frame_id"], sequence)
                sequence_record = json.loads(
                    (path / "frame-sequence.json").read_text())
                self.assertEqual(sequence_record["schema"],
                                 "gfx-frame-sequence/v1")
                self.assertEqual(sequence_record["sequence_index"], sequence)
                self.assertEqual(sequence_record["campaign_frames"], 3)
                self.assertEqual(
                    sequence_record["event_stream_sha256"],
                    hashlib.sha256(
                        (path / "frame-events.jsonl").read_bytes()
                    ).hexdigest())
                if previous is None:
                    self.assertIsNone(sequence_record["previous"])
                else:
                    self.assertEqual(
                        sequence_record["previous"]["capture_frame_id"],
                        previous)
                    names = [
                        name
                        for item in sequence_record["lifecycle"]
                        if item.get("kind") == "delete"
                        for name in item.get("names") or []
                    ]
                    lifecycle_seen.extend(names)
                previous = sequence
            self.assertIn("443", lifecycle_seen)
            self.assertFalse((directory / "frame-000004").exists())

    def _sequence(self, directory: Path, index: int) -> dict:
        return json.loads(
            (directory / f"frame-{index:06d}" / "frame-sequence.json").read_text())

    def test_carried_set_persists_across_an_unwritten_unused_frame(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary) / "campaign"
            self._run("continuous-carried", directory, 3, 64 * 1024 * 1024)
            first = self._sequence(directory, 1)
            second = self._sequence(directory, 2)
            third = self._sequence(directory, 3)
            self.assertIn("443", first["render_targets"])
            self.assertEqual(first["carried_renderer_outputs"], [])
            # Frame 2 neither writes nor samples the target.
            self.assertEqual(second["render_targets"], [])
            self.assertIn("443", second["carried_renderer_outputs"])
            # Frame 3 still sees the frame-1 resource; replacing the set with
            # the preceding frame's writes would have dropped it here.
            self.assertEqual(third["render_targets"], [])
            self.assertIn("443", third["carried_renderer_outputs"])

    def test_in_frame_delete_keeps_the_frame_start_carried_snapshot(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary) / "campaign"
            self._run("continuous-carried-delete", directory, 3,
                      64 * 1024 * 1024)
            third = self._sequence(directory, 3)
            # 443 was carried into this frame and deleted at event 1.  A
            # sample before that event may still use it, so the frame-start
            # snapshot must advertise it alongside the lifecycle delete.
            self.assertIn("443", third["carried_renderer_outputs"])
            deletes = [item for item in third["lifecycle"]
                       if item.get("kind") == "delete"]
            self.assertEqual(len(deletes), 1, third["lifecycle"])
            self.assertEqual(deletes[0]["event"], 1)
            self.assertIn("443", deletes[0]["names"])

    def test_delete_recreate_publishes_lifecycle_and_recarries_the_name(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary) / "campaign"
            self._run("continuous-carried-lifecycle", directory, 3,
                      64 * 1024 * 1024)
            first = self._sequence(directory, 1)
            second = self._sequence(directory, 2)
            third = self._sequence(directory, 3)
            self.assertIn("443", first["render_targets"])
            self.assertIn("443", second["render_targets"])
            deletes = [item for item in second["lifecycle"]
                       if item.get("kind") == "delete"]
            self.assertEqual(len(deletes), 1, second["lifecycle"])
            self.assertIn("443", deletes[0]["names"])
            self.assertIsInstance(deletes[0]["event"], int)
            # The recreated name is carried by its new write; the delete did
            # not permanently remove the resource identity.
            self.assertIn("443", third["carried_renderer_outputs"])

    def test_continuous_byte_budget_stops_incomplete_without_dropping_frames(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary) / "campaign"
            self._run("continuous-byte-budget", directory)
            campaign = json.loads((directory / "sequence.json").read_text())
            self.assertEqual(campaign["status"], "incomplete")
            self.assertEqual(campaign["reason"], "byte_budget")
            self.assertEqual(campaign["frames_completed"], 1)
            self.assertEqual(len(campaign["frames"]), 1)
            self.assertTrue((directory / "frame-000001" / "frame-trace.json").is_file())
            self.assertFalse((directory / "frame-000002").exists())

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
            self.assertTrue(seal["event_stream_verified"])
            events_bytes = (directory / "frame-events.jsonl").read_bytes()
            self.assertEqual(seal["event_stream_bytes"], len(events_bytes))
            self.assertEqual(
                seal["event_stream_sha256"],
                hashlib.sha256(events_bytes).hexdigest(),
            )
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

    def test_event_digest_failure_invalidates_stream_and_never_notifies(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary) / "capture"
            result = self._run("seal-event-digest", directory)
            self.assertIn("callback_calls=0", result.stdout)
            self.assertIn("notifications=0", result.stdout)
            manifest = json.loads(
                (directory / "frame-trace.json").read_text()
            )
            self.assertFalse(manifest["complete"])
            self.assertFalse(manifest["stream_validated"])
            self.assertEqual(manifest["reason"], "event_stream_digest")
            seal = self._seal(directory)
            self.assertFalse(seal["sealed"])
            self.assertFalse(seal["event_stream_verified"])
            self.assertEqual(seal["failure_reason"], "event_stream_digest")

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
