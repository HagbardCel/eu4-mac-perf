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
import struct
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
    campaign_guard, gfx_frame_graph, gfx_independent_replay, gfx_prefix_case,
    gfx_prefix_check, m6_frame, native_build)
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

    def _make_r32f_event20_fixture(self, frame: Path) -> None:
        """Rewrite event 11 as an authenticated R32F clear with its reference."""
        manifest = json.loads((frame / "frame-trace.json").read_text())
        seal = json.loads((frame / "frame-seal.json").read_text())
        events = [json.loads(line) for line in
                  (frame / "frame-events.jsonl").read_text().splitlines()
                  if line.strip()]
        observations = list(manifest["prefix"]["observations"])
        width = int(manifest["frame"]["width"])
        height = int(manifest["frame"]["height"])
        event = events[11]
        event.clear()
        event.update({
            "kind": "clear",
            "flags": 1,
            "color": [1, 1, 1, 1],
            "target": {
                "framebuffer": 1,
                "draw_buffer": "0x8ce0",
                "read_buffer": "0x8ce0",
                "viewport": [0, 0, width, height],
                "color_attachment": {
                    "type": "texture", "name": 430, "level": 0,
                    "internal_format": "other", "width": width,
                    "height": height},
                "width": width, "height": height,
            },
            "state": {
                "scissor": {"enabled": False, "box": [0, 0, width, height]},
                "color_mask": [1, 1, 1, 1],
                "depth_mask": False,
                "stencil_mask": 0,
            },
        })
        payload = struct.pack("<f", 1.0) * (width * height)
        digest = hashlib.sha256(payload).hexdigest()
        blob = f"prefix-r32f-{digest}.bin"
        (frame / "blobs" / blob).write_bytes(payload)
        r32f = {
            "schema": "gfx-prefix-observation/v1",
            "event": 11,
            "timing": "post_event",
            "kind": "clear",
            "target": event["target"],
            "resource": "tex:430",
            "target_name": "430",
            "pixel_format": "R32F",
            "alpha_applicable": False,
            "origin": "lower_left",
            "width": width,
            "height": height,
            "row_stride": width * 4,
            "target_identity": {
                "internal_format": 0x822E,
                "component_type": 0x1406,
                "red_bits": 32,
                "green_bits": 0,
                "blue_bits": 0,
                "alpha_bits": 0,
                "depth_present": True,
                "stencil_present": False,
            },
            "color": {"file": blob, "sha256": digest,
                      "bytes": len(payload), "pixel_format": "R32F"},
            "depth": {"status": "not_used"},
            "stencil": {"status": "not_used"},
            "readback_ns": 0,
            "pid": seal.get("pid"),
            "session_id": seal.get("session_id"),
            "capture_frame_id": seal.get("capture_frame_id"),
            "budget": {},
        }
        post_index = next(
            index for index, item in enumerate(observations)
            if item.get("timing") == "post_event" and item.get("event") == 11)
        observations[post_index] = r32f
        (frame / "prefix-observations.jsonl").write_text(
            "".join(json.dumps(item, sort_keys=True) + "\n"
                    for item in observations))
        prefix = dict(manifest["prefix"])
        prefix.update({
            "end": 20,
            "status": "complete",
            "count": 21,
            "post_event_count": 21,
            "initial": True,
            "observation_file": "prefix-observations.jsonl",
            "observation_sha256": _sha(frame / "prefix-observations.jsonl"),
            "observation_bytes": (frame / "prefix-observations.jsonl").stat().st_size,
            "observations": observations,
        })
        manifest["prefix"] = prefix
        manifest["events"] = len(events)
        manifest["complete"] = True
        (frame / "frame-events.jsonl").write_text(
            "".join(json.dumps(item, sort_keys=True) + "\n"
                    for item in events))
        (frame / "frame-trace.json").write_text(
            json.dumps(manifest, indent=2) + "\n")
        seal.update({
            "event_stream_bytes": (frame / "frame-events.jsonl").stat().st_size,
            "event_stream_sha256": _sha(frame / "frame-events.jsonl"),
            "event_stream_verified": True,
            "manifest_sha256": _sha(frame / "frame-trace.json"),
            "operation_count": len(events),
            "draw_count": sum(1 for item in events if item["kind"] == "draw"),
            "clear_count": sum(1 for item in events if item["kind"] == "clear"),
            "copy_count": sum(1 for item in events
                             if item["kind"] in ("resolve", "copy")),
            "capture_dir": str(frame.resolve()),
        })
        (frame / "frame-seal.json").write_text(
            json.dumps(seal, indent=2) + "\n")

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
                "format": checkpoint.get("reference_pixel_format") or "BGRA8",
                "pixel_format": checkpoint.get("reference_pixel_format")
                or "BGRA8",
                "alpha_applicable": checkpoint.get("alpha_applicable", True),
                "compared_channels": checkpoint.get("compared_channels"),
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

    def _r32f_fixture(self):
        frame, _metal = self._fixture()
        self._make_r32f_event20_fixture(frame)
        metal = frame.parent / "shadow-r32f"
        metal.mkdir(exist_ok=True)
        self._metal_fixture(frame, metal)
        return frame, metal

    def test_complete_event20_r32f_fixture_qualifies(self):
        frame, metal = self._r32f_fixture()
        result = self._evaluate(frame, metal)
        self.assertEqual(result["verdict"], "EVENT20_EXACT",
                         result["problems"])
        self.assertEqual(result["verified_prefix"], {"start": 0, "end": 20})
        event11 = result["events"][11]
        self.assertEqual(event11["status"], "match")
        self.assertEqual(event11["reference_pixel_format"], "R32F")
        self.assertEqual(event11["compared_channels"], "R32F")
        self.assertIs(event11["alpha_applicable"], False)
        self.assertEqual(event11["independent"]["format_scope"], "R32F")
        self.assertTrue(event11["independent"]["exact_bitwise"])

    def test_r32f_one_bit_mismatch_is_divergent(self):
        frame, metal = self._r32f_fixture()
        path = sorted(metal.glob("prefix-event11-430.bgra"))[0]
        data = bytearray(path.read_bytes())
        data[0] ^= 1
        path.write_bytes(bytes(data))
        result = self._evaluate(frame, metal)
        self.assertEqual(result["verdict"], "FIRST_DIVERGENCE")
        self.assertEqual(result["first_divergence"], 11)
        event11 = result["events"][11]
        self.assertEqual(event11["status"], "divergent")
        self.assertEqual(event11["independent"]["mismatched_pixels"], 1)
        self.assertGreater(event11["independent"]["max_ulp_error"], 0)
        self.assertEqual(event11["independent"]["max_channel_error"], 0)

    def test_native_r32f_observation_is_format_aware(self):
        directory = Path(tempfile.mkdtemp(dir=self.temporary.name))
        result = subprocess.run(
            [str(self.binary), "prefix-r32f", str(directory)],
            capture_output=True, text=True, check=False)
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)
        manifest = json.loads((directory / "frame-trace.json").read_text())
        post = [record for record in manifest["prefix"]["observations"]
                if record["timing"] == "post_event"]
        self.assertEqual(len(post), 1)
        observation = post[0]
        self.assertEqual(observation["pixel_format"], "R32F")
        self.assertIs(observation["alpha_applicable"], False)
        self.assertEqual(observation["target_identity"]["internal_format"],
                         0x822E)
        self.assertEqual(observation["target_identity"]["component_type"],
                         0x1406)
        self.assertEqual(observation["color"]["bytes"], 4 * 4 * 4)
        self.assertEqual(observation["row_stride"], 4 * 4)
        self.assertEqual(observation["color"]["pixel_format"], "R32F")

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

    def test_trusted_execution_charges_one_replay_and_refuses_exhaustion(self):
        frame, _metal = self._fixture()
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        state_path = Path(temporary.name) / "state.json"
        guard = campaign_guard.Guard(state_path)
        guard.init(budgets={"replays": 1})
        previous = os.environ.get("EU4_CAMPAIGN_STATE")
        os.environ["EU4_CAMPAIGN_STATE"] = str(state_path)
        self.addCleanup(lambda: os.environ.pop("EU4_CAMPAIGN_STATE", None)
                        if previous is None else
                        os.environ.__setitem__("EU4_CAMPAIGN_STATE", previous))
        first = gfx_prefix_check.evaluate(
            frame, None, require_end=20, trusted_execution=True)
        self.assertEqual(first["verdict"], "requires_additional_evidence")
        self.assertEqual(guard.state()["counts"]["replays"], 1)
        second = gfx_prefix_check.evaluate(
            frame, None, require_end=20, trusted_execution=True)
        self.assertEqual(second["verdict"], "requires_additional_evidence")
        self.assertEqual((second.get("execution") or {}).get("status"),
                         "refused")
        self.assertEqual(guard.state()["counts"]["replays"], 1)

    def test_nested_trusted_execution_joins_one_budget_reservation(self):
        frame, _metal = self._fixture()
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        state_path = Path(temporary.name) / "state.json"
        guard = campaign_guard.Guard(state_path)
        guard.init(budgets={"replays": 1})
        previous = os.environ.get("EU4_CAMPAIGN_STATE")
        os.environ["EU4_CAMPAIGN_STATE"] = str(state_path)
        self.addCleanup(lambda: os.environ.pop("EU4_CAMPAIGN_STATE", None)
                        if previous is None else
                        os.environ.__setitem__("EU4_CAMPAIGN_STATE", previous))
        with campaign_guard.attempt("replays"):
            gfx_prefix_check.evaluate(
                frame, None, require_end=20, trusted_execution=True)
        self.assertEqual(guard.state()["counts"]["replays"], 1)

    def test_derived_prefix_frame_is_clearly_labelled(self):
        frame, _metal = self._fixture()
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        destination = Path(temporary.name) / "prefix"
        summary = gfx_prefix_case.build_prefix_frame(frame, 1, destination)
        self.assertEqual(summary["events"], 2)
        self.assertEqual(summary["end"], 1)
        self.assertLess(summary["bytes"], 512 * 1024 * 1024)
        derived = m6_frame.load_trace(destination, verify_blobs=True)
        self.assertEqual(derived["events"], 2)
        self.assertEqual([event["kind"] for event in derived["_events"]],
                         ["clear", "draw"])
        self.assertEqual(derived["_prefix"]["end"], 1)
        self.assertEqual(derived["derived_from"]["original_operation_count"],
                         21)
        self.assertIn("not the original full seal",
                      derived["derived_from"]["scope"])
        seal = m6_frame.load_seal(destination)
        self.assertTrue(seal["sealed"])
        self.assertEqual(seal["operation_count"], 2)
        self.assertEqual(seal["derived_from"]["original_operation_count"], 21)

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

    def test_alpha_only_mismatch_on_default_framebuffer_fails(self):
        frame, metal = self._fixture()
        path = self._event_file(metal, 5)
        data = bytearray(path.read_bytes())
        data[3] = (data[3] + 1) & 0xFF
        path.write_bytes(bytes(data))
        result = self._evaluate(frame, metal)
        self.assertEqual(result["verdict"], "FIRST_DIVERGENCE")
        self.assertEqual(result["first_divergence"], 5)
        entry = result["events"][5]
        self.assertEqual(entry["status"], "divergent")
        self.assertEqual(entry["independent"]["mismatched_pixels"], 1)
        self.assertEqual(entry["independent"]["alpha_different_pixels"], 1)
        self.assertEqual(entry["independent"]["max_channel_error"], 1)

    def test_request_substitution_with_consistent_receipt_is_rejected(self):
        frame, metal = self._fixture()
        request_path = metal / "checkpoint-request.json"
        request = json.loads(request_path.read_text())
        baseline = request["checkpoints"][0]
        first_reference = next(
            entry for entry in request["checkpoints"]
            if entry.get("timing") == "post_event")
        for entry in request["checkpoints"]:
            if entry.get("timing") != "post_event":
                continue
            entry["reference_file"] = first_reference["reference_file"]
            entry["reference_sha256"] = first_reference["reference_sha256"]
        request_path.write_text(json.dumps(request, indent=2) + "\n")
        request_sha = _sha(request_path)
        receipt_path = metal / "shader-receipt.json"
        receipt = json.loads(receipt_path.read_text())
        receipt["checkpoint_request_sha256"] = request_sha
        receipt["checkpoint_count"] = len(request["checkpoints"])
        receipt_path.write_text(json.dumps(receipt, indent=2) + "\n")
        replay_path = metal / "replay.json"
        replay = json.loads(replay_path.read_text())
        replay["checkpoint_request_sha256"] = request_sha
        replay["checkpoints"] = []
        for entry in request["checkpoints"]:
            if entry.get("timing") != "post_event":
                continue
            source = frame / "blobs" / entry["reference_file"]
            shutil.copyfile(source, metal / entry["file"])
            replay["checkpoints"].append({
                "event": entry["event"],
                "target": entry["target"],
                "resource": entry["resource"],
                "timing": "post_event",
                "file": entry["file"],
                "reference_file": entry["reference_file"],
                "reference_sha256": entry["reference_sha256"],
                "ok": True,
                "width": entry["reference_width"],
                "height": entry["reference_height"],
                "format": "BGRA8",
                "pixel_format": "BGRA8",
            })
        replay_path.write_text(json.dumps(replay, indent=2) + "\n")
        result = self._evaluate(frame, metal)
        self.assertNotEqual(result["verdict"], "EVENT20_EXACT")
        self.assertEqual(result["verdict"], "requires_additional_evidence")
        self.assertFalse(result["request"]["agrees_with_derivation"])
        _ = baseline

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

    def test_campaign_checker_does_not_accept_candidate_metal_output(self):
        frame, metal = self._fixture()
        self._mutate_first_byte(self._event_file(metal, 5))
        environment = dict(os.environ)
        environment["EU4_M7_EVENT20_TRUSTED_ROOT"] = str(
            Path(tempfile.mkdtemp(dir=self.temporary.name)))
        completed = subprocess.run(
            [sys.executable, str(CHECKER), str(frame.parent)],
            capture_output=True, text=True, check=False, env=environment)
        self.assertNotEqual(completed.returncode, 0)
        payload = json.loads(completed.stdout)
        self.assertEqual(payload["verdict"], "inconclusive")
        self.assertIn("trusted reference root", payload["diagnostics"])


