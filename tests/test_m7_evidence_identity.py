"""Capture/evidence identity regressions for M7-P2.

The identity of an observation is the tuple
``(session_id, capture_frame_id, event_stream_sha256)``.  These tests cover the
cases that are easy to get wrong offline: a frame id alone is not enough,
unknown identity is not a wildcard, and compact projections never upgrade
missing or stale evidence.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from automation import agent_tools, gfx_independent_replay

CAPTURE = {
    "session_id": "session-a",
    "capture_frame_id": 1,
    "event_stream_sha256": "a" * 64,
}
OTHER_SESSION_SAME_FRAME = dict(
    CAPTURE, session_id="session-b", event_stream_sha256="b" * 64)


class CaptureIdentityTupleTests(unittest.TestCase):
    def test_same_frame_id_in_another_session_is_a_different_capture(self):
        self.assertNotEqual(
            gfx_independent_replay.capture_identity(CAPTURE, {}),
            gfx_independent_replay.capture_identity(
                OTHER_SESSION_SAME_FRAME, {}))

    def test_missing_identity_is_unknown_not_a_wildcard(self):
        partial = gfx_independent_replay.capture_identity(
            {"session_id": "session-a", "capture_frame_id": 1}, {})
        self.assertIsNotNone(partial)
        self.assertIsNone(gfx_independent_replay._capture_key(partial))

    def test_seal_identity_is_not_mixed_with_a_different_trace(self):
        identity = gfx_independent_replay.capture_identity(
            {**CAPTURE, "reference_sha256": "c" * 64},
            {"present_index": 9,
             "frame": {"reference_sha256": "d" * 64}})
        self.assertEqual(identity["event_stream_sha256"], "a" * 64)
        self.assertEqual(identity["reference_sha256"], "c" * 64)


class CompactEvidenceTests(unittest.TestCase):
    def _case(self, root: Path) -> Path:
        case = root / "case"
        (case / "capture").mkdir(parents=True)
        (case / "capture" / "frame-seal.json").write_text(json.dumps({
            "session_id": CAPTURE["session_id"],
            "capture_frame_id": CAPTURE["capture_frame_id"],
            "event_stream_sha256": CAPTURE["event_stream_sha256"],
            "present_index": 5, "scene_index": 5,
        }))
        (case / "diagnostic-report.json").write_text(json.dumps({
            "schema": "gfx-frame-diagnosis/v1",
            "classification": "incomplete_diagnostics",
            "qualification": {"qualified": False},
            "pixels": {"final": {"status": "unavailable"}},
            "first_divergence": {"kind": "none"},
        }))
        (case / "graph.json").write_text(json.dumps({"status": "complete"}))
        (case / "case.json").write_text(json.dumps({
            "schema": "gfx-strategy-b-case/v1",
            "case_id": "compact-case",
            "diagnostic_report": "diagnostic-report.json",
            "graph": "graph.json",
            "files": [],
        }))
        return case

    def test_compact_view_keeps_case_and_capture_identity(self):
        with tempfile.TemporaryDirectory() as temporary:
            case = self._case(Path(temporary))
            verification = {
                "schema": "gfx-strategy-b-case-verification/v1",
                "case_id": "compact-case", "ok": True, "problems": [],
                "files": 0, "graph_status": "complete",
                "independently_reproducible": True,
            }
            manifest = json.loads((case / "case.json").read_text())
            value = agent_tools._compact_case_view(
                case, "gfx-strategy-b-case/v1", manifest, verification)
        self.assertEqual(value["case_id"], "compact-case")
        self.assertEqual(value["capture"]["session_id"], "session-a")
        self.assertEqual(value["capture"]["capture_frame_id"], 1)
        self.assertFalse(value["diagnosis"]["qualified"])

    def test_compact_view_does_not_invent_a_missing_diagnosis(self):
        with tempfile.TemporaryDirectory() as temporary:
            case = self._case(Path(temporary))
            (case / "diagnostic-report.json").unlink()
            verification = {
                "schema": "gfx-strategy-b-case-verification/v1",
                "case_id": "compact-case", "ok": True, "problems": [],
                "files": 0, "graph_status": "complete",
                "independently_reproducible": True,
            }
            manifest = json.loads((case / "case.json").read_text())
            value = agent_tools._compact_case_view(
                case, "gfx-strategy-b-case/v1", manifest, verification)
        self.assertEqual(value["diagnosis"]["schema"],
                         "gfx-agent-diagnosis/unavailable")
        self.assertIn("no readable diagnostic report",
                      value["diagnosis"]["reason"])

    def test_compact_case_stays_bounded_and_keeps_next_checkpoint(self):
        with tempfile.TemporaryDirectory() as temporary:
            case = self._case(Path(temporary))
            checkpoints = [{
                "event": index, "resource": "fb:0", "target": "0",
                "status": "divergent" if index == 142 else "match",
                "authoritative": index == 142,
                "statistics": {"mismatched_pixels": index,
                               "max_channel_error": 1},
            } for index in range(0, 143, 2)]
            (case / "diagnostic-report.json").write_text(json.dumps({
                "schema": "gfx-frame-diagnosis/v1",
                "classification": "difference_with_partial_state_coverage",
                "qualification": {"qualified": False,
                                  "axes": {"pixel_comparison": "divergent"}},
                "pixels": {"final": {"status": "divergent",
                                     "statistics": {"mismatched_pixels": 4,
                                                    "max_channel_error": 2}}},
                "first_divergence": {
                    "kind": "intermediate", "event": 142,
                    "resource": "fb:0", "target": "0",
                    "unresolved_interval": {
                        "start_event": None, "end_event": 142,
                        "lower_bound": "unbounded", "search_start_event": 0},
                    "next_checkpoint": {
                        "event": 71, "resource": "fb:0", "target": "0",
                        "requires_capture": True,
                        "reason": "bisect the unbounded interval"},
                },
                "checkpoints": checkpoints,
            }))
            verification = {
                "schema": "gfx-strategy-b-case-verification/v1",
                "case_id": "compact-case", "ok": True, "problems": [],
                "files": 0, "graph_status": "complete",
                "independently_reproducible": True,
            }
            manifest = json.loads((case / "case.json").read_text())
            value = agent_tools._compact_case_view(
                case, "gfx-strategy-b-case/v1", manifest, verification)
            envelope = agent_tools._bounded_envelope(agent_tools._envelope(
                "eu4_case_inspect", "ok", value))
        encoded = json.dumps(envelope, indent=2)
        self.assertLessEqual(len(encoded), agent_tools.COMPACT_OUTPUT_CAP)
        first = envelope["result"]["diagnosis"]["first_divergence"]
        self.assertEqual(first["next_checkpoint"]["event"], 71)
        self.assertIsNone(first["unresolved_interval"]["start_event"])
        self.assertEqual(first["unresolved_interval"]["lower_bound"],
                         "unbounded")
        self.assertLessEqual(len(envelope["result"]["diagnosis"]["checkpoints"]),
                             agent_tools.COMPACT_CHECKPOINT_LIMIT)

    def test_output_cap_reports_truncation(self):
        value = agent_tools._envelope(
            "eu4_case_inspect", "ok",
            {"diagnosis": {"checkpoints": list(range(1000)),
                           "first_divergence": {"event": 142},
                           "next_action": "capture event 71"}})
        reduced = agent_tools._bounded_envelope(value, cap=2000)
        self.assertTrue(reduced.get("truncated"))
        self.assertEqual(reduced["output_cap_bytes"], 2000)
        self.assertGreater(reduced["output_bytes_before_cap"], 2000)
        self.assertLessEqual(reduced["result"]["output_bytes"], 2000)
        self.assertEqual(reduced["result"]["diagnosis"]["next_action"],
                         "capture event 71")


if __name__ == "__main__":
    unittest.main()
