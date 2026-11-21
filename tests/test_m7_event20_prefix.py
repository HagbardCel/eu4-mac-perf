"""Adversarial regressions for exact-prefix event-20 qualification.

The native scenario writer produces a genuine sealed 21-event frame (no GL
context, TEST_ONLY readback) and the tests build a matching Metal replay
fixture.  Every verdict is then recomputed by the independent checker from the
original reference bytes, never from the replay's own counters.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from automation import (  # noqa: E402
    gfx_frame_graph, gfx_prefix_check, m6_frame, native_build)
BENCH = ROOT / "benchmark"
CHECKER = ROOT / "campaigns" / "eu4-m7-event20" / "accept.py"


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


@unittest.skipUnless(sys.platform == "darwin", "native trace host is macOS")
class Event20PrefixTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        cls.binary = Path(cls.temporary.name) / "gfx_frame_trace_scenarios"
        sources = [ROOT / "tests" / "gfx_frame_trace_scenarios.c",
                   BENCH / "eu4_gfx_frame_trace.c"]
        native_build.require_build(
            ["clang", "-O1", "-Wall", "-Wextra", "-I", str(BENCH), "-o",
             str(cls.binary), *map(str, sources), "-framework", "OpenGL"],
            label="event20-prefix-scenarios", sources=sources,
            frameworks=("OpenGL",), require_arm64=False)

    @classmethod
    def tearDownClass(cls):
        cls.temporary.cleanup()

    def _sealed_frame(self, directory: Path) -> None:
        result = subprocess.run(
            [str(self.binary), "prefix-seal", str(directory)],
            capture_output=True, text=True, check=False)
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)

    def _metal_fixture(self, frame: Path, metal: Path) -> dict:
        seal = json.loads((frame / "frame-seal.json").read_text())
        graph = gfx_frame_graph.build(frame)
        self.assertEqual(graph["prefix"]["status"], "complete")
        request = gfx_frame_graph.checkpoint_request(graph)
        metal.mkdir(parents=True, exist_ok=True)
        request_path = metal / "checkpoint-request.json"
        request_path.write_text(json.dumps(request, indent=2) + "\n")
        request_sha = _sha(request_path)
        receipt = {
            "schema": "m7-shadow-shader-receipt/v1",
            "capture_frame_id": seal["capture_frame_id"],
            "pid": seal["pid"],
            "session_id": seal["session_id"],
            "trace_manifest_sha256": seal["manifest_sha256"],
            "event_stream_sha256": seal["event_stream_sha256"],
            "checkpoint_request_file": request_path.name,
            "checkpoint_request_sha256": request_sha,
            "checkpoint_count": len(request["checkpoints"]),
        }
        (metal / "shader-receipt.json").write_text(
            json.dumps(receipt, indent=2) + "\n")
        results = []
        for checkpoint in request["checkpoints"]:
            if checkpoint.get("timing") != "post_event":
                continue
            source = frame / "blobs" / checkpoint["reference_file"]
            shutil.copyfile(source, metal / checkpoint["file"])
            results.append({
                "event": checkpoint["event"],
                "target": checkpoint["target"],
                "resource": checkpoint["resource"],
                "timing": "post_event",
                "file": checkpoint["file"],
                "reference_file": checkpoint["reference_file"],
                "reference_sha256": checkpoint["reference_sha256"],
                "ok": True,
                "width": checkpoint["reference_width"],
                "height": checkpoint["reference_height"],
                "format": "BGRA8",
                "pixel_format": "BGRA8",
                "generation": checkpoint["event"] + 1,
            })
        replay = {
            "schema": "gfx-metal-frame-replay/v1",
            "status": "ok",
            "events": 21,
            "encoded": 21,
            "failed": 0,
            "checkpoint_request_file": request_path.name,
            "checkpoint_request_sha256": request_sha,
            "checkpoints": results,
        }
        (metal / "replay.json").write_text(
            json.dumps(replay, indent=2) + "\n")
        return request

    def _evaluate(self, frame: Path, metal: Path) -> dict:
        return gfx_prefix_check.evaluate(frame, metal, require_end=20)

    def _fixture(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        base = Path(temporary.name)
        frame = base / "frame"
        frame.mkdir()
        self._sealed_frame(frame)
        metal = base / "shadow"
        self._metal_fixture(frame, metal)
        return frame, metal

    def _event_file(self, metal: Path, event: int) -> Path:
        matches = sorted(metal.glob(f"prefix-event{event}-*.bgra"))
        self.assertEqual(len(matches), 1)
        return matches[0]

    def _mutate_first_byte(self, path: Path) -> None:
        data = bytearray(path.read_bytes())
        data[0] = (data[0] + 1) & 0xFF
        path.write_bytes(bytes(data))

    def test_exact_prefix_qualifies_through_event_20(self):
        frame, metal = self._fixture()
        result = self._evaluate(frame, metal)
        self.assertEqual(result["verdict"], "EVENT20_EXACT", result["problems"])
        self.assertEqual(result["verified_prefix"], {"start": 0, "end": 20})
        self.assertEqual(len(result["events"]), 21)
        for entry in result["events"]:
            self.assertEqual(entry["status"], "match")
            self.assertEqual(entry["independent"]["mismatched_pixels"], 0)
            self.assertEqual(entry["independent"]["max_channel_error"], 0)
            self.assertEqual(entry["independent"]["alpha_different_pixels"], 0)

    def test_request_covers_every_event_with_its_own_resource(self):
        frame, metal = self._fixture()
        graph = gfx_frame_graph.build(frame)
        request = gfx_frame_graph.checkpoint_request(graph)
        post = [entry for entry in request["checkpoints"]
                if entry.get("timing") == "post_event"]
        self.assertEqual([entry["event"] for entry in post], list(range(21)))
        observations = {record["event"]: record
                        for record in graph["prefix"]["post_events"]}
        for entry in post:
            observation = observations[entry["event"]]
            self.assertEqual(entry["resource"], observation["resource"])
            self.assertEqual(str(entry["target"]),
                             str(observation["target_name"]))
            self.assertEqual(entry["reference_file"],
                             observation["color"]["file"])
            self.assertTrue((frame / "blobs" / entry["reference_file"]).is_file())
            self.assertEqual(entry["reference_pixel_format"],
                             observation["color"]["pixel_format"])

    def test_one_changed_channel_at_one_pixel_fails(self):
        frame, metal = self._fixture()
        self._mutate_first_byte(self._event_file(metal, 5))
        result = self._evaluate(frame, metal)
        self.assertEqual(result["verdict"], "FIRST_DIVERGENCE")
        self.assertEqual(result["first_divergence"], 5)
        self.assertEqual(result["verified_prefix"], {"start": 0, "end": 4})
        divergent = result["events"][5]
        self.assertEqual(divergent["status"], "divergent")
        self.assertEqual(divergent["independent"]["mismatched_pixels"], 1)
        self.assertEqual(divergent["independent"]["max_channel_error"], 1)

    def test_event_18_difference_that_disappears_by_20_still_fails(self):
        frame, metal = self._fixture()
        self._mutate_first_byte(self._event_file(metal, 18))
        # Event 20 is untouched and therefore matches the reference.
        result = self._evaluate(frame, metal)
        self.assertEqual(result["verdict"], "FIRST_DIVERGENCE")
        self.assertEqual(result["first_divergence"], 18)
        self.assertEqual(result["verified_prefix"], {"start": 0, "end": 17})
        statuses = {entry["event"]: entry["status"] for entry in result["events"]}
        self.assertEqual(statuses[18], "divergent")
        self.assertEqual(statuses[20], "match")

    def test_missing_post_event_20_evidence_fails(self):
        frame, metal = self._fixture()
        replay_path = metal / "replay.json"
        replay = json.loads(replay_path.read_text())
        replay["checkpoints"] = [
            entry for entry in replay["checkpoints"] if entry["event"] != 20]
        replay_path.write_text(json.dumps(replay, indent=2) + "\n")
        result = self._evaluate(frame, metal)
        self.assertNotEqual(result["verdict"], "EVENT20_EXACT")
        self.assertEqual(result["verdict"], "requires_additional_evidence")
        self.assertTrue(any(
            "event 20" in problem for problem in result["problems"]),
            result["problems"])
        self.assertIn(result["events"][20]["status"], {"missing", "invalid"})

    def test_observation_from_another_seal_is_rejected(self):
        frame, metal = self._fixture()
        receipt_path = metal / "shader-receipt.json"
        receipt = json.loads(receipt_path.read_text())
        receipt["session_id"] = "00000000-0000-0000-0000-000000000000"
        receipt_path.write_text(json.dumps(receipt, indent=2) + "\n")
        result = self._evaluate(frame, metal)
        self.assertEqual(result["verdict"], "requires_additional_evidence")
        self.assertTrue(any("session_id" in problem
                            for problem in result["problems"]),
                        result["problems"])

    def test_forged_or_stale_receipt_is_rejected(self):
        frame, metal = self._fixture()
        receipt_path = metal / "shader-receipt.json"
        receipt = json.loads(receipt_path.read_text())
        receipt["checkpoint_request_sha256"] = "0" * 64
        receipt["checkpoint_count"] = receipt["checkpoint_count"] + 1
        receipt_path.write_text(json.dumps(receipt, indent=2) + "\n")
        result = self._evaluate(frame, metal)
        self.assertEqual(result["verdict"], "requires_additional_evidence")
        self.assertTrue(any("request" in problem
                            for problem in result["problems"]),
                        result["problems"])

    def test_pixel_format_normalization_is_not_accepted(self):
        frame, metal = self._fixture()
        replay_path = metal / "replay.json"
        replay = json.loads(replay_path.read_text())
        replay["checkpoints"][0]["pixel_format"] = "RGBA8"
        replay_path.write_text(json.dumps(replay, indent=2) + "\n")
        result = self._evaluate(frame, metal)
        self.assertEqual(result["verdict"], "requires_additional_evidence")
        self.assertEqual(result["status"], "incomplete")
        self.assertEqual(result["events"][0]["status"], "invalid")
        self.assertIn("pixel format", result["events"][0].get("problem", ""))

    def test_tampered_prefix_observation_file_is_rejected(self):
        frame, metal = self._fixture()
        path = frame / "prefix-observations.jsonl"
        path.write_bytes(path.read_bytes() + b"\n")
        with self.assertRaises(m6_frame.FrameError):
            m6_frame.load_trace(frame, verify_blobs=True)

    def test_campaign_checker_stops_at_earliest_divergence(self):
        frame, metal = self._fixture()
        self._mutate_first_byte(self._event_file(metal, 5))
        environment = dict(os.environ)
        environment["EU4_M7_EVENT20_EVIDENCE"] = str(frame.parent)
        completed = subprocess.run(
            [sys.executable, str(CHECKER), str(frame.parent)],
            capture_output=True, text=True, check=False, env=environment)
        self.assertNotEqual(completed.returncode, 0)
        payload = json.loads(completed.stdout)
        self.assertEqual(payload["verdict"], "fail")
        self.assertIn("event 5", payload["diagnostics"])
        self.assertEqual(payload["result"]["first_divergence"], 5)
        self.assertEqual(payload["result"]["verified_prefix"],
                         {"start": 0, "end": 4})


class OffscreenCoverageTests(unittest.TestCase):
    def test_missing_offscreen_post_event_observation_is_invalid(self):
        events = [
            {"kind": "clear",
             "target": {"framebuffer": 0}},
            {"kind": "draw",
             "target": {"framebuffer": 6, "color_attachment": {
                 "type": "texture", "name": 433, "level": 0,
                 "internal_format": "RGB8", "width": 4, "height": 4}}},
        ]
        resources = {
            "fb:0": {"producers": [{"event": 0}], "consumers": [],
                     "origin": "renderer_generated"},
            "tex:433": {"producers": [{"event": 1}], "consumers": [],
                        "origin": "renderer_generated"},
        }
        trace = {"_prefix": {
            "end": 1,
            "initial": {"event": 0, "timing": "pre_event",
                        "resource": "fb:0"},
            "post_events": [
                {"event": 0, "timing": "post_event", "kind": "clear",
                 "resource": "fb:0", "target_name": "0"},
            ],
        }}
        coverage = gfx_frame_graph.prefix_coverage(trace, events, resources)
        self.assertEqual(coverage["status"], "invalid")
        self.assertTrue(any("event 1" in problem
                            for problem in coverage["problems"]),
                        coverage["problems"])

    def test_consumed_offscreen_resource_without_observed_write_is_invalid(self):
        events = [
            {"kind": "draw",
             "target": {"framebuffer": 6, "color_attachment": {
                 "type": "texture", "name": 433, "level": 0,
                 "internal_format": "RGB8", "width": 4, "height": 4}}},
            {"kind": "draw",
             "target": {"framebuffer": 0}},
        ]
        resources = {
            "fb:0": {"producers": [{"event": 1}], "consumers": [],
                     "origin": "renderer_generated"},
            "tex:433": {"producers": [{"event": 0}],
                        "consumers": [{"event": 1}],
                        "origin": "renderer_generated"},
        }
        trace = {"_prefix": {
            "end": 1,
            "initial": {"event": 0, "timing": "pre_event",
                        "resource": "fb:0"},
            "post_events": [
                {"event": 1, "timing": "post_event", "kind": "draw",
                 "resource": "fb:0", "target_name": "0"},
            ],
        }}
        coverage = gfx_frame_graph.prefix_coverage(trace, events, resources)
        self.assertEqual(coverage["status"], "invalid")
        self.assertTrue(any("tex:433" in problem
                            for problem in coverage["problems"]),
                        coverage["problems"])
