"""Identity-bound baseline reuse (M7-P2 B2)."""

from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

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
    add("shaders/shaders.json", b"{}")
    add("shaders/program.metal", b"kernel")
    add("replay/replay.json", json.dumps({
        "status": "ok", "events": 3, "encoded": 3}).encode())
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

    def test_edited_payload_invalidates_the_baseline(self):
        case_baseline.record(self.case, self.baseline)
        (self.case / "replay" / "metal-frame.bgra").write_bytes(b"\1" * 16)
        checked = case_baseline.load_check(self.case, self.baseline)
        self.assertFalse(checked["ok"])
        self.assertFalse(checked["reusable"])
        self.assertTrue(any(problem["code"] == "baseline_mismatch"
                            for problem in checked["problems"]))

    def test_changed_shader_inventory_invalidates_the_baseline(self):
        case_baseline.record(self.case, self.baseline)
        manifest = json.loads((self.case / "case.json").read_text())
        for item in manifest["files"]:
            if item["path"] == "shaders/program.metal":
                item["sha256"] = "0" * 64
        (self.case / "case.json").write_text(json.dumps(manifest))
        checked = case_baseline.load_check(self.case, self.baseline)
        self.assertFalse(checked["reusable"])

    def test_a_different_backend_never_reuses(self):
        case_baseline.record(self.case, self.baseline)
        checked = case_baseline.load_check(self.case, self.baseline,
                                           backend="opengl")
        self.assertFalse(checked["reusable"])
        self.assertTrue(any(problem["code"] == "baseline_backend"
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
