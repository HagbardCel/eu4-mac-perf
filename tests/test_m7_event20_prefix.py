"""Adversarial regressions for exact-prefix graphics qualification.

The native scenario writer produces a genuine sealed 21-event frame (no GL
context, TEST_ONLY readback) and the tests build a matching Metal replay
fixture.  Every verdict is then recomputed by the independent checker from the
original reference bytes, never from the replay's own counters.
"""

from __future__ import annotations

import contextlib
import hashlib
import io
import json
import os
import struct
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from automation import (  # noqa: E402
    campaign_guard, gfx_frame_graph, gfx_independent_replay, gfx_prefix_case,
    gfx_prefix_check, m6_frame, native_build, replay_host_dependencies)
BENCH = ROOT / "benchmark"
CHECKER = ROOT / "campaigns" / "eu4-m7-event20" / "accept.py"


def build_candidate_fixture(root: Path) -> Path:
    """Build a small deterministic replayable prefix frame for trust tests.

    A real triangle draw makes the frame replayable; event 0 is a default
    framebuffer clear and its authenticated observation is the trusted host's
    own output for that event.  The fixture is synthetic by construction and
    never claims original-frame authority.
    """
    from tests.test_m6_frame import _write_blob, build_synthetic_trace

    frame = Path(root) / "frame"
    build_synthetic_trace(frame)
    events = [json.loads(line) for line in
              (frame / "frame-events.jsonl").read_text().splitlines()
              if line.strip()]
    draw = next(event for event in events if event["kind"] == "draw")
    clear = next(event for event in events if event["kind"] == "clear")
    clear["target"] = {"framebuffer": 0, "width": 4, "height": 3,
                       "viewport": [0, 0, 4, 3]}
    clear["flags"] = 1
    clear["color"] = [0.25, 0.5, 0.75, 1.0]
    vertex = (b"#version 120\nattribute vec3 in_POSITION;\n"
              b"void main(){gl_Position=vec4(in_POSITION,1.0);}\n")
    pixel = (b"#version 120\nvoid main(){gl_FragColor=vec4(1.0,0.0,0.0,1.0);}\n")
    vertex_member = _write_blob(frame, "vs.vert", vertex)
    pixel_member = _write_blob(frame, "fs.frag", pixel)
    for stage in draw["program"]["shaders"]:
        member = vertex_member if stage["stage"] == "vertex" else pixel_member
        stage.update(file=member["file"], sha256=member["sha256"],
                     bytes=member["bytes"])
    (frame / "frame-events.jsonl").write_text(
        "".join(json.dumps(event) + "\n" for event in events))
    base = Path(root)
    shaders = base / "shaders"
    translated = subprocess.run(
        [sys.executable, "-m", "automation.m6_frame", "translate",
         "--frame", str(frame), "--out", str(shaders)],
        capture_output=True, text=True, check=False)
    if translated.returncode != 0:
        raise RuntimeError("fixture shader translation failed")
    host = m6_frame.build_replay_host()
    request = base / "checkpoint-request.json"
    request.write_text(json.dumps({"checkpoints": [{
        "event": 0, "target": "0", "file": "event0.bgra",
        "timing": "post_event"}]}))
    metal = base / "metal"
    metal.mkdir()
    replayed = subprocess.run(
        [str(host), "--frame", str(frame), "--shaders", str(shaders),
         "--out", str(metal), "--checkpoints", str(request)],
        capture_output=True, text=True, check=False)
    if replayed.returncode != 0:
        raise RuntimeError("fixture replay failed")
    colour = (metal / "event0.bgra").read_bytes()
    width, height = 4, 3
    if len(colour) != width * height * 4:
        raise RuntimeError("fixture checkpoint extent changed")
    colour_sha = _sha(metal / "event0.bgra")
    colour_name = "obs-event0.bgra"
    (frame / "blobs" / colour_name).write_bytes(colour)
    initial = bytes([0, 0, 0, 255] * (width * height))
    initial_sha = hashlib.sha256(initial).hexdigest()
    initial_name = "obs-initial.bgra"
    (frame / "blobs" / initial_name).write_bytes(initial)
    identity = {"internal_format": 0, "component_type": 0x8C17,
                "red_bits": 8, "green_bits": 8, "blue_bits": 8,
                "alpha_bits": 8, "depth_present": True,
                "stencil_present": True}
    observations = [
        {"schema": "gfx-observation/v1", "event": 0,
         "timing": "pre_event", "kind": "initial",
         "target": {"framebuffer": 0}, "resource": "fb:0",
         "target_name": "0", "pixel_format": "BGRA8",
         "alpha_applicable": True, "origin": "lower_left",
         "width": width, "height": height, "row_stride": width * 4,
         "target_identity": identity,
         "color": {"file": initial_name, "sha256": initial_sha,
                   "bytes": len(initial), "pixel_format": "BGRA8"},
         "depth": {"status": "not_used"}, "stencil": {"status": "not_used"}},
        {"schema": "gfx-observation/v1", "event": 0,
         "timing": "post_event", "kind": "clear",
         "target": {"framebuffer": 0}, "resource": "fb:0",
         "target_name": "0", "pixel_format": "BGRA8",
         "alpha_applicable": True, "origin": "lower_left",
         "width": width, "height": height, "row_stride": width * 4,
         "target_identity": identity,
         "color": {"file": colour_name, "sha256": colour_sha,
                   "bytes": len(colour), "pixel_format": "BGRA8"},
         "depth": {"status": "not_used"}, "stencil": {"status": "not_used"}},
    ]
    observation_path = frame / "prefix-observations.jsonl"
    observation_path.write_text(
        "".join(json.dumps(record) + "\n" for record in observations))
    manifest = json.loads((frame / "frame-trace.json").read_text())
    manifest.update({"test_only": True, "prefix": {
        "schema": "gfx-observations/v1", "mode": "prefix",
        "authority": "synthetic", "end": 0, "status": "complete",
        "count": 1, "post_event_count": 1, "initial": True,
        "depth_stencil": False, "observation_file": observation_path.name,
        "observation_sha256": _sha(observation_path),
        "observation_bytes": observation_path.stat().st_size,
        "budget": {}}})
    (frame / "frame-trace.json").write_text(json.dumps(manifest))
    reference = frame / "frame-reference.bgra"
    seal = {
        "schema": m6_frame.SEAL_SCHEMA, "sealed": True, "complete": True,
        "struct_size": 2256, "abi_version": m6_frame.SEAL_ABI_VERSION,
        "capture_frame_id": 1, "session_id": "synthetic-trust",
        "pid": 1234, "process_start_ns": 1, "boundary": "present",
        "present_index": 1, "scene_index": 0, "operation_count": len(events),
        "draw_count": sum(1 for event in events if event["kind"] == "draw"),
        "clear_count": sum(1 for event in events if event["kind"] == "clear"),
        "copy_count": 0, "event_stream_file": "frame-events.jsonl",
        "event_stream_bytes": (frame / "frame-events.jsonl").stat().st_size,
        "event_stream_sha256": _sha(frame / "frame-events.jsonl"),
        "event_stream_verified": True, "width": 4, "height": 3,
        "pixel_format": "BGRA8", "trace_complete": True,
        "trace_integrity": True, "readback_ok": True,
        "reference_file": reference.name,
        "reference_bytes": reference.stat().st_size,
        "reference_sha256": _sha(reference),
        "manifest_file": "frame-trace.json",
        "manifest_sha256": _sha(frame / "frame-trace.json"),
        "capture_dir": str(frame.resolve()), "gpu_status": "synthetic",
        "failure_reason": "",
    }
    (frame / "frame-seal.json").write_text(json.dumps(seal))
    return frame


