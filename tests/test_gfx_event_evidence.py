"""Focused tests for event-level graphics evidence inspection."""

from __future__ import annotations

import contextlib
import hashlib
import io
import json
import shutil
import struct
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from automation import (agent_tools, gfx_event_evidence, gfx_frame_graph,
                        gfx_independent_replay, gfx_replay_case)  # noqa: E402
from tests.test_gfx_frame import _write_blob, build_synthetic_trace  # noqa: E402

WIDTH, HEIGHT = 4, 3


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _reference() -> bytes:
    result = bytearray()
    for row in range(HEIGHT):
        for column in range(WIDTH):
            value = [0, 0, 0, 255]
            if (row, column) == (1, 2):
                value = [50, 60, 70, 255]
            result.extend(value)
    return bytes(result)


def _wrap(frame: Path, *, session: str = "session", frame_id: int = 1,
          caller: str = "0x100", metal: bool = True) -> tuple[Path, Path]:
    """Seal a synthetic trace and add observations plus a bound replay."""
    reference = _reference()
    build_synthetic_trace(frame, reference=reference)
    events = [json.loads(line) for line in
              (frame / "frame-events.jsonl").read_text().splitlines()]
    target = {"framebuffer": 3, "width": WIDTH, "height": HEIGHT,
              "color_attachment": {"type": "texture", "name": 9,
                                   "internal_format": "RGB8"}}
    events[0]["target"] = target
    events[0]["state"] = {"scissor": {"enabled": False,
                                      "box": [0, 0, WIDTH, HEIGHT]},
                          "color_mask": [1, 1, 1, 1]}
    events[1]["target"] = target
    events[1]["draw"]["caller_rva"] = caller
    uniform = _write_blob(frame, "uniform-real.bin",
                          struct.pack("<8f", 0.125, -0.5, 1.0, 2.0,
                                      3.0, 4.0, 5.0, 6.0))
    events[1]["program"]["uniforms"][0].update(
        **{key: uniform[key] for key in ("file", "sha256", "bytes")})
    events_bytes = ("\n".join(json.dumps(item) for item in events) + "\n").encode()
    (frame / "frame-events.jsonl").write_bytes(events_bytes)
    zero = bytes([0, 0, 0, 255] * (WIDTH * HEIGHT))
    initial = _write_blob(frame, "obs-initial.bgra", zero)
    event0 = _write_blob(frame, "obs-event0.bgra", zero)
    event1 = _write_blob(frame, "obs-prefix-event1-9.bgra", reference)

    def observation(event, kind, blob, timing, name, identity):
        return {
            "schema": "gfx-observation/v1", "event": event, "timing": timing,
            "kind": kind, "origin": "lower_left", "width": WIDTH,
            "height": HEIGHT, "row_stride": WIDTH * 4,
            "pixel_format": "BGRA8", "target_name": name,
            "resource": f"tex:{name}" if name != "0" else "fb:0",
            "target_identity": identity,
            "color": {**blob, "pixel_format": "BGRA8"},
            "depth": {"status": "not_present"},
            "stencil": {"status": "not_present"},
        }
    rgb = {"internal_format": 0x8051, "component_type": 0x8C17,
           "red_bits": 8, "green_bits": 8, "blue_bits": 8, "alpha_bits": 0}
    fb0 = {"internal_format": 0, "component_type": 0, "red_bits": 8,
           "green_bits": 8, "blue_bits": 8, "alpha_bits": 8}
    observed = [
        observation(0, "initial", initial, "pre_event", "0", fb0),
        observation(0, "clear", event0, "post_event", "9", rgb),
        observation(1, "draw", event1, "post_event", "9", rgb),
    ]
    observed_bytes = ("\n".join(json.dumps(item) for item in observed)
                      + "\n").encode()
    (frame / "prefix-observations.jsonl").write_bytes(observed_bytes)
    trace = json.loads((frame / "frame-trace.json").read_text())
    trace.update({"test_only": True, "present_index": 1, "scene_index": 1,
                  "prefix": {"schema": "gfx-observations/v1",
                             "mode": "prefix", "status": "complete",
                             "count": 2, "end": 1, "initial": True,
                             "observation_file": "prefix-observations.jsonl",
                             "observation_bytes": len(observed_bytes),
                             "observation_sha256": _sha(observed_bytes),
                             "post_event_count": 2}})
    trace_bytes = (json.dumps(trace) + "\n").encode()
    (frame / "frame-trace.json").write_bytes(trace_bytes)
    (frame / "frame-seal.json").write_text(json.dumps({
        "schema": "gfx-frame-seal/v1", "sealed": True, "complete": True,
        "struct_size": 2256, "abi_version": 2, "capture_frame_id": frame_id,
        "session_id": session, "pid": 1, "present_index": 1, "scene_index": 1,
        "operation_count": 2, "draw_count": 1, "clear_count": 1,
        "copy_count": 0, "event_stream_file": "frame-events.jsonl",
        "event_stream_bytes": len(events_bytes),
        "event_stream_sha256": _sha(events_bytes), "event_stream_verified": True,
        "width": WIDTH, "height": HEIGHT, "pixel_format": "BGRA8",
        "trace_complete": True, "trace_integrity": True, "readback_ok": True,
        "reference_file": "frame-reference.bgra",
        "reference_bytes": len(reference), "reference_sha256": _sha(reference),
        "manifest_file": "frame-trace.json",
        "manifest_sha256": _sha(trace_bytes), "capture_dir": str(frame),
        "test_only": True}))
    if not metal:
        return frame, None
    metal_dir = frame.parent / "metal"
    metal_dir.mkdir()
    candidate = bytearray(reference)
    candidate[(1 * WIDTH + 2) * 4 + 1] -= 1
    (metal_dir / "prefix-event1-9.bgra").write_bytes(candidate)
    graph = gfx_frame_graph.build(frame)
    request = gfx_frame_graph.write_checkpoint_request(
        graph, metal_dir / "checkpoint-request.json")
    seal = json.loads((frame / "frame-seal.json").read_text())
    (metal_dir / "shader-receipt.json").write_text(json.dumps({
        "schema": "gfx-shader-receipt/v1",
        "checkpoint_request_file": "checkpoint-request.json",
        "checkpoint_request_sha256": _sha(
            (metal_dir / "checkpoint-request.json").read_bytes()),
        "checkpoint_count": len(request.get("checkpoints") or []),
        "capture_frame_id": frame_id, "pid": 1, "session_id": session,
        "trace_manifest_sha256": seal["manifest_sha256"]}))
    replay = {
        "schema": "gfx-metal-frame-replay/v1", "status": "ok",
        "stage": "complete", "events": 2, "encoded": 2, "failed": 0,
        "checkpoints": [{"event": 1, "target": "9", "file": "prefix-event1-9.bgra",
                         "timing": "post_event", "width": WIDTH,
                         "height": HEIGHT, "ok": True,
                         "reference_sha256": event1["sha256"],
                         "format": "RGB8", "pixel_format": "BGRA8"}]}
    (metal_dir / "replay.json").write_text(json.dumps(replay))
    (metal_dir / "trusted-execution.json").write_text(json.dumps({
        "schema": "gfx-trusted-execution/v1",
        "mode": "candidate-build-and-replay",
        "outputs": [{"file": "prefix-event1-9.bgra", "bytes": len(candidate),
                     "sha256": _sha(bytes(candidate))}],
        "replay_json_sha256": _sha((metal_dir / "replay.json").read_bytes()),
        "receipt_sha256": _sha((metal_dir / "shader-receipt.json").read_bytes()),
        "request_sha256": _sha(
            (metal_dir / "checkpoint-request.json").read_bytes()),
        "frame_dir": str(frame.resolve()),
        "status": "ok", "events": 2, "encoded": 2, "failed": 0}))
    return frame, metal_dir


