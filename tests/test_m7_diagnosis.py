"""M7 PR3 same-frame diagnosis and qualification contract.

These tests synthesize one complete captured frame and already-produced Metal
result directories.  No EU4 process, GPU replay or second capture is involved;
the diagnosis path is exercised exactly as it consumes live in-process output.
"""
from __future__ import annotations

import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from automation import (  # noqa: E402
    gfx_frame_graph,
    gfx_independent_replay,
    gfx_replay_case,
    m6_compare,
    m6_frame,
)

WIDTH, HEIGHT = 8, 8
TEXTURE_WIDTH, TEXTURE_HEIGHT = 4, 4
SHADER = b"void main(){}"


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _texture(name, unit, payload, *, width=TEXTURE_WIDTH, height=TEXTURE_HEIGHT,
             internal="RGBA8"):
    digest = sha(payload)
    return {
        "name": name,
        "sampler_name": f"tex{unit}",
        "unit": unit,
        "target": "2D",
        "width": width,
        "height": height,
        "internal_format": internal,
        "levels": [{
            "level": 0, "width": width, "height": height,
            "pixel_format": "RGBA8", "bytes": len(payload),
            "sha256": digest, "file": f"tex-{digest}.bin",
        }],
    }


def _target(name):
    if name is None:
        return {"framebuffer": 0, "width": WIDTH, "height": HEIGHT,
                "color_attachment": None}
    return {"framebuffer": 1, "width": WIDTH, "height": HEIGHT,
            "color_attachment": {
                "type": "texture", "name": name, "level": 0,
                "internal_format": "RGBA8", "width": WIDTH, "height": HEIGHT}}


def _draw(index, target, samples, *, program=1, dither=False,
          multisample=False):
    return {
        "kind": "draw", "index": index,
        "draw": {"entry": "_GfxDraw", "mode": 4, "count": 3,
                 "caller_rva": "0x1000"},
        "state": {
            "viewport": [0, 0, WIDTH, HEIGHT],
            "blend": {"enabled": False},
            "depth": {"test": False, "write": False},
            "cull": {"enabled": False},
            "color_mask": [True, True, True, True],
            "scissor": {"enabled": False},
            "dither": dither,
            "multisample": multisample,
        },
        "program": {"name": program, "shaders": [
            {"stage": "vertex", "sha256": sha(SHADER),
             "file": sha(SHADER) + ".vert"},
            {"stage": "pixel", "sha256": sha(SHADER),
             "file": sha(SHADER) + ".frag"},
        ]},
        "target": _target(target),
        "textures": samples,
        "attributes": [{
            "location": 0, "name": "in_POSITION", "components": 2,
            "gl_type": "0x1406", "stride": 8,
            "data": "verts.bin", "sha256": sha(b"verts!"),
        }],
        "seed": None,
    }


def _clear(target, flags=7):
    return {
        "kind": "clear", "flags": flags, "color": [0, 0, 0, 1],
        "depth": 1, "stencil": 0, "target": _target(target),
    }