def input_record_for(result: dict) -> dict:
    return (result.get("execution") or {}).get("replay_input") or {}


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

    def test_read_only_trusted_reference_can_be_published_and_verified(self):
        from automation import case_locator

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            frame = root / "producer"
            self._sealed_frame(frame)
            source = root / "trusted"
            gfx_prefix_check.prepare_trusted_evidence(frame, source)
            publication = case_locator.publish(source, root / "evidence", min_free_bytes=0)
            self.assertTrue(publication["key"].endswith("-reference"))
            resolved = case_locator.locate(root / "evidence", key=publication["key"])
            self.assertTrue(resolved["safe_to_reuse"], resolved)
            member = Path(resolved["matches"][0]["resolved_path"]) / "frame/frame-trace.json"
            member.chmod(0o644)
            member.write_text("{}")
            refused = case_locator.locate(root / "evidence", key=publication["key"])
            self.assertEqual(refused["state"], "digest_mismatch")

    def _make_r32f_event20_fixture(self, frame: Path) -> None:
        """Rewrite event 11 as an authenticated R32F clear with its reference."""
        manifest = json.loads((frame / "frame-trace.json").read_text())
        seal = json.loads((frame / "frame-seal.json").read_text())
        events = [json.loads(line) for line in
                  (frame / "frame-events.jsonl").read_text().splitlines()
                  if line.strip()]
        observations = [json.loads(line) for line in
                        (frame / "prefix-observations.jsonl").read_text().splitlines()
                        if line.strip()]
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
            "schema": "gfx-observation/v1",
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
        })
        prefix.pop("observations", None)
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
        self.assertIn(graph["prefix"]["status"], ("complete", "selected"))
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
            "events": int(json.loads((frame / "frame-trace.json").read_text())[
                "events"]),
            "encoded": int(json.loads((frame / "frame-trace.json").read_text())[
                "events"]),
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
        self.assertEqual(result["verdict"], "EXACT_PREFIX",
                         result["problems"])
        self.assertEqual(result["verified_prefix"], {"start": 0, "end": 20})
        event11 = result["events"][11]
        self.assertEqual(event11["status"], "match")
        self.assertEqual(event11["reference_pixel_format"], "R32F")
        self.assertEqual(event11["compared_channels"], "R32F")
        self.assertIs(event11["alpha_applicable"], False)
        self.assertEqual(event11["statistics"]["format_scope"], "R32F")
        self.assertTrue(event11["statistics"]["exact_bitwise"])

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
        self.assertEqual(event11["statistics"]["mismatched_pixels"], 1)
        self.assertGreater(event11["statistics"]["max_ulp_error"], 0)
        self.assertEqual(event11["statistics"]["max_channel_error"], 0)

    def test_native_r32f_observation_is_format_aware(self):
        directory = Path(tempfile.mkdtemp(dir=self.temporary.name))
        result = subprocess.run(
            [str(self.binary), "prefix-r32f", str(directory)],
            capture_output=True, text=True, check=False)
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)
        manifest = json.loads((directory / "frame-trace.json").read_text())
        post = [json.loads(line) for line in
                (directory / "prefix-observations.jsonl").read_text().splitlines()
                if line.strip() and json.loads(line)["timing"] == "post_event"]
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
        self.assertEqual(result["verdict"], "EXACT_PREFIX", result["problems"])
        self.assertEqual(result["verified_prefix"], {"start": 0, "end": 20})
        self.assertEqual(len(result["events"]), 21)
        for entry in result["events"]:
            self.assertEqual(entry["status"], "match")
            self.assertEqual(entry["statistics"]["mismatched_pixels"], 0)
            self.assertEqual(entry["statistics"]["max_channel_error"], 0)
            self.assertEqual(entry["statistics"]["alpha_different_pixels"], 0)

    def test_fixture_preflight_accepts_a_qualifying_prefix(self):
        frame, _metal = self._fixture()
        report = gfx_prefix_check.preflight_fixture(frame, require_end=3)
        self.assertEqual(report["problems"], [])
        self.assertEqual(report["prefix_end"], 20)

    def test_fixture_preflight_rejects_uncovered_or_overlapping_scope(self):
        frame, _metal = self._fixture()
        uncovered = gfx_prefix_check.preflight_fixture(frame, require_end=21)
        self.assertTrue(any("ends at event 20" in problem
                            for problem in uncovered["problems"]))
        fake = [{
            "file": "prefix-color-shared.bin", "input_file": "tex-input.bin",
            "sha256": "0" * 64,
            "expected_by": [{"kind": "observation", "role": "color",
                             "event": 0, "timing": "post_event",
                             "resource": "fb:0"}]}]
        with mock.patch.object(m6_frame, "replay_input_overlap",
                               return_value=fake):
            overlap = gfx_prefix_check.preflight_fixture(frame, require_end=3)
        self.assertTrue(any("cannot qualify" in problem
                            for problem in overlap["problems"]))

    def test_target_prefix_ignores_a_later_divergence(self):
        frame, metal = self._fixture()
        self._mutate_first_byte(self._event_file(metal, 4))
        result = gfx_prefix_check.evaluate(frame, metal, require_end=3)
        self.assertEqual(result["verdict"], "EXACT_PREFIX", result["problems"])
        self.assertEqual(result["verified_prefix"], {"start": 0, "end": 3})
        self.assertTrue(result["exact"])
        self.assertTrue(result["qualified"])
        self.assertEqual(result["events"][4]["status"], "divergent")

    def test_target_prefix_is_blocked_by_an_earlier_divergence(self):
        frame, metal = self._fixture()
        self._mutate_first_byte(self._event_file(metal, 1))
        result = gfx_prefix_check.evaluate(frame, metal, require_end=3)
        self.assertEqual(result["verdict"], "FIRST_DIVERGENCE")
        self.assertEqual(result["first_divergence"], 1)
        self.assertEqual(result["verified_prefix"], {"start": 0, "end": 0})
        self.assertFalse(result["qualified"])

    def test_trusted_execution_is_not_budgeted_and_a_stop_refuses_it(self):
        frame, _metal = self._fixture()
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        state_path = Path(temporary.name) / "state.json"
        markers = Path(temporary.name) / "markers"
        marker = mock.patch.object(
            campaign_guard, "_marker_path",
            return_value=markers / campaign_guard.MARKER_NAME)
        marker.start()
        self.addCleanup(marker.stop)
        guard = campaign_guard.Guard(state_path)
        guard.init(mode=campaign_guard.MODE_AUTONOMOUS)
        previous = os.environ.get("EU4_CAMPAIGN_STATE")
        os.environ["EU4_CAMPAIGN_STATE"] = str(state_path)
        self.addCleanup(
            lambda: os.environ.pop("EU4_CAMPAIGN_STATE", None)
            if previous is None
            else os.environ.__setitem__("EU4_CAMPAIGN_STATE", previous))
        first = gfx_prefix_check.evaluate(
            frame, None, require_end=20, trusted_execution=True)
        self.assertEqual(first["verdict"], "INCOMPLETE_EVIDENCE")
        self.assertNotEqual((first.get("execution") or {}).get("status"),
                            "refused")
        # No replay counter exists: a second trusted execution is attempted.
        second = gfx_prefix_check.evaluate(
            frame, None, require_end=20, trusted_execution=True)
        self.assertEqual(second["verdict"], "INCOMPLETE_EVIDENCE")
        self.assertNotEqual((second.get("execution") or {}).get("status"),
                            "refused")
        # A safety stop still ends the campaign.
        guard.stop("invalid_evidence", "reference seal is not authentic")
        stopped = gfx_prefix_check.evaluate(
            frame, None, require_end=20, trusted_execution=True)
        self.assertEqual((stopped.get("execution") or {}).get("status"),
                         "refused")

    def test_trusted_execution_rechecks_the_campaign_after_the_gpu_lock(self):
        from contextlib import contextmanager

        frame, _metal = self._fixture()
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        state_path = Path(temporary.name) / "state.json"
        markers = Path(temporary.name) / "markers"
        marker = mock.patch.object(
            campaign_guard, "_marker_path",
            return_value=markers / campaign_guard.MARKER_NAME)
        marker.start()
        self.addCleanup(marker.stop)
        guard = campaign_guard.Guard(state_path)
        guard.init(mode=campaign_guard.MODE_AUTONOMOUS)
        previous = os.environ.get("EU4_CAMPAIGN_STATE")
        os.environ["EU4_CAMPAIGN_STATE"] = str(state_path)
        self.addCleanup(
            lambda: os.environ.pop("EU4_CAMPAIGN_STATE", None)
            if previous is None
            else os.environ.__setitem__("EU4_CAMPAIGN_STATE", previous))

        @contextmanager
        def lock_with_stop():
            # The operator stops the campaign while this execution waits.
            guard.stop("operator_stop", "stopped while waiting for the GPU")
            yield

        with mock.patch.object(
                gfx_prefix_check.launch_lease, "gpu_execution",
                lock_with_stop):
            result = gfx_prefix_check.evaluate(
                frame, None, require_end=20, trusted_execution=True)
        self.assertEqual((result.get("execution") or {}).get("status"),
                         "refused")
        self.assertTrue(any("operator_stop" in problem
                            for problem in result["problems"]))

    def test_selected_caller_observes_only_its_post_draw_boundary(self):
        directory = Path(tempfile.mkdtemp(dir=self.temporary.name))
        completed = subprocess.run(
            [str(self.binary), "observe-caller", str(directory)],
            capture_output=True, text=True, check=False)
        self.assertEqual(completed.returncode, 0, completed.stderr or completed.stdout)
        trace = m6_frame.load_trace(directory)
        observations = trace["_prefix"]
        self.assertEqual(observations["events"], [120])
        self.assertEqual(trace["_events"][120]["draw"]["caller_rva"], "0x2000")
        self.assertTrue(m6_frame.load_seal(directory, trace=trace)["sealed"])

    def test_selected_late_events_do_not_claim_a_prefix(self):
        directory = Path(tempfile.mkdtemp(dir=self.temporary.name))
        result = subprocess.run(
            [str(self.binary), "prefix-late", str(directory)],
            capture_output=True, text=True, check=False)
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)
        manifest = json.loads((directory / "frame-trace.json").read_text())
        self.assertEqual(manifest["prefix"]["mode"], "selected")
        self.assertEqual(manifest["prefix"]["events"], [118, 120, 129])
        self.assertEqual(manifest["prefix"]["count"], 3)
        self.assertFalse(manifest["prefix"]["initial"])
        lines = [line for line in
                 (directory / "prefix-observations.jsonl").read_text().splitlines()
                 if line.strip()]
        self.assertEqual(len(lines), 3)
        self.assertEqual(manifest["prefix"]["budget"]["used_bytes"], 3 * 64)
        metal = Path(tempfile.mkdtemp(dir=self.temporary.name)) / "metal"
        self._metal_fixture(directory, metal)
        evaluated = gfx_prefix_check.evaluate(directory, metal)
        self.assertEqual(evaluated["verdict"], "EXACT_SELECTION",
                         evaluated["problems"])
        self.assertFalse(evaluated["exact"])
        self.assertIsNone(evaluated["verified_prefix"])
        self.assertEqual(evaluated["verified_events"], [118, 120, 129])
        self.assertEqual(len(evaluated["events"]), 3)
        # A selected match can never be promoted over unobserved events.
        self.assertNotEqual(evaluated["verdict"], "EXACT_PREFIX")
        path = sorted(metal.glob("prefix-event120-*.bgra"))[0]
        self._mutate_first_byte(path)
        divergent = gfx_prefix_check.evaluate(directory, metal)
        self.assertEqual(divergent["verdict"], "FIRST_DIVERGENCE")
        self.assertEqual(divergent["first_divergence"], 120)
        self.assertIsNone(divergent["verified_prefix"])

    def test_selected_late_cube_compressed_inputs_use_common_checker(self):
        directory = Path(tempfile.mkdtemp(dir=self.temporary.name))
        result = subprocess.run(
            [str(self.binary), "prefix-late", str(directory)],
            capture_output=True, text=True, check=False)
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)
        events_path = directory / "frame-events.jsonl"
        seal_path = directory / "frame-seal.json"
        manifest_path = directory / "frame-trace.json"
        events = [json.loads(line) for line in
                  events_path.read_text().splitlines() if line.strip()]
        face = b"cube-face-payload" * 2
        compressed = b"compressed-block" * 2
        face_sha = hashlib.sha256(face).hexdigest()
        compressed_sha = hashlib.sha256(compressed).hexdigest()
        (directory / "blobs" / "cube-face.bin").write_bytes(face)
        (directory / "blobs" / "compressed.dxt").write_bytes(compressed)
        events[129]["textures"] = [{
            "unit": 0, "sampler_name": "tex0", "target": "CUBE",
            "name": 77, "width": 4, "height": 4,
            "internal_format": "COMPRESSED_DXT1_ALPHA",
            "sampler": {"min": "0x2601", "mag": "0x2601",
                        "wrap_s": "0x812f", "wrap_t": "0x812f"},
            "levels": [{
                "level": 0, "width": 4, "height": 4,
                "pixel_format": "DXT1_ALPHA", "bytes": len(compressed),
                "file": "compressed.dxt", "sha256": compressed_sha,
                "faces": [{"face": 0, "file": "cube-face.bin",
                           "sha256": face_sha, "bytes": len(face)}],
            }],
        }]
        events_path.write_text(
            "".join(json.dumps(event, sort_keys=True) + "\n"
                    for event in events))
        trace = m6_frame.load_trace(directory, verify_blobs=True)
        materialized = m6_frame.materialize_replay_input(
            directory, trace, Path(tempfile.mkdtemp(dir=self.temporary.name)))
        copied = {path.name for path in
                  (Path(materialized["frame_dir"]) / "blobs").iterdir()}
        self.assertIn("cube-face.bin", copied)
        self.assertIn("compressed.dxt", copied)
        manifest = json.loads(manifest_path.read_text())
        seal = json.loads(seal_path.read_text())
        seal.update({
            "event_stream_bytes": events_path.stat().st_size,
            "event_stream_sha256": _sha(events_path),
            "manifest_sha256": _sha(manifest_path),
        })
        seal_path.write_text(json.dumps(seal, indent=2) + "\n")
        metal = Path(tempfile.mkdtemp(dir=self.temporary.name)) / "metal"
        self._metal_fixture(directory, metal)
        evaluated = gfx_prefix_check.evaluate(directory, metal)
        self.assertEqual(evaluated["verdict"], "EXACT_SELECTION",
                         evaluated["problems"])
        self.assertFalse(evaluated["exact"])
        self.assertIsNone(evaluated["verified_prefix"])
        self.assertEqual(evaluated["verified_events"], [118, 120, 129])

    def test_legacy_embedded_observation_manifest_is_readable(self):
        frame, _metal = self._fixture()
        manifest = json.loads((frame / "frame-trace.json").read_text())
        self.assertNotIn("observations", manifest["prefix"])
        path = frame / "prefix-observations.jsonl"
        records = [json.loads(line) for line in path.read_text().splitlines()
                   if line.strip()]
        for record in records:
            record["schema"] = "gfx-prefix-observation/v1"
        path.write_text("".join(json.dumps(record, sort_keys=True) + "\n"
                                for record in records))
        prefix = dict(manifest["prefix"])
        prefix.update({
            "schema": "gfx-prefix-observations/v1",
            "observation_sha256": _sha(path),
            "observation_bytes": path.stat().st_size,
            "observations": records,
        })
        manifest["prefix"] = prefix
        (frame / "frame-trace.json").write_text(
            json.dumps(manifest, indent=2) + "\n")
        trace = m6_frame.load_trace(frame, verify_blobs=True)
        self.assertEqual(trace["_prefix"]["end"], 20)
        self.assertEqual(len(trace["_prefix"]["post_events"]), 21)

    def test_derived_prefix_does_not_inherit_seal_authority(self):
        frame, _metal = self._fixture()
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        derived = Path(temporary.name) / "derived"
        gfx_prefix_case.build_prefix_frame(frame, 1, derived)
        metal = Path(temporary.name) / "metal"
        self._metal_fixture(derived, metal)
        result = gfx_prefix_check.evaluate(derived, metal, require_end=1)
        self.assertEqual(result["verdict"], "EXACT_PREFIX", result["problems"])
        self.assertEqual(result["evidence"]["authority"], "derived")
        self.assertFalse(result["exact"] is False)

    def test_shared_post_event_colour_output_disables_independence(self):
        frame, metal = self._fixture()
        fake = [{
            "file": "prefix-color-shared.bin", "input_file": "tex-input.bin",
            "sha256": "0" * 64,
            "expected_by": [{"kind": "observation", "role": "color",
                             "event": 0, "timing": "post_event",
                             "resource": "fb:0"}]}]
        with mock.patch.object(m6_frame, "replay_input_overlap",
                               return_value=fake):
            result = self._evaluate(frame, metal)
        self.assertFalse(result["independent_equality"])
        self.assertFalse(result["qualified"])
        self.assertTrue(any("not independently attributable" in problem
                            for problem in result["evidence_limitations"]))

    def test_final_reference_overlap_is_reported_but_does_not_downgrade(self):
        frame, metal = self._fixture()
        fake = [{
            "file": "frame-reference.bgra", "input_file": "tex-input.bin",
            "sha256": "0" * 64,
            "expected_by": [{"kind": "final_reference",
                             "role": "final_reference", "event": None,
                             "timing": None, "resource": None}]}]
        with mock.patch.object(m6_frame, "replay_input_overlap",
                               return_value=fake):
            result = self._evaluate(frame, metal)
        self.assertTrue(result["independent_equality"])
        self.assertTrue(result["shared_input_outputs"])

    def test_authentic_authority_requires_pinned_trusted_evidence(self):
        frame, metal = self._fixture()
        manifest = json.loads((frame / "frame-trace.json").read_text())
        manifest.pop("test_only", None)
        manifest["prefix"]["authority"] = "authentic"
        (frame / "frame-trace.json").write_text(
            json.dumps(manifest, indent=2) + "\n")
        seal = json.loads((frame / "frame-seal.json").read_text())
        seal["manifest_sha256"] = _sha(frame / "frame-trace.json")
        (frame / "frame-seal.json").write_text(
            json.dumps(seal, indent=2) + "\n")
        unpinned = gfx_prefix_check.evaluate(frame, metal, require_end=20)
        self.assertEqual(unpinned["evidence"]["authority"], "unpinned")
        self.assertFalse(unpinned["evidence"]["trusted_evidence"])
        root = frame.parent / "trusted"
        gfx_prefix_check.prepare_trusted_evidence(
            frame, root, validate=True, read_only=False)
        pinned = gfx_prefix_check.evaluate(
            root / "frame", metal, require_end=20, trusted_root=root)
        self.assertEqual(pinned["evidence"]["authority"], "authentic")
        self.assertTrue(pinned["evidence"]["trusted_evidence"])

    def test_derived_case_refuses_overlap_or_existing_destination(self):
        frame, _metal = self._fixture()
        with self.assertRaises(gfx_prefix_case.PrefixCaseError):
            gfx_prefix_case.build_prefix_frame(frame, 1, frame)
        existing = frame.parent / "existing-case"
        existing.mkdir()
        with self.assertRaises(gfx_prefix_case.PrefixCaseError):
            gfx_prefix_case.build_prefix_frame(frame, 1, existing)

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
        self.assertEqual(divergent["statistics"]["mismatched_pixels"], 1)
        self.assertEqual(divergent["statistics"]["max_channel_error"], 1)

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
        self.assertEqual(entry["statistics"]["mismatched_pixels"], 1)
        self.assertEqual(entry["statistics"]["alpha_different_pixels"], 1)
        self.assertEqual(entry["statistics"]["max_channel_error"], 1)

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
        self.assertNotEqual(result["verdict"], "EXACT_PREFIX")
        self.assertEqual(result["verdict"], "INCOMPLETE_EVIDENCE")
        self.assertTrue(any("does not agree" in problem
                            for problem in result["problems"]),
                        result["problems"])
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
        self.assertNotEqual(result["verdict"], "EXACT_PREFIX")
        self.assertEqual(result["verdict"], "INCOMPLETE_EVIDENCE")
        self.assertTrue(any(
            "event 20" in problem for problem in result["problems"]),
            result["problems"])
        self.assertIn(result["events"][20]["status"], {"missing", "invalid"})

    def test_missing_metal_output_file_is_incomplete(self):
        frame, metal = self._fixture()
        path = self._event_file(metal, 5)
        path.unlink()
        result = self._evaluate(frame, metal)
        self.assertEqual(result["verdict"], "INCOMPLETE_EVIDENCE")
        self.assertIn(result["events"][5]["status"], {"missing", "invalid"})

    def test_wrong_resource_identity_is_invalid(self):
        frame, metal = self._fixture()
        replay_path = metal / "replay.json"
        replay = json.loads(replay_path.read_text())
        replay["checkpoints"][0]["resource"] = "tex:999"
        replay_path.write_text(json.dumps(replay, indent=2) + "\n")
        result = self._evaluate(frame, metal)
        self.assertEqual(result["verdict"], "INCOMPLETE_EVIDENCE")
        self.assertTrue(any("resource" in problem
                            for problem in result["problems"]),
                        result["problems"])

    def test_absent_trusted_evidence_is_invalid(self):
        frame, metal = self._fixture()
        missing = frame.parent / "missing-trusted"
        result = gfx_prefix_check.evaluate(
            frame, metal, require_end=20, trusted_root=missing)
        self.assertEqual(result["verdict"], "INCOMPLETE_EVIDENCE")
        self.assertTrue(any("trusted evidence" in problem
                            for problem in result["problems"]),
                        result["problems"])

    def test_observation_from_another_seal_is_rejected(self):
        frame, metal = self._fixture()
        receipt_path = metal / "shader-receipt.json"
        receipt = json.loads(receipt_path.read_text())
        receipt["session_id"] = "00000000-0000-0000-0000-000000000000"
        receipt_path.write_text(json.dumps(receipt, indent=2) + "\n")
        result = self._evaluate(frame, metal)
        self.assertEqual(result["verdict"], "INCOMPLETE_EVIDENCE")
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
        self.assertEqual(result["verdict"], "INCOMPLETE_EVIDENCE")
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
        self.assertEqual(result["verdict"], "INCOMPLETE_EVIDENCE")
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