class FormatScopeTests(unittest.TestCase):
    def test_default_framebuffer_alpha_is_applicable(self):
        scope = gfx_frame_graph.format_scope({
            "pixel_format": "BGRA8",
            "target_identity": {"internal_format": 0, "alpha_bits": 0},
        })
        self.assertIs(scope["alpha_applicable"], True)
        self.assertEqual(scope["compared_channels"], "RGBA")
        self.assertEqual(scope["problems"], [])

    def test_rgba8_alpha_is_applicable(self):
        scope = gfx_frame_graph.format_scope({
            "pixel_format": "RGBA8",
            "target_identity": {"internal_format": 0x8058, "alpha_bits": 8},
        })
        self.assertIs(scope["alpha_applicable"], True)
        self.assertEqual(scope["compared_channels"], "RGBA")
        self.assertEqual(scope["problems"], [])

    def test_rgb8_alpha_is_not_stored(self):
        scope = gfx_frame_graph.format_scope({
            "pixel_format": "BGRA8",
            "alpha_applicable": False,
            "target_identity": {"internal_format": 0x8051, "alpha_bits": 0},
        })
        self.assertIs(scope["alpha_applicable"], False)
        self.assertEqual(scope["compared_channels"], "RGB")
        self.assertEqual(scope["problems"], [])

    def test_r32f_compares_the_float_payload(self):
        scope = gfx_frame_graph.format_scope({
            "pixel_format": "R32F",
            "alpha_applicable": False,
            "target_identity": {"internal_format": 0x822E,
                                "component_type": 0x1406,
                                "red_bits": 32, "alpha_bits": 0},
        })
        self.assertIs(scope["alpha_applicable"], False)
        self.assertEqual(scope["compared_channels"], "R32F")
        self.assertEqual(scope["problems"], [])

    def test_forged_false_alpha_on_default_and_rgba8_is_refused(self):
        for internal, pixel_format in ((0, "BGRA8"), (0x8058, "RGBA8")):
            scope = gfx_frame_graph.format_scope({
                "pixel_format": pixel_format,
                "alpha_applicable": False,
                "target_identity": {"internal_format": internal,
                                    "alpha_bits": 8 if internal else 0},
            })
            self.assertTrue(scope["problems"], (internal, scope))

    def test_inconsistent_attachment_identity_is_refused(self):
        cases = [
            {"pixel_format": "BGRA8", "target_identity": {
                "internal_format": 0x8051, "alpha_bits": 8}},
            {"pixel_format": "RGBA8", "target_identity": {
                "internal_format": 0x8058, "alpha_bits": 0}},
            {"pixel_format": "R32F", "target_identity": {
                "internal_format": 0x822E, "alpha_bits": 8}},
        ]
        for observation in cases:
            self.assertTrue(
                gfx_frame_graph.format_scope(observation)["problems"],
                observation)

    def test_forged_false_alpha_on_default_is_rejected_by_coverage(self):
        events = [{"kind": "draw", "target": {"framebuffer": 0},
                   "state": {"depth": {"test": False, "write": False},
                             "stencil": {"test": False}}}]
        resources = {
            "fb:0": {"producers": [{"event": 0}], "consumers": [],
                     "origin": "renderer_generated"},
        }
        trace = {"_prefix": {
            "end": 0,
            "initial": {"event": 0, "timing": "pre_event",
                        "resource": "fb:0"},
            "post_events": [{
                "event": 0, "timing": "post_event", "kind": "draw",
                "resource": "fb:0", "target_name": "0",
                "pixel_format": "BGRA8", "alpha_applicable": False,
                "target_identity": {"internal_format": 0, "alpha_bits": 0},
                "color": {"status": "captured", "file": "x.bin",
                          "sha256": "0" * 64, "bytes": 64,
                          "pixel_format": "BGRA8"},
            }],
        }}
        coverage = gfx_frame_graph.prefix_coverage(trace, events, resources)
        self.assertEqual(coverage["status"], "invalid")
        self.assertTrue(any("alpha applicability" in problem
                            for problem in coverage["problems"]),
                        coverage["problems"])

    def test_draw_before_full_clear_has_no_initial_state_proof(self):
        events = [{"kind": "draw", "target": {"framebuffer": 0},
                   "state": {"depth": {"test": False, "write": False},
                             "stencil": {"test": False}}}]
        resources = {
            "fb:0": {"producers": [{"event": 0}], "consumers": [{"event": 0}],
                     "origin": "renderer_generated"},
        }
        trace = {"_prefix": {
            "end": 0,
            "initial": {"event": 0, "timing": "pre_event",
                        "resource": "fb:0"},
            "post_events": [{
                "event": 0, "timing": "post_event", "kind": "draw",
                "resource": "fb:0", "target_name": "0",
                "pixel_format": "BGRA8", "alpha_applicable": True,
                "target_identity": {"internal_format": 0, "alpha_bits": 0},
                "color": {"status": "captured", "file": "x.bin",
                          "sha256": "0" * 64, "bytes": 64,
                          "pixel_format": "BGRA8"},
            }],
        }}
        coverage = gfx_frame_graph.prefix_coverage(trace, events, resources)
        self.assertEqual(coverage["status"], "invalid")
        self.assertTrue(any("before a full colour clear" in problem
                            for problem in coverage["problems"]),
                        coverage["problems"])

    def test_r32f_auxiliary_clear_is_planned_and_compared(self):
        events = [{
            "kind": "clear",
            "target": {"framebuffer": 1, "color_attachment": {
                "type": "texture", "name": 430, "level": 0,
                "internal_format": "other", "width": 2048, "height": 2048}},
            "flags": 1,
            "state": {"scissor": {"enabled": False, "box": [0, 0, 2048, 2048]},
                      "color_mask": [1, 1, 1, 1]},
        }]
        resources = {
            "tex:430": {"producers": [{"event": 0, "cleared": True,
                                       "coverage": {"color_full": True}}],
                        "consumers": [], "origin": "renderer_generated"},
        }
        trace = {
            "_prefix": {
                "end": 0,
                "initial": {"event": 0, "timing": "pre_event",
                            "resource": "fb:0"},
                "post_events": [{
                    "event": 0, "timing": "post_event", "kind": "clear",
                    "resource": "tex:430", "target_name": "430",
                    "pixel_format": "R32F", "alpha_applicable": False,
                    "target_identity": {"internal_format": 0x822E,
                                        "component_type": 0x1406,
                                        "red_bits": 32, "alpha_bits": 0},
                    "color": {"status": "captured", "file": "r32f.bin",
                              "sha256": "0" * 64, "bytes": 2048 * 2048 * 4,
                              "pixel_format": "R32F"},
                }],
            },
        }
        coverage = gfx_frame_graph.prefix_coverage(trace, events, resources)
        self.assertEqual(coverage["status"], "complete", coverage["problems"])
        self.assertEqual(coverage["excluded_events"], [])
        graph = {
            "resources": resources,
            "prefix": coverage,
            "renderer_generated_sampled": [],
        }
        plan = gfx_frame_graph.prefix_checkpoint_plan(graph)
        self.assertEqual(len(plan), 1)
        self.assertEqual(plan[0]["reference_pixel_format"], "R32F")
        self.assertEqual(plan[0]["compared_channels"], "R32F")
        self.assertIs(plan[0]["alpha_applicable"], False)

    def test_full_clear_proves_initial_state_irrelevant(self):
        events = [
            {"kind": "clear", "target": {"framebuffer": 0}, "flags": 1,
             "state": {"scissor": {"enabled": False, "box": [0, 0, 4, 4]},
                       "color_mask": [1, 1, 1, 1]}},
            {"kind": "draw", "target": {"framebuffer": 0},
             "state": {"depth": {"test": False, "write": False},
                       "stencil": {"test": False}}},
        ]
        resources = {
            "fb:0": {"producers": [
                         {"event": 0, "cleared": True,
                          "coverage": {"color_full": True}},
                         {"event": 1}],
                     "consumers": [{"event": 1}],
                     "origin": "renderer_generated"},
        }
        trace = {"_prefix": {
            "end": 1,
            "initial": {"event": 0, "timing": "pre_event",
                        "resource": "fb:0"},
            "post_events": [
                {"event": 0, "timing": "post_event", "kind": "clear",
                 "resource": "fb:0", "target_name": "0",
                 "pixel_format": "BGRA8", "alpha_applicable": True,
                 "target_identity": {"internal_format": 0, "alpha_bits": 0},
                 "color": {"status": "captured", "file": "x.bin",
                           "sha256": "0" * 64, "bytes": 64,
                           "pixel_format": "BGRA8"}},
                {"event": 1, "timing": "post_event", "kind": "draw",
                 "resource": "fb:0", "target_name": "0",
                 "pixel_format": "BGRA8", "alpha_applicable": True,
                 "target_identity": {"internal_format": 0, "alpha_bits": 0},
                 "color": {"status": "captured", "file": "x.bin",
                           "sha256": "0" * 64, "bytes": 64,
                           "pixel_format": "BGRA8"}},
            ],
        }}
        coverage = gfx_frame_graph.prefix_coverage(trace, events, resources)
        self.assertEqual(coverage["status"], "complete", coverage["problems"])
        row = next(item for item in coverage["initial_state"]
                   if item["resource"] == "fb:0")
        self.assertEqual(row["status"], "overwritten_by_producer")

    def test_missing_event_before_later_divergence_is_not_first_divergence(self):
        rows = [{"event": event, "status": "match"} for event in range(6)]
        rows[0] = {"event": 0, "status": "missing"}
        rows[5] = {"event": 5, "status": "divergent"}
        result = gfx_prefix_check.classify_prefix(
            rows, problems=[], end=20, require_end=20)
        self.assertEqual(result["verdict"], "requires_additional_evidence")
        self.assertIsNone(result["first_divergence"])
        self.assertEqual(result["unverified_event"], 0)

    def test_invalid_event_before_divergence_is_not_first_divergence(self):
        rows = [{"event": event, "status": "match"} for event in range(6)]
        rows[3] = {"event": 3, "status": "invalid"}
        rows[5] = {"event": 5, "status": "divergent"}
        result = gfx_prefix_check.classify_prefix(
            rows, problems=[], end=20, require_end=20)
        self.assertEqual(result["verdict"], "requires_additional_evidence")
        self.assertIsNone(result["first_divergence"])

    def test_problem_invalidates_first_divergence(self):
        rows = [{"event": event, "status": "match"} for event in range(6)]
        rows[1] = {"event": 1, "status": "divergent"}
        result = gfx_prefix_check.classify_prefix(
            rows, problems=["foreign receipt"], end=20, require_end=20)
        self.assertEqual(result["verdict"], "requires_additional_evidence")
        self.assertIsNone(result["first_divergence"])