def _package(root: Path) -> Path:
    case = root / "case"
    case.mkdir(parents=True)
    frame, metal = _wrap(case / "capture")
    metal.rename(case / "replay")
    replay = case / "replay"
    (replay / "trusted-execution.json").unlink()
    seal = json.loads((frame / "frame-seal.json").read_text())
    checkpoint = (replay / "prefix-event1-9.bgra").read_bytes()
    (replay / "producer-receipt.json").write_text(json.dumps({
        "schema": "gfx-replay-producer-receipt/v1",
        "producer": {"executable": "x", "executable_sha256": "c" * 64,
                     "build_fingerprint": "b" * 64},
        "capture": {key: seal[key] for key in
                    ("session_id", "capture_frame_id", "event_stream_sha256")},
        "result": {"status": "ok", "stage": "complete", "events": 2,
                   "encoded": 2, "failed": 0,
                   "replay_json_sha256": _sha(
                       (replay / "replay.json").read_bytes()),
                   "metal_frame_sha256": None,
                   "checkpoint_files": [{"file": "prefix-event1-9.bgra",
                                         "sha256": _sha(checkpoint)}]}}))
    (case / "case.json").write_text(json.dumps({
        "schema": "gfx-strategy-b-case/v1", "case_id": "packaged",
        "capture": "capture", "replay": "replay"}))
    return case


