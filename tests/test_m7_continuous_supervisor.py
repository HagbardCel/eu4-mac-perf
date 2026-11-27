"""M7 PR4 continuous supervisor and bounded ordering contract."""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from automation import (gfx_independent_replay, gfx_replay_case, gfx_frame,
                        m7_continuous)  # noqa: E402


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


class EngineIdentityTests(unittest.TestCase):
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

    def identify(self, indices):
        for frame, present in zip(self.frames, indices):
            path = frame / "frame-sequence.json"
            value = json.loads(path.read_text())
            value["present_index"] = present
            value["scene_index"] = present
            path.write_text(json.dumps(value))

    def test_consecutive_engine_indices_are_gap_free(self):
        self.identify((616, 617, 618))
        validation = m7_continuous.validate_sequence(self.frames)
        self.assertEqual(validation["engine_identity"]["status"], "gap_free")
        self.assertTrue(validation["engine_identity"]["gap_free"])
        self.assertEqual(
            validation["engine_identity"]["present_indices"],
            [616, 617, 618])

    def test_a_skipped_engine_frame_fails_closed(self):
        self.identify((616, 618, 619))
        with self.assertRaises(m7_continuous.ContinuousError) as raised:
            m7_continuous.validate_sequence(self.frames)
        self.assertEqual(raised.exception.kind, "engine_frame_gap")

    def test_missing_identity_is_unavailable_but_required_fails(self):
        validation = m7_continuous.validate_sequence(self.frames)
        self.assertEqual(validation["engine_identity"]["status"],
                         "unavailable")
        self.assertFalse(validation["engine_identity"]["gap_free"])
        with self.assertRaises(m7_continuous.ContinuousError) as raised:
            m7_continuous.validate_sequence(
                self.frames, require_engine_identity=True)
        self.assertEqual(raised.exception.kind,
                         "engine_identity_required")

    def test_partial_identity_fails_closed(self):
        self.identify((616, 617, 618))
        path = self.frames[1] / "frame-sequence.json"
        value = json.loads(path.read_text())
        value.pop("present_index")
        path.write_text(json.dumps(value))
        with self.assertRaises(m7_continuous.ContinuousError) as raised:
            m7_continuous.validate_sequence(self.frames)
        self.assertEqual(raised.exception.kind, "engine_identity_partial")


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
                 max_frames=64, fault=None):
        import io

        class FakeProcess:
            def __init__(self):
                self.stdout = iter(
                    json.dumps(row) + "\n" for row in rows)
                self.stderr = io.StringIO("")
                self.returncode = 0

            def poll(self):
                return self.returncode

            def wait(self, timeout=None):
                return self.returncode

            def terminate(self):
                self.returncode = 0

            def kill(self):
                self.returncode = 0

        with mock.patch.object(m7_continuous.gfx_frame,
                               "build_replay_host",
                               return_value=Path("/bin/true")), \
             mock.patch.object(m7_continuous.subprocess, "Popen",
                               return_value=FakeProcess()), \
             mock.patch.object(gfx_independent_replay, "diagnose_frame",
                               side_effect=diagnoses) as diagnose, \
             mock.patch.object(m7_continuous.gfx_replay_case,
                               "package_sequence_diagnosis",
                               return_value={"case_id": "case",
                                             "files": [{"bytes": 1}]}):
            report = m7_continuous.run_campaign(
                self.frames, self.root / "out",
                shaders=self.root, stop_on_difference=stop,
                cancel=cancel, max_frames=max_frames, fault=fault)
        return report, diagnose

    def test_sequence_checkpoints_are_published_and_passed_to_the_core(self):
        import io

        captured = []
        rows = self.rows()

        class FakeProcess:
            def __init__(self):
                self.stdout = iter(
                    json.dumps(row) + "\n" for row in rows)
                self.stderr = io.StringIO("")
                self.returncode = 0

            def poll(self):
                return self.returncode

            def wait(self, timeout=None):
                return self.returncode

            def terminate(self):
                self.returncode = 0

            def kill(self):
                self.returncode = 0

        diagnosis = {"classification": "qualified_match",
                     "qualification": {"axes": {}},
                     "first_divergence": {"kind": "none"},
                     "pixels": {"statistics": {}},
                     "checkpoint_validation": {"status": "ok"}}

        def popen(command, **kwargs):
            captured.extend(command)
            return FakeProcess()

        with mock.patch.object(m7_continuous.gfx_frame,
                               "build_replay_host",
                               return_value=Path("/bin/true")), \
             mock.patch.object(m7_continuous.subprocess, "Popen",
                               side_effect=popen), \
             mock.patch.object(gfx_independent_replay, "diagnose_frame",
                               return_value=diagnosis), \
             mock.patch.object(m7_continuous.gfx_replay_case,
                               "package_sequence_diagnosis",
                               return_value={"case_id": "case",
                                             "files": [{"bytes": 1}]}):
            report = m7_continuous.run_campaign(
                self.frames, self.root / "out", shaders=self.root,
                stop_on_difference=False)
        self.assertEqual(report["status"], "complete")
        self.assertIn("--sequence-checkpoints", captured)
        self.assertIn("--frame-timeout-ms", captured)
        timeout_index = captured.index("--frame-timeout-ms") + 1
        self.assertGreater(int(captured[timeout_index]), 0)
        for index, frame in enumerate(self.frames, start=1):
            directory = self.root / "out" / "checkpoints" / f"frame-{index:03d}"
            request = directory / "checkpoint-request.json"
            receipt = directory / "shader-receipt.json"
            self.assertTrue(request.is_file(), request)
            self.assertTrue(receipt.is_file(), receipt)
            receipt_value = json.loads(receipt.read_text())
            self.assertEqual(receipt_value["schema"],
                             "m7-shadow-shader-receipt/v1")
            self.assertEqual(receipt_value["checkpoint_request_sha256"],
                             _sha(request.read_bytes()))
            self.assertEqual(receipt_value["capture_frame_id"], index)
            self.assertEqual(receipt_value["sequence"], index)

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

    def test_queued_rows_after_a_difference_are_not_diagnosed(self):
        import io

        rows = self.rows()

        class BurstProcess:
            def __init__(self):
                self.stdout = iter(
                    json.dumps(row) + "\n" for row in rows)
                self.stderr = io.StringIO("")
                self.returncode = 0

            def poll(self):
                return self.returncode

            def wait(self, timeout=None):
                return self.returncode

            def terminate(self):
                self.returncode = 0

            def kill(self):
                self.returncode = 0

        diagnosis = mock.Mock(side_effect=[
            {"classification": "qualified_difference",
             "qualification": {"axes": {}},
             "first_divergence": {"kind": "final_only"},
             "pixels": {"statistics": {}},
             "checkpoint_validation": {"status": "ok"}},
        ])
        with mock.patch.object(m7_continuous.gfx_frame,
                               "build_replay_host",
                               return_value=Path("/bin/true")), \
             mock.patch.object(m7_continuous.subprocess, "Popen",
                               return_value=BurstProcess()), \
             mock.patch.object(gfx_independent_replay, "diagnose_frame",
                               side_effect=diagnosis), \
             mock.patch.object(m7_continuous.gfx_replay_case,
                               "package_sequence_diagnosis",
                               return_value={"case_id": "case",
                                             "files": [{"bytes": 1}]}):
            report = m7_continuous.run_campaign(
                self.frames, self.root / "out", shaders=self.root,
                stop_on_difference=True)
        self.assertEqual(report["status"], "stopped")
        self.assertEqual(report["stopped_at"], 1)
        self.assertEqual(diagnosis.call_count, 1)
        self.assertEqual([row["sequence"] for row in
                          report["unexecuted_frames"]], [2, 3])
        self.assertNotIn("2", [row["sequence"] for row in report["frames"]])

    def test_gpu_failure_marks_queued_frames_unexecuted(self):
        report, diagnose = self.run_with(
            self.rows((2, {"ok": False, "history_status": 0,
                           "error": "command buffer failed"})),
            [{"classification": "qualified_match",
              "qualification": {"axes": {}},
              "first_divergence": {"kind": "none"},
              "pixels": {"statistics": {}},
              "checkpoint_validation": {"status": "ok"}}])
        self.assertEqual(report["status"], "stopped")
        self.assertEqual(report["stop_reason"], "gpu_execution_failure")
        self.assertEqual(report["stopped_at"], 2)
        self.assertEqual([row["sequence"] for row in
                          report["unexecuted_frames"]], [3])
        self.assertEqual(diagnose.call_count, 1)

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

    def test_hard_failure_stops_even_when_differences_continue(self):
        report, diagnose = self.run_with(
            self.rows((2, {"ok": False, "failed": 1,
                          "stage": "event_encode",
                          "error": "event_encode: failure"})),
            [{"classification": "qualified_match",
              "qualification": {"axes": {}}}], stop=False)
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

    @staticmethod
    def qualified_diagnosis(**overrides):
        value = {
            "schema": gfx_independent_replay.DIAGNOSIS_SCHEMA,
            "classification": "qualified_match",
            "qualification": {
                "axes": {"gpu_execution": "ok",
                          "pixel_comparison": "match"}},
            "pixels": {
                "status": "match",
                "statistics": {"pixels": 4, "mismatched_pixels": 0},
            },
            "checkpoint_validation": {"status": "ok", "divergent": 0},
            "first_divergence": {"kind": "none"},
        }
        value.update(overrides)
        return value

    def test_duplicate_result_sequence_is_rejected_before_diagnosis(self):
        rows = self.rows()
        report, diagnose = self.run_with(
            [rows[0], dict(rows[0]), rows[2]],
            [self.qualified_diagnosis()])
        self.assertEqual(report["status"], "failed")
        self.assertEqual(report["stop_reason"], "sequence_order")
        self.assertEqual(report["sequence_order"]["kind"], "duplicate")
        self.assertEqual(report["sequence_order"]["expected"], 2)
        self.assertEqual(report["sequence_order"]["received"], 1)
        self.assertEqual(diagnose.call_count, 1)
        self.assertEqual([row["sequence"] for row in report["frames"]], [1])
        self.assertFalse(report["qualified"])

    def test_skipped_result_sequence_is_rejected_before_diagnosis(self):
        rows = self.rows()
        report, diagnose = self.run_with(
            [rows[0], rows[2]], [self.qualified_diagnosis()])
        self.assertEqual(report["status"], "failed")
        self.assertEqual(report["stop_reason"], "sequence_order")
        self.assertEqual(report["sequence_order"]["kind"], "skipped")
        self.assertEqual(report["sequence_order"]["expected"], 2)
        self.assertEqual(report["sequence_order"]["received"], 3)
        self.assertEqual(diagnose.call_count, 1)
        self.assertFalse(report["qualified"])

    def test_missing_first_result_sequence_is_rejected_before_diagnosis(self):
        rows = self.rows()
        report, diagnose = self.run_with(
            [rows[1], rows[0], rows[2]], [])
        self.assertEqual(report["status"], "failed")
        self.assertEqual(report["stop_reason"], "sequence_order")
        self.assertEqual(report["sequence_order"]["kind"], "skipped")
        self.assertEqual(report["sequence_order"]["received"], 2)
        self.assertEqual(diagnose.call_count, 0)
        self.assertEqual(report["frames"], [])
        self.assertFalse(report["qualified"])

    def test_valid_result_sequence_is_qualified(self):
        report, diagnose = self.run_with(
            self.rows(),
            [self.qualified_diagnosis() for _ in range(3)],
            stop=False)
        self.assertEqual(report["status"], "complete")
        self.assertNotIn("sequence_order", report)
        self.assertEqual([row["sequence"] for row in report["frames"]],
                         [1, 2, 3])
        self.assertTrue(all(row["diagnostic_completed"]
                            for row in report["frames"]))
        self.assertTrue(report["qualified"])
        self.assertEqual(diagnose.call_count, 3)

    def test_out_of_range_result_is_rejected_before_diagnosis(self):
        rows = self.rows()
        beyond = dict(rows[0], sequence=4)
        report, diagnose = self.run_with([beyond], [])
        self.assertEqual(report["status"], "failed")
        self.assertEqual(report["stop_reason"], "sequence_order")
        self.assertEqual(report["sequence_order"]["kind"], "out_of_range")
        self.assertEqual(diagnose.call_count, 0)

    def test_gpu_failure_row_never_claims_a_completed_diagnostic(self):
        rows = self.rows()
        rows[1]["ok"] = False
        rows[1]["error"] = "command buffer failed"
        report, diagnose = self.run_with(
            rows, [self.qualified_diagnosis()], stop=False)
        self.assertEqual(report["status"], "stopped")
        self.assertEqual(report["stop_reason"], "gpu_execution_failure")
        self.assertEqual(diagnose.call_count, 1)
        first, failed = report["frames"]
        self.assertTrue(first["diagnostic_completed"])
        self.assertTrue(first["diagnostic_report"])
        self.assertFalse(failed["diagnostic_completed"])
        self.assertIsNone(failed["diagnostic_report"])
        self.assertEqual(failed["diagnostic_evidence"]["missing"],
                         ["diagnosis"])
        self.assertFalse(report["qualified"])

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

    def test_offline_sequence_stops_on_terminal_classification(self):
        report, diagnose = self.run_with(
            self.rows(),
            [{"classification": "qualified_difference",
              "qualification": {"axes": {}},
              "first_divergence": {"kind": "final_only"},
              "pixels": {"statistics": {}},
              "checkpoint_validation": {"status": "ok"}},
             {"classification": "invalid_provenance",
              "qualification": {"axes": {}},
              "first_divergence": {"kind": "invalid_checkpoint"},
              "pixels": {"statistics": {}},
              "checkpoint_validation": {"status": "invalid"}}],
            stop=False)
        self.assertEqual(report["status"], "stopped")
        self.assertEqual(report["stop_reason"], "invalid_provenance")
        self.assertEqual(report["stopped_at"], 2)
        self.assertEqual(diagnose.call_count, 2)
        self.assertEqual([row["sequence"] for row in report["frames"]], [1, 2])

    def test_policy_is_forwarded_and_digest_is_saved(self):
        import io

        policy = self.root / "policy.json"
        policy.write_text(json.dumps({
            "schema": "gfx-frame-comparison-policy/v1",
            "name": "campaign-policy",
            "thresholds": {"max_channel_error": 0},
        }))
        rows = self.rows()
        seen = {}

        class FakeProcess:
            def __init__(self):
                self.stdout = iter(
                    json.dumps(row) + "\n" for row in rows)
                self.stderr = io.StringIO("")
                self.returncode = 0

            def poll(self):
                return self.returncode

            def wait(self, timeout=None):
                return self.returncode

            def terminate(self):
                self.returncode = 0

            def kill(self):
                self.returncode = 0

        def diagnose(frame, output, out_dir, selected_policy, **kwargs):
            seen["policy"] = str(selected_policy)
            return {"classification": "qualified_match",
                    "qualification": {"axes": {}},
                    "first_divergence": {"kind": "none"},
                    "pixels": {"statistics": {}},
                    "checkpoint_validation": {"status": "ok"}}

        with mock.patch.object(m7_continuous.gfx_frame, "build_replay_host",
                               return_value=Path("/bin/true")), \
             mock.patch.object(m7_continuous.subprocess, "Popen",
                               return_value=FakeProcess()), \
             mock.patch.object(gfx_independent_replay, "diagnose_frame",
                               side_effect=diagnose), \
             mock.patch.object(gfx_replay_case, "package_sequence_diagnosis",
                               return_value={"case_id": "case",
                                             "files": [{"bytes": 1}]}):
            report = m7_continuous.run_campaign(
                self.frames, self.root / "policy-out", shaders=self.root,
                policy=policy, stop_on_difference=False)
        self.assertEqual(report["status"], "complete")
        self.assertEqual(Path(seen["policy"]).resolve(), policy.resolve())
        digest = hashlib.sha256(policy.read_bytes()).hexdigest()
        self.assertEqual(report["policy"]["sha256"], digest)
        self.assertEqual(report["policy"]["source"], "explicit")
        saved = json.loads(
            (self.root / "policy-out" / "campaign-report.json").read_text())
        self.assertEqual(saved["policy"]["sha256"], digest)

    def test_missing_policy_is_refused_before_execution(self):
        missing = self.root / "missing-policy.json"
        with mock.patch.object(m7_continuous.subprocess, "run") as run:
            with self.assertRaises(m7_continuous.ContinuousError) as caught:
                m7_continuous.run_campaign(
                    self.frames, self.root / "missing-policy-out",
                    shaders=self.root, policy=missing)
        self.assertEqual(caught.exception.kind, "policy_unavailable")
        run.assert_not_called()

    def test_frame_and_byte_budgets_fail_closed(self):
        with self.assertRaises(m7_continuous.ContinuousError) as caught:
            m7_continuous.validate_sequence(self.frames, max_frames=2)
        self.assertEqual(caught.exception.kind, "too_many_frames")
        with self.assertRaises(m7_continuous.ContinuousError) as caught:
            m7_continuous.validate_sequence(self.frames, max_bytes=1)
        self.assertEqual(caught.exception.kind, "byte_budget")


