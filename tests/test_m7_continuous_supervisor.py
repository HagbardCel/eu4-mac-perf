"""M7 PR4 continuous supervisor and bounded ordering contract."""
from __future__ import annotations

import hashlib
import json
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from automation import gfx_independent_replay, m7_continuous  # noqa: E402


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def make_frame(root: Path, index: int, previous: Path | None) -> Path:
    frame = root / f"frame-{index:06d}"
    frame.mkdir(parents=True)
    events = (json.dumps({"kind": "draw", "index": index}) + "\n").encode()
    (frame / "frame-events.jsonl").write_bytes(events)
    reference = bytes([index]) * 64
    (frame / "frame-reference.bgra").write_bytes(reference)
    manifest = {
        "schema": "gfx-frame-trace/v1", "complete": True,
        "reason": "scene_present", "events": 1,
        "frame": {
            "width": 4, "height": 4, "origin": "lower_left",
            "pixel_format": "BGRA8", "row_stride": 16,
            "reference": "frame-reference.bgra",
            "reference_bytes": len(reference),
            "reference_sha256": _sha(reference),
        },
    }
    (frame / "frame-trace.json").write_text(json.dumps(manifest))
    sequence = {
        "schema": "gfx-frame-sequence/v1",
        "session_id": "session", "pid": 1, "sequence_index": index,
        "campaign_frames": 3, "capture_frame_id": index,
        "complete": True, "boundary": "scene", "reason": "scene_present",
        "previous": None, "render_targets": [],
        "carried_renderer_outputs": [], "lifecycle": [],
        "reference_sha256": _sha(reference),
        "manifest_sha256": _sha((frame / "frame-trace.json").read_bytes()),
        "event_stream_sha256": _sha(events),
    }
    if previous is not None:
        sequence["previous"] = {
            "capture_frame_id": index - 1,
            "dir": str(previous),
            "manifest_sha256": _sha(
                (previous / "frame-trace.json").read_bytes()),
            "event_stream_sha256": _sha(
                (previous / "frame-events.jsonl").read_bytes()),
        }
    (frame / "frame-sequence.json").write_text(json.dumps(sequence))
    return frame


class SupervisorTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.frames = []
        previous = None
        for index in (1, 2, 3):
            frame = make_frame(self.root, index, previous)
            self.frames.append(frame)
            previous = frame

    def tearDown(self):
        self.temporary.cleanup()

    def rows(self, *overrides):
        base = [
            {"sequence": 1, "output_dir": str(self.root / "out" / "out-001"),
             "ok": True, "events": 1, "encoded": 1, "failed": 0,
             "history_status": 1, "carried_bindings": 0,
             "missing_history": 0, "stale_history": 0,
             "duration_ns": 1_000_000},
            {"sequence": 2, "output_dir": str(self.root / "out" / "out-002"),
             "ok": True, "events": 1, "encoded": 1, "failed": 0,
             "history_status": 1, "carried_bindings": 1,
             "missing_history": 0, "stale_history": 0,
             "duration_ns": 2_000_000},
            {"sequence": 3, "output_dir": str(self.root / "out" / "out-003"),
             "ok": True, "events": 1, "encoded": 1, "failed": 0,
             "history_status": 1, "carried_bindings": 1,
             "missing_history": 0, "stale_history": 0,
             "duration_ns": 3_000_000},
        ]
        for sequence, value in overrides:
            base[sequence - 1].update(value)
        return base

    def run_with(self, rows, diagnoses, *, stop=True, cancel=None,
                 max_frames=64):
        completed = mock.Mock()
        completed.returncode = 0
        completed.stdout = "\n".join(json.dumps(row) for row in rows)
        completed.stderr = ""
        with mock.patch.object(m7_continuous.m6_frame,
                               "build_replay_host",
                               return_value=Path("/bin/true")), \
             mock.patch.object(m7_continuous.subprocess, "run",
                               return_value=completed), \
             mock.patch.object(gfx_independent_replay, "diagnose_frame",
                               side_effect=diagnoses) as diagnose, \
             mock.patch.object(m7_continuous.gfx_replay_case,
                               "package_diagnosis",
                               return_value={"case_id": "case",
                                             "files": [{"bytes": 1}]}):
            report = m7_continuous.run_campaign(
                self.frames, self.root / "out",
                shaders=self.root, stop_on_difference=stop,
                cancel=cancel, max_frames=max_frames)
        return report, diagnose

    def test_stops_at_first_nonqualified_frame_and_preserves_case(self):
        report, diagnose = self.run_with(
            self.rows(),
            [{"classification": "qualified_match",
              "qualification": {"axes": {}}},
             {"classification": "qualified_difference",
              "qualification": {"axes": {}},
              "first_divergence": {"kind": "final_only"},
              "pixels": {"statistics": {}}}])
        self.assertEqual(report["status"], "stopped")
        self.assertEqual(report["stop_reason"], "qualified_difference")
        self.assertEqual(report["stopped_at"], 2)
        self.assertEqual(len(report["frames"]), 2)
        self.assertNotIn("3", [row["sequence"] for row in report["frames"]])
        self.assertTrue(report["frames"][1].get("case"))
        self.assertEqual(diagnose.call_count, 2)

    def test_gpu_failure_stops_before_diagnosis_of_later_frames(self):
        report, diagnose = self.run_with(
            self.rows((2, {"ok": False, "failed": 1,
                          "stage": "event_encode",
                          "error": "event_encode: failure"})),
            [{"classification": "qualified_match",
              "qualification": {"axes": {}}}])
        self.assertEqual(report["status"], "stopped")
        self.assertEqual(report["stop_reason"], "gpu_execution_failure")
        self.assertEqual(report["stopped_at"], 2)
        self.assertEqual(diagnose.call_count, 1)

    def test_cancellation_is_distinguishable(self):
        cancel = threading.Event()
        cancel.set()
        report, _ = self.run_with(
            self.rows(),
            [{"classification": "qualified_match",
              "qualification": {"axes": {}}}],
            cancel=cancel)
        self.assertEqual(report["status"], "cancelled")
        self.assertEqual(report["stop_reason"], "cancelled")

    def test_sequence_identity_is_validated_before_execution(self):
        sequence = json.loads(
            (self.frames[1] / "frame-sequence.json").read_text())
        sequence["previous"]["manifest_sha256"] = "0" * 64
        (self.frames[1] / "frame-sequence.json").write_text(
            json.dumps(sequence))
        with mock.patch.object(m7_continuous.subprocess, "run") as run:
            with self.assertRaises(m7_continuous.ContinuousError) as caught:
                m7_continuous.run_campaign(
                    self.frames, self.root / "out", shaders=self.root)
        self.assertEqual(caught.exception.kind, "previous_manifest")
        run.assert_not_called()

    def test_frame_and_byte_budgets_fail_closed(self):
        with self.assertRaises(m7_continuous.ContinuousError) as caught:
            m7_continuous.validate_sequence(self.frames, max_frames=2)
        self.assertEqual(caught.exception.kind, "too_many_frames")
        with self.assertRaises(m7_continuous.ContinuousError) as caught:
            m7_continuous.validate_sequence(self.frames, max_bytes=1)
        self.assertEqual(caught.exception.kind, "byte_budget")


if __name__ == "__main__":
    unittest.main()
