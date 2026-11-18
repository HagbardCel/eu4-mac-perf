"""Deterministic P2 workflow: compact evidence, reuse, bounded narrowing.

This is an offline integration test of the *interface* an autonomous agent
uses.  It does not execute Metal, start EU4 or read a historical `.local` case.
The authentic-case demonstration lives in `analysis/m7/p2-efficiency.md`.
"""

from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from automation import agent_tools, case_baseline, gfx_replay_case


class P2WorkflowTests(unittest.TestCase):
    def _case(self, root: Path) -> Path:
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
            "event_stream_sha256": "a" * 64,
            "present_index": 617, "scene_index": 617,
        }).encode())
        add("capture/frame-trace.json", json.dumps({"complete": True}).encode())
        add("capture/frame-events.jsonl", b"")
        add("shaders/shaders.json", b"{}")
        add("replay/replay.json", json.dumps({
            "status": "ok", "stage": "complete", "readback": True,
            "events": 3, "encoded": 3}).encode())
        add("replay/metal-frame.bgra", b"\0" * 16)
        add("diagnostic-report.json", json.dumps({
            "schema": "gfx-frame-diagnosis/v1",
            "classification": "difference_with_partial_state_coverage",
            "qualification": {"qualified": False,
                              "axes": {"pixel_comparison": "divergent"}},
            "pixels": {"final": {"status": "divergent",
                                 "statistics": {"mismatched_pixels": 4,
                                                "max_channel_error": 2}}},
            "first_divergence": {
                "kind": "intermediate", "event": 142, "resource": "fb:0",
                "target": "0",
                "unresolved_interval": {
                    "start_event": None, "end_event": 142,
                    "lower_bound": "unbounded", "search_start_event": 0},
                "next_checkpoint": {
                    "event": 71, "resource": "fb:0", "target": "0",
                    "requires_capture": True,
                    "reason": "bisect the unbounded interval"},
            },
            "checkpoints": [{
                "event": 142, "resource": "fb:0", "target": "0",
                "status": "divergent", "authoritative": True,
                "statistics": {"mismatched_pixels": 4,
                               "max_channel_error": 2},
            }],
        }).encode())
        add("dependency-graph.json", json.dumps({"status": "complete"}).encode())
        add("graph.json", json.dumps({"status": "complete"}).encode())
        add("case.json", json.dumps({
            "schema": "gfx-strategy-b-case/v1", "case_id": "p2-case",
            "diagnostic_report": "diagnostic-report.json",
            "graph": "graph.json", "files": files,
        }).encode())
        return case

    def _verification(self) -> dict:
        return {
            "schema": "gfx-strategy-b-case-verification/v1",
            "case_id": "p2-case", "ok": True, "problems": [],
            "files": 7, "graph_status": "complete",
            "independently_reproducible": True,
        }

    def test_compact_case_read_is_under_the_cap_and_actionable(self):
        with tempfile.TemporaryDirectory() as temporary:
            case = self._case(Path(temporary))
            with mock.patch.object(agent_tools.gfx_replay_case, "verify",
                                   return_value=self._verification()):
                value = agent_tools.eu4_case_inspect(
                    agent_tools._parser().parse_args(
                        ["eu4_case_inspect", "--case", str(case)]))
        encoded = agent_tools._emit_text(value).encode("utf-8")
        self.assertLessEqual(len(encoded), agent_tools.COMPACT_OUTPUT_CAP)
        diagnosis = value["result"]["diagnosis"]
        self.assertEqual(diagnosis["first_divergence"]["event"], 142)
        self.assertIsNone(
            diagnosis["first_divergence"]["unresolved_interval"]["start_event"])
        self.assertEqual(
            diagnosis["first_divergence"]["next_checkpoint"]["event"], 71)
        self.assertTrue(
            diagnosis["first_divergence"]["next_checkpoint"]
            ["requires_capture"])
        self.assertEqual(value["result"]["capture"]["session_id"],
                         "session-a")

    def test_existing_baseline_is_reused_without_a_new_replay(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            case = self._case(root)
            baseline = root / "baseline.json"
            case_baseline.record(case, baseline)
            checked = case_baseline.load_check(case, baseline)
        self.assertTrue(checked["reusable"], checked)
        self.assertFalse(checked["baseline"]["result"]["qualified"])

    def test_changed_evidence_invalidates_the_baseline(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            case = self._case(root)
            baseline = root / "baseline.json"
            case_baseline.record(case, baseline)
            (case / "replay" / "metal-frame.bgra").write_bytes(b"\1" * 16)
            with self.assertRaises(case_baseline.BaselineError):
                case_baseline.load_check(case, baseline)

    def test_synthetic_substitution_is_not_accepted_by_the_case_verifier(self):
        with tempfile.TemporaryDirectory() as temporary:
            case = self._case(Path(temporary))
            (case / "replay" / "metal-frame.bgra").write_bytes(b"\2" * 16)
            value = gfx_replay_case.verify(case)
        self.assertFalse(value["ok"])
        self.assertTrue(any("digest drift" in problem
                            for problem in value["problems"]), value)


if __name__ == "__main__":
    unittest.main()
