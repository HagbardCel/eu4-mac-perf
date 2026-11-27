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
    gfx_frame_pixels,
    gfx_frame,
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
    def __init__(self, root: Path, *, session_id: str = "session-1",
                 capture_frame_id: int = 7, final_only: bool = False,
                 partial_graph: bool = False):
        self.root = root
        self.session_id = session_id
        self.capture_frame_id = capture_frame_id
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
        if final_only:
            # No renderer-generated resource is sampled in this frame, so the
            # capture graph requires no intermediate checkpoint.  The final
            # readback is the only authoritative observation.
            self.events = [
                _clear(None, 7),
                _draw(1, None, [self.asset], program=1),
            ]
        else:
            self.events = [
                _clear(None, 7),
                _draw(1, None, [self.asset], program=1),
                _clear(10, 1),
                _draw(2, 10, [self.asset], program=2),
                _draw(3, None, [self.rt], program=3),
            ]
        if partial_graph:
            for event in self.events:
                if event.get("kind") == "draw":
                    event.setdefault("program", {})["uniforms"] = [
                        {"name": "u_color", "type": "vec4"}]
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
            "capture_frame_id": self.capture_frame_id,
            "session_id": self.session_id, "pid": 4242,
            "process_start_ns": 100, "boundary": "scene",
            "present_index": 2, "scene_index": 2,
            "operation_count": len(self.events),
            "draw_count": sum(1 for event in self.events
                              if event.get("kind") == "draw"),
            "clear_count": sum(1 for event in self.events
                               if event.get("kind") == "clear"),
            "copy_count": sum(1 for event in self.events
                              if event.get("kind") == "copy"),
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
        (self.shaders / "v.metal").write_text("// vertex\n")
        (self.shaders / "f.metal").write_text("// pixel\n")
        def stage(stage_name, msl_file, entry):
            return {
                "stage": stage_name, "msl_file": msl_file,
                "msl_entry": entry, "msl_uniform_buffer": None,
                "msl_samplers": [], "uniforms": [], "attributes": [],
                "source_sha256": sha(SHADER),
            }
        (self.shaders / "shaders.json").write_text(json.dumps({
            "schema": "gfx-shader-translation/v1",
            "programs": [{
                "program": program, "status": "ok",
                "stages": {
                    "vertex": stage("vertex", "v.metal", "vmain"),
                    "pixel": stage("pixel", "f.metal", "fmain"),
                },
            } for program in (1, 2, 3)],
        }))

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

    def test_permissive_policy_cannot_promote_one_byte_to_exact_match(self):
        """A caller threshold is a policy decision, never raw equality."""
        self.fixture.alter_candidate(offset=0, delta=1)
        policy = json.loads(
            gfx_independent_replay.DEFAULT_POLICY.read_text())
        policy["thresholds"] = {
            "max_channel_error": 1,
            "mismatched_fraction": 1,
            "mean_abs_error": 1,
        }
        path = self.fixture.root / "permissive-policy.json"
        path.write_text(json.dumps(policy))
        report = self.fixture.diagnose(policy=path)
        self.assertEqual(report["classification"], "qualified_difference")
        final = report["pixels"]["final"]
        self.assertEqual(final["status"], "divergent")
        self.assertFalse(final["exact"])
        self.assertTrue(final["policy_pass"])
        statistics = final["statistics"]
        self.assertEqual(statistics["mismatched_pixels"], 1)
        self.assertEqual(statistics["max_channel_error"], 1)
        self.assertFalse(report["qualification"]["qualified"])

    def test_alpha_only_one_byte_delta_is_not_an_exact_match(self):
        self.fixture.alter_candidate(offset=3, delta=1)
        policy = json.loads(
            gfx_independent_replay.DEFAULT_POLICY.read_text())
        path = self.fixture.root / "alpha-permissive-policy.json"
        policy["alpha"] = {"ignored": True}
        policy["thresholds"] = {
            "max_channel_error": 1,
            "mismatched_fraction": 1,
            "mean_abs_error": 1,
        }
        path.write_text(json.dumps(policy))
        report = self.fixture.diagnose(policy=path)
        self.assertEqual(report["classification"], "qualified_difference")
        final = report["pixels"]["final"]
        self.assertEqual(final["status"], "divergent")
        self.assertFalse(final["exact"])
        self.assertEqual(
            final["statistics"]["alpha_different_pixels"], 1)
        self.assertEqual(final["statistics"]["mismatched_pixels"], 1)

    def test_exact_comparison_rejects_inconsistent_statistics(self):
        valid = {
            "pixels": 4, "mismatched_pixels": 0, "alpha_different_pixels": 0,
            "max_channel_error": 0, "mean_abs_error": [0, 0, 0, 0],
        }
        exact, reason = gfx_independent_replay.exact_comparison_statistics(
            valid, 2, 2)
        self.assertTrue(exact, reason)
        missing = dict(valid)
        missing.pop("max_channel_error")
        exact, reason = gfx_independent_replay.exact_comparison_statistics(
            missing, 2, 2)
        self.assertIsNone(exact)
        self.assertIn("incomplete", reason)
        wrong_extent = dict(valid, pixels=3)
        exact, reason = gfx_independent_replay.exact_comparison_statistics(
            wrong_extent, 2, 2)
        self.assertIsNone(exact)
        self.assertIn("disagrees", reason)
        three_channels = dict(valid, mean_abs_error=[0, 0, 0])
        exact, reason = gfx_independent_replay.exact_comparison_statistics(
            three_channels, 2, 2)
        self.assertIsNone(exact)
        self.assertIn("four", reason)

    def test_diagnosis_does_not_execute_a_host(self):
        with mock.patch.object(
                gfx_frame, "build_replay_host",
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
        interval = divergence["unresolved_interval"]
        self.assertEqual(interval["end_event"], 4)
        self.assertIsNone(interval["start_event"])
        self.assertEqual(interval["lower_bound"], "unbounded")
        self.assertEqual(interval["search_start_event"], 0)

    def test_missing_checkpoint_result_is_incomplete_diagnostics(self):
        replay = json.loads((self.fixture.metal / "replay.json").read_text())
        replay.pop("checkpoints")
        (self.fixture.metal / "replay.json").write_text(json.dumps(replay))
        report = self.fixture.diagnose()
        self.assertEqual(report["classification"], "incomplete_diagnostics")
        self.assertEqual(report["first_divergence"]["kind"],
                         "incomplete_checkpoint")
        self.assertFalse(report["qualification"]["diagnostic_qualified"])

    def _rewrite_request(self, mutate):
        """Replace the request and rebind the shadow report to the new file."""
        request = json.loads(
            (self.fixture.metal / "checkpoint-request.json").read_text())
        mutate(request)
        path = self.fixture.metal / "checkpoint-request.json"
        path.write_text(json.dumps(request))
        shadow_path = self.fixture.metal / "shadow-report.json"
        shadow = json.loads(shadow_path.read_text())
        shadow["checkpoint_request_file"] = path.name
        shadow["checkpoint_request_sha256"] = sha(path.read_bytes())
        shadow["checkpoint_count"] = len(request.get("checkpoints") or [])
        shadow_path.write_text(json.dumps(shadow))
        return request

    def test_stale_empty_request_fails_closed(self):
        self._rewrite_request(lambda request: request.update(checkpoints=[]))
        report = self.fixture.diagnose()
        self.assertEqual(report["classification"], "invalid_provenance")
        kinds = [problem["kind"] for problem in
                 report["checkpoint_validation"]["problems"]]
        self.assertIn("missing_required_request", kinds)
        self.assertFalse(report["qualification"]["qualified"])

    def test_request_with_same_count_but_different_events_fails_closed(self):
        def mutate(request):
            for item in request["checkpoints"]:
                item["event"] = int(item["event"]) + 100
        self._rewrite_request(mutate)
        report = self.fixture.diagnose()
        self.assertEqual(report["classification"], "invalid_provenance")
        kinds = [problem["kind"] for problem in
                 report["checkpoint_validation"]["problems"]]
        self.assertIn("unexpected_request", kinds)
        self.assertIn("missing_required_request", kinds)

    def test_wrong_request_digest_fails_closed(self):
        shadow_path = self.fixture.metal / "shadow-report.json"
        shadow = json.loads(shadow_path.read_text())
        shadow["checkpoint_request_sha256"] = "0" * 64
        shadow_path.write_text(json.dumps(shadow))
        report = self.fixture.diagnose()
        self.assertEqual(report["classification"], "invalid_provenance")
        kinds = [problem["kind"] for problem in
                 report["checkpoint_validation"]["problems"]]
        self.assertIn("request_digest_mismatch", kinds)

    def test_request_without_authoritative_binding_is_incomplete(self):
        shadow_path = self.fixture.metal / "shadow-report.json"
        shadow = json.loads(shadow_path.read_text())
        for field in ("checkpoint_request_file", "checkpoint_request_sha256",
                      "checkpoint_count"):
            shadow.pop(field, None)
        shadow_path.write_text(json.dumps(shadow))
        report = self.fixture.diagnose()
        self.assertEqual(report["classification"],
                         "incomplete_diagnostics")
        self.assertEqual(report["checkpoint_validation"]["status"],
                         "incomplete")
        kinds = [problem["kind"] for problem in
                 report["checkpoint_validation"]["problems"]]
        self.assertIn("unbound_request", kinds)

    def test_unexpected_extra_checkpoint_fails_closed(self):
        def mutate(request):
            extra = dict(request["checkpoints"][0])
            extra["event"] = int(extra["event"]) + 1
            extra["file"] = "intermediate-extra.bgra"
            request["checkpoints"].append(extra)
        self._rewrite_request(mutate)
        report = self.fixture.diagnose()
        self.assertEqual(report["classification"], "invalid_provenance")
        kinds = [problem["kind"] for problem in
                 report["checkpoint_validation"]["problems"]]
        self.assertIn("unexpected_request", kinds)

    def test_duplicate_request_entry_fails_closed(self):
        def mutate(request):
            request["checkpoints"].append(
                dict(request["checkpoints"][0]))
        self._rewrite_request(mutate)
        report = self.fixture.diagnose()
        self.assertEqual(report["classification"], "invalid_provenance")
        kinds = [problem["kind"] for problem in
                 report["checkpoint_validation"]["problems"]]
        self.assertIn("duplicate_request", kinds)

    def test_substituted_checkpoint_resource_fails_closed(self):
        def mutate(request):
            request["checkpoints"][0]["resource"] = "tex:999"
        self._rewrite_request(mutate)
        report = self.fixture.diagnose()
        self.assertEqual(report["classification"], "invalid_provenance")
        kinds = [problem["kind"] for problem in
                 report["checkpoint_validation"]["problems"]]
        self.assertIn("substituted_request", kinds)

    def test_unbound_fixture_with_empty_plan_does_not_qualify(self):
        self.fixture.events = [
            _clear(None, 7),
            _draw(1, None, [self.fixture.asset], program=1),
        ]
        self.fixture.write_frame()
        self.fixture.write_metal()
        (self.fixture.metal / "checkpoint-request.json").unlink()
        shadow_path = self.fixture.metal / "shadow-report.json"
        shadow = json.loads(shadow_path.read_text())
        for field in ("checkpoint_request_file", "checkpoint_request_sha256",
                      "checkpoint_count"):
            shadow.pop(field, None)
        shadow_path.write_text(json.dumps(shadow))
        report = self.fixture.diagnose()
        self.assertEqual(report["classification"],
                         "incomplete_diagnostics", report)
        self.assertEqual(report["checkpoint_validation"]["status"],
                         "incomplete")
        self.assertEqual(report["checkpoint_validation"]["requested"], 0)
        self.assertFalse(report["qualification"]["qualified"])

    def test_malformed_checkpoint_event_fails_closed_without_traceback(self):
        def mutate(request):
            request["checkpoints"][0]["event"] = {"not": "an-int"}
        self._rewrite_request(mutate)
        report = self.fixture.diagnose()
        self.assertEqual(report["classification"], "invalid_provenance")
        self.assertFalse(report["qualification"]["qualified"])

    def test_request_output_file_deviation_fails_closed(self):
        def mutate(request):
            request["checkpoints"][0]["file"] = "substituted.bgra"
        self._rewrite_request(mutate)
        report = self.fixture.diagnose()
        self.assertEqual(report["classification"], "invalid_provenance")
        kinds = [problem["kind"] for problem in
                 report["checkpoint_validation"]["problems"]]
        self.assertIn("substituted_request", kinds)

    def test_request_name_path_traversal_is_rejected(self):
        shadow_path = self.fixture.metal / "shadow-report.json"
        shadow = json.loads(shadow_path.read_text())
        shadow["checkpoint_request_file"] = "../checkpoint-request.json"
        shadow_path.write_text(json.dumps(shadow))
        report = self.fixture.diagnose()
        self.assertEqual(report["classification"], "invalid_provenance")
        kinds = [problem["kind"] for problem in
                 report["checkpoint_validation"]["problems"]]
        self.assertIn("request_name_mismatch", kinds)

    def test_unsealed_frame_with_receipt_binding_does_not_qualify(self):
        seal = json.loads(
            (self.fixture.frame / "frame-seal.json").read_text())
        (self.fixture.frame / "frame-seal.json").unlink()
        (self.fixture.metal / "shadow-report.json").unlink()
        request_path = self.fixture.metal / "checkpoint-request.json"
        request = json.loads(request_path.read_text())
        (self.fixture.metal / "shader-receipt.json").write_text(json.dumps({
            "schema": "m7-shadow-shader-receipt/v1",
            "capture_frame_id": seal["capture_frame_id"],
            "pid": seal["pid"],
            "session_id": seal["session_id"],
            "trace_manifest_sha256": seal["manifest_sha256"],
            "checkpoint_request_file": request_path.name,
            "checkpoint_request_sha256": sha(request_path.read_bytes()),
            "checkpoint_count": len(request.get("checkpoints") or []),
        }))
        report = self.fixture.diagnose()
        self.assertEqual(report["identity"]["status"],
                         "unverified_session")
        self.assertEqual(report["classification"],
                         "incomplete_diagnostics")

    def test_unsealed_frame_with_shadow_report_does_not_qualify(self):
        (self.fixture.frame / "frame-seal.json").unlink()
        report = self.fixture.diagnose()
        self.assertEqual(report["identity"]["status"],
                         "unverified_session")
        self.assertEqual(report["classification"],
                         "incomplete_diagnostics")

    def test_shader_receipt_for_another_frame_fails_closed(self):
        request_path = self.fixture.metal / "checkpoint-request.json"
        (self.fixture.metal / "shader-receipt.json").write_text(json.dumps({
            "schema": "m7-shadow-shader-receipt/v1",
            "capture_frame_id": 999,
            "pid": 4242,
            "session_id": "session-1",
            "trace_manifest_sha256": sha(
                (self.fixture.frame / "frame-trace.json").read_bytes()),
            "checkpoint_request_file": request_path.name,
            "checkpoint_request_sha256": sha(request_path.read_bytes()),
            "checkpoint_count": len(json.loads(
                request_path.read_text())["checkpoints"]),
        }))
        report = self.fixture.diagnose()
        self.assertEqual(report["classification"], "invalid_provenance")
        kinds = [problem["kind"] for problem in
                 report["checkpoint_validation"]["problems"]]
        self.assertIn("request_frame_mismatch", kinds)

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

    def test_missing_shadow_identity_fails_closed(self):
        (self.fixture.metal / "shadow-report.json").unlink(missing_ok=True)
        report = self.fixture.diagnose()
        self.assertEqual(report["identity"]["status"],
                         "unverified_session")
        self.assertEqual(report["classification"],
                         "incomplete_diagnostics")
        self.assertFalse(report["qualification"]["diagnostic_qualified"])

    def test_shadow_identity_missing_one_field_fails_closed(self):
        shadow_path = self.fixture.metal / "shadow-report.json"
        shadow = json.loads(shadow_path.read_text())
        shadow.pop("pid")
        shadow_path.write_text(json.dumps(shadow))
        report = self.fixture.diagnose()
        self.assertEqual(report["identity"]["status"], "incomplete")
        self.assertEqual(report["classification"],
                         "incomplete_diagnostics")

    def test_require_seal_rejects_an_unsealed_frame(self):
        (self.fixture.frame / "frame-seal.json").unlink()
        report = self.fixture.diagnose(require_seal=True)
        self.assertEqual(report["identity"]["status"], "missing")
        self.assertEqual(report["classification"],
                         "incomplete_diagnostics")
        self.assertFalse(report["qualification"]["diagnostic_qualified"])

    def test_malformed_checkpoint_request_fails_closed(self):
        (self.fixture.metal / "checkpoint-request.json").write_text(
            json.dumps({"schema": "gfx-frame-checkpoint-request/v1",
                        "checkpoints": "not-an-array"}))
        report = self.fixture.diagnose()
        self.assertEqual(report["classification"], "invalid_provenance")
        self.assertEqual(report["checkpoint_validation"]["status"],
                         "invalid")
        self.assertIn("checkpoint request",
                      json.dumps(report["checkpoint_validation"]))

    def test_state_coverage_does_not_invent_unobserved_state(self):
        report = gfx_independent_replay.state_coverage(
            self.fixture.frame,
            trace={"_events": [{"kind": "draw", "state": {}}]})
        self.assertEqual(report["status"], "partial")
        self.assertIn("draw_state", report["unproven"])
        self.assertEqual(report["supported"], [])

    def test_polygon_offset_is_inert_when_fill_mode_is_disabled(self):
        report = gfx_independent_replay.state_coverage(
            self.fixture.frame,
            trace={"_events": [{
                "kind": "draw",
                "state": {
                    "polygon": {
                        "mode": ["0x1b02"],
                        "offset_fill": False,
                        "offset_factor": 1,
                        "offset_units": 1,
                    },
                },
            }]})
        self.assertNotIn("polygon_offset", report["unsupported"])

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

    def test_custom_policy_changes_state_coverage_and_records_digest(self):
        self.fixture.write_frame(dither=True)
        default = self.fixture.diagnose()
        self.assertEqual(default["classification"], "unsupported_state")
        policy = self.fixture.root / "custom-policy.json"
        document = json.loads(
            gfx_independent_replay.DEFAULT_POLICY.read_text())
        document["name"] = "custom-state-equivalence"
        document["state_equivalence"] = {
            "gl_dither": {
                "state": "gl_dither", "status": "proven_equivalent",
                "evidence": "focused regression fixture, not production evidence",
            },
        }
        policy.write_text(json.dumps(document))
        report = self.fixture.diagnose(policy=policy)
        self.assertEqual(report["classification"], "qualified_match", report)
        self.assertEqual(report["state_coverage"]["status"], "complete")
        self.assertEqual(report["policy"]["source"], "explicit")
        self.assertEqual(report["policy"]["sha256"],
                         sha(policy.read_bytes()))
        self.assertEqual(report["policy"]["path"], str(policy.resolve()))

    def test_missing_or_invalid_policy_fails_closed(self):
        missing = self.fixture.root / "missing-policy.json"
        with self.assertRaises(gfx_independent_replay.ReplayError) as caught:
            self.fixture.diagnose(policy=missing)
        self.assertEqual(caught.exception.kind, "policy_unavailable")
        invalid = self.fixture.root / "invalid-policy.json"
        invalid.write_text("{not json")
        with self.assertRaises(gfx_independent_replay.ReplayError) as caught:
            self.fixture.diagnose(policy=invalid)
        self.assertEqual(caught.exception.kind, "policy_invalid")

    def test_exit_codes_are_distinguishable(self):
        report = self.fixture.diagnose()
        self.assertEqual(gfx_independent_replay.diagnosis_exit_code(report), 0)
        self.fixture.alter_candidate()
        report = self.fixture.diagnose()
        self.assertEqual(gfx_independent_replay.diagnosis_exit_code(report), 2)
        (self.fixture.metal / "replay.json").unlink()
        report = self.fixture.diagnose()
        self.assertEqual(gfx_independent_replay.diagnosis_exit_code(report), 1)


class DiagnoseCliPackagingTests(unittest.TestCase):
    """Exercise the real ``gfx_frame diagnose --package`` CLI entry point."""

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.fixture = DiagnosisFixture(Path(self.temporary.name))

    def tearDown(self):
        self.temporary.cleanup()

    def _run(self):
        return gfx_frame.main([
            "diagnose",
            "--frame", str(self.fixture.frame),
            "--metal", str(self.fixture.metal),
            "--package",
        ])

    def test_default_package_is_outside_frame_and_metal_directories(self):
        status = self._run()
        self.assertEqual(status, 0)
        case_dir = self.fixture.root / "metal-case"
        self.assertTrue((case_dir / "case.json").is_file())
        self.assertFalse((self.fixture.frame / "case").exists())
        self.assertFalse((self.fixture.metal / "case").exists())
        report = json.loads(
            (self.fixture.metal / "diagnostic-report.json").read_text())
        self.assertEqual(Path(report["case"]["files"][0]["path"]).is_absolute(),
                         False)

    def test_repeated_invocation_replaces_the_owned_case(self):
        self.assertEqual(self._run(), 0)
        case_dir = self.fixture.root / "metal-case"
        first = (case_dir / "case.json").read_text()
        self.assertEqual(self._run(), 0)
        self.assertTrue((case_dir / "case.json").is_file())
        self.assertEqual(json.loads(first)["schema"],
                         json.loads((case_dir / "case.json").read_text())["schema"])

    def test_non_owned_output_conflict_is_rejected(self):
        case_dir = self.fixture.root / "metal-case"
        case_dir.mkdir()
        (case_dir / "unrelated.txt").write_text("keep me")
        status = self._run()
        self.assertEqual(status, 1)
        self.assertTrue((case_dir / "unrelated.txt").is_file())
        self.assertFalse((case_dir / "case.json").exists())


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

    def test_single_frame_packaging_refuses_missing_shader_identity(self):
        report = self.fixture.diagnose()
        case = self.fixture.root / "identity-case"
        report_path = self.fixture.shaders / "shaders.json"
        original_report = report_path.read_text()
        original_events = [dict(event) for event in self.fixture.events]

        def package():
            return gfx_replay_case.package_diagnosis(
                self.fixture.frame, self.fixture.metal, case, report,
                case_id="identity")

        variants = {}
        # Missing captured vertex identity.
        self.fixture.events[1]["program"]["shaders"][0].pop("sha256", None)
        self.fixture.write_frame()
        variants["missing-vertex"] = package()
        self.fixture.events = [dict(event) for event in original_events]
        self.fixture.write_frame()
        # Missing captured pixel identity.
        self.fixture.events[1]["program"]["shaders"][1].pop("sha256", None)
        self.fixture.write_frame()
        variants["missing-pixel"] = package()
        self.fixture.events = [dict(event) for event in original_events]
        self.fixture.write_frame()
        # Empty translated report.
        report_path.write_text(json.dumps({
            "schema": "gfx-shader-translation/v1", "programs": []}))
        variants["empty-report"] = package()
        report_path.write_text(original_report)
        # Translated identity disagrees.
        document = json.loads(original_report)
        document["programs"][0]["stages"]["pixel"]["source_sha256"] = "0" * 64
        report_path.write_text(json.dumps(document))
        variants["mismatch"] = package()
        report_path.write_text(original_report)

        for name, manifest in variants.items():
            with self.subTest(variant=name):
                self.assertEqual(manifest["reproduction"]["status"],
                                 "incomplete")
                self.assertFalse(
                    manifest["reproduction"]["independently_reproducible"])
                self.assertEqual(manifest["replay_command"], [])
                self.assertIn(manifest["reproduction"]["reason"],
                              ("shader_identity_unavailable",
                               "shader_artifact_unavailable"))

    def test_verifier_rejects_packaged_case_missing_captured_identity(self):
        report = self.fixture.diagnose()
        case = self.fixture.root / "verify-case"
        gfx_replay_case.package_diagnosis(
            self.fixture.frame, self.fixture.metal, case, report,
            case_id="identity")
        self.assertTrue(gfx_replay_case.verify(case)["ok"])
        events_path = case / "capture" / "frame-events.jsonl"
        events = [json.loads(line)
                  for line in events_path.read_text().splitlines() if line]
        draw = next(event for event in events if event.get("kind") == "draw")
        draw["program"]["shaders"][0].pop("sha256", None)
        events_path.write_text(
            "\n".join(json.dumps(event) for event in events) + "\n")
        manifest = json.loads((case / "case.json").read_text())
        for item in manifest["files"]:
            if item["path"] == "capture/frame-events.jsonl":
                item["bytes"] = events_path.stat().st_size
                item["sha256"] = sha(events_path.read_bytes())
                break
        (case / "case.json").write_text(json.dumps(manifest, indent=2) + "\n")
        verified = gfx_replay_case.verify(case)
        self.assertFalse(verified["ok"], verified)
        self.assertTrue(any("has no valid captured sha256 identity" in problem
                            for problem in verified["problems"]), verified)
        with self.assertRaises(gfx_replay_case.CaseError):
            gfx_replay_case.replay_case(case, self.fixture.root / "refused")

    def test_packaged_case_is_self_diagnosable_after_relocation(self):
        report = self.fixture.diagnose(package=True)
        case_dir = self.fixture.root / "metal-case"
        relocated = self.fixture.root / "relocated-case"
        import shutil
        shutil.copytree(case_dir, relocated)
        reopened = gfx_independent_replay.diagnose_frame(
            relocated / "capture", relocated / "replay",
            self.fixture.root / "relocated-diagnosis", require_seal=True)
        self.assertEqual(reopened["classification"], "qualified_match",
                         reopened)
        self.assertEqual(reopened["capture"]["seal"]["status"], "verified")
        self.assertEqual(reopened["identity"]["status"], "verified")
        manifest = json.loads((relocated / "case.json").read_text())
        seal = json.loads(
            (relocated / "capture" / "frame-seal.json").read_text())
        self.assertEqual(seal["capture_dir"], "capture")
        self.assertEqual(seal["portable_original_capture_dir"],
                         str(self.fixture.frame))
        self.assertTrue(report.get("case"))

    def test_diagnose_package_flag_publishes_case(self):
        report = self.fixture.diagnose(package=True)
        case_dir = self.fixture.root / "metal-case"
        self.assertTrue((case_dir / "case.json").is_file())
        self.assertTrue(report.get("case"))
        self.assertFalse((self.fixture.metal / "case").exists())


if __name__ == "__main__":
    unittest.main()


class FaultExperimentClassificationTests(unittest.TestCase):
    def classify(self, **overrides):
        base = dict(
            capture_validity="valid", identity="verified", execution="ok",
            provenance="verified", operation_coverage="complete",
            state_cov="complete", pixels="match", divergence="none",
            checkpoint_validation={"status": "ok"},
            fault_experiment=True, fault_status="fired")
        base.update(overrides)
        return gfx_independent_replay.classify_diagnosis(**base)

    def test_pixel_neutral_fault_is_a_fault_experiment_not_a_match(self):
        classification, qualification = self.classify()
        self.assertEqual(classification, "fault_experiment")
        self.assertFalse(qualification["qualified"])
        self.assertFalse(qualification["renderer_equivalence"])
        self.assertTrue(qualification["diagnostic_qualified"])
        self.assertEqual(qualification["axes"]["base_classification"],
                         "qualified_match")
        self.assertEqual(qualification["axes"]["fault_experiment"], "fired")

    def test_unfired_fault_is_still_not_qualified(self):
        classification, qualification = self.classify(
            fault_status="not_fired")
        self.assertEqual(classification, "fault_experiment")
        self.assertFalse(qualification["qualified"])
        self.assertEqual(qualification["axes"]["fault_experiment"],
                         "not_fired")

    def test_fault_does_not_hide_incomplete_evidence(self):
        classification, qualification = self.classify(
            pixels="unavailable")
        self.assertEqual(classification, "incomplete_diagnostics")
        self.assertFalse(qualification["qualified"])

    def test_fault_does_not_hide_invalid_provenance(self):
        classification, qualification = self.classify(identity="mismatch")
        self.assertEqual(classification, "invalid_provenance")
        self.assertFalse(qualification["qualified"])

    def test_fault_does_not_hide_gpu_failure(self):
        classification, qualification = self.classify(execution="failed")
        self.assertEqual(classification, "gpu_execution_failure")
        self.assertFalse(qualification["qualified"])


class CompactAgentDiagnosisTests(unittest.TestCase):
    def test_compact_projection_is_bounded_and_actionable(self):
        report = {
            "classification": "difference_with_partial_state_coverage",
            "qualification": {"qualified": False,
                              "axes": {"pixel_comparison": "divergent"}},
            "frame": {"capture_frame_id": 3, "present_index": 617,
                      "scene_index": 617, "events": 325, "sealed": True},
            "execution": {"status": "ok", "events": 325, "encoded": 325,
                          "failed": 0, "command_status": 4},
            "first_divergence": {
                "kind": "intermediate", "event": 286, "target": "442",
                "resource": "tex:442", "operation": "glCopyTexSubImage2D",
                "producer_event": 283, "producer_program": 346,
                "confidence": "confirmed", "root_cause": "not_established",
                "unresolved_interval": {"start_event": 13,
                                        "end_event": 284},
                "next_checkpoint": {"event": 148, "target": "0",
                                    "resource": "fb:0",
                                    "requires_capture": True,
                                    "reason": "bisect the interval"},
                "pixel_location": {"x": 0, "y": 0, "offset": 0},
                "differing_bounds": {"x": 0, "y": 0, "width": 8,
                                     "height": 8},
                "inputs": [{"resource": "tex:428"}] * 40,
                "limitations": ["not a root cause"],
            },
            "checkpoints": [{"event": index, "target": "442",
                             "status": "divergent" if index == 286 else "match",
                             "statistics": {"mismatched_pixels": index,
                                            "max_channel_error": 1}}
                            for index in range(1, 300)],
            "pixels": {"status": "divergent",
                       "final": {"statistics": {"mismatched_pixels": 10}}},
            "checkpoint_validation": {"status": "ok", "requested": 7,
                                      "returned": 7},
            "graph": {"large": "not projected"},
            "artifacts": {"diagnostic_report": "/tmp/diagnostic-report.json"},
        }
        compact = gfx_independent_replay.compact_diagnosis(report)
        self.assertEqual(compact["schema"], "gfx-agent-diagnosis/v1")
        self.assertEqual(compact["first_divergence"]["event"], 286)
        self.assertEqual(compact["first_divergence"]["next_checkpoint"]["event"],
                         148)
        self.assertLessEqual(len(compact["checkpoints"]), 32)
        self.assertGreater(compact["checkpoint_selection"]["total"], 32)
        selected_events = {item["event"] for item in compact["checkpoints"]}
        self.assertIn(286, selected_events)
        self.assertIn(285, selected_events)
        self.assertIn(148, selected_events)
        self.assertIn(1, selected_events)
        self.assertIn(299, selected_events)
        self.assertEqual(compact["checkpoints"][0]["selected_because"],
                         ["first_observation"])
        self.assertIn("bisect", compact["next_action"])
        self.assertEqual(compact["first_divergence"]["recommended_checkpoint"]
                         ["source"], "next_checkpoint")

    def test_selection_is_deterministic_and_causally_bounded(self):
        report = {
            "classification": "difference_with_partial_state_coverage",
            "qualification": {"qualified": False, "axes": {}},
            "frame": {"capture_frame_id": 1},
            "execution": {"status": "ok"},
            "first_divergence": {
                "kind": "intermediate", "event": 150, "target": "442",
                "resource": "tex:442", "producer_event": 148,
                "recommended_checkpoint": {
                    "event": 190, "target": "0", "resource": "fb:0",
                    "requires_capture": True, "reason": "next bisection",
                    "source": "next_checkpoint"},
            },
            "checkpoints": [{
                "event": index, "target": "442",
                "resource": "tex:442",
                "status": "divergent" if index == 150 else "match",
                "statistics": {"mismatched_pixels": 1},
            } for index in range(1, 201)],
            "pixels": {"status": "divergent"},
            "checkpoint_validation": {"status": "ok"},
        }
        first = gfx_independent_replay.compact_diagnosis(report)
        second = gfx_independent_replay.compact_diagnosis(report)
        self.assertEqual(first["checkpoints"], second["checkpoints"])
        events = {item["event"] for item in first["checkpoints"]}
        self.assertLessEqual(len(first["checkpoints"]), 32)
        for expected in (1, 149, 150, 190, 200):
            self.assertIn(expected, events)
        self.assertNotIn(50, events)
        self.assertIn("150", str(first["checkpoints"]))
        selected = {item["event"]: item["selected_because"]
                    for item in first["checkpoints"]}
        self.assertIn("first_divergent", selected[150])
        self.assertIn("last_matching", selected[149])
        self.assertIn("recommended_next", selected[190])

    def test_compact_diagnosis_sanitizes_absolute_paths(self):
        report = {
            "classification": "invalid_provenance",
            "qualification": {"qualified": False, "axes": {}},
            "frame": {"capture_frame_id": 1},
            "execution": {
                "status": "failed",
                "error": "open /Users/operator/evidence/replay.json failed",
            },
            "first_divergence": {
                "kind": "intermediate",
                "event": 4,
                "target": "10",
                "resource": "tex:10",
                "reason": "checkpoint /private/tmp/metal/capture.bgra is invalid",
                "limitations": ["read /var/folders/x/stage.bin failed"],
            },
            "checkpoints": [],
            "pixels": {"status": "unavailable"},
            "checkpoint_validation": {
                "status": "invalid",
                "problems": [{
                    "kind": "invalid_result",
                    "event": 4,
                    "target": "10",
                    "reason": (
                        "checkpoint_size: /private/var/folders/x/metal/"
                        "intermediate-event4-tex10.bgra is 7 bytes, expected "
                        "256"),
                }],
            },
            "artifacts": {"diagnostic_report": "/tmp/out/diagnostic-report.json"},
        }
        compact = gfx_independent_replay.compact_diagnosis(report)
        text = json.dumps(compact)
        for leaked in ("/private/", "/Users/", "/var/", "/tmp/"):
            self.assertNotIn(leaked, text)
        self.assertIn("intermediate-event4-tex10.bgra", text)
        self.assertIn("replay.json", text)
        self.assertIn("diagnostic-report.json", text)

    def test_compact_diagnosis_is_written_next_to_the_report(self):
        report = {
            "classification": "qualified_match",
            "qualification": {"qualified": True, "axes": {}},
            "frame": {"capture_frame_id": 1},
            "first_divergence": {"kind": "none"},
            "artifacts": {},
        }
        with tempfile.TemporaryDirectory() as directory:
            path = gfx_independent_replay.write_compact_diagnosis(
                directory, report)
            self.assertTrue(path.is_file())
            value = json.loads(path.read_text())
            self.assertEqual(value["schema"], "gfx-agent-diagnosis/v1")


class CompactSourceEvidenceDigestTests(unittest.TestCase):
    REFERENCE_SHA = "a" * 64
    METAL_SHA = "b" * 64

    def report(self, checkpoint=True):
        if checkpoint:
            evidence = {
                "status": "match",
                "reason": "the copy-source checkpoint was independently "
                          "observed to match",
                "event": 284,
                "target": "0",
                "checkpoint": {
                    "event": 284,
                    "target": "0",
                    "resource": "fb:0",
                    "status": "match",
                    "authoritative": True,
                    "statistics": {"mismatched_pixels": 0,
                                   "max_channel_error": 0},
                    "reference_sha256": self.REFERENCE_SHA,
                    "metal_sha256": self.METAL_SHA,
                },
            }
        else:
            evidence = {
                "status": "unobserved",
                "reason": "no copy-source checkpoint was captured at the "
                          "producer event",
                "event": 284,
                "target": "0",
            }
        return {
            "classification": "difference_with_partial_state_coverage",
            "qualification": {"qualified": False, "axes": {}},
            "frame": {"capture_frame_id": 1},
            "execution": {"status": "ok"},
            "first_divergence": {
                "kind": "intermediate",
                "event": 286,
                "target": "442",
                "resource": "tex:442",
                "producer_event": 284,
                "copy_source_evidence": evidence,
                "recommended_action": {
                    "action": "investigate_transfer",
                    "event": 284,
                    "observation_event": 286,
                    "reason": "the source checkpoint matches",
                },
            },
            "checkpoints": [],
            "pixels": {"status": "divergent"},
            "checkpoint_validation": {"status": "ok"},
            "artifacts": {},
        }

    def test_compact_source_evidence_preserves_both_digests(self):
        report = self.report()
        compact = gfx_independent_replay.compact_diagnosis(report)
        evidence = compact["first_divergence"]["copy_source_evidence"]
        source = report["first_divergence"]["copy_source_evidence"]
        checkpoint = evidence["checkpoint"]
        authoritative = source["checkpoint"]
        # The reference digest is authoritative and must survive projection.
        self.assertEqual(checkpoint["gl_sha256"], self.REFERENCE_SHA)
        self.assertEqual(checkpoint["gl_sha256"],
                         authoritative["reference_sha256"])
        # The Metal digest is preserved independently.
        self.assertEqual(checkpoint["metal_sha256"], self.METAL_SHA)
        self.assertEqual(checkpoint["metal_sha256"],
                         authoritative["metal_sha256"])
        # Both digests still name the source checkpoint, not the divergent one.
        self.assertEqual(checkpoint["event"], 284)
        self.assertEqual(checkpoint["target"], "0")
        self.assertEqual(checkpoint["resource"], "fb:0")
        self.assertEqual(checkpoint["status"], "match")
        self.assertEqual(compact["first_divergence"]["event"], 286)
        self.assertEqual(compact["first_divergence"]["event"],
                         report["first_divergence"]["event"])

    def test_missing_source_checkpoint_stays_explicitly_missing(self):
        report = self.report(checkpoint=False)
        compact = gfx_independent_replay.compact_diagnosis(report)
        evidence = compact["first_divergence"]["copy_source_evidence"]
        self.assertEqual(evidence["status"], "unobserved")
        self.assertIn("no copy-source checkpoint", evidence["reason"])
        self.assertIsNone(evidence["checkpoint"])
        self.assertIsNone(evidence["gl_sha256"])
        self.assertIsNone(evidence["metal_sha256"])
        self.assertEqual(evidence["event"], 284)
        self.assertEqual(evidence["target"], "0")


class CausalLowerBoundTests(unittest.TestCase):
    def graph(self):
        return {
            "events": [{
                "event": 4, "kind": "draw", "target": "fb:0",
                "entry": "_GfxDraw", "program": 7,
            }],
            "resources": {
                "fb:0": {
                    "producers": [{"event": 4, "kind": "draw",
                                   "entry": "_GfxDraw", "program": 7}],
                },
            },
        }

    def divergent(self):
        return {
            "event": 4, "target": "0", "resource": "fb:0",
            "status": "divergent", "authoritative": True,
            "statistics": {"mismatched_pixels": 4},
        }

    def test_unrelated_texture_match_does_not_bound_framebuffer(self):
        checkpoints = [
            {"event": 3, "target": "10", "resource": "tex:10",
             "status": "match", "authoritative": True},
            self.divergent(),
        ]
        value = gfx_independent_replay.first_divergence(
            self.graph(), checkpoints,
            {"status": "divergent", "statistics": {}},
            checkpoint_validation={"status": "ok"})
        self.assertEqual(value["kind"], "intermediate")
        interval = value["unresolved_interval"]
        self.assertIsNone(interval["start_event"])
        self.assertEqual(interval["lower_bound"], "unbounded")
        self.assertEqual(interval["search_start_event"], 0)

    def test_same_resource_match_bounds_the_framebuffer(self):
        checkpoints = [
            {"event": 3, "target": "0", "resource": "fb:0",
             "status": "match", "authoritative": True},
            self.divergent(),
        ]
        value = gfx_independent_replay.first_divergence(
            self.graph(), checkpoints,
            {"status": "divergent", "statistics": {}},
            checkpoint_validation={"status": "ok"})
        interval = value["unresolved_interval"]
        self.assertEqual(interval["start_event"], 3)
        self.assertEqual(interval["lower_bound"], "same_resource_match")

    def test_final_only_ignores_unrelated_matching_observations(self):
        checkpoints = [
            {"event": 3, "target": "10", "resource": "tex:10",
             "status": "match", "authoritative": True},
            {"event": 5, "target": "11", "resource": "tex:11",
             "status": "match", "authoritative": True},
        ]
        value = gfx_independent_replay.first_divergence(
            self.graph(), checkpoints,
            {"status": "divergent",
             "statistics": {"mismatched_pixels": 4}},
            checkpoint_validation={"status": "ok"})
        self.assertEqual(value["kind"], "final_only")
        interval = value["unresolved_interval"]
        self.assertIsNone(interval["start_event"])
        self.assertEqual(interval["lower_bound"], "unbounded")
        self.assertEqual(interval["search_start_event"], 0)

    def test_final_only_same_resource_match_bounds_the_interval(self):
        checkpoints = [
            {"event": 2, "target": "0", "resource": "fb:0",
             "status": "match", "authoritative": True},
        ]
        value = gfx_independent_replay.first_divergence(
            self.graph(), checkpoints,
            {"status": "divergent",
             "statistics": {"mismatched_pixels": 4}},
            checkpoint_validation={"status": "ok"})
        interval = value["unresolved_interval"]
        self.assertEqual(interval["start_event"], 2)
        self.assertEqual(interval["lower_bound"], "same_resource_match")

    def test_destination_interval_does_not_use_post_transfer_source_match(self):
        graph = {
            "events": [
                {"event": 284, "kind": "resolve",
                 "operation": "glCopyTexSubImage2D", "target": "fb:0"},
                {"event": 286, "kind": "draw", "target": "tex:442",
                 "entry": "_GfxDraw", "program": 352,
                 "samples": [{"resource": "tex:442"}]},
            ],
            "resources": {
                "tex:442": {
                    "producers": [{
                        "event": 284, "kind": "resolve",
                        "operation": "glCopyTexSubImage2D",
                        "source": "0", "source_framebuffer": 0,
                    }],
                    "consumers": [{"event": 286, "unit": 0,
                                   "sampler": "tex0"}],
                },
            },
        }
        checkpoints = [
            # A source observation after the transfer must not bound the
            # destination version.
            {"event": 285, "target": "0", "resource": "fb:0",
             "status": "match", "authoritative": True},
            {"event": 286, "target": "442", "resource": "tex:442",
             "status": "divergent", "authoritative": True,
             "statistics": {"mismatched_pixels": 4}},
        ]
        value = gfx_independent_replay.first_divergence(
            graph, checkpoints, {"status": "divergent", "statistics": {}},
            checkpoint_validation={"status": "ok"})
        self.assertEqual(value["event"], 286)
        interval = value["unresolved_interval"]
        self.assertIsNone(interval["start_event"])
        self.assertEqual(interval["lower_bound"], "unbounded")
        self.assertEqual(interval["search_start_event"], 0)

    def test_compact_selects_the_recorded_copy_source_identity(self):
        report = {
            "classification": "difference_with_partial_state_coverage",
            "qualification": {"qualified": False, "axes": {}},
            "frame": {"capture_frame_id": 1},
            "first_divergence": {
                "kind": "intermediate", "event": 286, "target": "442",
                "resource": "tex:442", "producer_event": 284,
                "copy_source_evidence": {
                    "status": "match",
                    "checkpoint": {
                        "event": 284, "target": "123",
                        "resource": "tex:123", "status": "match",
                    },
                },
            },
            "checkpoints": [
                {"event": 284, "target": "0", "resource": "fb:0",
                 "status": "match", "authoritative": True},
                {"event": 284, "target": "123", "resource": "tex:123",
                 "status": "match", "authoritative": True},
                {"event": 286, "target": "442", "resource": "tex:442",
                 "status": "divergent", "authoritative": True,
                 "statistics": {"mismatched_pixels": 4}},
            ],
            "pixels": {"status": "divergent"},
            "checkpoint_validation": {"status": "ok"},
        }
        compact = gfx_independent_replay.compact_diagnosis(report)
        reasons = {(item["event"], str(item["target"])):
                   item["selected_because"]
                   for item in compact["checkpoints"]}
        self.assertIn("copy_source", reasons[(284, "123")])
        self.assertNotIn("copy_source", reasons[(284, "0")])

    def test_source_divergence_at_event_one_can_capture_event_zero(self):
        graph = {
            "events": [{"event": 1, "kind": "resolve",
                        "operation": "glCopyTexSubImage2D",
                        "target": "fb:0"}],
            "resources": {"fb:0": {"producers": []}},
        }
        checkpoints = [{
            "event": 1, "target": "0", "resource": "fb:0",
            "status": "divergent", "authoritative": True,
            "statistics": {"mismatched_pixels": 4},
        }]
        value = gfx_independent_replay.first_divergence(
            graph, checkpoints, {"status": "divergent", "statistics": {}},
            checkpoint_validation={"status": "ok"})
        checkpoint = value["next_checkpoint"]
        self.assertEqual(checkpoint["event"], 0)
        self.assertTrue(checkpoint["requires_capture"])
        self.assertEqual(checkpoint["interval"], [0, 1])
        self.assertEqual(checkpoint["lower_bound"], "unbounded")
        self.assertEqual(value["unresolved_interval"]["lower_bound"],
                         "unbounded")

    def test_source_divergence_at_event_zero_is_initial_state(self):
        graph = {
            "events": [{"event": 0, "kind": "resolve",
                        "operation": "glCopyTexSubImage2D",
                        "target": "fb:0"}],
            "resources": {"fb:0": {"producers": []}},
        }
        checkpoints = [{
            "event": 0, "target": "0", "resource": "fb:0",
            "status": "divergent", "authoritative": True,
            "statistics": {"mismatched_pixels": 4},
        }]
        value = gfx_independent_replay.first_divergence(
            graph, checkpoints, {"status": "divergent", "statistics": {}},
            checkpoint_validation={"status": "ok"})
        self.assertIsNone(value["next_checkpoint"])
        self.assertEqual(value["recommended_action"]["action"],
                         "investigate_initial_state")
        self.assertEqual(value["recommended_action"]["event"], 0)
        self.assertIn("event 0", value["recommended_action"]["reason"])

    def test_compact_final_only_retains_lower_bound_checkpoint(self):
        report = {
            "classification": "difference_with_partial_state_coverage",
            "qualification": {"qualified": False, "axes": {}},
            "frame": {"capture_frame_id": 1},
            "first_divergence": {
                "kind": "final_only", "event": None,
                "unresolved_interval": {
                    "start_event": 5, "end_event": 9,
                    "lower_bound": "same_resource_match",
                },
            },
            "checkpoints": [
                {"event": 2, "target": "10", "resource": "tex:10",
                 "status": "match", "authoritative": True},
                {"event": 5, "target": "0", "resource": "fb:0",
                 "status": "match", "authoritative": True},
                {"event": 7, "target": "11", "resource": "tex:11",
                 "status": "match", "authoritative": True},
            ],
            "pixels": {"status": "divergent"},
            "checkpoint_validation": {"status": "ok"},
        }
        compact = gfx_independent_replay.compact_diagnosis(report)
        reasons = {(item["event"], str(item["target"])):
                   item["selected_because"]
                   for item in compact["checkpoints"]}
        self.assertIn("last_matching", reasons[(5, "0")])

    def test_compact_selection_does_not_label_unrelated_match(self):
        report = {
            "classification": "difference_with_partial_state_coverage",
            "qualification": {"qualified": False, "axes": {}},
            "frame": {"capture_frame_id": 1},
            "first_divergence": {
                "kind": "intermediate", "event": 4, "target": "0",
                "resource": "fb:0", "producer_event": 3,
                "copy_source_evidence": None,
            },
            "checkpoints": [
                {"event": 2, "target": "0", "resource": "fb:0",
                 "status": "match", "authoritative": True},
                {"event": 3, "target": "10", "resource": "tex:10",
                 "status": "match", "authoritative": True},
                {"event": 4, "target": "0", "resource": "fb:0",
                 "status": "divergent", "authoritative": True,
                 "statistics": {"mismatched_pixels": 4}},
            ],
            "pixels": {"status": "divergent"},
            "checkpoint_validation": {"status": "ok"},
        }
        compact = gfx_independent_replay.compact_diagnosis(report)
        reasons = {item["event"]: item["selected_because"]
                   for item in compact["checkpoints"]}
        self.assertIn("last_matching", reasons[2])
        self.assertNotIn("last_matching", reasons.get(3, []))
        labels = {label for values in reasons.values() for label in values}
        self.assertNotIn("copy_source", labels)


class CheckpointBisectionTests(unittest.TestCase):
    """Selected fb:0 checkpoints narrow the causal interval truthfully."""

    def graph(self):
        return {
            "events": [
                {"event": 142, "kind": "draw", "target": "fb:0",
                 "entry": "_GfxDraw", "program": 346},
                {"event": 284, "kind": "resolve",
                 "operation": "glCopyTexSubImage2D", "target": "fb:0"},
                {"event": 286, "kind": "draw", "target": "tex:442",
                 "entry": "_GfxDraw", "program": 352,
                 "samples": [{"resource": "tex:442"}]},
            ],
            "resources": {
                "fb:0": {"producers": [{
                    "event": 142, "kind": "draw", "entry": "_GfxDraw",
                    "program": 346}]},
                "tex:442": {
                    "producers": [{
                        "event": 284, "kind": "resolve",
                        "operation": "glCopyTexSubImage2D",
                        "source": "0", "source_framebuffer": 0}],
                    "consumers": [{"event": 286, "unit": 0,
                                   "sampler": "tex0"}],
                },
            },
        }

    def checkpoint(self, event, status, *, resource="fb:0", target="0",
                   mismatched=0):
        return {
            "event": event, "target": target, "resource": resource,
            "status": status, "authoritative": True,
            "statistics": {"mismatched_pixels": mismatched},
        }

    def test_matching_142_and_divergent_284_narrow_to_142_284(self):
        value = gfx_independent_replay.first_divergence(
            self.graph(),
            [self.checkpoint(142, "match"),
             self.checkpoint(284, "divergent", mismatched=4)],
            {"status": "divergent", "statistics": {}},
            checkpoint_validation={"status": "ok"})
        self.assertEqual(value["kind"], "intermediate")
        self.assertEqual(value["event"], 284)
        interval = value["unresolved_interval"]
        self.assertEqual(interval["start_event"], 142)
        self.assertEqual(interval["end_event"], 284)
        self.assertEqual(interval["lower_bound"], "same_resource_match")
        self.assertEqual(interval["search_start_event"], 142)
        checkpoint = value["next_checkpoint"]
        self.assertEqual(checkpoint["event"], 213)
        self.assertEqual(checkpoint["target"], "0")
        self.assertEqual(checkpoint["resource"], "fb:0")
        self.assertTrue(checkpoint["requires_capture"])
        self.assertEqual(checkpoint["interval"], [142, 284])
        self.assertEqual(checkpoint["lower_bound"], "same_resource_match")

    def test_divergent_142_is_unbounded_and_recommends_71(self):
        value = gfx_independent_replay.first_divergence(
            self.graph(),
            [self.checkpoint(142, "divergent", mismatched=4)],
            {"status": "divergent", "statistics": {}},
            checkpoint_validation={"status": "ok"})
        self.assertEqual(value["kind"], "intermediate")
        self.assertEqual(value["event"], 142)
        interval = value["unresolved_interval"]
        self.assertIsNone(interval["start_event"])
        self.assertEqual(interval["end_event"], 142)
        self.assertEqual(interval["lower_bound"], "unbounded")
        self.assertEqual(interval["search_start_event"], 0)
        checkpoint = value["next_checkpoint"]
        self.assertEqual(checkpoint["event"], 71)
        self.assertEqual(checkpoint["interval"], [0, 142])
        self.assertEqual(checkpoint["lower_bound"], "unbounded")
        self.assertEqual(value["confidence"], "confirmed")
        self.assertEqual(value["root_cause"], "not_established")

    def test_unrelated_texture_match_does_not_bound_divergent_142(self):
        value = gfx_independent_replay.first_divergence(
            self.graph(),
            [self.checkpoint(140, "match", resource="tex:5", target="5"),
             self.checkpoint(142, "divergent", mismatched=4)],
            {"status": "divergent", "statistics": {}},
            checkpoint_validation={"status": "ok"})
        interval = value["unresolved_interval"]
        self.assertIsNone(interval["start_event"])
        self.assertEqual(interval["lower_bound"], "unbounded")
        self.assertEqual(value["next_checkpoint"]["event"], 71)

    def test_adjacent_match_candidate_is_the_operation_after_it(self):
        value = gfx_independent_replay.first_divergence(
            self.graph(),
            [self.checkpoint(141, "match"),
             self.checkpoint(142, "divergent", mismatched=4)],
            {"status": "divergent", "statistics": {}},
            checkpoint_validation={"status": "ok"})
        self.assertIsNone(value["next_checkpoint"])
        self.assertIsNone(value["recommended_checkpoint"])
        action = value["recommended_action"]
        self.assertEqual(action["action"], "investigate_event")
        self.assertEqual(action["event"], 141)
        self.assertEqual(action["observation_event"], 142)
        self.assertIn("observes the result", action["reason"])

    def checkpoint_with_capture(self, event, status, capture, *,
                                mismatched=0):
        value = self.checkpoint(event, status, mismatched=mismatched)
        if capture is not None:
            value["capture"] = dict(capture)
        return value

    def capture(self, *, session="session-1", frame=7, stream="e" * 64):
        return {
            "session_id": session,
            "capture_frame_id": frame,
            "event_stream_sha256": stream,
        }

    def test_three_same_frame_checkpoints_bound_a_tight_interval(self):
        capture = self.capture()
        value = gfx_independent_replay.first_divergence(
            self.graph(),
            [self.checkpoint_with_capture(14, "match", capture),
             self.checkpoint_with_capture(71, "match", capture),
             self.checkpoint_with_capture(142, "divergent", capture,
                                          mismatched=4)],
            {"status": "divergent", "statistics": {}},
            checkpoint_validation={"status": "ok"})
        self.assertEqual(value["kind"], "intermediate")
        self.assertEqual(value["event"], 142)
        interval = value["unresolved_interval"]
        self.assertEqual(interval["start_event"], 71)
        self.assertEqual(interval["end_event"], 142)
        self.assertEqual(interval["lower_bound"], "same_resource_match")
        self.assertEqual(value["next_checkpoint"]["event"], 106)
        self.assertEqual(value["next_checkpoint"]["interval"], [71, 142])
        self.assertNotIn(14, [index for index in
                              value["cross_capture_excluded"][
                                  "capture_matches"]])

    def test_matching_checkpoint_from_another_capture_does_not_bound(self):
        capture = self.capture()
        foreign = self.capture(session="session-0", frame=3)
        value = gfx_independent_replay.first_divergence(
            self.graph(),
            [self.checkpoint_with_capture(71, "match", foreign),
             self.checkpoint_with_capture(142, "divergent", capture,
                                          mismatched=4)],
            {"status": "divergent", "statistics": {}},
            checkpoint_validation={"status": "ok"})
        interval = value["unresolved_interval"]
        self.assertIsNone(interval["start_event"])
        self.assertEqual(interval["lower_bound"], "unbounded")
        self.assertEqual(
            value["cross_capture_excluded"]["capture_matches"], [71])
        self.assertEqual(value["next_checkpoint"]["event"], 71)

    def test_identity_without_an_event_stream_does_not_match(self):
        capture = self.capture()
        partial = {"session_id": "session-1", "capture_frame_id": 7}
        value = gfx_independent_replay.first_divergence(
            self.graph(),
            [self.checkpoint_with_capture(71, "match", partial),
             self.checkpoint_with_capture(142, "divergent", capture,
                                          mismatched=4)],
            {"status": "divergent", "statistics": {}},
            checkpoint_validation={"status": "ok"})
        interval = value["unresolved_interval"]
        self.assertIsNone(interval["start_event"])
        self.assertEqual(interval["lower_bound"], "unbounded")

    def test_unavailable_checkpoint_does_not_narrow(self):
        for status in ("incomplete", "invalid"):
            value = gfx_independent_replay.first_divergence(
                self.graph(), [],
                {"status": "divergent", "statistics": {}},
                checkpoint_validation={
                    "status": status,
                    "problems": [{"kind": "missing_result", "event": 142}]})
            self.assertEqual(
                value["kind"],
                "invalid_checkpoint" if status == "invalid"
                else "incomplete_checkpoint")
            self.assertIsNone(value["recommended_checkpoint"])
            self.assertEqual(value["recommended_action"]["action"],
                             "repair_evidence")
            self.assertIn("instrumentation failure", value["reason"])


class FirstDivergenceSourceEvidenceTests(unittest.TestCase):
    def graph(self):
        return {
            "events": [
                {"event": 284, "kind": "resolve",
                 "operation": "glCopyTexSubImage2D", "target": "fb:0"},
                {"event": 286, "kind": "draw", "target": "tex:442",
                 "entry": "_GfxDraw", "program": 352,
                 "samples": [{"resource": "tex:442"}]},
            ],
            "resources": {
                "tex:442": {
                    "producers": [{
                        "event": 284, "kind": "resolve",
                        "operation": "glCopyTexSubImage2D",
                        "source": "0", "source_framebuffer": 0,
                    }],
                    "consumers": [{"event": 286, "unit": 0,
                                   "sampler": "tex0"}],
                    # Destination captures must never be used as source
                    # evidence.
                    "captured_versions": [{
                        "event": 286, "file": "destination.bin",
                        "sha256": "d" * 64,
                    }],
                },
            },
        }

    def divergent(self, event=286, target="442", resource="tex:442"):
        return {
            "event": event, "target": target, "resource": resource,
            "status": "divergent", "authoritative": True,
            "statistics": {"mismatched_pixels": 10},
        }

    def test_independent_divergent_source_justifies_upstream_bisection(self):
        checkpoints = [{
            "event": 284, "target": "0", "resource": "fb:0",
            "status": "divergent", "authoritative": True,
            "statistics": {"mismatched_pixels": 10},
        }]
        value = gfx_independent_replay.first_divergence(
            self.graph(), checkpoints, {"status": "divergent",
                                        "statistics": {}},
            checkpoint_validation={"status": "ok"})
        self.assertEqual(value["event"], 284)
        self.assertEqual(value["copy_source_evidence"]["status"], "divergent")
        self.assertTrue(value["recommended_checkpoint"]["requires_capture"])
        self.assertEqual(value["recommended_checkpoint"]["event"], 142)
        self.assertIn("copy source already differs",
                      value["recommended_checkpoint"]["reason"])

    def test_unrelated_matching_checkpoint_does_not_tighten_the_interval(self):
        checkpoints = [
            {"event": 280, "target": "10", "resource": "tex:10",
             "status": "match", "authoritative": True},
            {"event": 284, "target": "0", "resource": "fb:0",
             "status": "divergent", "authoritative": True,
             "statistics": {"mismatched_pixels": 10}},
        ]
        value = gfx_independent_replay.first_divergence(
            self.graph(), checkpoints, {"status": "divergent",
                                        "statistics": {}},
            checkpoint_validation={"status": "ok"})
        self.assertIsNone(value["unresolved_interval"]["start_event"])
        self.assertEqual(value["recommended_checkpoint"]["event"], 142)
        self.assertEqual(value["recommended_checkpoint"]["lower_bound"],
                         "unbounded")

    def test_same_resource_match_sets_the_bisection_lower_bound(self):
        checkpoints = [
            {"event": 280, "target": "0", "resource": "fb:0",
             "status": "match", "authoritative": True},
            {"event": 284, "target": "0", "resource": "fb:0",
             "status": "divergent", "authoritative": True,
             "statistics": {"mismatched_pixels": 10}},
        ]
        value = gfx_independent_replay.first_divergence(
            self.graph(), checkpoints, {"status": "divergent",
                                        "statistics": {}},
            checkpoint_validation={"status": "ok"})
        self.assertEqual(value["unresolved_interval"]["start_event"], 280)
        self.assertEqual(value["recommended_checkpoint"]["event"], 282)
        self.assertEqual(value["recommended_checkpoint"]["lower_bound"],
                         "same_resource_match")

    def test_no_interior_checkpoint_stops_bisection(self):
        checkpoints = [
            {"event": 283, "target": "0", "resource": "fb:0",
             "status": "match", "authoritative": True},
            {"event": 284, "target": "0", "resource": "fb:0",
             "status": "divergent", "authoritative": True,
             "statistics": {"mismatched_pixels": 10}},
        ]
        value = gfx_independent_replay.first_divergence(
            self.graph(), checkpoints, {"status": "divergent",
                                        "statistics": {}},
            checkpoint_validation={"status": "ok"})
        self.assertIsNone(value["next_checkpoint"])
        self.assertIsNone(value["recommended_checkpoint"])
        self.assertEqual(value["recommended_action"]["action"],
                         "investigate_event")
        self.assertEqual(value["recommended_action"]["event"], 283)
        self.assertEqual(value["recommended_action"]["observation_event"], 284)
        self.assertIn("observes the result",
                      value["recommended_action"]["reason"])

    def test_transfer_source_evidence_uses_the_transfer_event(self):
        graph = {
            "events": [
                {"event": 280, "kind": "draw", "target": "tex:123",
                 "entry": "_GfxDraw", "program": 1},
                {"event": 284, "kind": "resolve",
                 "operation": "glCopyTexSubImage2D", "target": "fb:0"},
                {"event": 286, "kind": "draw", "target": "tex:442",
                 "entry": "_GfxDraw", "program": 2},
            ],
            "resources": {
                "tex:123": {
                    "producers": [{"event": 280, "kind": "draw",
                                   "entry": "_GfxDraw"}],
                    "consumers": [{"event": 284, "unit": 0,
                                   "sampler": "resolve"}],
                },
                "tex:442": {
                    "producers": [{"event": 284, "kind": "resolve",
                                   "operation": "glCopyTexSubImage2D",
                                   "source": "123", "source_framebuffer": 1}],
                    "consumers": [{"event": 286, "unit": 0,
                                   "sampler": "tex0"}],
                },
            },
        }
        checkpoints = [{
            "event": 284, "target": "123", "resource": "tex:123",
            "status": "divergent", "authoritative": True,
            "statistics": {"mismatched_pixels": 10},
        }, {
            "event": 286, "target": "442", "resource": "tex:442",
            "status": "divergent", "authoritative": True,
            "statistics": {"mismatched_pixels": 10},
        }]
        value = gfx_independent_replay.first_divergence(
            graph, checkpoints, {"status": "divergent", "statistics": {}},
            checkpoint_validation={"status": "ok"})
        self.assertEqual(value["event"], 284)
        self.assertEqual(value["copy_source_evidence"]["status"], "divergent")
        self.assertEqual(value["copy_source_evidence"]["target"], "123")
        self.assertNotIn("no copy-source checkpoint",
                         value["copy_source_evidence"]["reason"])

    def test_non_default_source_keeps_its_texture_target(self):
        graph = self.graph()
        graph["resources"]["tex:442"]["producers"][0].update({
            "source": "123", "source_framebuffer": 1})
        checkpoints = [{
            "event": 286, "target": "442", "resource": "tex:442",
            "status": "divergent", "authoritative": True,
            "statistics": {"mismatched_pixels": 10},
        }]
        # The source checkpoint must use the actual source target.
        checkpoints.insert(0, {
            "event": 284, "target": "123", "resource": "tex:123",
            "status": "match", "authoritative": True,
        })
        value = gfx_independent_replay.first_divergence(
            graph, checkpoints, {"status": "divergent", "statistics": {}},
            checkpoint_validation={"status": "ok"})
        self.assertEqual(value["copy_source_evidence"]["status"], "match")
        self.assertEqual(value["recommended_action"]["action"],
                         "investigate_transfer")

    def test_non_default_divergent_source_recommends_the_same_target(self):
        graph = self.graph()
        graph["resources"]["tex:442"]["producers"][0].update({
            "source": "123", "source_framebuffer": 1})
        checkpoints = [{
            "event": 284, "target": "123", "resource": "tex:123",
            "status": "divergent", "authoritative": True,
            "statistics": {"mismatched_pixels": 10},
        }, {
            "event": 286, "target": "442", "resource": "tex:442",
            "status": "divergent", "authoritative": True,
            "statistics": {"mismatched_pixels": 10},
        }]
        value = gfx_independent_replay.first_divergence(
            graph, checkpoints, {"status": "divergent", "statistics": {}},
            checkpoint_validation={"status": "ok"})
        self.assertEqual(value["copy_source_evidence"]["status"], "divergent")
        self.assertEqual(value["recommended_checkpoint"]["target"], "123")
        self.assertEqual(value["recommended_checkpoint"]["resource"],
                         "tex:123")

    def test_matching_source_recommends_transfer_investigation(self):
        checkpoints = [
            {"event": 284, "target": "0", "status": "match",
             "authoritative": True},
            self.divergent(),
        ]
        value = gfx_independent_replay.first_divergence(
            self.graph(), checkpoints, {"status": "divergent",
                                        "statistics": {}},
            checkpoint_validation={"status": "ok"})
        self.assertEqual(value["event"], 286)
        self.assertEqual(value["copy_source_evidence"]["status"], "match")
        self.assertIsNone(value["next_checkpoint"])
        self.assertIsNone(value["recommended_checkpoint"])
        self.assertEqual(value["recommended_action"]["action"],
                         "investigate_transfer")
        self.assertEqual(value["recommended_action"]["event"], 284)
        self.assertEqual(value["recommended_action"]["observation_event"], 286)
        self.assertIn("do not bisect upstream",
                      value["recommended_action"]["reason"])

    def test_destination_captures_do_not_substitute_for_source_evidence(self):
        value = gfx_independent_replay.first_divergence(
            self.graph(), [self.divergent()],
            {"status": "divergent", "statistics": {}},
            checkpoint_validation={"status": "ok"})
        self.assertEqual(value["copy_source_evidence"]["status"], "unobserved")
        self.assertEqual(value["next_checkpoint"]["event"], 284)
        self.assertEqual(value["next_checkpoint"]["target"], "0")
        self.assertTrue(value["next_checkpoint"]["requires_capture"])
        self.assertEqual(value["recommended_checkpoint"]["source"],
                         "next_checkpoint")

    def test_draw_producer_does_not_emit_copy_source_evidence(self):
        graph = self.graph()
        producer = graph["resources"]["tex:442"]["producers"][0]
        producer["kind"] = "draw"
        producer["entry"] = "_GfxDraw"
        producer.pop("source", None)
        producer.pop("source_framebuffer", None)
        value = gfx_independent_replay.first_divergence(
            graph, [self.divergent()],
            {"status": "divergent", "statistics": {}},
            checkpoint_validation={"status": "ok"})
        # A draw output is not a transfer source: no copy-source block and no
        # false "capture the copy source" limitation may be emitted.
        self.assertIsNone(value["copy_source_evidence"])
        self.assertFalse(any("copy-source" in str(item)
                             for item in value["limitations"]))
        self.assertIsNone(value["next_checkpoint"])
        self.assertIsNone(value["recommended_checkpoint"])

    def test_invalid_source_recommends_repair(self):
        checkpoints = [
            {"event": 284, "target": "0", "status": "invalid",
             "authoritative": False},
            self.divergent(),
        ]
        value = gfx_independent_replay.first_divergence(
            self.graph(), checkpoints, {"status": "divergent",
                                        "statistics": {}},
            checkpoint_validation={"status": "ok"})
        self.assertEqual(value["copy_source_evidence"]["status"], "invalid")
        self.assertTrue(value["next_checkpoint"]["requires_capture"])
        self.assertIn("invalid", value["next_checkpoint"]["reason"])

    def test_final_only_normalizes_suggested_checkpoint(self):
        graph = self.graph()
        graph["events"].append({
            "event": 290, "kind": "draw", "target": "fb:0",
            "entry": "_GfxDraw", "program": 1,
        })
        value = gfx_independent_replay.first_divergence(
            graph, [], {"status": "divergent",
                        "statistics": {"mismatched_pixels": 4}},
            checkpoint_validation={"status": "ok"})
        self.assertEqual(value["kind"], "final_only")
        self.assertIsNotNone(value["suggested_checkpoint"])
        self.assertEqual(value["recommended_checkpoint"]["event"], 290)
        self.assertEqual(value["recommended_checkpoint"]["source"],
                         "suggested_checkpoint")
        self.assertEqual(value["recommended_checkpoint"]["target"], "0")
        self.assertTrue(value["recommended_checkpoint"]["requires_capture"])

    def test_missing_evidence_has_no_checkpoint_recommendation(self):
        value = gfx_independent_replay.first_divergence(
            self.graph(), [], {"status": "divergent", "statistics": {}},
            checkpoint_validation={"status": "incomplete"})
        self.assertIsNone(value["recommended_checkpoint"])
        self.assertEqual(value["recommended_action"]["action"],
                         "repair_evidence")

    def test_unsupported_state_retains_the_final_recommendation(self):
        graph = self.graph()
        graph["events"].append({
            "event": 290, "kind": "draw", "target": "fb:0",
            "entry": "_GfxDraw", "program": 1,
        })
        value = gfx_independent_replay.first_divergence(
            graph, [], {"status": "divergent", "statistics": {}},
            checkpoint_validation={"status": "ok"},
            state_coverage_report={"unsupported": ["gl_dither"]})
        self.assertEqual(value["unsupported_state"], ["gl_dither"])
        self.assertEqual(value["recommended_checkpoint"]["event"], 290)
