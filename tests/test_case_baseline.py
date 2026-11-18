"""Identity-bound baseline reuse (M7-P2 B2)."""

from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock
from automation import case_baseline


def _write_case(root: Path) -> Path:
    case = root / "case"
    (case / "capture").mkdir(parents=True)
    (case / "replay").mkdir()
    (case / "shaders").mkdir()
    files = []

    def add(relative: str, payload: bytes) -> None:
        path = case / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(payload)
        files.append({"path": relative, "bytes": len(payload),
                      "sha256": hashlib.sha256(payload).hexdigest()})

    add("capture/frame-seal.json", json.dumps({
        "session_id": "session-a", "capture_frame_id": 1,
        "event_stream_sha256": "a" * 64, "present_index": 5,
        "scene_index": 5,
    }).encode())
    add("capture/frame-trace.json", json.dumps({"complete": True}).encode())
    add("capture/frame-events.jsonl", b"")
    add("shaders/shaders.json", b"{}")
    add("shaders/program.metal", b"kernel")
    add("replay/replay.json", json.dumps({
        "status": "ok", "stage": "complete", "readback": True,
        "events": 3, "encoded": 3}).encode())
    add("replay/metal-frame.bgra", b"\0" * 16)
    add("diagnostic-report.json", json.dumps({
        "schema": "gfx-frame-diagnosis/v1",
        "classification": "difference_with_partial_state_coverage",
        "qualification": {"qualified": False, "diagnostic_qualified": True},
        "pixels": {"final": {"statistics": {"mismatched_pixels": 4,
                                            "max_channel_error": 2}}},
        "first_divergence": {"event": 2, "resource": "fb:0", "target": "0",
                             "producer_event": 1, "confidence": "confirmed"},
    }).encode())
    add("dependency-graph.json", json.dumps({"status": "complete"}).encode())
    add("graph.json", json.dumps({"status": "complete"}).encode())
    add("case.json", json.dumps({
        "schema": "gfx-strategy-b-case/v1", "case_id": "identity-case",
        "diagnostic_report": "diagnostic-report.json", "graph": "graph.json",
        "files": files,
    }).encode())
    return case


class BaselineIdentityTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.case = _write_case(self.root)
        self.baseline = self.root / "baseline.json"

    def tearDown(self):
        self.temporary.cleanup()

    def test_record_and_check_reuses_the_same_identity(self):
        recorded = case_baseline.record(self.case, self.baseline)
        checked = case_baseline.load_check(self.case, self.baseline)
        self.assertTrue(checked["ok"], checked)
        self.assertTrue(checked["reusable"])
        self.assertEqual(checked["current_fingerprint"],
                         recorded["identity"]["fingerprint"])
        self.assertEqual(recorded["result"]["first_divergence"]["event"], 2)
        self.assertEqual(recorded["identity"]["validity"]["payload_validation"],
                         "ok")
        self.assertEqual(recorded["identity"]["validity"]["replay_status"],
                         "ok")

    def test_edited_shader_payload_without_manifest_change_is_rejected(self):
        case_baseline.record(self.case, self.baseline)
        (self.case / "shaders" / "program.metal").write_bytes(b"changed")
        with self.assertRaises(case_baseline.BaselineError) as caught:
            case_baseline.load_check(self.case, self.baseline)
        self.assertEqual(caught.exception.kind, "payload_validation")
        self.assertIn("program.metal", caught.exception.detail)

    def test_deleted_declared_payload_is_rejected(self):
        case_baseline.record(self.case, self.baseline)
        (self.case / "shaders" / "program.metal").unlink()
        with self.assertRaises(case_baseline.BaselineError):
            case_baseline.load_check(self.case, self.baseline)

    def test_corrupt_replay_readback_is_rejected(self):
        case_baseline.record(self.case, self.baseline)
        (self.case / "replay" / "metal-frame.bgra").write_bytes(b"\1" * 16)
        with self.assertRaises(case_baseline.BaselineError):
            case_baseline.load_check(self.case, self.baseline)

    def test_changed_replay_status_invalidates_the_baseline(self):
        case_baseline.record(self.case, self.baseline)
        result = json.loads((self.case / "replay" / "replay.json").read_text())
        result["status"] = "partial"
        (self.case / "replay" / "replay.json").write_bytes(
            json.dumps(result).encode())
        manifest = json.loads((self.case / "case.json").read_text())
        for item in manifest["files"]:
            if item["path"] == "replay/replay.json":
                payload = (self.case / "replay" / "replay.json").read_bytes()
                item["bytes"] = len(payload)
                item["sha256"] = hashlib.sha256(payload).hexdigest()
        (self.case / "case.json").write_text(json.dumps(manifest))
        with self.assertRaises(case_baseline.BaselineError) as caught:
            case_baseline.load_check(self.case, self.baseline)
        self.assertEqual(caught.exception.kind, "replay_incomplete")

    def test_modifying_renderer_semantics_invalidates_reuse(self):
        case_baseline.record(self.case, self.baseline)
        baseline = json.loads(self.baseline.read_text())
        original = case_baseline.identity(self.case)
        changed = dict(original)
        sources = dict(original["renderer_sources"])
        self.assertIn("benchmark/eu4_gfx_replay_semantics.h", sources)
        sources["benchmark/eu4_gfx_replay_semantics.h"] = "f" * 64
        changed["renderer_sources"] = sources
        changed["fingerprint"] = hashlib.sha256(json.dumps(
            {key: value for key, value in changed.items()
             if key != "fingerprint"},
            sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        checked = case_baseline.evaluate(changed, baseline)
        self.assertFalse(checked["reusable"])
        self.assertTrue(any(problem["code"] == "baseline_mismatch"
                            for problem in checked["problems"]))

    def test_renderer_closure_tracks_a_semantics_header_change(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary).resolve()
            bench = root / "benchmark"
            bench.mkdir()
            semantics = bench / "eu4_gfx_replay_semantics.h"
            semantics.write_text("first semantics")
            (bench / "root.mm").write_text('#include "eu4_gfx_replay_semantics.h"\n')
            with mock.patch.object(case_baseline, "ROOT", root), \
                 mock.patch.object(case_baseline, "BENCH", bench):
                first = case_baseline._renderer_closure(
                    ("benchmark/root.mm",))
                semantics.write_text("changed semantics")
                second = case_baseline._renderer_closure(
                    ("benchmark/root.mm",))
        self.assertIn("benchmark/eu4_gfx_replay_semantics.h", first)
        self.assertNotEqual(first, second)

    def test_changing_required_build_dependency_invalidates_reuse(self):
        case_baseline.record(self.case, self.baseline)
        baseline = json.loads(self.baseline.read_text())
        current = case_baseline.identity(self.case)
        sources = dict(current["implementation_sources"])
        self.assertIn("automation/gfx_independent_replay.py", sources)
        sources["automation/gfx_independent_replay.py"] = "e" * 64
        current["implementation_sources"] = sources
        current["fingerprint"] = hashlib.sha256(json.dumps(
            {key: value for key, value in current.items()
             if key != "fingerprint"},
            sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        checked = case_baseline.evaluate(current, baseline)
        self.assertFalse(checked["reusable"])

    def test_changing_policy_invalidates_reuse(self):
        case_baseline.record(self.case, self.baseline)
        other_policy = self.root / "other-policy.json"
        other_policy.write_text('{"policy": "different"}')
        checked = case_baseline.load_check(
            self.case, self.baseline, policy=other_policy)
        self.assertFalse(checked["reusable"])
        self.assertTrue(any(problem["code"] == "baseline_mismatch"
                            for problem in checked["problems"]))

    def test_a_different_backend_never_reuses(self):
        case_baseline.record(self.case, self.baseline)
        checked = case_baseline.load_check(self.case, self.baseline,
                                           backend="opengl")
        self.assertFalse(checked["reusable"])
        self.assertTrue(any(problem["code"] == "baseline_backend"
                            for problem in checked["problems"]))

    def test_invalid_case_cannot_be_recorded(self):
        (self.case / "graph.json").unlink()
        with self.assertRaises(case_baseline.BaselineError):
            case_baseline.record(self.case, self.baseline)
        self.assertFalse(self.baseline.exists())

    def test_fingerprint_match_is_not_enough_without_validity(self):
        case_baseline.record(self.case, self.baseline)
        baseline = json.loads(self.baseline.read_text())
        current = case_baseline.identity(self.case)
        current["validity"] = dict(current["validity"], verified=False)
        checked = case_baseline.evaluate(current, baseline)
        self.assertFalse(checked["reusable"])
        self.assertTrue(any(problem["code"] == "current_not_verified"
                            for problem in checked["problems"]))

    def test_stored_baseline_without_validity_is_not_reusable(self):
        case_baseline.record(self.case, self.baseline)
        baseline = json.loads(self.baseline.read_text())
        baseline["identity"]["validity"]["verified"] = False
        current = case_baseline.identity(self.case)
        checked = case_baseline.evaluate(current, baseline)
        self.assertFalse(checked["reusable"])
        self.assertTrue(any(problem["code"] == "baseline_not_verified"
                            for problem in checked["problems"]))

    def test_missing_baseline_is_refused(self):
        with self.assertRaises(case_baseline.BaselineError):
            case_baseline.load_check(self.case, self.root / "absent.json")

    def test_baseline_is_not_claimed_as_a_qualified_match(self):
        recorded = case_baseline.record(self.case, self.baseline)
        self.assertFalse(recorded["result"]["qualified"])
        self.assertTrue(recorded["result"]["diagnostic_qualified"])


if __name__ == "__main__":
    unittest.main()