class DiagnosticEvidenceTests(unittest.TestCase):
    """diagnostic_evidence requires the declared evidence, not just labels."""

    @staticmethod
    def diagnosis(**overrides):
        value = {
            "schema": gfx_independent_replay.DIAGNOSIS_SCHEMA,
            "classification": "qualified_match",
            "qualification": {"axes": {"pixel_comparison": "match"}},
            "pixels": {
                "status": "match",
                "statistics": {"pixels": 16, "mismatched_pixels": 0},
            },
            "checkpoint_validation": {"status": "ok"},
            "first_divergence": {"kind": "none"},
        }
        value.update(overrides)
        return value

    def test_missing_pixel_statistics_is_incomplete(self):
        evidence = m7_continuous.diagnostic_evidence(
            self.diagnosis(pixels={"status": "match"}))
        self.assertFalse(evidence["completed"])
        self.assertIn("pixels.statistics", evidence["missing"])

    def test_invalid_pixel_statistics_are_incomplete(self):
        variants = {
            "zero_pixels": {"pixels": 0, "mismatched_pixels": 0},
            "negative": {"pixels": 16, "mismatched_pixels": -1},
            "over_count": {"pixels": 16, "mismatched_pixels": 17},
            "non_numeric": {"pixels": "16", "mismatched_pixels": 2},
        }
        for name, statistics in variants.items():
            with self.subTest(variant=name):
                evidence = m7_continuous.diagnostic_evidence(
                    self.diagnosis(pixels={
                        "status": "match", "statistics": statistics}))
                self.assertFalse(evidence["completed"])
                self.assertIn("pixels.statistics", evidence["missing"])

    def test_incomplete_checkpoint_evidence_is_not_complete(self):
        diagnosis = self.diagnosis(
            classification="incomplete_diagnostics",
            checkpoint_validation={"status": "incomplete"})
        evidence = m7_continuous.diagnostic_evidence(diagnosis)
        self.assertFalse(evidence["completed"])
        self.assertIn("checkpoint_validation.status", evidence["missing"])
        self.assertEqual(evidence["checkpoint_status"], "incomplete")
        # The explicit classification is preserved; it is not rewritten into a
        # pixel difference.
        self.assertEqual(diagnosis["classification"], "incomplete_diagnostics")

    def test_invalid_checkpoint_evidence_is_not_complete(self):
        evidence = m7_continuous.diagnostic_evidence(self.diagnosis(
            classification="invalid_provenance",
            checkpoint_validation={"status": "invalid"}))
        self.assertFalse(evidence["completed"])
        self.assertEqual(evidence["checkpoint_status"], "invalid")

    def test_complete_evidence_is_complete(self):
        evidence = m7_continuous.diagnostic_evidence(self.diagnosis())
        self.assertTrue(evidence["completed"], evidence)
        self.assertEqual(evidence["mismatched_pixels"], 0)