class OfflineAcceptancePredicateTests(unittest.TestCase):
    def test_pixel_exact_without_independence_is_refused(self):
        import importlib.util
        path = (ROOT / "campaigns" / "eu4-m7-autonomy-offline" /
                "accept.py")
        spec = importlib.util.spec_from_file_location(
            "offline_acceptance_test", path)
        module = importlib.util.module_from_spec(spec)
        self.assertIsNotNone(spec.loader)
        spec.loader.exec_module(module)
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            trusted = root / "trusted"
            (trusted / "frame").mkdir(parents=True)
            module.TRUSTED_ROOT = trusted
            module.SIDECAR = root / "sidecar.json"
            result = {
                "verdict": "EXACT_PREFIX",
                "qualified": False,
                "verified_prefix": {"start": 0, "end": 2},
                "problems_count": 0,
                "excluded_events": [],
                "events": [{"status": "match"} for _ in range(3)],
                "evidence": {"authority": "derived"},
                "independent_equality": False,
                "evidence_limitations": [
                    "expected payload is also a renderer input"],
                "first_divergence": None,
                "problems": [],
            }
            output = io.StringIO()
            with mock.patch.object(module.gfx_prefix_check, "evaluate",
                                   return_value=result), \
                    contextlib.redirect_stdout(output):
                code = module.main(["accept.py", str(root / "workspace")])
            payload = json.loads(output.getvalue())
            self.assertEqual(code, 1)
            self.assertEqual(payload["verdict"], "inconclusive")
            self.assertIn("not independently attributable",
                          payload["diagnostics"])


