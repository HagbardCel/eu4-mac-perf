"""Portable Strategy B case verification (offline)."""

from __future__ import annotations

import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from automation import gfx_replay_case  # noqa: E402


class CaseVerificationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name) / "case"
        (self.root / "capture").mkdir(parents=True)
        (self.root / "shaders").mkdir()
        (self.root / "capture" / "frame-trace.json").write_text(
            json.dumps({"schema": "gfx-frame-trace/v1", "complete": True}))
        (self.root / "capture" / "frame-events.jsonl").write_text("")
        (self.root / "shaders" / "shaders.json").write_text("{}")
        (self.root / "report.json").write_text(json.dumps({"status": "partial"}))
        (self.root / "graph.json").write_text(json.dumps({"status": "partial"}))
        files = []
        for path in sorted(self.root.rglob("*")):
            if path.is_file():
                payload = path.read_bytes()
                files.append({
                    "path": path.relative_to(self.root).as_posix(),
                    "bytes": len(payload),
                    "sha256": hashlib.sha256(payload).hexdigest(),
                })
        (self.root / "case.json").write_text(json.dumps({
            "schema": gfx_replay_case.SCHEMA,
            "case_id": "synthetic",
            "files": files,
            "replay_status": "partial",
        }))

    def tearDown(self):
        self.temporary.cleanup()

    def test_valid_case_verifies(self):
        value = gfx_replay_case.verify(self.root)
        self.assertTrue(value["ok"], value["problems"])
        self.assertEqual(value["graph_status"], "partial")

    def test_tampered_payload_is_rejected(self):
        (self.root / "report.json").write_text(
            json.dumps({"status": "PARTIAL"}))
        value = gfx_replay_case.verify(self.root)
        self.assertFalse(value["ok"])
        self.assertTrue(any("digest drift" in problem
                            for problem in value["problems"]))

    def test_missing_required_file_is_rejected(self):
        (self.root / "shaders" / "shaders.json").unlink()
        value = gfx_replay_case.verify(self.root)
        self.assertFalse(value["ok"])
        self.assertTrue(any("missing" in problem for problem in value["problems"]))

    def test_replay_refuses_an_invalid_case(self):
        (self.root / "graph.json").write_text("not json")
        with self.assertRaises(gfx_replay_case.CaseError) as caught:
            gfx_replay_case.replay_case(self.root, self.root.parent / "out")
        self.assertEqual(caught.exception.kind, "verification")


if __name__ == "__main__":
    unittest.main()