class LiveCoordinatorPackagingTests(unittest.TestCase):
    """The live coordinator packages the exact per-frame translations it used."""

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.run = self.root / "run"
        self.shadow = self.run / "shadow"
        for index in (1, 2, 3):
            frame = self.run / "frames" / f"frame-{index:06d}"
            frame.mkdir(parents=True)
            shader = self.shadow / f"frame-{index:06d}" / "shaders"
            shader.mkdir(parents=True)
            (shader / "shaders.json").write_text(json.dumps({
                "schema": "gfx-shader-report/v1",
                "programs": [{"program": index}],
            }))
            checkpoint = self.shadow / f"frame-{index:06d}"
            (checkpoint / "checkpoint-request.json").write_text(
                json.dumps({"frame": index}))
            (checkpoint / "shader-receipt.json").write_text(
                json.dumps({"frame": index}))
        (self.shadow / "frame-000003" / "diagnostic-report.json").write_text(
            json.dumps({"classification": "qualified_match"}))
        self.args = type("Args", (), {
            "case_dir": "", "case_max_bytes": 0,
        })()

    def tearDown(self):
        self.temporary.cleanup()

    def test_packages_all_included_frames_with_their_own_shaders(self):
        captured = {}

        def package(frames, metal_dir, case_dir, report, **kwargs):
            captured.update({
                "frames": [str(item) for item in frames],
                "metal_dir": str(metal_dir), "case_dir": str(case_dir),
                "report": report, "kwargs": kwargs,
            })
            return {
                "case_id": "live", "files": [{"bytes": 10}],
                "reproduction": {"status": "complete",
                                 "independently_reproducible": True},
            }

        with mock.patch.object(gfx_replay_case,
                               "package_sequence_diagnosis",
                               side_effect=package):
            value = gfx_frame._package_continuous_case(
                self.run, self.shadow, 3, self.args)
        self.assertEqual(value["status"], "complete")
        self.assertEqual(len(captured["frames"]), 3)
        self.assertEqual(captured["metal_dir"],
                         str(self.shadow / "frame-000003"))
        for index, frame in enumerate(captured["frames"], start=1):
            self.assertEqual(
                str(captured["kwargs"]["frame_shaders"][frame]),
                str(self.shadow / f"frame-{index:06d}" / "shaders"))
            self.assertEqual(
                str(captured["kwargs"]["checkpoint_sources"][index - 1]),
                str(self.shadow / f"frame-{index:06d}"))
        self.assertEqual(captured["kwargs"]["max_bytes"],
                         512 * 1024 * 1024)

    def test_published_case_is_labeled_with_its_reproduction_scope(self):
        def package(frames, metal_dir, case_dir, report, **kwargs):
            path = Path(case_dir)
            path.mkdir(parents=True, exist_ok=True)
            (path / "case.json").write_text(json.dumps({
                "schema": "gfx-strategy-b-case/v2", "case_id": "live",
            }))
            return {"case_id": "live", "files": [],
                    "reproduction": {"status": "incomplete",
                                     "independently_reproducible": False}}

        with mock.patch.object(gfx_replay_case,
                               "package_sequence_diagnosis",
                               side_effect=package):
            value = gfx_frame._package_continuous_case(
                self.run, self.shadow, 3, self.args,
                scope="completed_prefix",
                terminal_failure={"kind": "gpu_execution_failure",
                                  "frame": 3})
        self.assertEqual(value["scope"], "completed_prefix")
        manifest = json.loads(
            (self.shadow / "case" / "case.json").read_text())
        self.assertEqual(manifest["reproduction_scope"],
                         "completed_prefix")
        self.assertEqual(manifest["terminal_failure"]["kind"],
                         "gpu_execution_failure")

    def test_continuation_predicate_excludes_terminal_and_ineligible(self):
        self.assertFalse(gfx_frame._continuous_should_continue(
            "invalid_provenance", 1, True))
        self.assertFalse(gfx_frame._continuous_should_continue(
            "incomplete_diagnostics", 1, True))
        self.assertFalse(gfx_frame._continuous_should_continue(
            "qualified_difference", 0, True))
        self.assertFalse(gfx_frame._continuous_should_continue(
            "unsupported_state", 1, True))
        self.assertTrue(gfx_frame._continuous_should_continue(
            "qualified_difference", 1, True))
        self.assertFalse(gfx_frame._continuous_should_continue(
            "qualified_difference", 1, False))
        self.assertTrue(gfx_frame._continuous_should_continue(
            "qualified_match", 1, False))

    def test_complete_v1_single_frame_package_is_not_mislabelled(self):
        def package(frames, metal_dir, case_dir, report, **kwargs):
            path = Path(case_dir)
            path.mkdir(parents=True, exist_ok=True)
            (path / "case.json").write_text(json.dumps({
                "schema": gfx_replay_case.SCHEMA,
                "case_id": "single-frame",
                "files": [],
            }))
            return {"schema": gfx_replay_case.SCHEMA,
                    "case_id": "single-frame", "files": []}

        with mock.patch.object(gfx_replay_case,
                               "package_sequence_diagnosis",
                               side_effect=package):
            value = gfx_frame._package_continuous_case(
                self.run, self.shadow, 3, self.args,
                scope="target_frame")
        self.assertEqual(value["status"], "complete")
        self.assertEqual(value["reproduction"]["status"], "complete")
        self.assertTrue(value["reproduction"]["independently_reproducible"])

    def test_incomplete_reproduction_is_not_claimed_complete(self):
        with mock.patch.object(
                gfx_replay_case, "package_sequence_diagnosis",
                return_value={
                    "case_id": "live", "files": [],
                    "reproduction": {
                        "status": "incomplete",
                        "independently_reproducible": False,
                        "reason": "artifact_budget",
                    },
                }):
            value = gfx_frame._package_continuous_case(
                self.run, self.shadow, 3, self.args)
        self.assertEqual(value["status"], "incomplete")
        self.assertFalse(value["reproduction"]["independently_reproducible"])

    def test_packaging_refusal_is_structured(self):
        with mock.patch.object(
                gfx_replay_case, "package_sequence_diagnosis",
                side_effect=gfx_replay_case.CaseError(
                    "missing_predecessor", "no producer")):
            value = gfx_frame._package_continuous_case(
                self.run, self.shadow, 3, self.args)
        self.assertEqual(value["status"], "failed")
        self.assertIn("missing_predecessor", value["reason"])

    def test_continue_on_difference_stops_on_a_hard_failure(self):
        summaries = [
            {"sequence": 1, "classification": "qualified_match",
             "history": 1, "diagnostic_report": "f1.json"},
            {"sequence": 2,
             "classification": "difference_with_partial_state_coverage",
             "history": 1, "diagnostic_report": "f2.json"},
            {"sequence": 3, "classification": "invalid_provenance",
             "history": 1, "diagnostic_report": "f3.json"},
        ]
        args = type("Args", (), {
            "run": str(self.run), "shadow_dir": str(self.shadow),
            "max_frames": 4, "timeout": 30.0, "continue_on_difference": True,
            "case_dir": "", "case_max_bytes": 0,
        })()
        packaged = []

        def coordinator(run, shadow, frame_index, args, deadline):
            return summaries[frame_index - 1], {"classification": "x"}

        def package(run, shadow, count, args, **kwargs):
            packaged.append((count, kwargs.get("scope")))
            return {"status": "incomplete", "scope": kwargs.get("scope"),
                    "reproduction": {"independently_reproducible": False}}

        with mock.patch.object(gfx_frame, "_continuous_frame_coordinator",
                               side_effect=coordinator), \
             mock.patch.object(gfx_frame, "_package_continuous_case",
                               side_effect=package):
            code = gfx_frame.shadow_coordinator_continuous(args)
        self.assertEqual(code, 1)
        report = json.loads((self.shadow / "coordinator.json").read_text())
        self.assertEqual(report["status"], "failed")
        self.assertEqual(report["stop_reason"], "invalid_provenance")
        self.assertEqual(report["terminal_failure"]["kind"],
                         "invalid_provenance")
        self.assertFalse(report["qualified"])
        self.assertEqual(packaged, [(2, "completed_prefix")])

    def test_continue_on_difference_continues_eligible_differences(self):
        summaries = [
            {"sequence": 1,
             "classification": "difference_with_partial_state_coverage",
             "history": 1, "diagnostic_report": "f1.json"},
            {"sequence": 2, "classification": "qualified_match",
             "history": 1, "diagnostic_report": "f2.json"},
        ]
        args = type("Args", (), {
            "run": str(self.run), "shadow_dir": str(self.shadow),
            "max_frames": 2, "timeout": 30.0, "continue_on_difference": True,
            "case_dir": "", "case_max_bytes": 0,
        })()

        def coordinator(run, shadow, frame_index, args, deadline):
            return summaries[frame_index - 1], {"classification": "x"}

        with mock.patch.object(gfx_frame, "_continuous_frame_coordinator",
                               side_effect=coordinator), \
             mock.patch.object(
                gfx_frame, "_package_continuous_case",
                return_value={"status": "complete",
                              "scope": "completed_sequence",
                              "reproduction": {
                                  "independently_reproducible": True}}):
            code = gfx_frame.shadow_coordinator_continuous(args)
        self.assertEqual(code, 0)
        report = json.loads((self.shadow / "coordinator.json").read_text())
        self.assertEqual(report["status"], "ok")
        self.assertEqual(report["stop_reason"], "frame_limit")
        self.assertEqual(len(report["frames"]), 2)
        self.assertFalse(report["qualified"])

    def test_failed_continuous_report_never_publishes_qualified(self):
        report = {
            "schema": "m7-shadow-coordinator/v1", "mode": "continuous",
            "status": "failed", "stop_reason": "tool_failure",
            "error": "frame 2 preparation failed",
            "frames": [{
                "sequence": 1, "classification": "qualified_match",
                "history": 1, "diagnostic_report": "f1.json",
            }],
        }
        result = {}
        failure = gfx_frame.apply_shadow_report(result, report)
        self.assertIn("frame 2 preparation failed", failure)
        self.assertEqual(result["campaign_execution"], "failed")
        self.assertEqual(result["classification"], "tool_failure")
        self.assertEqual(result["terminal_failure"]["kind"], "tool_failure")
        self.assertFalse(result["qualified"])
        self.assertFalse(result["diagnostic_complete"])

    def test_successful_continuous_report_can_publish_qualified(self):
        report = {
            "schema": "m7-shadow-coordinator/v1", "mode": "continuous",
            "status": "ok", "stop_reason": "frame_limit",
            "frames": [{
                "sequence": 1, "classification": "qualified_match",
                "history": 1, "diagnostic_report": "f1.json",
            }],
        }
        result = {}
        self.assertIsNone(gfx_frame.apply_shadow_report(result, report))
        self.assertEqual(result["campaign_execution"], "ok")
        self.assertTrue(result["qualified"])
        self.assertTrue(result["diagnostic_complete"])

    def test_stop_and_frame_limit_publish_the_case(self):
        summaries = [
            {"sequence": 1, "classification": "qualified_match"},
            {"sequence": 2, "classification": "qualified_difference"},
        ]

        def coordinator(run, shadow, frame_index, args, deadline):
            return summaries[frame_index - 1], {"classification": "x"}

        args = type("Args", (), {
            "run": str(self.run), "shadow_dir": str(self.shadow),
            "max_frames": 3, "timeout": 30.0, "continue_on_difference": False,
            "case_dir": "", "case_max_bytes": 0,
        })()
        packaged = []

        def package(run, shadow, count, args, **kwargs):
            packaged.append(count)
            return {"status": "complete", "scope": kwargs.get("scope"),
                    "reproduction": {"independently_reproducible": True}}

        with mock.patch.object(gfx_frame, "_continuous_frame_coordinator",
                               side_effect=coordinator), \
             mock.patch.object(gfx_frame, "_package_continuous_case",
                               side_effect=package):
            code = gfx_frame.shadow_coordinator_continuous(args)
        self.assertEqual(code, 0)
        self.assertEqual(packaged, [2])
        report = json.loads(
            (self.shadow / "coordinator.json").read_text())
        self.assertEqual(report["status"], "ok")
        self.assertEqual(report["stop_reason"],
                         "first_qualified_difference")
        self.assertEqual(report["case"]["status"], "complete")