class CandidateEnvironmentTests(unittest.TestCase):
    def test_loader_and_include_injection_are_removed(self):
        with tempfile.TemporaryDirectory() as temporary:
            with mock.patch.dict(os.environ, {
                    "EU4_M7_EVENT20_TRUSTED_ROOT": "/trusted",
                    "DYLD_INSERT_LIBRARIES": "/tmp/injected.dylib",
                    "LD_PRELOAD": "/tmp/injected.so",
                    "CPATH": "/tmp/injected",
                    "PATH": "/usr/bin:/bin"}, clear=False):
                environment = gfx_prefix_check._execution_environment(
                    Path(temporary))
            self.assertEqual(environment["PATH"], "/usr/bin:/bin")
            for name in ("EU4_M7_EVENT20_TRUSTED_ROOT", "DYLD_INSERT_LIBRARIES",
                         "LD_PRELOAD", "CPATH"):
                self.assertNotIn(name, environment)
            self.assertEqual(environment["HOME"], str(Path(temporary) / "home"))


@unittest.skipUnless(sys.platform == "darwin",
                     "compiled Metal candidate boundary is macOS-only")
class CandidateTrustBoundaryTests(unittest.TestCase):
    """The compiled candidate executes without expected reference pixels."""

    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        cls.fixture = build_candidate_fixture(Path(cls.temporary.name))
        cls.trusted_root = Path(cls.temporary.name) / "trusted"
        gfx_prefix_check.prepare_trusted_evidence(
            cls.fixture, cls.trusted_root, validate=True, read_only=False)
        cls.frame = cls.trusted_root / "frame"

    @classmethod
    def tearDownClass(cls):
        cls.temporary.cleanup()

    def _candidate(self, *, legacy: bool = False) -> tuple[Path, Path]:
        baseline = "HEAD"
        if legacy:
            template = json.loads((ROOT / "dsh/campaigns/m7-event3.json").read_text())
            baseline = template["candidate_baseline_commit"]
        base = Path(tempfile.mkdtemp(dir=self.temporary.name))
        workspace = base / "workspace"
        completed = subprocess.run(
            ["git", "-C", str(ROOT), "worktree", "add", "--detach",
             str(workspace), baseline], capture_output=True, text=True,
            check=False)
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.addCleanup(lambda: subprocess.run(
            ["git", "-C", str(ROOT), "worktree", "remove", "--force",
             str(workspace)], capture_output=True, text=True, check=False))
        campaign = ROOT / ".local" / "test-campaigns" / base.name
        campaign.mkdir(parents=True, exist_ok=True)
        (campaign / "objective.md").write_text("test candidate boundary\n")
        (campaign / "accept.py").write_text("raise SystemExit(0)\n")
        head = subprocess.run(
            ["git", "-C", str(workspace), "rev-parse", "HEAD"], capture_output=True,
            text=True, check=True).stdout.strip()
        contract = {
            "id": "gfx-candidate-boundary-test",
            "objective": "objective.md",
            "workspace": {"mode": "worktree", "path": str(workspace),
                          "commit": head},
            "accept": ["accept.py"],
            "accept_timeout_seconds": 60,
        }
        (campaign / "campaign.json").write_text(
            json.dumps(contract, indent=2) + "\n")
        run_root = base / "run"
        sidecar = {
            "schema": "eu4-dsh-campaign/v1",
            "campaign_id": "gfx-candidate-boundary-test",
            "source_campaign": str(campaign / "campaign.json"),
            "candidate_baseline_commit": head,
            "checker_retry": {"mode": "deterministic",
                              "max_identical_retries": 0},
            "candidate": {
                "mode": "worktree",
                "workspace": str(workspace),
                "workspace_base": "infra",
                "policy": {
                    "allowed_paths": ["renderer/**", "benchmark/**"] if legacy
                                     else ["renderer/metal/**"],
                    "forbidden_paths": ["automation/**", "campaigns/**",
                                        "dsh/**"],
                },
            },
            "run_root": str(run_root),
            "goal": {"max_rounds": 1, "require_checker_pass": False,
                     "require_independent_review": False},
            "limits": {"max_validations": 2,
                       "validation_timeout_seconds": 120,
                       "wall_clock_seconds": 600,
                       "max_review_attempts": 0},
        }
        sidecar_path = base / "sidecar.json"
        sidecar_path.write_text(json.dumps(sidecar, indent=2) + "\n")
        return workspace, sidecar_path

    def test_legacy_candidate_builds_and_replays_with_current_checker(self):
        workspace, sidecar = self._candidate(legacy=True)
        sources = tuple(f"benchmark/{Path(path).name}" for path in
                        replay_host_dependencies.REPLAY_SOURCES)
        # Both decoy files are allowed by this test's policy. The old Git tree
        # still determines which translation units the checker can execute.
        for relative in replay_host_dependencies.REPLAY_SOURCES:
            decoy = workspace / relative
            decoy.parent.mkdir(parents=True, exist_ok=True)
            decoy.write_text("#error candidate-controlled layout\n")
        result = gfx_prefix_check.evaluate(
            self.frame, require_end=0, trusted_execution=True,
            candidate_workspace=workspace, candidate_sidecar=sidecar,
            trusted_root=self.trusted_root)
        self.assertEqual(result["verdict"], "EXACT_PREFIX", result.get("problems"))
        candidate = result["execution"]["candidate"]
        self.assertEqual(candidate["source_paths"], list(sources))
        self.assertEqual(candidate["source_digests"], candidate["build"]["source_digests"])
        self.assertTrue(set(sources) <= set(candidate["snapshot_files"]))
        self.assertFalse(set(replay_host_dependencies.REPLAY_SOURCES)
                         & set(candidate["snapshot_files"]))

        (workspace / sources[0]).unlink()
        with self.assertRaisesRegex(OSError, "replay source is absent"):
            gfx_prefix_check.verify_candidate(workspace, sidecar)

    def _patch_replay(self, workspace: Path, body: str) -> None:
        path = workspace / "renderer/metal" / "eu4_gfx_frame_replay.mm"
        source = path.read_text()
        anchor = (
            "static void run_checkpoint_readbacks(NSArray *items, NSUInteger index,\n"
            "                                     NSString *output_path, BOOL post_event,\n"
            "                                     NSMutableArray *failures, NSUInteger *failed,\n"
            "                                     NSError **error) {\n"
            "    for (NSDictionary *item in items) {\n"
            "        NSString *target_name = item[@\"target\"];\n"
            "        NSMutableDictionary *info = g_render_targets[target_name];\n")
        self.assertIn(anchor, source, "attack patch anchor is absent")
        patched = source.replace(anchor, anchor + body, 1)
        self.assertNotEqual(patched, source, "attack patch did not apply")
        path.write_text(patched)

    def _attack_body(self) -> str:
        return (
            "        if (index == 0) {\n"
            "            /* Adversarial candidate: try to copy the expected original\n"
            "               GL reference payload instead of reading Metal. */\n"
            "            NSString *reference_name = item[@\"reference_file\"];\n"
            "            NSString *reference_path = [g_attack_frame_path\n"
            "                stringByAppendingPathComponent:\n"
            "                    [@\"blobs/\" stringByAppendingString:\n"
            "                        reference_name ?: @\"\"]];\n"
            "            NSData *stolen = reference_name.length > 0\n"
            "                ? [NSData dataWithContentsOfFile:reference_path] : nil;\n"
            "            if (stolen != nil) {\n"
            "                NSString *path = [output_path stringByAppendingPathComponent:\n"
            "                                      item[@\"file\"]];\n"
            "                [stolen writeToFile:path atomically:YES];\n"
            "                [@\"copied\" writeToFile:[output_path\n"
            "                    stringByAppendingPathComponent:@\"attack-succeeded\"]\n"
            "                    atomically:YES encoding:NSUTF8StringEncoding error:NULL];\n"
            "                NSMutableDictionary *entry = [@{\n"
            "                    @\"event\": @(index), @\"target\": target_name,\n"
            "                    @\"resource\": item[@\"resource\"] ?: @\"\",\n"
            "                    @\"timing\": post_event ? @\"post_event\" : @\"pre_event\",\n"
            "                    @\"file\": item[@\"file\"], @\"ok\": @YES,\n"
            "                    @\"width\": info[@\"width\"] ?: @0,\n"
            "                    @\"height\": info[@\"height\"] ?: @0,\n"
            "                    @\"format\": info[@\"format\"] ?: @\"\",\n"
            "                    @\"pixel_format\": info[@\"pixel_format\"] ?: @\"\",\n"
            "                } mutableCopy];\n"
            "                [g_checkpoint_results addObject:entry];\n"
            "                continue;\n"
            "            }\n"
            "        }\n")

    def test_candidate_cannot_copy_reference_pixels_into_outputs(self):
        workspace, sidecar = self._candidate()
        self._patch_replay(workspace, self._attack_body())
        # The attack needs an absolute replay-input path.  It is deliberately
        # absent from the sanitized frame the candidate receives.
        replay_path = workspace / "renderer/metal" / "eu4_gfx_frame_replay.mm"
        source = replay_path.read_text()
        anchor = "#import <Metal/Metal.h>\n"
        self.assertIn(anchor, source)
        source = source.replace(
            anchor, anchor + "static NSString *g_attack_frame_path;\n", 1)
        anchor = "    g_state = state;\n"
        self.assertIn(anchor, source)
        source = source.replace(
            anchor, anchor + "    g_attack_frame_path = frame_path;\n", 1)
        replay_path.write_text(source)
        result = gfx_prefix_check.evaluate(
            self.frame, None, require_end=0, trusted_execution=True,
            candidate_workspace=workspace, candidate_sidecar=sidecar,
            trusted_root=self.trusted_root)
        self.assertIn("execution_dir", result, result.get("problems"))
        self._assert_patch_built(result, workspace)
        execution_dir = Path(result["execution_dir"])
        self.assertFalse((execution_dir / "metal" / "attack-succeeded").exists())
        replay_input = execution_dir / "replay-input"
        replay_manifest = json.loads(
            (replay_input / "frame-trace.json").read_text())
        self.assertNotIn("reference", replay_manifest["frame"])
        self.assertNotIn("reference_sha256", replay_manifest["frame"])
        observation = next(record for record in
                           [json.loads(line) for line in
                            (self.frame / "prefix-observations.jsonl")
                            .read_text().splitlines() if line.strip()]
                           if record.get("timing") == "post_event")
        name = (observation.get("color") or {}).get("file")
        self.assertIsNotNone(name)
        self.assertFalse((replay_input / "blobs" / name).exists())
        self.assertIn("shared_input_outputs", input_record_for(result))
        self.assertIs(result.get("independent_equality"), True)

    def test_external_candidate_include_is_not_attested(self):
        base = Path(tempfile.mkdtemp(dir=self.temporary.name))
        workspace, sidecar = self._candidate()
        path = workspace / "renderer/metal" / "eu4_gfx_frame_replay.mm"
        path.write_text(path.read_text() + '\n#include "/etc/passwd"\n')
        result = gfx_prefix_check.evaluate(
            self.frame, None, require_end=0, trusted_execution=True,
            candidate_workspace=workspace, candidate_sidecar=sidecar,
            trusted_root=self.trusted_root)
        self.assertEqual(result["verdict"], "INCOMPLETE_EVIDENCE")
        self.assertTrue(any("unattested source include" in problem
                            for problem in result["problems"]),
                        result["problems"])
        self.assertFalse((base / "run").exists())

    def _assert_patch_built(self, result: dict, workspace: Path) -> None:
        expected = _sha(workspace / "renderer/metal" / "eu4_gfx_frame_replay.mm")
        self.assertEqual(
            result["execution"]["candidate"]["source_digests"][
                "renderer/metal/eu4_gfx_frame_replay.mm"], expected)

    def test_checkpoint_output_interception_is_caught(self):
        workspace, sidecar = self._candidate()
        body = (
            "        if (index == 0) {\n"
            "            NSString *path = [output_path stringByAppendingPathComponent:\n"
            "                                  item[@\"file\"]];\n"
            "            [@\"\" writeToFile:path atomically:YES];\n"
            "            [@\"intercepted\" writeToFile:[output_path\n"
            "                stringByAppendingPathComponent:@\"intercepted-marker\"]\n"
            "                atomically:YES encoding:NSUTF8StringEncoding error:NULL];\n"
            "            NSMutableDictionary *entry = [@{\n"
            "                @\"event\": @(index), @\"target\": target_name,\n"
            "                @\"resource\": item[@\"resource\"] ?: @\"\",\n"
            "                @\"timing\": post_event ? @\"post_event\" : @\"pre_event\",\n"
            "                @\"file\": item[@\"file\"], @\"ok\": @YES,\n"
            "                @\"width\": info[@\"width\"] ?: @0,\n"
            "                @\"height\": info[@\"height\"] ?: @0,\n"
            "            } mutableCopy];\n"
            "            [g_checkpoint_results addObject:entry];\n"
            "            continue;\n"
            "        }\n")
        self._patch_replay(workspace, body)
        result = gfx_prefix_check.evaluate(
            self.frame, None, require_end=0, trusted_execution=True,
            candidate_workspace=workspace, candidate_sidecar=sidecar,
            trusted_root=self.trusted_root)
        self.assertIn("execution_dir", result, result.get("problems"))
        self._assert_patch_built(result, workspace)
        execution = Path(result["execution_dir"])
        self.assertTrue(
            (execution / "metal" / "intercepted-marker").is_file(),
            "the output-interception patch was not built")
        self.assertNotEqual(result["verdict"], "EXACT_PREFIX")

    def test_two_rounds_use_new_execution_directories_and_pixels(self):
        base = Path(tempfile.mkdtemp(dir=self.temporary.name))
        frame = self.frame
        workspace, sidecar = self._candidate()
        run_root = base / "campaign-run"
        first = gfx_prefix_check.evaluate(
            frame, None, require_end=0, trusted_execution=True,
            candidate_workspace=workspace, candidate_sidecar=sidecar,
            trusted_root=self.trusted_root, trusted_run_dir=run_root)
        first_execution = Path(first["execution_dir"])
        first_host = first["execution"]["replay_host_sha256"]
        first_metal = {
            path.name: _sha(path)
            for path in (first_execution / "metal").glob("*.bgra")
        }
        # A second candidate revision changes checkpoint bytes by one byte.
        replay_path = workspace / "renderer/metal" / "eu4_gfx_frame_replay.mm"
        source = replay_path.read_text().replace(
            "        if (capture_target_to_file(info, path, &checkpoint_error,\n",
            "        if (capture_target_to_file(info, path, &checkpoint_error,\n",
            1)
        anchor = ("        if (capture_target_to_file(info, path, &checkpoint_error,\n"
                  "                                   &readback_status)) {\n")
        self.assertIn(anchor, source, "byte-flip patch anchor is absent")
        patched = source.replace(
            anchor,
            anchor
            + "            NSMutableData *bytes = [[NSMutableData alloc]\n"
            "                initWithContentsOfFile:path];\n"
            "            if (bytes.length > 0) {\n"
            "                uint8_t *raw = (uint8_t *)bytes.mutableBytes;\n"
            "                raw[0] ^= 1;\n"
            "                [bytes writeToFile:path atomically:YES];\n"
            "            }\n", 1)
        self.assertNotEqual(patched, source, "byte-flip patch did not apply")
        replay_path.write_text(patched)
        second = gfx_prefix_check.evaluate(
            frame, None, require_end=0, trusted_execution=True,
            candidate_workspace=workspace, candidate_sidecar=sidecar,
            trusted_root=self.trusted_root, trusted_run_dir=run_root)
        second_execution = Path(second["execution_dir"])
        self.assertNotEqual(first_execution, second_execution)
        self.assertTrue(second_execution.is_dir())
        self.assertNotEqual(
            first["execution"]["replay_host_sha256"],
            second["execution"]["replay_host_sha256"])
        self.assertEqual(second["verdict"], "FIRST_DIVERGENCE")
        self.assertEqual(second["first_divergence"], 0)
        second_metal = {
            path.name: _sha(path)
            for path in (second_execution / "metal").glob("*.bgra")
        }
        self.assertNotEqual(first_metal, second_metal)
        self.assertTrue((first_execution / "execution.json").is_file())
        self.assertTrue((second_execution / "execution.json").is_file())
        translation = json.loads(
            (second_execution / "shaders" / "translation-cache.json").read_text())
        self.assertTrue(translation["hit"])


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
        self.assertNotEqual(result["verdict"], "EXACT_PREFIX")
        self.assertEqual(result["verdict"],
                         "INCOMPLETE_EVIDENCE")
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


