"""Checkpoint reconciliation and qualification regressions."""
from __future__ import annotations

import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from automation import gfx_independent_replay  # noqa: E402


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class CheckpointFixture:
    def __init__(self, root: Path):
        self.root = root
        self.frame = root / "frame"
        self.out = root / "out"
        (self.frame / "blobs").mkdir(parents=True)
        self.out.mkdir()
        self.width = 4
        self.height = 4
        self.reference = bytes(range(self.width * self.height * 4))
        self.ref_name = "tex-ref.bin"
        (self.frame / "blobs" / self.ref_name).write_bytes(self.reference)
        self.metal_name = "intermediate-event5-tex10.bgra"
        (self.out / self.metal_name).write_bytes(self.reference)
        self.graph = {
            "resources": {"tex:10": {"format": "RGBA8"}},
            "events": [],
        }
        self.request = {
            "schema": "gfx-frame-checkpoint-request/v1",
            "checkpoints": [{
                "event": 5, "target": "10", "resource": "tex:10",
                "file": self.metal_name, "reference_file": self.ref_name,
                "reference_sha256": sha(self.reference),
                "reference_width": self.width,
                "reference_height": self.height,
                "producer_events": [1],
            }],
        }
        self.replay = {
            "checkpoints": [{
                "event": 5, "target": "10", "ok": True,
                "file": self.metal_name, "reference_file": self.ref_name,
                "reference_sha256": sha(self.reference),
                "width": self.width, "height": self.height,
                "format": "RGBA8", "generation": 1,
            }],
        }

    def reconcile(self):
        return gfx_independent_replay.reconcile_checkpoints(
            self.graph, self.request, self.replay, self.frame, self.out)


class ReconcileTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.fixture = CheckpointFixture(Path(self.temporary.name))

    def tearDown(self):
        self.temporary.cleanup()

    def test_all_requested_checkpoints_match(self):
        results, validation = self.fixture.reconcile()
        self.assertEqual(validation["status"], "ok", validation)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["status"], "match")
        self.assertEqual(validation["matched"], 1)

    def test_missing_result_is_invalid(self):
        self.fixture.replay["checkpoints"] = []
        results, validation = self.fixture.reconcile()
        self.assertEqual(validation["status"], "invalid")
        self.assertIn("missing_result",
                      [item["kind"] for item in validation["problems"]])
        self.assertEqual(results[0]["status"], "invalid")

    def test_failed_readback_is_invalid(self):
        self.fixture.replay["checkpoints"][0]["ok"] = False
        self.fixture.replay["checkpoints"][0]["error"] = "checkpoint readback failed"
        results, validation = self.fixture.reconcile()
        self.assertEqual(validation["status"], "invalid")
        self.assertEqual(results[0]["status"], "invalid")
        self.assertIn("readback", results[0]["reason"])

    def test_missing_output_file_is_invalid(self):
        (self.fixture.out / self.fixture.metal_name).unlink()
        results, validation = self.fixture.reconcile()
        self.assertEqual(validation["status"], "invalid")
        self.assertEqual(results[0]["status"], "invalid")

    def test_missing_reference_is_invalid(self):
        (self.fixture.frame / "blobs" / self.fixture.ref_name).unlink()
        results, validation = self.fixture.reconcile()
        self.assertEqual(validation["status"], "invalid")
        self.assertEqual(results[0]["status"], "invalid")

    def test_corrupted_reference_is_invalid(self):
        path = self.fixture.frame / "blobs" / self.fixture.ref_name
        path.write_bytes(b"\x00" * path.stat().st_size)
        results, validation = self.fixture.reconcile()
        self.assertEqual(validation["status"], "invalid")
        self.assertIn("sha256", results[0]["reason"])

    def test_duplicate_result_is_invalid(self):
        self.fixture.replay["checkpoints"].append(
            json.loads(json.dumps(self.fixture.replay["checkpoints"][0])))
        results, validation = self.fixture.reconcile()
        self.assertEqual(validation["status"], "invalid")
        self.assertIn("duplicate_result",
                      [item["kind"] for item in validation["problems"]])
        self.assertEqual(results[0]["status"], "invalid")

    def test_unexpected_result_is_invalid(self):
        extra = json.loads(json.dumps(self.fixture.replay["checkpoints"][0]))
        extra["event"] = 99
        self.fixture.replay["checkpoints"].append(extra)
        _, validation = self.fixture.reconcile()
        self.assertEqual(validation["status"], "invalid")
        self.assertIn("unexpected_result",
                      [item["kind"] for item in validation["problems"]])

    def test_valid_diagnostic_difference_is_not_authoritative(self):
        self.fixture.graph["resources"]["tex:10"]["format"] = "RGB8"
        self.fixture.replay["checkpoints"][0]["format"] = "RGB8"
        metal = bytearray(self.fixture.reference)
        metal[0] = (metal[0] + 7) % 256
        (self.fixture.out / self.fixture.metal_name).write_bytes(bytes(metal))
        results, validation = self.fixture.reconcile()
        self.assertEqual(validation["status"], "ok", validation)
        self.assertEqual(results[0]["status"], "divergent_diagnostic")
        self.assertFalse(results[0]["authoritative"])
        self.assertEqual(validation["diagnostic"], 1)

    def test_capture_format_cannot_be_downgraded_by_replay(self):
        self.fixture.replay["checkpoints"][0]["format"] = "RGB8"
        results, validation = self.fixture.reconcile()
        self.assertEqual(validation["status"], "invalid")
        self.assertEqual(results[0]["status"], "invalid")
        self.assertIn("format", results[0]["reason"])

    def test_rgb8_capture_keeps_the_documented_diagnostic_case(self):
        self.fixture.graph["resources"]["tex:10"]["format"] = "RGB8"
        self.fixture.replay["checkpoints"][0]["format"] = "RGB8"
        swapped = bytearray(self.fixture.reference)
        for offset in range(0, len(swapped), 4):
            swapped[offset], swapped[offset + 2] = (
                swapped[offset + 2], swapped[offset])
        (self.fixture.out / self.fixture.metal_name).write_bytes(
            bytes(swapped))
        results, validation = self.fixture.reconcile()
        self.assertEqual(validation["status"], "ok", validation)
        self.assertEqual(results[0]["status"], "match")
        self.assertFalse(results[0]["authoritative"])

    def test_conflicting_returned_reference_file_is_invalid(self):
        self.fixture.replay["checkpoints"][0]["reference_file"] = "other.bin"
        results, validation = self.fixture.reconcile()
        self.assertEqual(validation["status"], "invalid")
        self.assertEqual(results[0]["status"], "invalid")
        self.assertIn("reference file", results[0]["reason"])

    def test_authoritative_divergence_is_divergent(self):
        metal = bytearray(self.fixture.reference)
        metal[0] = (metal[0] + 7) % 256
        (self.fixture.out / self.fixture.metal_name).write_bytes(bytes(metal))
        results, validation = self.fixture.reconcile()
        self.assertEqual(validation["status"], "ok")
        self.assertEqual(results[0]["status"], "divergent")
        self.assertTrue(results[0]["authoritative"])