class BoundedWorkerTests(unittest.TestCase):
    """Real child processes prove deadlines, draining and bounded cleanup."""

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.frames = []
        previous = None
        for index in (1, 2, 3):
            frame = make_frame(self.root, index, previous)
            self.frames.append(frame)
            previous = frame
        self.pid_file = self.root / "host.pid"

    def tearDown(self):
        self.temporary.cleanup()

    def host(self, body: str) -> Path:
        script = self.root / "host.py"
        script.write_text(
            "#!/usr/bin/env python3\n"
            "import json, os, sys, time\n"
            f"open({str(self.pid_file)!r}, 'w').write(str(os.getpid()))\n"
            + body)
        script.chmod(0o755)
        return script

    def row(self, sequence: int) -> str:
        return json.dumps({
            "sequence": sequence,
            "output_dir": str(self.root / "out" / f"out-{sequence:03d}"),
            "ok": True, "events": 1, "encoded": 1, "failed": 0,
            "history_status": 1, "carried_bindings": 0,
            "missing_history": 0, "stale_history": 0,
            "duration_ns": 1_000_000,
        })

    def run_campaign(self, host: Path, *, timeout=5.0, frame_timeout=0.5,
                     stop=True):
        diagnosis = {
            "classification": "qualified_match",
            "qualification": {"axes": {}},
            "first_divergence": {"kind": "none"},
            "pixels": {"statistics": {}},
            "checkpoint_validation": {"status": "ok"},
        }
        with mock.patch.object(gfx_independent_replay, "diagnose_frame",
                               return_value=diagnosis), \
             mock.patch.object(m7_continuous.gfx_replay_case,
                               "package_sequence_diagnosis",
                               return_value={"case_id": "case",
                                             "files": [{"bytes": 1}]}):
            return m7_continuous.run_campaign(
                self.frames, self.root / "out", shaders=self.root,
                host=host, stop_on_difference=stop, timeout=timeout,
                frame_timeout=frame_timeout)

    def assert_bounded(self, started: float, seconds: float = 6.0) -> None:
        self.assertLess(time.monotonic() - started, seconds)

    def assert_child_gone(self, pid: int) -> None:
        for _ in range(50):
            try:
                os.kill(pid, 0)
            except OSError:
                return
            time.sleep(0.05)
        self.fail(f"worker {pid} was not cleaned up")

    def test_worker_hanging_before_its_first_result_is_bounded(self):
        host = self.host("time.sleep(60)\n")
        started = time.monotonic()
        report = self.run_campaign(host)
        self.assert_bounded(started)
        self.assertEqual(report["status"], "failed")
        self.assertEqual(report["stop_reason"], "frame_timeout")
        self.assertIn("no frame result", report["error"])
        self.assertEqual(report["frames"], [])
        self.assertEqual(len(report["unexecuted_frames"]), 3)
        pid = int(self.pid_file.read_text())
        self.assert_child_gone(pid)

    def test_worker_hanging_halfway_through_a_sequence_is_bounded(self):
        body = (
            f"sys.stdout.write({(self.row(1) + chr(10))!r})\n"
            "sys.stdout.flush()\n"
            "time.sleep(60)\n")
        host = self.host(body)
        started = time.monotonic()
        report = self.run_campaign(host)
        self.assert_bounded(started)
        self.assertEqual(report["status"], "failed")
        self.assertEqual(report["stop_reason"], "frame_timeout")
        self.assertEqual([row["sequence"] for row in report["frames"]], [1])
        self.assertEqual([row["sequence"] for row in
                          report["unexecuted_frames"]], [2, 3])
        pid = int(self.pid_file.read_text())
        self.assert_child_gone(pid)

    def test_heavy_stderr_cannot_deadlock_the_worker(self):
        template = {
            "sequence": 0, "output_dir": "",
            "ok": True, "events": 1, "encoded": 1, "failed": 0,
            "history_status": 1, "carried_bindings": 0,
            "missing_history": 0, "stale_history": 0,
            "duration_ns": 1_000_000,
        }
        body = (
            "payload = 'x' * 65536\n"
            "for index in range(32):\n"
            "    sys.stderr.write(payload)\n"
            "sys.stderr.flush()\n"
            "for sequence in (1, 2, 3):\n"
            f"    row = {template!r}\n"
            "    row['sequence'] = sequence\n"
            f"    row['output_dir'] = {str(self.root / 'out')!r} + "
            "('/out-%03d' % sequence)\n"
            "    sys.stdout.write(json.dumps(row) + '\\n')\n"
            "    sys.stdout.flush()\n"
            "    time.sleep(0.05)\n")
        host = self.host(body)
        started = time.monotonic()
        report = self.run_campaign(host, timeout=10.0, frame_timeout=2.0,
                                   stop=False)
        self.assert_bounded(started, 8.0)
        self.assertEqual(report["status"], "complete",
                         report.get("stop_reason") or report.get("error"))
        self.assertEqual(len(report["frames"]), 3)
        self.assertLessEqual(len(report["stderr"]), 1_000_000)
        self.assertGreater(len(report["stderr"]), 0)

    def test_cancellation_interrupts_a_silent_worker(self):
        host = self.host("time.sleep(60)\n")
        cancel = threading.Event()
        thread = threading.Timer(0.3, cancel.set)
        thread.start()
        started = time.monotonic()
        report = m7_continuous.run_campaign(
            self.frames, self.root / "out", shaders=self.root,
            host=host, cancel=cancel, timeout=10.0, frame_timeout=5.0)
        thread.cancel()
        self.assert_bounded(started, 6.0)
        self.assertEqual(report["status"], "cancelled")
        pid = int(self.pid_file.read_text())
        self.assert_child_gone(pid)


