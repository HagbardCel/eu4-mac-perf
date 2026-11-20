"""M7 PR4 synchronous continuous coordinator over synthetic sealed frames."""
from __future__ import annotations

import json
import sys
import tempfile
import threading
import time
import unittest
from argparse import Namespace
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from automation import gfx_independent_replay, m6_frame  # noqa: E402
from tests.test_m7_frame_seal import _write_frame  # noqa: E402


def _sha(path: Path) -> str:
    import hashlib
    return hashlib.sha256(path.read_bytes()).hexdigest()


class ContinuousCoordinatorTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.run = Path(self.temporary.name) / "run"
        self.shadow = self.run / "shadow"
        self.frames = []
        for index in (1, 2):
            frame = self.run / "frames" / f"frame-{index:06d}"
            _write_frame(frame, operation_count=2)
            seal_path = frame / "frame-seal.json"
            seal = json.loads(seal_path.read_text())
            seal["capture_frame_id"] = index
            seal["session_id"] = "continuous-session"
            seal_path.write_text(json.dumps(seal))
            (self.run / "frames").mkdir(parents=True, exist_ok=True)
            self.frames.append(frame)

    def tearDown(self):
        self.temporary.cleanup()

    def bridge(self):
        for index in (1, 2):
            output = self.shadow / f"frame-{index:06d}"
            ready = output / "shaders" / "ready"
            deadline = time.monotonic() + 5
            while not ready.is_file() and time.monotonic() < deadline:
                time.sleep(0.01)
            self.assertTrue(ready.is_file(), "coordinator never published ready")
            frame = self.frames[index - 1]
            seal = json.loads((frame / "frame-seal.json").read_text())
            request = output / "checkpoint-request.json"
            report = {
                "schema": "m7-shadow-report/v1",
                "mode": "continuous",
                "status": "ok",
                "stage": "complete",
                "capture_frame_id": seal["capture_frame_id"],
                "pid": seal["pid"],
                "session_id": seal["session_id"],
                "gl_reference_sha256": seal["reference_sha256"],
                "trace_manifest_sha256": seal["manifest_sha256"],
                "operation_count": seal["operation_count"],
                "checkpoint_request_file": request.name,
                "checkpoint_request_sha256": _sha(request),
                "history_status": 1,
                "carried_bindings": 1 if index == 2 else 0,
                "missing_history": 0,
                "stale_history": 0,
                "metal": {
                    "ok": True, "history_status": 1,
                    "carried_bindings": 1 if index == 2 else 0,
                    "missing_history": 0, "stale_history": 0,
                },
            }
            (output / "shadow-report.json").write_text(json.dumps(report))

    def test_coordinator_stops_at_first_difference_and_keeps_sequence(self):
        diagnoses = [
            {"classification": "qualified_match",
             "qualification": {"axes": {"gpu_execution": "ok"}},
             "pixels": {"statistics": {}},
             "state_coverage": {"status": "complete"},
             "checkpoint_validation": {"status": "ok"},
             "first_divergence": {"kind": "none"}},
            {"classification": "qualified_difference",
             "qualification": {"axes": {"gpu_execution": "ok"}},
             "pixels": {"statistics": {"mismatched_pixels": 1}},
             "state_coverage": {"status": "complete"},
             "checkpoint_validation": {"status": "ok"},
             "first_divergence": {"kind": "final_only"}},
        ]
        args = Namespace(
            run=str(self.run), shadow_dir=str(self.shadow),
            timeout=10.0, seal_timeout=5.0, translation_timeout=5.0,
            replay_timeout=5.0, compare_timeout=5.0, policy=None,
            continuous=True, max_frames=2, continue_on_difference=False,
        )
        def fake_translate(command, **kwargs):
            output = Path(command[command.index("--out") + 1])
            output.mkdir(parents=True, exist_ok=True)
            (output / "shaders.json").write_text(json.dumps({
                "schema": "gfx-shader-translation/v1", "programs": []}))
            (output / "program.metal").write_text("// translated")
            return mock.Mock(returncode=0, stdout="", stderr="")

        thread = threading.Thread(target=self.bridge)
        thread.start()
        with mock.patch.object(m6_frame.subprocess, "run",
                               side_effect=fake_translate), \
             mock.patch.object(gfx_independent_replay, "diagnose_frame",
                               side_effect=diagnoses) as diagnose:
            status = m6_frame.shadow_coordinator(args)
        thread.join(timeout=10)
        self.assertEqual(status, 0)
        self.assertEqual(diagnose.call_count, 2)
        report = json.loads((self.shadow / "coordinator.json").read_text())
        self.assertEqual(report["mode"], "continuous")
        self.assertEqual(report["status"], "ok")
        self.assertEqual(report["stop_reason"], "first_qualified_difference")
        self.assertEqual(report["stopped_at"], 2)
        self.assertEqual([item["sequence"] for item in report["frames"]], [1, 2])
        self.assertEqual(report["frames"][1]["classification"],
                         "qualified_difference")
        self.assertEqual(report["frames"][1]["carried_bindings"], 1)

    def test_coordinator_signals_stop_before_returning_on_frame_one(self):
        def fake_translate(command, **kwargs):
            self.assertNotIn("--force", command)
            self.assertIn("--cache-dir", command)
            output = Path(command[command.index("--out") + 1])
            output.mkdir(parents=True, exist_ok=True)
            (output / "shaders.json").write_text(json.dumps({
                "schema": "gfx-shader-translation/v1", "programs": []}))
            return mock.Mock(returncode=0, stdout="", stderr="")

        diagnosis = {
            "classification": "qualified_difference",
            "qualification": {"axes": {"gpu_execution": "ok"}},
            "pixels": {"statistics": {"mismatched_pixels": 1}},
            "state_coverage": {"status": "complete"},
            "checkpoint_validation": {"status": "ok"},
            "first_divergence": {"kind": "final_only"},
        }
        args = Namespace(
            run=str(self.run), shadow_dir=str(self.shadow),
            timeout=10.0, seal_timeout=5.0, translation_timeout=5.0,
            replay_timeout=5.0, compare_timeout=5.0, policy=None,
            continuous=True, max_frames=2, continue_on_difference=False,
        )

        def bridge_once():
            output = self.shadow / "frame-000001"
            ready = output / "shaders" / "ready"
            deadline = time.monotonic() + 5
            while not ready.is_file() and time.monotonic() < deadline:
                time.sleep(0.01)
            self.assertTrue(ready.is_file())
            frame = self.frames[0]
            seal = json.loads((frame / "frame-seal.json").read_text())
            request = output / "checkpoint-request.json"
            (output / "shadow-report.json").write_text(json.dumps({
                "schema": "m7-shadow-report/v1", "mode": "continuous",
                "status": "ok", "stage": "complete",
                "capture_frame_id": seal["capture_frame_id"],
                "pid": seal["pid"], "session_id": seal["session_id"],
                "gl_reference_sha256": seal["reference_sha256"],
                "trace_manifest_sha256": seal["manifest_sha256"],
                "operation_count": seal["operation_count"],
                "checkpoint_request_file": request.name,
                "checkpoint_request_sha256": _sha(request),
                "history_status": 1,
            }))

        thread = threading.Thread(target=bridge_once)
        thread.start()
        with mock.patch.object(m6_frame.subprocess, "run",
                               side_effect=fake_translate), \
             mock.patch.object(gfx_independent_replay, "diagnose_frame",
                               return_value=diagnosis) as diagnose:
            status = m6_frame.shadow_coordinator(args)
        thread.join(timeout=10)
        self.assertEqual(status, 0)
        self.assertEqual(diagnose.call_count, 1)
        decision = json.loads(
            (self.shadow / "frame-000001" / "decision.json").read_text())
        self.assertFalse(decision["continue"])
        self.assertEqual(decision["sequence"], 1)
        report = json.loads((self.shadow / "coordinator.json").read_text())
        self.assertEqual(report["stopped_at"], 1)
        self.assertFalse((self.shadow / "frame-000002").exists())

    def test_successful_translation_without_shader_report_fails_closed(self):
        """Exit zero without shaders.json must never publish a ready marker."""
        def fake_translate(command, **kwargs):
            output = Path(command[command.index("--out") + 1])
            output.mkdir(parents=True, exist_ok=True)
            # A stage artifact alone is not the required translated report.
            (output / "vertex.metal").write_text("// stage without report")
            return mock.Mock(returncode=0, stdout="", stderr="")

        args = Namespace(
            run=str(self.run), shadow_dir=str(self.shadow),
            timeout=10.0, seal_timeout=5.0, translation_timeout=5.0,
            replay_timeout=5.0, compare_timeout=5.0, policy=None,
            continuous=True, max_frames=1, continue_on_difference=False,
        )
        with mock.patch.object(m6_frame.subprocess, "run",
                               side_effect=fake_translate), \
             mock.patch.object(gfx_independent_replay, "diagnose_frame") as diagnose:
            status = m6_frame.shadow_coordinator(args)
        self.assertEqual(status, 1)
        diagnose.assert_not_called()
        report = json.loads((self.shadow / "coordinator.json").read_text())
        self.assertEqual(report["status"], "failed")
        self.assertEqual(report["stop_reason"], "tool_failure")
        self.assertIn("translated shader report is missing", report["error"])
        output = self.shadow / "frame-000001"
        self.assertFalse((output / "shaders" / "ready").exists())
        self.assertFalse((output / "shader-receipt.json").exists())
        decision = json.loads((output / "decision.json").read_text())
        self.assertFalse(decision["continue"])
        self.assertEqual(decision["reason"], "tool_failure")

    def test_translation_without_stages_fails_closed_before_diagnosis(self):
        """A report with no translated stages must fail, never qualify."""
        def fake_translate(command, **kwargs):
            output = Path(command[command.index("--out") + 1])
            output.mkdir(parents=True, exist_ok=True)
            (output / "shaders.json").write_text(json.dumps({
                "schema": "gfx-shader-translation/v1",
                "programs": [{"program": 1, "status": "ok", "stages": {}}],
            }))
            return mock.Mock(returncode=0, stdout="", stderr="")

        def bridge_failed():
            output = self.shadow / "frame-000001"
            ready = output / "shaders" / "ready"
            deadline = time.monotonic() + 5
            while not ready.is_file() and time.monotonic() < deadline:
                time.sleep(0.01)
            self.assertTrue(ready.is_file())
            frame = self.frames[0]
            seal = json.loads((frame / "frame-seal.json").read_text())
            request = output / "checkpoint-request.json"
            (output / "shadow-report.json").write_text(json.dumps({
                "schema": "m7-shadow-report/v1", "mode": "continuous",
                "status": "failed", "stage": "event_encode",
                "error": "event_encode (operation 1): program 1 has no "
                         "translated stages",
                "failure": {
                    "stage": "event_encode", "operation": 1,
                    "error": "program 1 has no translated stages",
                },
                "capture_frame_id": seal["capture_frame_id"],
                "pid": seal["pid"], "session_id": seal["session_id"],
                "gl_reference_sha256": seal["reference_sha256"],
                "trace_manifest_sha256": seal["manifest_sha256"],
                "operation_count": seal["operation_count"],
                "checkpoint_request_file": request.name,
                "checkpoint_request_sha256": _sha(request),
                "history_status": 1,
            }))

        args = Namespace(
            run=str(self.run), shadow_dir=str(self.shadow),
            timeout=10.0, seal_timeout=5.0, translation_timeout=5.0,
            replay_timeout=5.0, compare_timeout=5.0, policy=None,
            continuous=True, max_frames=1, continue_on_difference=False,
        )
        thread = threading.Thread(target=bridge_failed)
        thread.start()
        with mock.patch.object(m6_frame.subprocess, "run",
                               side_effect=fake_translate), \
             mock.patch.object(gfx_independent_replay, "diagnose_frame") as diagnose:
            status = m6_frame.shadow_coordinator(args)
        thread.join(timeout=10)
        self.assertEqual(status, 1)
        diagnose.assert_not_called()
        report = json.loads((self.shadow / "coordinator.json").read_text())
        self.assertEqual(report["status"], "failed")
        self.assertEqual(report["stop_reason"], "tool_failure")
        self.assertIn("no translated stages", report["error"])
        self.assertFalse(
            (self.shadow / "frame-000001" / "diagnostic-report.json").exists())

    def test_translation_failure_is_a_tool_failure_not_a_difference(self):
        args = Namespace(
            run=str(self.run), shadow_dir=str(self.shadow),
            timeout=10.0, seal_timeout=5.0, translation_timeout=5.0,
            replay_timeout=5.0, compare_timeout=5.0, policy=None,
            continuous=True, max_frames=2, continue_on_difference=False,
        )
        with mock.patch.object(m6_frame.subprocess, "run",
                               return_value=mock.Mock(returncode=1)), \
             mock.patch.object(gfx_independent_replay, "diagnose_frame") as diagnose:
            status = m6_frame.shadow_coordinator(args)
        self.assertEqual(status, 1)
        diagnose.assert_not_called()
        report = json.loads((self.shadow / "coordinator.json").read_text())
        self.assertEqual(report["status"], "failed")
        self.assertEqual(report["stop_reason"], "tool_failure")
        self.assertIn("shader translation failed", report["error"])


if __name__ == "__main__":
    unittest.main()