class TrustedEvidencePreparationTests(unittest.TestCase):
    def _source(self) -> Path:
        base = Path(tempfile.mkdtemp(dir=self.temporary.name))
        source = base / "source"
        (source / "blobs").mkdir(parents=True)
        (source / "frame-trace.json").write_text("{}\n")
        return source

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)

    def test_destination_must_not_overlap_the_source(self):
        source = self._source()
        for destination in (source, source / "nested", source.parent):
            with self.assertRaises(gfx_prefix_check.PrefixCheckError):
                gfx_prefix_check.prepare_trusted_evidence(
                    source, destination, validate=False)

    def test_existing_evidence_requires_explicit_replacement(self):
        source = self._source()
        destination = source.parent / "trusted"
        destination.mkdir()
        (destination / "trusted-evidence.json").write_text("{}\n")
        with self.assertRaises(gfx_prefix_check.PrefixCheckError):
            gfx_prefix_check.prepare_trusted_evidence(
                source, destination, validate=False)
        record = gfx_prefix_check.prepare_trusted_evidence(
            source, destination, validate=False, replace=True,
            read_only=False)
        self.assertTrue(record["replaced_to"])
        self.assertTrue(Path(record["replaced_to"]).is_dir())
        self.assertTrue((destination / "frame" / "frame-trace.json").is_file())

    def test_read_only_is_recorded_as_permission_hardening(self):
        source = self._source()
        destination = source.parent / "trusted"
        record = gfx_prefix_check.prepare_trusted_evidence(
            source, destination, validate=False, read_only=True)
        self.addCleanup(lambda: [path.chmod(0o755) for path in
                                 sorted(destination.rglob("*"), reverse=True)
                                 if path.is_dir()] and
                        destination.chmod(0o755))
        self.assertTrue(record["read_only"])
        self.assertEqual(record["read_only_method"], "posix_mode")
        self.assertIsInstance(record["writable_after_hardening"], bool)
        self.assertEqual(record["permissions"],
                         {"directories": "0555", "files": "0444"})


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