if __name__ == "__main__":
    unittest.main()

class FaultCampaignTests(SupervisorTests):
    """A selected fault experiment is never a qualified campaign."""

    def diagnoses(self, *, fired, divergent=False, fault_evidence=None):
        base = self.qualified_diagnosis()
        if divergent:
            base = {
                "schema": gfx_independent_replay.DIAGNOSIS_SCHEMA,
                "classification": "qualified_difference",
                "qualification": {
                    "axes": {"gpu_execution": "ok",
                             "pixel_comparison": "divergent"}},
                "pixels": {"status": "divergent",
                           "statistics": {"pixels": 4,
                                          "mismatched_pixels": 4}},
                "checkpoint_validation": {"status": "ok", "divergent": 0},
                "first_divergence": {"kind": "final_only", "event": 4},
                "diagnostic_completed": True,
            }
        evidence = fault_evidence if fault_evidence is not None else {
            "schema": "gfx-fault-evidence/v1",
            "fired": fired,
            "shifted_bindings": 1 if fired else 0,
            "reason": "fired" if fired else "did not fire",
        }
        first = dict(base)
        second = dict(base, fault_evidence=evidence)
        third = dict(base)
        return [first, second, third]

    def test_fault_that_changes_pixels_is_never_qualified(self):
        report, _ = self.run_with(
            self.rows(),
            self.diagnoses(fired=True, divergent=True),
            stop=False,
            fault={"frame": 2, "event": 1, "sampler_shift": 1,
                   "sampler_shift_min": 0})
        self.assertEqual(report["status"], "complete")
        self.assertFalse(report["qualified"])
        self.assertTrue(report["fault_evidence"]["fired"])
        self.assertEqual(report["fault_evidence"]["requested"]["frame"], 2)

    def test_nonexistent_fault_event_does_not_qualify(self):
        report, _ = self.run_with(
            self.rows(),
            self.diagnoses(fired=False),
            stop=False,
            fault={"frame": 2, "event": 99999, "sampler_shift": 1,
                   "sampler_shift_min": 0})
        self.assertEqual(report["status"], "complete")
        self.assertFalse(report["qualified"])
        self.assertFalse(report["fault_evidence"]["fired"])
        self.assertIn("did not fire", report["fault_evidence"]["reason"])

    def test_effective_but_pixel_neutral_fault_does_not_qualify(self):
        report, _ = self.run_with(
            self.rows(),
            self.diagnoses(fired=True),
            stop=False,
            fault={"frame": 2, "event": 1, "sampler_shift": 1,
                   "sampler_shift_min": 0})
        self.assertEqual(report["status"], "complete")
        self.assertFalse(report["qualified"])
        self.assertTrue(report["fault_evidence"]["fired"])
        self.assertTrue(any(record["fired"] for record in
                            report["fault_evidence"]["frames"]))

    def test_fault_free_campaign_still_qualifies(self):
        report, _ = self.run_with(
            self.rows(),
            [self.qualified_diagnosis(),
             self.qualified_diagnosis(),
             self.qualified_diagnosis()],
            stop=False)
        self.assertEqual(report["status"], "complete")
        self.assertTrue(report["qualified"])
        self.assertIsNone(report["fault_injection"])
        self.assertFalse(report["fault_evidence"]["fired"])