def _call(argv):
    output = Path(tempfile.mkdtemp()) / "tool.json"
    with contextlib.redirect_stdout(io.StringIO()):
        code = agent_tools.main(["--out", str(output), *argv])
    return code, json.loads(output.read_text())


class EventEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.frame, self.metal = _wrap(self.root / "one" / "frame")

    def inspect(self, **kwargs):
        return gfx_event_evidence.inspect_event(
            self.frame, 1, metal=self.metal, **kwargs)
    def test_event_inputs_metal_binding_and_pixels(self):
        value = self.inspect(pixel=(2, 1))
        metal = value["metal"]
        self.assertEqual(metal["correspondence"], "verified")
        self.assertEqual(metal["checkpoint_sha256"],
                         _sha((self.metal / "prefix-event1-9.bgra").read_bytes()))
        pixel = value["divergence"]["first_divergent_pixel"]
        self.assertEqual(pixel["signed_difference"], {"R": 0, "G": 1, "B": 0})
        self.assertNotIn("before", value["divergence"])
        (self.metal / "prefix-event1-9.bgra").write_bytes(
            bytes([0, 0, 0, 255] * (WIDTH * HEIGHT)))
        broken = self.inspect()
        self.assertEqual(broken["metal"]["correspondence"], "unverified")
        self.assertIn("trusted checkpoint output changed",
                      broken["metal"]["reason"])
    def test_fp32_bits_come_directly_from_captured_bytes(self):
        bits = [0x00000000, 0x80000000, 0x00000001, 0x7F800000, 0x7FC00001]
        data = b"".join(struct.pack("<I", value) for value in bits)
        blob = _write_blob(self.frame, "uniform-special.bin", data)
        decoded = gfx_event_evidence._uniform_values(
            self.frame, {"file": blob["file"], "type": "float",
                         "components": 1, "elements": len(bits),
                         "bytes": len(data)}, True)
        self.assertEqual([item["bits"] for item in decoded["values"]],
                         ["0x%08x" % value for value in bits])
    def test_unbound_replays_are_never_verified(self):
        replay_path = self.metal / "replay.json"
        replay = json.loads(replay_path.read_text())
        replay["checkpoints"][0]["reference_sha256"] = "f" * 64
        replay_path.write_text(json.dumps(replay))
        value = self.inspect(pixel=(2, 1))
        self.assertEqual(value["metal"]["correspondence"], "unverified")
        self.assertEqual(value["divergence"]["requested_pixel"]["status"],
                         "metal_unavailable")
        replay = json.loads(replay_path.read_text())
        replay["status"] = "failed"
        replay["failed"] = 1
        replay_path.write_text(json.dumps(replay))
        self.assertEqual(self.inspect()["metal"]["correspondence"],
                         "unverified")
        second, _ = _wrap(self.root / "two" / "frame", session="other",
                          frame_id=2, caller="0x200")
        first_seal = json.loads((self.frame / "frame-seal.json").read_text())
        second_seal = json.loads((second / "frame-seal.json").read_text())
        self.assertEqual(first_seal["reference_sha256"],
                         second_seal["reference_sha256"])
        self.assertNotEqual(first_seal["event_stream_sha256"],
                            second_seal["event_stream_sha256"])
        self.assertEqual(gfx_event_evidence.inspect_event(
            second, 1, metal=self.metal)["metal"]["correspondence"],
            "unverified")
        case = self.root / "invalid"
        case.mkdir()
        _wrap(case / "frame")
        (case / "case.json").write_text(json.dumps({
            "schema": "gfx-strategy-b-case/v1", "case_id": "invalid",
            "capture": "frame", "replay": "metal"}))
        invalid = gfx_event_evidence.inspect_event(
            case, 1, metal=self.metal, verification={"ok": False})
        self.assertEqual(invalid["metal"]["correspondence"], "unverified")
        self.assertIn("verification", invalid["metal"]["reason"])
        (case / "case.json").write_text(json.dumps({
            "schema": "gfx-strategy-b-case/v1", "case_id": "invalid"}))
        authentic = {"capture": {"sealed": True, "authority": "authentic"},
                     "divergence": {"status": "match"}}
        with mock.patch.object(agent_tools, "_strategy_b_verify",
                               return_value={"ok": False, "problems": ["bad"]}), \
                mock.patch.object(gfx_event_evidence, "inspect_event",
                                  return_value=authentic):
            code, value = _call(["eu4_case_inspect", "--case",
                                 str(case), "--event", "1"])
        self.assertEqual(value["outcome"]["evidence"], "invalid")
        substituted = self.root / "substituted"
        substituted.mkdir()
        for name in ("checkpoint-request.json", "shader-receipt.json",
                     "trusted-execution.json"):
            shutil.copy2(self.metal / name, substituted / name)
        for name in ("replay.json", "prefix-event1-9.bgra"):
            shutil.copy2(second.parent / "metal" / name, substituted / name)
        broken = gfx_event_evidence.inspect_event(
            second, 1, metal=substituted)
        self.assertEqual(broken["metal"]["correspondence"], "unverified")
        self.assertNotIn("statistics", broken["metal"])
    def test_only_post_event_checkpoint_is_selected(self):
        replay_path = self.metal / "replay.json"
        replay = json.loads(replay_path.read_text())
        post = replay["checkpoints"][0]
        pre = dict(post, timing="pre_event")
        (self.metal / "pre.bgra").write_bytes(
            bytes([0, 0, 0, 255] * (WIDTH * HEIGHT)))
        replay["checkpoints"] = [pre, post]
        replay_path.write_text(json.dumps(replay))
        execution_path = self.metal / "trusted-execution.json"
        execution = json.loads(execution_path.read_text())
        execution["replay_json_sha256"] = _sha(replay_path.read_bytes())
        execution_path.write_text(json.dumps(execution))
        value = self.inspect(pixel=(2, 1))
        self.assertEqual(value["metal"]["correspondence"], "verified")
        self.assertEqual(value["divergence"]["first_divergent_pixel"][
                             "signed_difference"], {"R": 0, "G": 1, "B": 0})
        replay["checkpoints"].append(dict(post))
        replay_path.write_text(json.dumps(replay))
        execution["replay_json_sha256"] = _sha(replay_path.read_bytes())
        execution_path.write_text(json.dumps(execution))
        self.assertEqual(self.inspect()["metal"]["correspondence"],
                         "unverified")
        replay["checkpoints"] = [pre]
        replay_path.write_text(json.dumps(replay))
        execution["replay_json_sha256"] = _sha(replay_path.read_bytes())
        execution_path.write_text(json.dumps(execution))
        self.assertEqual(self.inspect()["metal"]["correspondence"],
                         "unverified")

    def test_trusted_execution_must_list_the_checkpoint(self):
        path = self.metal / "trusted-execution.json"
        execution = json.loads(path.read_text())
        execution["outputs"] = []
        path.write_text(json.dumps(execution))
        value = self.inspect()
        self.assertEqual(value["metal"]["correspondence"], "unverified")
        self.assertIn("checkpoint", value["metal"]["reason"])

    def test_real_packaged_receipt_and_override(self):
        case = _package(self.root / "packaged")
        self.assertEqual(gfx_event_evidence.inspect_event(
            case, 1, verification={"ok": True})[
                "metal"]["correspondence"], "verified")
        other = self.root / "other"
        shutil.copytree(case / "replay", other)
        self.assertEqual(gfx_event_evidence.inspect_event(
            case, 1, metal=other, verification={"ok": True})[
                "metal"]["correspondence"], "unverified")

    def test_no_cross_frame_contamination(self):
        case = self.root / "sequence"
        case.mkdir()
        _wrap(case / "one" / "frame")
        second, _ = _wrap(case / "two" / "frame", session="two", frame_id=2)
        (case / "dependency-graph.json").write_text(json.dumps({
            "schema": "gfx-frame-graph/v1", "status": "complete",
            "resources": {"tex:9": {"id": "tex:9", "producers": []}},
            "events": [{"event": 1, "target": "tex:9"}], "prefix": {}}))
        (case / "sequence.json").write_text(json.dumps({
            "schema": "gfx-strategy-b-case-sequence/v1", "target_index": 1,
            "frames": [{"index": 1, "dir": "one/frame"},
                       {"index": 2, "dir": "two/frame",
                        "capture_frame_id": 2}]}))
        (case / "case.json").write_text(json.dumps({
            "schema": "gfx-strategy-b-case/v2", "case_id": "seq",
            "sequence": "sequence.json", "replay": "replay",
            "shaders": "shaders", "diagnostic_report": "report.json",
            "dependency_graph": "dependency-graph.json"}))
        value = gfx_event_evidence.inspect_event(
            case, 1, frame_index=2, verification={"ok": True})
        self.assertEqual(value["capture"]["session_id"], "two")
        self.assertEqual(value["metal"]["correspondence"], "unavailable")
    def test_r32f_pixel_uses_raw_bits(self):
        root = self.root / "r32f"
        root.mkdir()
        (root / "blobs").mkdir()
        reference = struct.pack("<II", 0x00000000, 0x7FC00001)
        (root / "blobs" / "ref.bin").write_bytes(reference)
        (root / "cand.bin").write_bytes(
            struct.pack("<II", 0x80000000, 0x7FC00001))
        observation = {
            "event": 1, "timing": "post_event", "kind": "draw",
            "origin": "lower_left", "width": 2, "height": 1,
            "pixel_format": "R32F", "_frame_dir": str(root),
            "target_identity": {"internal_format": 0x822E,
                                "component_type": 0x1406, "red_bits": 32,
                                "green_bits": 0, "blue_bits": 0,
                                "alpha_bits": 0},
            "color": {"file": "ref.bin", "sha256": _sha(reference)},
        }
        metal = {"correspondence": "verified", "checkpoint_file": "cand.bin"}
        zero = gfx_event_evidence._pixel(observation, metal, (0, 0), root)
        self.assertEqual(zero["original_bits"], {"R": "0x00000000"})
        self.assertEqual(zero["metal_bits"], {"R": "0x80000000"})
        self.assertEqual(zero["status"], "different")
        same_nan = gfx_event_evidence._pixel(observation, metal, (1, 0), root)
        self.assertEqual(same_nan["status"], "exact")
        (root / "cand.bin").write_bytes(
            struct.pack("<II", 0x00000000, 0x7FC00002))
        diff_nan = gfx_event_evidence._pixel(observation, metal, (1, 0), root)
        self.assertEqual(diff_nan["status"], "different")
        json.dumps([zero, same_nan, diff_nan], allow_nan=False)
        whole = gfx_independent_replay.compare_r32f(
            reference, struct.pack("<II", 0x00000000, 0x7FC00002), 2, 1)
        self.assertEqual(whole["first_differing_pixel"]["x"], 1)
    def test_seal_compact_and_default_behavior(self):
        code, value = _call([
            "eu4_case_inspect", "--case", str(self.frame),
            "--event", "1", "--pixel", "2,1", "--metal", str(self.metal)])
        self.assertLessEqual(len(json.dumps(value, separators=(",", ":"))), 8192)
        self.assertEqual(value["outcome"]["verdict"], "different")
        self.assertEqual(value["outcome"]["evidence"], "unverified")
        derived = {"capture": {"sealed": True, "authority": "derived"},
                   "divergence": {"status": "match"}}
        with mock.patch.object(gfx_event_evidence, "inspect_event",
                               return_value=derived), \
                mock.patch.object(agent_tools.gfx_replay_case, "case_schema",
                                  return_value=(agent_tools.gfx_replay_case.CASE_KIND_STRATEGY_B,
                                                "gfx-strategy-b-case/v1", {}, None)), \
                mock.patch.object(agent_tools, "_strategy_b_verify",
                                  return_value={"ok": True}):
            code, value = _call(["eu4_case_inspect", "--case",
                                 str(self.frame), "--event", "1"])
        self.assertEqual(value["outcome"]["evidence"], "unverified")
        (self.frame / "blobs" / "replace.bgra").write_bytes(b"")
        seal = json.loads((self.frame / "frame-seal.json").read_text())
        seal["sealed"] = False
        (self.frame / "frame-seal.json").write_text(json.dumps(seal))
        with self.assertRaises(gfx_event_evidence.EventEvidenceError):
            self.inspect()
        code, value = _call(["eu4_case_inspect", "--case",
                             str(self.frame.parent.parent)])
        self.assertEqual(code, 2)
        self.assertEqual(value["status"], "not_found")
        huge = {"capture": {"sealed": True, "authority": "authentic"},
                "event": {}, "divergence": {"status": "match"},
                "artifacts": {"frame_dir": "f"}, "padding": "x" * 9000}
        with mock.patch.object(gfx_event_evidence, "inspect_event",
                               return_value=huge):
            code, value = _call(["eu4_case_inspect", "--case",
                                 str(self.frame), "--event", "1"])
        self.assertEqual(code, 2)
        self.assertEqual(value["result"]["error"],
                         "event_output_cap_exceeded")