class ExcludedAuxiliaryTests(unittest.TestCase):
    def test_isolated_unsupported_aux_clear_is_not_event20_exact(self):
        rows = [{"event": event, "status": "match"} for event in range(21)]
        rows[11] = {"event": 11, "status": "unsupported_auxiliary"}
        result = gfx_prefix_check.classify_prefix(
            rows, problems=[], end=20, require_end=20)
        self.assertEqual(result["verdict"],
                         "requires_additional_evidence")
        self.assertEqual(result["status"], "incomplete")
        self.assertEqual(result["excluded_events"], [11])
        self.assertNotIn(11, result["verified_color_events"])
        self.assertEqual(result["unverified_event"], 11)

    def test_isolated_aux_clear_coverage_is_complete_but_excluded(self):
        events = [
            {"kind": "clear",
             "target": {"framebuffer": 6, "color_attachment": {
                 "type": "texture", "name": 433, "level": 0,
                 "internal_format": "other", "width": 2048, "height": 2048}},
             "flags": 1},
            {"kind": "clear",
             "target": {"framebuffer": 0},
             "flags": 1,
             "state": {"scissor": {"enabled": False, "box": [0, 0, 4, 4]},
                       "color_mask": [1, 1, 1, 1]}},
            {"kind": "draw",
             "target": {"framebuffer": 0},
             "state": {"depth": {"test": False, "write": False},
                       "stencil": {"test": False}}},
        ]
        resources = {
            "tex:433": {"producers": [{"event": 0, "cleared": True,
                                       "coverage": {"color_full": True}}],
                        "consumers": [], "origin": "renderer_generated"},
            "fb:0": {"producers": [
                         {"event": 1, "cleared": True,
                          "coverage": {"color_full": True}},
                         {"event": 2}],
                     "consumers": [{"event": 2}],
                     "origin": "renderer_generated"},
        }
        trace = {"_prefix": {
            "end": 2,
            "initial": {"event": 0, "timing": "pre_event",
                        "resource": "fb:0"},
            "post_events": [
                {"event": 0, "timing": "post_event", "kind": "clear",
                 "resource": "tex:433", "target_name": "433",
                 "color": {"status": "unsupported_format"}},
                {"event": 1, "timing": "post_event", "kind": "clear",
                 "resource": "fb:0", "target_name": "0",
                 "color": {"status": "captured", "file": "x.bin",
                           "sha256": "0" * 64, "bytes": 64,
                           "pixel_format": "BGRA8"}},
                {"event": 2, "timing": "post_event", "kind": "draw",
                 "resource": "fb:0", "target_name": "0",
                 "color": {"status": "captured", "file": "x.bin",
                           "sha256": "0" * 64, "bytes": 64,
                           "pixel_format": "BGRA8"}},
            ],
        }}
        coverage = gfx_frame_graph.prefix_coverage(trace, events, resources)
        self.assertEqual(coverage["status"], "complete", coverage["problems"])
        self.assertEqual(coverage["excluded_events"], [0])

    def test_consumed_unsupported_aux_clear_is_invalid(self):
        events = [
            {"kind": "clear",
             "target": {"framebuffer": 6, "color_attachment": {
                 "type": "texture", "name": 433, "level": 0,
                 "internal_format": "other", "width": 2048, "height": 2048}},
             "flags": 1},
            {"kind": "clear",
             "target": {"framebuffer": 0},
             "flags": 1,
             "state": {"scissor": {"enabled": False, "box": [0, 0, 4, 4]},
                       "color_mask": [1, 1, 1, 1]}},
            {"kind": "draw",
             "target": {"framebuffer": 0},
             "state": {"depth": {"test": False, "write": False},
                       "stencil": {"test": False}}},
        ]
        resources = {
            "tex:433": {"producers": [{"event": 0, "cleared": True,
                                       "coverage": {"color_full": True}}],
                        "consumers": [{"event": 2}],
                        "origin": "renderer_generated"},
            "fb:0": {"producers": [
                         {"event": 1, "cleared": True,
                          "coverage": {"color_full": True}},
                         {"event": 2}],
                     "consumers": [{"event": 2}],
                     "origin": "renderer_generated"},
        }
        trace = {"_prefix": {
            "end": 2,
            "initial": {"event": 0, "timing": "pre_event",
                        "resource": "fb:0"},
            "post_events": [
                {"event": 0, "timing": "post_event", "kind": "clear",
                 "resource": "tex:433", "target_name": "433",
                 "color": {"status": "unsupported_format"}},
                {"event": 1, "timing": "post_event", "kind": "clear",
                 "resource": "fb:0", "target_name": "0",
                 "color": {"status": "captured", "file": "x.bin",
                           "sha256": "0" * 64, "bytes": 64,
                           "pixel_format": "BGRA8"}},
                {"event": 2, "timing": "post_event", "kind": "draw",
                 "resource": "fb:0", "target_name": "0",
                 "color": {"status": "captured", "file": "x.bin",
                           "sha256": "0" * 64, "bytes": 64,
                           "pixel_format": "BGRA8"}},
            ],
        }}
        coverage = gfx_frame_graph.prefix_coverage(trace, events, resources)
        self.assertEqual(coverage["status"], "invalid")
        self.assertTrue(any("consumed" in problem
                            for problem in coverage["problems"]),
                        coverage["problems"])


class TrustedExecutionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        cls.binary = Path(cls.temporary.name) / "gfx_frame_trace_scenarios"
        sources = [ROOT / "tests" / "gfx_frame_trace_scenarios.c",
                   BENCH / "eu4_gfx_frame_trace.c"]
        native_build.require_build(
            ["clang", "-O1", "-Wall", "-Wextra", "-I", str(BENCH), "-o",
             str(cls.binary), *map(str, sources), "-framework", "OpenGL"],
            label="event20-trusted-scenarios", sources=sources,
            frameworks=("OpenGL",), require_arm64=False)

    @classmethod
    def tearDownClass(cls):
        cls.temporary.cleanup()

    def _frame(self, directory: Path) -> None:
        result = subprocess.run(
            [str(self.binary), "prefix-seal", str(directory)],
            capture_output=True, text=True, check=False)
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)

    def test_reference_copied_into_candidate_metal_output_does_not_qualify(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        base = Path(temporary.name)
        frame = base / "frame"
        frame.mkdir()
        self._frame(frame)
        metal = base / "shadow"
        metal.mkdir()
        graph = gfx_frame_graph.build(frame)
        observations = {record["event"]: record
                        for record in graph["prefix"]["post_events"]}
        for event, observation in observations.items():
            shutil.copyfile(frame / "blobs" / observation["color"]["file"],
                            metal / f"prefix-event{event}-433.bgra")
        result = gfx_prefix_check.evaluate(
            frame, metal, require_end=20, trusted_execution=True)
        self.assertNotEqual(result["verdict"], "EVENT20_EXACT")
        self.assertEqual(result["verdict"],
                         "requires_additional_evidence")
        self.assertEqual(result.get("execution", {}).get("status"),
                         "failed")

    def test_trusted_evidence_detects_a_modified_frame(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        base = Path(temporary.name)
        frame = base / "frame"
        frame.mkdir()
        self._frame(frame)
        root = base / "trusted"
        gfx_prefix_check.prepare_trusted_evidence(
            frame, root, read_only=False)
        copied = root / "frame"
        record = gfx_prefix_check.verify_trusted_evidence(root, copied)
        seal = m6_frame.load_seal(copied, trusted_evidence=record)
        self.assertTrue(seal["sealed"])
        path = copied / "frame-events.jsonl"
        data = bytearray(path.read_bytes())
        data[-2] = (data[-2] + 1) & 0xFF
        path.write_bytes(bytes(data))
        with self.assertRaises(gfx_prefix_check.PrefixCheckError):
            gfx_prefix_check.verify_trusted_evidence(root, copied)

    def test_trusted_evidence_rejects_the_untrusted_original_frame(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        base = Path(temporary.name)
        frame = base / "frame"
        frame.mkdir()
        self._frame(frame)
        root = base / "trusted"
        gfx_prefix_check.prepare_trusted_evidence(
            frame, root, read_only=False)
        with self.assertRaises(gfx_prefix_check.PrefixCheckError):
            gfx_prefix_check.verify_trusted_evidence(root, frame)


class TrustedRootSelectionTests(unittest.TestCase):
    def test_explicit_root_selects_the_assigned_seal_only(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        base = Path(temporary.name)
        first = base / "seal-a"
        second = base / "seal-b"
        for path in (first, second):
            path.mkdir()
            (path / "trusted-evidence.json").write_text("{}\n")
        selected = gfx_prefix_check.resolve_trusted_reference(
            first, base / "candidate", root=base)
        self.assertEqual(selected, first.resolve())
        with self.assertRaises(gfx_prefix_check.PrefixCheckError):
            gfx_prefix_check.resolve_trusted_reference(
                base, base / "candidate", root=base)
        with self.assertRaises(gfx_prefix_check.PrefixCheckError):
            gfx_prefix_check.resolve_trusted_reference(
                None, base / "candidate", root=base)
        with self.assertRaises(gfx_prefix_check.PrefixCheckError):
            gfx_prefix_check.resolve_trusted_reference(
                first, first, root=base)

class R32FComparisonTests(unittest.TestCase):
    def test_r32f_is_compared_as_exact_float_bits(self):
        reference = struct.pack("<f", 1.0) * 4
        identical = gfx_independent_replay.compare_r32f(
            reference, reference, 2, 2)
        self.assertEqual(identical["format_scope"], "R32F")
        self.assertEqual(identical["mismatched_pixels"], 0)
        self.assertTrue(identical["exact_bitwise"])
        self.assertEqual(identical["max_channel_error"], 0)
        changed = bytearray(reference)
        changed[0] ^= 1
        different = gfx_independent_replay.compare_r32f(
            reference, bytes(changed), 2, 2)
        self.assertEqual(different["mismatched_pixels"], 1)
        self.assertFalse(different["exact_bitwise"])
        self.assertGreater(different["max_ulp_error"], 0)
        self.assertEqual(different["max_channel_error"], 0)
        self.assertEqual(different["alpha_different_pixels"], 0)

class ExecutionReceiptTests(unittest.TestCase):
    def _fixture(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        base = Path(temporary.name)
        (base / "out.bgra").write_bytes(b"abcd")
        (base / "replay.json").write_text('{"status":"ok"}\n')
        (base / "shader-receipt.json").write_text('{"schema":"x"}\n')
        execution = {
            "outputs": [{"file": "out.bgra", "bytes": 4,
                         "sha256": _sha(base / "out.bgra")}],
            "replay_json_sha256": _sha(base / "replay.json"),
            "receipt_sha256": _sha(base / "shader-receipt.json"),
        }
        return base, execution

    def _sha(path: Path) -> str:
        return hashlib.sha256(path.read_bytes()).hexdigest()

    def test_replaced_output_receipt_or_replay_is_detected(self):
        base, execution = self._fixture()
        self.assertEqual(
            gfx_prefix_check.verify_execution_outputs(execution, base), [])
        (base / "out.bgra").write_bytes(b"wxyz")
        self.assertTrue(gfx_prefix_check.verify_execution_outputs(
            execution, base))
        (base / "out.bgra").write_bytes(b"abcd")
        (base / "shader-receipt.json").write_text('{"schema":"y"}\n')
        self.assertTrue(gfx_prefix_check.verify_execution_outputs(
            execution, base))
        (base / "shader-receipt.json").write_text('{"schema":"x"}\n')
        (base / "replay.json").write_text('{"status":"changed"}\n')
        self.assertTrue(gfx_prefix_check.verify_execution_outputs(
            execution, base))

    def test_candidate_source_digest_changes_with_content(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        source = root / "benchmark" / "eu4_gfx_frame_replay.mm"
        source.parent.mkdir(parents=True)
        source.write_text("// baseline\n")
        for header in ("eu4_gfx_frame_replay_cli.mm",):
            (root / "benchmark" / header).write_text("// cli\n")
        before = gfx_prefix_check._candidate_source_digests(root)
        source.write_text("// changed\n")
        after = gfx_prefix_check._candidate_source_digests(root)
        self.assertNotEqual(before, after)

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