class FaultCampaignOutcomeTests(unittest.TestCase):
    def row(self, **overrides):
        value = {
            "sequence": 1,
            "classification": "qualified_match",
            "diagnostic_completed": True,
            "axes": {"capture_validity": "valid",
                     "resource_provenance": "verified",
                     "gpu_execution": "ok",
                     "pixel_comparison": "match"},
            "pixels": {"mismatched_pixels": 0},
            "checkpoint_validation": {"status": "ok", "divergent": 0},
        }
        value.update(overrides)
        return value

    def outcome(self, report):
        from automation.agent_tools import _campaign_outcome
        return _campaign_outcome(report)

    def test_fault_campaign_never_reports_qualified(self):
        report = {
            "status": "complete",
            "qualified": False,
            "fault_injection": {"frame": 2, "event": 1},
            "fault_evidence": {"fired": True},
            "frames": [self.row(sequence=1), self.row(sequence=2)],
        }
        status, diagnostic, verdict, evidence, qualified, details = (
            self.outcome(report))
        self.assertFalse(qualified)
        self.assertFalse(details["qualified"])
        self.assertEqual(details["fault_experiment"]["fired"], True)
        self.assertEqual(verdict, "unverified")

    def test_unfired_fault_is_incomplete_not_qualified(self):
        report = {
            "status": "complete",
            "qualified": False,
            "fault_injection": {"frame": 2, "event": 99999},
            "fault_evidence": {"fired": False},
            "frames": [self.row(sequence=1), self.row(sequence=2)],
        }
        status, diagnostic, verdict, evidence, qualified, details = (
            self.outcome(report))
        self.assertFalse(qualified)
        self.assertEqual(status, "incomplete")
        self.assertEqual(diagnostic, "incomplete")

    def test_fault_with_pixels_is_different_but_not_qualified(self):
        report = {
            "status": "complete",
            "qualified": False,
            "fault_injection": {"frame": 2, "event": 1},
            "fault_evidence": {"fired": True},
            "frames": [
                self.row(sequence=1),
                self.row(sequence=2,
                         classification="qualified_difference",
                         axes={"capture_validity": "valid",
                               "resource_provenance": "verified",
                               "gpu_execution": "ok",
                               "pixel_comparison": "divergent"},
                         pixels={"mismatched_pixels": 4}),
            ],
        }
        status, diagnostic, verdict, evidence, qualified, details = (
            self.outcome(report))
        self.assertFalse(qualified)
        self.assertEqual(verdict, "different")

    def test_fault_free_qualified_campaign_still_qualifies(self):
        report = {
            "status": "complete",
            "qualified": True,
            "fault_injection": None,
            "fault_evidence": {"fired": False},
            "frames": [self.row(sequence=1), self.row(sequence=2)],
        }
        status, diagnostic, verdict, evidence, qualified, details = (
            self.outcome(report))
        self.assertTrue(qualified)
        self.assertEqual(verdict, "equal")