class CliExitContractTests(unittest.TestCase):
    def test_invalid_checkpoint_cli_returns_failure(self):
        import contextlib
        import io
        from unittest import mock

        report = {
            "status": "invalid",
            "outcome": {"execution": "ok", "checkpoints": "invalid",
                        "qualified": False},
            "checkpoint_validation": {"status": "invalid",
                                      "problems": [{"kind": "missing_result"}]},
            "checkpoints": [], "graph": {}, "evaluation": {},
            "binding_audit": {}, "final": {}, "first_divergence": {},
        }
        with tempfile.TemporaryDirectory() as temporary:
            out = Path(temporary) / "out"
            rationale = Path(temporary) / "rationale.json"
            rationale.write_text(json.dumps({
                "question": "does the checkpoint bind?",
                "evidence": [temporary],
                "gap": "checkpoint binding was not checked",
                "hypothesis": "an invalid checkpoint blocks qualification",
                "action": "CLI replay",
                "expected_information": "exit code and status",
                "cheaper_alternatives_checked": True,
            }))
            with mock.patch.object(gfx_independent_replay, "replay",
                                   return_value=report), \
                 contextlib.redirect_stdout(io.StringIO()):
                code = gfx_independent_replay.main([
                    "--frame", str(Path(temporary) / "frame"),
                    "--out", str(out),
                    "--rationale", str(rationale),
                ])
        self.assertEqual(code, 1)


class QualificationClassificationTests(unittest.TestCase):
    def test_canonical_diagnosis_and_exit_code(self):
        exact = {
            "capture_validity": "valid", "identity": "verified",
            "execution": "ok", "provenance": "verified",
            "operation_coverage": "complete", "state_cov": "complete",
            "pixels": "match", "divergence": "none",
            "checkpoint_validation": {"status": "ok"},
        }
        for updates, expected, code in (
            ({}, "qualified_match", 0),
            ({"checkpoint_validation": {"status": "invalid"}}, "invalid_provenance", 1),
            ({"operation_coverage": "unknown"}, "incomplete_capture", 1),
            ({"pixels": "invalid"}, "incomplete_diagnostics", 1),
            ({"pixels": "unknown"}, "incomplete_diagnostics", 1),
            ({"state_cov": "invalid"}, "unsupported_state", 1),
            ({"state_cov": "partial"}, "unsupported_state", 2),
            ({"divergence": "diagnostic_only"}, "qualified_match", 0),
            ({"divergence": "intermediate"}, "qualified_difference", 2),
            ({"pixels": "divergent", "state_cov": "partial"},
             "difference_with_partial_state_coverage", 2),
            ({"pixels": "divergent", "identity": "unverified_session"},
             "incomplete_diagnostics", 2),
            ({"identity": "unverified_session"}, "incomplete_diagnostics", 2),
            ({"fault_experiment": True}, "fault_experiment", 3),
            ({"fault_experiment": True, "pixels": "divergent"}, "fault_experiment", 3),
            ({"fault_experiment": True, "checkpoint_validation": {"status": "invalid"}},
             "invalid_provenance", 1),
        ):
            with self.subTest(updates=updates):
                classification, qualification = gfx_independent_replay.classify_diagnosis(
                    **{**exact, **updates})
                report = {"classification": classification, "qualification": qualification}
                self.assertEqual(classification, expected)
                self.assertIs(qualification["qualified"], code == 0)
                self.assertEqual(gfx_independent_replay.qualification_exit_code(report), code)


if __name__ == "__main__":
    unittest.main()
