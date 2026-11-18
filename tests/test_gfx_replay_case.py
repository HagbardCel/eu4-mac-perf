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

    def _manifest(self) -> dict:
        return json.loads((self.root / "case.json").read_text())

    def _write_manifest(self, manifest: dict) -> None:
        (self.root / "case.json").write_text(json.dumps(manifest))

    def test_duplicate_manifest_entries_are_rejected(self):
        manifest = self._manifest()
        manifest["files"].append(dict(manifest["files"][0]))
        self._write_manifest(manifest)
        value = gfx_replay_case.verify(self.root)
        self.assertFalse(value["ok"])
        self.assertTrue(any("duplicate manifest path" in problem
                            for problem in value["problems"]), value)

    def test_absolute_manifest_path_is_rejected(self):
        outside = self.root.parent / "outside.txt"
        outside.write_text("outside")
        payload = outside.read_bytes()
        manifest = self._manifest()
        manifest["files"].append({
            "path": str(outside), "bytes": len(payload),
            "sha256": hashlib.sha256(payload).hexdigest()})
        self._write_manifest(manifest)
        value = gfx_replay_case.verify(self.root)
        self.assertFalse(value["ok"])
        self.assertTrue(any("nonportable manifest path" in problem
                            for problem in value["problems"]), value)

    def test_parent_traversal_manifest_path_is_rejected(self):
        outside = self.root.parent / "outside.txt"
        outside.write_text("outside")
        payload = outside.read_bytes()
        manifest = self._manifest()
        manifest["files"].append({
            "path": "../outside.txt", "bytes": len(payload),
            "sha256": hashlib.sha256(payload).hexdigest()})
        self._write_manifest(manifest)
        value = gfx_replay_case.verify(self.root)
        self.assertFalse(value["ok"])
        self.assertTrue(any("nonportable manifest path" in problem
                            for problem in value["problems"]), value)

    def test_symlink_escape_is_rejected(self):
        outside = self.root.parent / "outside.txt"
        outside.write_text("outside")
        link = self.root / "escape.txt"
        link.symlink_to(outside)
        payload = outside.read_bytes()
        manifest = self._manifest()
        manifest["files"].append({
            "path": "escape.txt", "bytes": len(payload),
            "sha256": hashlib.sha256(payload).hexdigest()})
        self._write_manifest(manifest)
        value = gfx_replay_case.verify(self.root)
        self.assertFalse(value["ok"])
        self.assertTrue(any("escapes the case directory" in problem
                            for problem in value["problems"]), value)

    def test_replay_refuses_an_invalid_case(self):
        (self.root / "graph.json").write_text("not json")
        with self.assertRaises(gfx_replay_case.CaseError) as caught:
            gfx_replay_case.replay_case(self.root, self.root.parent / "out")
        self.assertEqual(caught.exception.kind, "verification")


class FaultExperimentPackagingTests(unittest.TestCase):
    def test_fault_experiment_is_not_published_as_reproducible(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            metal = root / "metal"
            metal.mkdir()
            report = {
                "schema": "gfx-frame-diagnosis/v1",
                "classification": "fault_experiment",
                "faults": {"frame": 2, "event": 1,
                           "sampler_shift": 1},
                "fault_evidence": {"fired": True},
                "qualification": {"qualified": False},
                "first_divergence": {"kind": "semantic", "event": 1},
                "graph": {"schema": "gfx-frame-graph/v1",
                          "status": "complete", "reasons": [],
                          "events": [], "resources": {}},
            }
            manifest = gfx_replay_case.package_sequence_diagnosis(
                [], metal, root / "case", report,
                case_id="fault-case", max_bytes=1024 * 1024)
            reproduction = manifest["reproduction"]
            self.assertEqual(reproduction["status"], "incomplete")
            self.assertEqual(reproduction["reason"],
                             "fault_experiment_not_portable")
            self.assertFalse(reproduction["independently_reproducible"])
            self.assertEqual(manifest["replay_command"], [])

    def test_single_frame_fault_experiment_is_not_published(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            metal = root / "metal"
            metal.mkdir()
            frames = root / "frame"
            frames.mkdir()
            report = {
                "schema": "gfx-frame-diagnosis/v1",
                "classification": "fault_experiment",
                "faults": {"frame": 2, "event": 1,
                           "sampler_shift": 1},
                "fault_evidence": {"fired": True},
                "qualification": {"qualified": False},
                "first_divergence": {"kind": "semantic", "event": 1},
                "graph": {"schema": "gfx-frame-graph/v1",
                          "status": "complete", "reasons": [],
                          "events": [], "resources": {}},
            }
            manifest = gfx_replay_case.package_diagnosis(
                frames, metal, root / "case", report,
                case_id="fault-single", max_bytes=1024 * 1024)
            reproduction = manifest["reproduction"]
            self.assertEqual(reproduction["status"], "incomplete")
            self.assertEqual(reproduction["reason"],
                             "fault_experiment_not_portable")
            self.assertFalse(reproduction["independently_reproducible"])
            self.assertEqual(manifest["replay_command"], [])


if __name__ == "__main__":
    unittest.main()
