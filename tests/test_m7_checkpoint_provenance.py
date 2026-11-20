"""Focused regressions for same-capture causal bounds (M7-P1).

A checkpoint observed in a different EU4 frame or launch must never bound the
interval of a divergence in a sealed frame, even when the scene-selection
parameters look identical.
"""

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from automation import gfx_independent_replay


CAPTURE_A = {
    "session_id": "session-a",
    "capture_frame_id": 1,
    "present_index": 617,
    "scene_index": 617,
    "event_stream_sha256": "a" * 64,
    "reference_sha256": "b" * 64,
    "manifest_sha256": "c" * 64,
}
CAPTURE_B = dict(CAPTURE_A, session_id="session-b", capture_frame_id=2,
                 event_stream_sha256="d" * 64)


class CaptureIdentityTests(unittest.TestCase):
    def test_seal_fields_are_preferred_over_trace(self):
        identity = gfx_independent_replay.capture_identity(
            {"session_id": "s", "capture_frame_id": 3,
             "event_stream_sha256": "e" * 64, "reference_sha256": "f" * 64,
             "manifest_sha256": "1" * 64, "present_index": 9},
            {"present_index": 8, "frame": {"reference_sha256": "0" * 64}})
        self.assertEqual(identity["session_id"], "s")
        self.assertEqual(identity["capture_frame_id"], 3)
        self.assertEqual(identity["event_stream_sha256"], "e" * 64)
        self.assertEqual(identity["present_index"], 9)

    def test_empty_inputs_report_unknown(self):
        self.assertIsNone(gfx_independent_replay.capture_identity(None, {}))


class CausalProvenanceTests(unittest.TestCase):
    def graph(self):
        return {
            "events": [{
                "event": 142, "kind": "draw", "target": "fb:0",
                "entry": "_GfxDrawIndexed", "program": 331,
            }],
            "resources": {
                "fb:0": {
                    "producers": [{"event": 141, "kind": "draw",
                                   "entry": "_GfxDrawIndexed", "program": 331}],
                },
            },
        }

    def divergent(self, capture=CAPTURE_A):
        return {
            "event": 142, "target": "0", "resource": "fb:0",
            "status": "divergent", "authoritative": True, "capture": capture,
            "statistics": {"mismatched_pixels": 871620,
                           "max_channel_error": 103},
        }

    def match(self, event, capture=CAPTURE_A):
        return {
            "event": event, "target": "0", "resource": "fb:0",
            "status": "match", "authoritative": True, "capture": capture,
        }

    def run_first(self, checkpoints):
        return gfx_independent_replay.first_divergence(
            self.graph(), checkpoints,
            {"status": "divergent", "statistics": {"mismatched_pixels": 1}},
            checkpoint_validation={"status": "ok"})

    def test_same_capture_match_bounds_the_interval(self):
        value = self.run_first([self.match(71), self.divergent()])
        interval = value["unresolved_interval"]
        self.assertEqual(interval["start_event"], 71)
        self.assertEqual(interval["lower_bound"], "same_resource_match")
        self.assertEqual(value["cross_capture_excluded"]["capture_matches"], [])

    def test_match_from_another_capture_is_excluded(self):
        value = self.run_first([self.match(71, CAPTURE_B), self.divergent()])
        interval = value["unresolved_interval"]
        self.assertIsNone(interval["start_event"])
        self.assertEqual(interval["lower_bound"], "unbounded")
        self.assertEqual(value["cross_capture_excluded"]["capture_matches"], [71])
        self.assertTrue(any("different capture" in item
                            for item in value["limitations"]))
        self.assertEqual(value["next_checkpoint"]["lower_bound"], "unbounded")

    def test_known_capture_cannot_be_bounded_by_unknown_identity(self):
        unknown = {"event": 71, "target": "0", "resource": "fb:0",
                   "status": "match", "authoritative": True}
        value = self.run_first([unknown, self.divergent()])
        self.assertIsNone(value["unresolved_interval"]["start_event"])
        self.assertEqual(value["cross_capture_excluded"]["capture_matches"], [71])

    def test_legacy_unknown_identity_still_bounds(self):
        first = {"event": 71, "target": "0", "resource": "fb:0",
                 "status": "match", "authoritative": True}
        second = dict(self.divergent(), capture=None)
        value = self.run_first([first, second])
        self.assertEqual(value["unresolved_interval"]["start_event"], 71)


class ReconcileCaptureStampTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.frame = self.root / "frame"
        (self.frame / "blobs").mkdir(parents=True)
        self.out = self.root / "metal"
        self.out.mkdir()
        self.reference = bytes([1, 2, 3, 255] * 4)
        self.metal = self.reference
        (self.frame / "blobs" / "ref.bin").write_bytes(self.reference)
        (self.out / "intermediate.bgra").write_bytes(self.metal)
        self.graph = {
            "resources": {"tex:10": {"format": "RGBA8"}},
            "events": [{"event": 5, "kind": "draw"}],
        }

    def tearDown(self):
        self.temporary.cleanup()

    def test_every_reconciled_checkpoint_carries_the_capture(self):
        request = {"checkpoints": [{
            "event": 5, "target": "10", "resource": "tex:10",
            "file": "intermediate.bgra", "reference_file": "ref.bin",
            "reference_sha256": hashlib.sha256(self.reference).hexdigest(),
            "reference_width": 2, "reference_height": 2,
        }]}
        replay = {"checkpoints": [{
            "event": 5, "target": "10", "ok": True,
            "file": "intermediate.bgra", "reference_file": "ref.bin",
            "reference_sha256": hashlib.sha256(self.reference).hexdigest(),
            "width": 2, "height": 2, "format": "RGBA8",
        }]}
        results, validation = gfx_independent_replay.reconcile_checkpoints(
            self.graph, request, replay, self.frame, self.out,
            capture=CAPTURE_A)
        self.assertEqual(validation["status"], "ok", validation)
        self.assertEqual(results[0]["capture"], CAPTURE_A)
        self.assertEqual(results[0]["status"], "match")

    def test_non_list_checkpoint_containers_fail_closed(self):
        # Issue #233: a scalar/string container must produce invalid evidence,
        # never a TypeError from len()/list().
        for bad in (7, "not-a-list", {"event": 5}):
            for request, replay in (
                    ({"checkpoints": bad}, {"checkpoints": []}),
                    ({"checkpoints": []}, {"checkpoints": bad})):
                results, validation = (
                    gfx_independent_replay.reconcile_checkpoints(
                        self.graph, request, replay, self.frame, self.out,
                        capture=CAPTURE_A))
                self.assertEqual(validation["status"], "invalid", bad)
                self.assertTrue(validation["problems"])
                self.assertTrue(any(
                    problem.get("kind") in
                    ("malformed_request", "malformed_result")
                    for problem in validation["problems"]))
                self.assertEqual(results, [])

    def test_non_object_request_fails_closed(self):
        results, validation = gfx_independent_replay.reconcile_checkpoints(
            self.graph, ["not", "an", "object"], {"checkpoints": []},
            self.frame, self.out)
        self.assertEqual(validation["status"], "invalid")
        self.assertEqual(validation["problems"][0]["kind"], "malformed_request")
        self.assertEqual(results, [])


if __name__ == "__main__":
    unittest.main()