class DiagnosisFixture:
    def __init__(self, root: Path):
        self.root = root
        self.frame = root / "frame"
        self.metal = root / "metal"
        self.shaders = root / "translated"
        self.blobs = self.frame / "blobs"
        for path in (self.frame, self.metal, self.shaders, self.blobs):
            path.mkdir(parents=True)
        self.asset_payload = b"asset-pixels-01"
        self.rt_payload = bytes((index * 3) & 0xFF
                                for index in range(WIDTH * HEIGHT * 4))
        self.asset = _texture(1, 1, self.asset_payload)
        self.rt = _texture(10, 10, self.rt_payload,
                           width=WIDTH, height=HEIGHT)
        self.events = [
            _clear(None, 7),
            _draw(1, None, [self.asset], program=1),
            _clear(10, 1),
            _draw(2, 10, [self.asset], program=2),
            _draw(3, None, [self.rt], program=3),
        ]
        self.reference = bytes((index * 7) & 0xFF
                               for index in range(WIDTH * HEIGHT * 4))
        self.write_frame()
        self.write_shaders()
        self.write_metal()

    # -- capture ---------------------------------------------------------
    def write_frame(self, *, dither=False, multisample=False):
        for event in self.events:
            if event.get("kind") == "draw" and event.get("target", {}).get(
                    "color_attachment") is None:
                event["state"]["dither"] = dither
                event["state"]["multisample"] = multisample
        (self.frame / "frame-events.jsonl").write_text(
            "\n".join(json.dumps(event) for event in self.events) + "\n")
        self.write_blob("verts.bin", b"verts!")
        self.write_blob(sha(SHADER) + ".vert", SHADER)
        self.write_blob(sha(SHADER) + ".frag", SHADER)
        self.write_blob(self.asset["levels"][0]["file"], self.asset_payload)
        self.write_blob(self.rt["levels"][0]["file"], self.rt_payload)
        (self.frame / "frame-reference.bgra").write_bytes(self.reference)
        manifest = {
            "schema": "gfx-frame-trace/v1", "complete": True,
            "reason": "scene_present", "events": len(self.events),
            "frame": {
                "width": WIDTH, "height": HEIGHT, "origin": "lower_left",
                "pixel_format": "BGRA8", "row_stride": WIDTH * 4,
                "reference": "frame-reference.bgra",
                "reference_bytes": len(self.reference),
                "reference_sha256": sha(self.reference),
            },
            "seed": {"width": 0, "height": 0},
        }
        (self.frame / "frame-trace.json").write_text(json.dumps(manifest))
        manifest_bytes = (self.frame / "frame-trace.json").read_bytes()
        events_bytes = (self.frame / "frame-events.jsonl").read_bytes()
        (self.frame / "frame-seal.json").write_text(json.dumps({
            "schema": "gfx-frame-seal/v1", "sealed": True, "complete": True,
            "struct_size": 2256, "abi_version": 2,
            "capture_frame_id": 7, "session_id": "session-1", "pid": 4242,
            "process_start_ns": 100, "boundary": "scene",
            "present_index": 2, "scene_index": 2,
            "operation_count": len(self.events),
            "draw_count": 3, "clear_count": 2, "copy_count": 0,
            "event_stream_file": "frame-events.jsonl",
            "event_stream_bytes": len(events_bytes),
            "event_stream_sha256": sha(events_bytes),
            "event_stream_verified": True,
            "width": WIDTH, "height": HEIGHT, "pixel_format": "BGRA8",
            "trace_complete": True, "trace_integrity": True,
            "readback_ok": True,
            "reference_file": "frame-reference.bgra",
            "reference_bytes": len(self.reference),
            "reference_sha256": sha(self.reference),
            "manifest_file": "frame-trace.json",
            "manifest_sha256": sha(manifest_bytes),
            "capture_dir": str(self.frame),
        }))

    def write_blob(self, name, payload):
        (self.blobs / name).write_bytes(payload)

    def write_shaders(self):
        (self.shaders / "shaders.json").write_text(json.dumps({
            "schema": "gfx-shader-translation/v1", "programs": []}))

    # -- produced Metal result -------------------------------------------
    def write_metal(self, *, candidate=None, checkpoints=True,
                    checkpoint_payload=None, semantic_fault=False,
                    checkpoint_request=True, shadow=True, sealed=True):
        candidate = self.reference if candidate is None else candidate
        request = gfx_frame_graph.checkpoint_request(
            gfx_frame_graph.build(self.frame))
        if checkpoint_request:
            (self.metal / "checkpoint-request.json").write_text(
                json.dumps(request))
        checkpoint_file = None
        results = []
        if checkpoints:
            plan = request.get("checkpoints") or []
            payload = self.rt_payload if checkpoint_payload is None else (
                checkpoint_payload)
            for item in plan:
                checkpoint_file = item["file"]
                (self.metal / checkpoint_file).write_bytes(payload)
                results.append({
                    "event": item["event"], "target": item["target"],
                    "ok": True, "file": item["file"],
                    "reference_file": item["reference_file"],
                    "reference_sha256": item["reference_sha256"],
                    "width": item["reference_width"],
                    "height": item["reference_height"],
                    "format": "RGBA8", "generation": 2,
                })
        samples = [
            {"event": 1, "texture": "1", "sampler": "tex1", "unit": 1,
             "source": "external_capture", "generation": -1,
             "expected_texture": "1", "selected_texture": "1"},
            {"event": 3, "texture": "1", "sampler": "tex1", "unit": 1,
             "source": "external_capture", "generation": -1,
             "expected_texture": "1", "selected_texture": "1"},
            {"event": 4, "texture": "10", "sampler": "tex10", "unit": 10,
             "source": "metal_render_target", "generation": 2,
             "expected_texture": "10", "selected_texture": "10"},
        ]
        if semantic_fault:
            samples[0].update({
                "fault_shader_unit": 0,
                "fault_sampler_shift": -1,
            })
        (self.metal / "replay-provenance.jsonl").write_text(
            "\n".join(json.dumps(row) for row in samples) + "\n")
        (self.metal / "metal-frame.bgra").write_bytes(candidate)
        replay = {
            "schema": "gfx-metal-frame-replay/v1", "status": "ok",
            "events": len(self.events), "encoded": len(self.events),
            "failed": 0, "command_status": 4, "readback": True,
            "partial": False, "timed_out": False,
            "manifest_reference_sha256": sha(self.reference),
            "frame": {
                "width": WIDTH, "height": HEIGHT, "origin": "lower_left",
                "pixel_format": "BGRA8", "row_stride": WIDTH * 4,
                "raw_file": "metal-frame.bgra",
                "raw_bytes": len(candidate),
            },
            "provenance": {
                "bindings": len(samples),
                "metal_render_target_bindings": 1,
                "external_capture_bindings": 2,
                "render_target_textures": ["10"],
                "external_textures": ["1"],
                "first_sample_before_any_write": [],
            },
            "transfers": [],
        }
        if checkpoints:
            replay["checkpoints"] = results
        (self.metal / "replay.json").write_text(json.dumps(replay))
        if shadow:
            seal = json.loads((self.frame / "frame-seal.json").read_text())
            identity = {
                "capture_frame_id": seal["capture_frame_id"],
                "pid": seal["pid"],
                "session_id": seal["session_id"],
                "gl_reference_sha256": seal["reference_sha256"],
                "trace_manifest_sha256": seal["manifest_sha256"],
                "operation_count": seal["operation_count"],
            }
            if not sealed:
                identity["capture_frame_id"] = 8
            request_path = self.metal / "checkpoint-request.json"
            identity.update({
                "checkpoint_request_file": request_path.name,
                "checkpoint_request_sha256": sha(request_path.read_bytes()),
            })
            (self.metal / "shadow-report.json").write_text(json.dumps(identity))

    def diagnose(self, **kwargs):
        out = self.root / "diagnosis"
        return gfx_independent_replay.diagnose_frame(
            self.frame, self.metal, out, **kwargs)

    def alter_candidate(self, offset=0, delta=7):
        candidate = bytearray(self.reference)
        candidate[offset] = (candidate[offset] + delta) % 256
        self.write_metal(candidate=bytes(candidate))


class DiagnosisContractTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.fixture = DiagnosisFixture(Path(self.temporary.name))

    def tearDown(self):
        self.temporary.cleanup()

    def test_complete_evidence_is_qualified_match(self):
        report = self.fixture.diagnose()
        self.assertEqual(report["classification"], "qualified_match", report)
        self.assertEqual(report["first_divergence"]["kind"], "none")
        self.assertEqual(report["pixels"]["status"], "match")
        self.assertEqual(report["checkpoint_validation"]["status"], "ok")
        self.assertEqual(report["identity"]["status"], "verified")
        self.assertEqual(report["provenance"]["status"], "verified")
        self.assertEqual(report["operation_coverage"]["status"], "complete")
        self.assertEqual(report["state_coverage"]["status"], "complete")

    def test_diagnosis_does_not_execute_a_host(self):
        with mock.patch.object(
                m6_frame, "build_replay_host",
                side_effect=AssertionError("host build must not run")), \
             mock.patch.object(
                gfx_independent_replay.subprocess, "run",
                side_effect=AssertionError("subprocess must not run")):
            report = self.fixture.diagnose()
        self.assertEqual(report["classification"], "qualified_match")

    def test_partial_state_difference_is_not_a_proven_renderer_defect(self):
        self.fixture.write_frame(dither=True)
        self.fixture.write_metal(candidate=(bytes([1]) + self.fixture.reference[1:]))
        report = self.fixture.diagnose()
        self.assertEqual(report["classification"],
                         "difference_with_partial_state_coverage")
        self.assertIn("gl_dither", report["state_coverage"]["unsupported"])
        divergence = report["first_divergence"]
        self.assertEqual(divergence["kind"], "final_only")
        self.assertEqual(divergence["confidence"], "final_only")
        self.assertEqual(divergence["root_cause"], "not_established")
        self.assertIsNotNone(divergence["suggested_checkpoint"])
        self.assertFalse(report["qualification"]["renderer_equivalence"])

    def test_complete_state_difference_is_a_qualified_difference(self):
        self.fixture.alter_candidate()
        report = self.fixture.diagnose()
        self.assertEqual(report["classification"], "qualified_difference")
        self.assertEqual(report["first_divergence"]["kind"], "final_only")
        self.assertEqual(
            report["pixels"]["statistics"]["mismatched_pixels"], 1)

    def test_intermediate_checkpoint_difference_is_localized_with_evidence(self):
        self.fixture.write_metal(
            candidate=(bytes([2]) + self.fixture.reference[1:]),
            checkpoint_payload=bytes([9]) * (WIDTH * HEIGHT * 4))
        report = self.fixture.diagnose()
        divergence = report["first_divergence"]
        self.assertEqual(divergence["kind"], "intermediate")
        self.assertEqual(divergence["event"], 4)
        self.assertEqual(divergence["target"], "10")
        self.assertEqual(divergence["producer_event"], 3)
        self.assertEqual(divergence["program"], 2)
        self.assertEqual(divergence["operation"], "draw")
        self.assertEqual(
            divergence["authoritative_because"],
            "this is a reconciled request/response checkpoint whose captured "
            "reference digest and Metal output were verified; it is the "
            "earliest such checkpoint before the final framebuffer")
        self.assertIsNotNone(divergence["gl_sha256"])
        self.assertIsNotNone(divergence["metal_sha256"])
        self.assertEqual(divergence["root_cause"], "not_established")
        self.assertEqual(len(divergence["unresolved_interval"]), 3)

    def test_missing_checkpoint_result_is_incomplete_diagnostics(self):
        replay = json.loads((self.fixture.metal / "replay.json").read_text())
        replay.pop("checkpoints")
        (self.fixture.metal / "replay.json").write_text(json.dumps(replay))
        report = self.fixture.diagnose()
        self.assertEqual(report["classification"], "incomplete_diagnostics")
        self.assertEqual(report["first_divergence"]["kind"],
                         "incomplete_checkpoint")
        self.assertFalse(report["qualification"]["diagnostic_qualified"])

    def test_checkpoint_request_not_bound_is_incomplete_diagnostics(self):
        (self.fixture.metal / "checkpoint-request.json").unlink()
        report = self.fixture.diagnose()
        self.assertEqual(report["classification"], "incomplete_diagnostics")
        self.assertEqual(report["checkpoint_validation"]["request_source"],
                         "derived")
        self.assertIn("request_not_bound",
                      [problem["kind"] for problem in
                       report["checkpoint_validation"]["problems"]])

    def test_corrupt_checkpoint_output_is_invalid_provenance(self):
        request = json.loads(
            (self.fixture.metal / "checkpoint-request.json").read_text())
        name = request["checkpoints"][0]["file"]
        (self.fixture.metal / name).write_bytes(b"truncated")
        report = self.fixture.diagnose()
        self.assertEqual(report["classification"], "invalid_provenance")
        self.assertEqual(report["checkpoint_validation"]["status"], "invalid")

    def test_corrupt_capture_blob_is_invalid_provenance(self):
        (self.fixture.blobs / "verts.bin").write_bytes(b"tampered")
        report = self.fixture.diagnose()
        self.assertEqual(report["classification"], "invalid_provenance")
        self.assertIn("blob digest", json.dumps(report["capture"]))

    def test_seal_identity_mismatch_is_invalid_provenance(self):
        self.fixture.write_metal(sealed=False)
        report = self.fixture.diagnose()
        self.assertEqual(report["identity"]["status"], "mismatch")
        self.assertEqual(report["classification"], "invalid_provenance")

    def test_missing_metal_result_is_incomplete_diagnostics(self):
        (self.fixture.metal / "replay.json").unlink()
        report = self.fixture.diagnose()
        self.assertEqual(report["classification"], "incomplete_diagnostics")
        self.assertEqual(report["first_divergence"]["kind"], "incomplete")

    def test_wrong_texture_unit_is_semantic_not_pixel_root_cause(self):
        self.fixture.write_metal(semantic_fault=True)
        report = self.fixture.diagnose()
        divergence = report["first_divergence"]
        self.assertEqual(divergence["kind"], "semantic")
        self.assertEqual(divergence["event"], 1)
        self.assertEqual(divergence["semantic"]["kind"],
                         "shader_unit_collision")
        self.assertEqual(report["pixels"]["status"], "match")
        self.assertEqual(report["classification"], "qualified_difference")

    def test_single_pixel_intermediate_difference_is_localized(self):
        payload = bytearray(self.fixture.rt_payload)
        payload[0] = (payload[0] + 1) % 256
        self.fixture.write_metal(candidate=self.fixture.reference,
                                 checkpoint_payload=bytes(payload))
        report = self.fixture.diagnose()
        divergence = report["first_divergence"]
        self.assertEqual(divergence["kind"], "intermediate")
        self.assertEqual(divergence["event"], 4)
        self.assertEqual(
            divergence["statistics"]["mismatched_pixels"], 1)

    def test_wrong_render_target_selection_is_invalid_provenance(self):
        rows = (self.fixture.metal / "replay-provenance.jsonl").read_text(
        ).splitlines()
        records = [json.loads(row) for row in rows]
        records[2]["texture"] = "1"
        (self.fixture.metal / "replay-provenance.jsonl").write_text(
            "\n".join(json.dumps(row) for row in records) + "\n")
        report = self.fixture.diagnose()
        self.assertEqual(report["classification"], "invalid_provenance")
        kinds = [problem["kind"] for problem in
                 report["provenance"]["consumer_problems"]]
        self.assertIn("wrong_resource", kinds)

    def test_state_equivalence_requires_focused_evidence(self):
        self.fixture.write_frame(dither=True)
        report = self.fixture.diagnose()
        self.assertEqual(report["classification"], "unsupported_state")
        (self.fixture.frame / "state-equivalence.json").write_text(json.dumps({
            "schema": "m7-state-equivalence/v1",
            "states": [{
                "state": "gl_dither", "status": "proven_equivalent",
                "evidence": "offline A/B test fixture",
            }],
        }))
        report = self.fixture.diagnose()
        self.assertEqual(report["classification"], "qualified_match")
        self.assertIn("gl_dither", report["state_coverage"]["proven_equivalent"])

    def test_exit_codes_are_distinguishable(self):
        report = self.fixture.diagnose()
        self.assertEqual(gfx_independent_replay.diagnosis_exit_code(report), 0)
        self.fixture.alter_candidate()
        report = self.fixture.diagnose()
        self.assertEqual(gfx_independent_replay.diagnosis_exit_code(report), 2)
        (self.fixture.metal / "replay.json").unlink()
        report = self.fixture.diagnose()
        self.assertEqual(gfx_independent_replay.diagnosis_exit_code(report), 1)


class DiagnosisPackageTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.fixture = DiagnosisFixture(Path(self.temporary.name))

    def tearDown(self):
        self.temporary.cleanup()

    def test_package_is_self_contained_and_digest_verified(self):
        report = self.fixture.diagnose()
        case_dir = self.fixture.root / "case"
        manifest = gfx_replay_case.package_diagnosis(
            self.fixture.frame, self.fixture.metal, case_dir, report,
            case_id="synthetic-difference")
        self.assertTrue((case_dir / "diagnostic-report.json").is_file())
        self.assertTrue((case_dir / "dependency-graph.json").is_file())
        self.assertTrue((case_dir / "capture" / "frame-seal.json").is_file())
        self.assertTrue((case_dir / "replay" / "metal-frame.bgra").is_file())
        self.assertTrue(manifest.get("files"))
        verified = gfx_replay_case.verify(case_dir)
        self.assertTrue(verified["ok"], verified["problems"])
        path = case_dir / "diagnostic-report.json"
        payload = bytearray(path.read_bytes())
        payload[0] = ord("{") if payload[0] != ord("{") else ord("}")
        path.write_bytes(bytes(payload))
        tampered = gfx_replay_case.verify(case_dir)
        self.assertFalse(tampered["ok"])
        self.assertTrue(any("digest drift" in item
                            for item in tampered["problems"]))

    def test_diagnose_package_flag_publishes_case(self):
        report = self.fixture.diagnose(package=True)
        case_dir = self.fixture.root / "diagnosis" / "case"
        self.assertTrue((case_dir / "case.json").is_file())
        self.assertTrue(report.get("case"))


if __name__ == "__main__":
    unittest.main()