AUTHENTIC = ROOT / ".local" / "m7" / "event20" / "trusted-copy" / "47590918"
AUTHENTIC_FRAME = AUTHENTIC / "frame"
AUTHENTIC_METAL = (ROOT / ".local" / "m7" / "verify" / "event20-accept3"
                   / "exec-mobays0d" / "metal")


@unittest.skipUnless(
    (AUTHENTIC_FRAME / "frame-seal.json").is_file()
    and (AUTHENTIC_METAL / "replay.json").is_file(),
    "preserved M7 event-1 evidence is not available locally")
class AuthenticEventOneTests(unittest.TestCase):
    def test_event_one_is_verified_and_divergent(self):
        value = gfx_event_evidence.inspect_event(
            AUTHENTIC_FRAME, 1, metal=AUTHENTIC_METAL, pixel=(129, 4))
        self.assertEqual(value["metal"]["correspondence"], "verified")
        self.assertEqual(value["divergence"]["statistics"][
                             "mismatched_pixels"], 23)
        code, envelope = _call(["eu4_case_inspect", "--case",
                                str(AUTHENTIC_FRAME), "--event", "1",
                                "--metal", str(AUTHENTIC_METAL)])
        self.assertEqual(code, 0)
        self.assertEqual(envelope["outcome"]["evidence"], "valid")


if __name__ == "__main__":
    unittest.main()
