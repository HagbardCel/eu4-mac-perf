"""Strategy B: dependency graph, replay contract and binding audit.

These tests are fully offline and synthesize a small frame trace.  No EU4
launch, GPU execution or external capture is required.
"""

from __future__ import annotations

import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from automation import gfx_frame_graph, gfx_independent_replay  # noqa: E402

WIDTH, HEIGHT = 8, 8
TEXTURE_WIDTH, TEXTURE_HEIGHT = 4, 4
SHADER = b"void main(){}"


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def texture(name, unit, payload, *, internal="RGBA8",
            width=TEXTURE_WIDTH, height=TEXTURE_HEIGHT):
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
            "level": 0,
            "width": width,
            "height": height,
            "pixel_format": "RGBA8",
            "bytes": len(payload),
            "sha256": digest,
            "file": f"tex-{digest}.bin",
        }],
    }


def draw(index, target, samples, *, program=1, color=(0.2, 0.4, 0.6, 1.0)):
    attachment = None
    if target is not None:
        attachment = {
            "type": "texture", "name": target, "level": 0,
            "internal_format": "RGBA8", "width": WIDTH, "height": HEIGHT,
        }
    return {
        "kind": "draw",
        "index": index,
        "draw": {"entry": "_GfxDraw", "mode": 4, "count": 3,
                 "caller_rva": "0x1000"},
        "state": {"viewport": [0, 0, WIDTH, HEIGHT]},
        "program": {"name": program, "shaders": [
            {"stage": "vertex", "sha256": sha(SHADER),
             "file": sha(SHADER) + ".vert"},
            {"stage": "pixel", "sha256": sha(SHADER),
             "file": sha(SHADER) + ".frag"},
        ]},
        "target": {
            "framebuffer": 0 if target is None else 1,
            "width": WIDTH, "height": HEIGHT,
            "color_attachment": attachment,
        },
        "textures": samples,
        "attributes": [{
            "location": 0, "name": "in_POSITION", "components": 2,
            "gl_type": "0x1406", "stride": 8,
            "data": "verts.bin", "sha256": sha(b"verts!"),
        }],
        "seed": None,
    }


def clear(target, flags=7):
    return {
        "kind": "clear",
        "flags": flags,
        "color": [0, 0, 0, 1],
        "depth": 1,
        "stencil": 0,
        "target": {
            "framebuffer": 0 if target is None else 1,
            "width": WIDTH, "height": HEIGHT,
            "color_attachment": None if target is None else {
                "type": "texture", "name": target, "level": 0,
                "internal_format": "RGBA8", "width": WIDTH, "height": HEIGHT,
            },
        },
    }


class FrameFixture:
    def __init__(self, root: Path):
        self.root = root
        self.frame = root / "frame"
        self.blobs = self.frame / "blobs"
        self.blobs.mkdir(parents=True)
        self.asset_payload = b"asset-pixels-01"
        self.rt_payload = b"target-content01"
        self.asset = texture(1, 1, self.asset_payload)
        self.rt = texture(10, 10, self.rt_payload,
                          width=WIDTH, height=HEIGHT)
        self.events = [
            clear(None, 7),
            draw(1, None, [self.asset], program=1),
            clear(10, 1),
            draw(2, 10, [self.asset], program=2),
            draw(3, None, [self.rt], program=3),
        ]
        self.events[1]["seed"] = {
            "width": WIDTH, "height": HEIGHT,
            "file": "seed.bin", "sha256": sha(b"seed-pixels-00"),
        }
        self.reference = bytes(range(WIDTH * HEIGHT * 4))
        self.write()

    def write(self):
        self.frame.mkdir(exist_ok=True)
        (self.frame / "frame-events.jsonl").write_text(
            "\n".join(json.dumps(event) for event in self.events) + "\n")
        self.write_blob("seed.bin", b"seed-pixels-00")
        self.write_blob("verts.bin", b"verts!")
        self.write_blob(sha(SHADER) + ".vert", SHADER)
        self.write_blob(sha(SHADER) + ".frag", SHADER)
        self.write_blob(self.asset["levels"][0]["file"], self.asset_payload)
        self.write_blob(self.rt["levels"][0]["file"], self.rt_payload)
        (self.frame / "frame-reference.bgra").write_bytes(self.reference)
        manifest = {
            "schema": "gfx-frame-trace/v1",
            "complete": True,
            "reason": "scene_present",
            "events": len(self.events),
            "frame": {
                "width": WIDTH, "height": HEIGHT, "origin": "lower_left",
                "pixel_format": "BGRA8", "row_stride": WIDTH * 4,
                "reference": "frame-reference.bgra",
                "reference_bytes": len(self.reference),
                "reference_sha256": sha(self.reference),
            },
            "seed": {"width": WIDTH, "height": HEIGHT},
        }
        (self.frame / "frame-trace.json").write_text(json.dumps(manifest))

    def write_blob(self, name, payload):
        (self.blobs / name).write_bytes(payload)


class GraphTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.fixture = FrameFixture(Path(self.temporary.name))

    def tearDown(self):
        self.temporary.cleanup()

    def test_graph_separates_external_and_renderer_generated(self):
        graph = gfx_frame_graph.build(self.fixture.frame)
        self.assertEqual(graph["status"], "complete", graph["reasons"])
        self.assertIn("tex:1", graph["external_inputs"])
        self.assertIn("tex:10", graph["renderer_generated_sampled"])
        self.assertEqual(graph["unproven"], [])
        self.assertTrue(graph["resources"]["tex:10"]["has_clear"])
        self.assertEqual(graph["counts"]["resolves"], 0)

    def test_boundary_seed_is_superseded_by_in_frame_clear(self):
        graph = gfx_frame_graph.build(self.fixture.frame)
        self.assertTrue(graph["boundary"]["seed_superseded_by_in_frame_clear"])

    def test_checkpoint_plan_uses_first_consumer_reference(self):
        graph = gfx_frame_graph.build(self.fixture.frame)
        plan = gfx_frame_graph.checkpoint_plan(graph)
        self.assertEqual(len(plan), 1)
        self.assertEqual(plan[0]["event"], 4)
        self.assertEqual(plan[0]["target"], "10")
        self.assertEqual(plan[0]["reference_sha256"],
                         sha(self.fixture.rt_payload))

    def test_sampled_full_extent_texture_without_producer_is_partial(self):
        fixture = self.fixture
        ghost = texture(99, 9, b"ghost-pixels-01",
                        width=WIDTH, height=HEIGHT)
        fixture.events.append(draw(4, None, [ghost], program=4))
        fixture.events[-1]["seed"] = None
        fixture.write_blob(ghost["levels"][0]["file"], b"ghost-pixels-01")
        fixture.events.append(clear(None, 3))
        fixture.write()
        # Manifest events must match.
        manifest = json.loads((fixture.frame / "frame-trace.json").read_text())
        manifest["events"] = len(fixture.events)
        (fixture.frame / "frame-trace.json").write_text(json.dumps(manifest))
        graph = gfx_frame_graph.build(fixture.frame)
        self.assertEqual(graph["status"], "partial")
        self.assertEqual(graph["unproven"], ["tex:99"])
        self.assertTrue(any("resolve/copy" in reason for reason in graph["reasons"]))

    def test_missing_blob_is_invalid_not_silently_accepted(self):
        (self.fixture.blobs / self.fixture.rt["levels"][0]["file"]).unlink()
        graph = gfx_frame_graph.build(self.fixture.frame)
        self.assertEqual(graph["status"], "invalid")
        self.assertTrue(graph["reasons"])


def replay_provenance(*, seeded=False, generation=2):
    sample = {
        "event": 4, "texture": "10", "sampler": "tex10", "unit": 10,
        "source": "external_capture" if seeded else "metal_render_target",
        "generation": 0 if generation == 0 else generation,
        "expected_texture": "10", "selected_texture": "10",
    }
    asset = {
        "event": 1, "texture": "1", "sampler": "tex1", "unit": 1,
        "source": "external_capture", "generation": -1,
        "expected_texture": "1", "selected_texture": "1",
    }
    asset3 = dict(asset)
    asset3["event"] = 3
    return [asset, asset3, sample]


def fake_replay(**overrides):
    value = {
        "status": "ok", "events": 5, "encoded": 5, "failed": 0,
        "command_status": 4, "readback": True,
        "provenance": {
            "bindings": 2, "metal_render_target_bindings": 1,
            "external_capture_bindings": 1,
            "render_target_textures": ["10"], "external_textures": ["1"],
            "first_sample_before_any_write": [],
        },
        "_provenance_samples": replay_provenance(),
    }
    value.update(overrides)
    return value


class ReplayContractTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.fixture = FrameFixture(Path(self.temporary.name))
        self.graph = gfx_frame_graph.build(self.fixture.frame)

    def tearDown(self):
        self.temporary.cleanup()

    def test_independent_replay_is_complete(self):
        value = gfx_frame_graph.evaluate_replay(self.graph, fake_replay())
        self.assertEqual(value["status"], "complete", value["reasons"])
        self.assertEqual(value["seeded_intermediates"], [])

    def test_gl_intermediate_seeding_invalidates_the_replay(self):
        replay = fake_replay()
        replay["_provenance_samples"] = replay_provenance(seeded=True)
        value = gfx_frame_graph.evaluate_replay(self.graph, replay)
        self.assertEqual(value["status"], "invalid")
        self.assertTrue(any(item["resource"] == "tex:10"
                            for item in value["seeded_intermediates"]))

    def test_zero_generation_target_is_not_independent(self):
        replay = fake_replay()
        replay["_provenance_samples"] = replay_provenance(generation=0)
        value = gfx_frame_graph.evaluate_replay(self.graph, replay)
        self.assertIn(value["status"], ("invalid", "partial"))
        self.assertTrue(value["seeded_intermediates"])

    def test_second_sample_requires_the_newer_generation(self):
        fixture = self.fixture
        fixture.events.append(draw(4, 10, [fixture.asset], program=2))
        fixture.events.append(draw(5, None, [fixture.rt], program=3))
        fixture.write()
        manifest = json.loads((fixture.frame / "frame-trace.json").read_text())
        manifest["events"] = len(fixture.events)
        (fixture.frame / "frame-trace.json").write_text(json.dumps(manifest))
        graph = gfx_frame_graph.build(fixture.frame)
        generated = graph["resources"]["tex:10"]
        self.assertEqual(
            [(row["event"], row["required_generation"])
             for row in generated["consumer_generations"]],
            [(4, 2), (6, 3)])
        replay = fake_replay()
        replay["_provenance_samples"] = [
            {"event": 1, "texture": "1", "sampler": "tex1", "unit": 1,
             "source": "external_capture", "generation": -1,
             "expected_texture": "1", "selected_texture": "1"},
            {"event": 4, "texture": "10", "sampler": "tex10", "unit": 10,
             "source": "metal_render_target", "generation": 2,
             "expected_texture": "10", "selected_texture": "10"},
            {"event": 6, "texture": "10", "sampler": "tex10", "unit": 10,
             "source": "metal_render_target", "generation": 2,
             "expected_texture": "10", "selected_texture": "10"},
        ]
        value = gfx_frame_graph.evaluate_replay(graph, replay)
        self.assertIn(value["status"], ("invalid", "partial"))
        stale = value["seeded_intermediates"][0]["stale"]
        self.assertEqual(stale[0]["event"], 6)
        self.assertEqual(stale[0]["expected_generation"], 3)

    def test_missing_second_consumer_is_rejected(self):
        fixture = self.fixture
        fixture.events.append(draw(4, 10, [fixture.asset], program=2))
        fixture.events.append(draw(5, None, [fixture.rt], program=3))
        fixture.write()
        manifest = json.loads((fixture.frame / "frame-trace.json").read_text())
        manifest["events"] = len(fixture.events)
        (fixture.frame / "frame-trace.json").write_text(json.dumps(manifest))
        graph = gfx_frame_graph.build(fixture.frame)
        replay = fake_replay()
        replay["_provenance_samples"] = [
            {"event": 1, "texture": "1", "sampler": "tex1", "unit": 1,
             "source": "external_capture", "generation": -1,
             "expected_texture": "1", "selected_texture": "1"},
            {"event": 4, "texture": "10", "sampler": "tex10", "unit": 10,
             "source": "metal_render_target", "generation": 2,
             "expected_texture": "10", "selected_texture": "10"},
        ]
        value = gfx_frame_graph.evaluate_replay(graph, replay)
        self.assertEqual(value["status"], "invalid")
        problems = value["consumer_validation"]["problems"]
        self.assertTrue(any(item["kind"] == "missing_consumer"
                            and item["event"] == 6 for item in problems), problems)

    def test_missing_metal_binding_is_invalid(self):
        replay = fake_replay()
        replay["_provenance_samples"] = replay_provenance()[:1]
        value = gfx_frame_graph.evaluate_replay(self.graph, replay)
        self.assertEqual(value["status"], "invalid")

    def test_partial_capture_stays_partial_even_with_clean_provenance(self):
        graph = dict(self.graph, status="partial", reasons=["missing resolve"])
        value = gfx_frame_graph.evaluate_replay(graph, fake_replay())
        self.assertEqual(value["status"], "partial")

    def test_binding_audit_detects_wrong_engine_unit(self):
        rows = [
            {"event": 14, "texture": "2", "sampler": "tex8", "unit": 3,
             "fault_shader_unit": 3, "expected_texture": "2",
             "selected_texture": "429", "selected_engine_unit": 7,
             "fault_sampler_shift": -1},
        ]
        replay = fake_replay(_provenance_samples=rows)
        audit = gfx_independent_replay.binding_audit(replay)
        self.assertFalse(audit["ok"])
        self.assertEqual(audit["earliest_event"], 14)
        self.assertEqual(audit["mismatches"][0]["kind"], "wrong_texture_selected")

    def test_binding_audit_detects_missing_tex6_unit(self):
        rows = [
            {"event": 14, "texture": "429", "sampler": "tex7", "unit": 1,
             "fault_shader_unit": 1, "expected_texture": "429",
             "selected_texture": "429", "fault_sampler_shift": -1,
             "fault_missing_engine_unit": 6},
        ]
        audit = gfx_independent_replay.binding_audit(
            fake_replay(_provenance_samples=rows))
        self.assertEqual(audit["mismatches"][0]["kind"], "missing_texture_unit")
        self.assertEqual(audit["mismatches"][0]["engine_unit"], 6)


class ExitCodePolicyTests(unittest.TestCase):
    def outcome(self, **overrides):
        value = {"execution": "ok", "coverage": "complete",
                 "pixels": "match", "semantic": "match",
                 "qualified": True, "fault_injection": False}
        value.update(overrides)
        return value

    def test_policy(self):
        policy = gfx_independent_replay.qualification_exit_code
        self.assertEqual(policy({"outcome": self.outcome()}), 0)
        for overrides in (
            {"coverage": "partial"},
            {"pixels": "divergent"},
            {"semantic": "divergent"},
        ):
            self.assertEqual(
                policy({"outcome": self.outcome(qualified=False, **overrides)}),
                2, overrides)
        self.assertEqual(
            policy({"outcome": self.outcome(qualified=False,
                                            fault_injection=True)}), 3)
        self.assertEqual(
            policy({"outcome": self.outcome(execution="failed",
                                            qualified=False)}), 1)
        self.assertEqual(policy({}), 1)


class FirstDivergenceTests(unittest.TestCase):
    def test_earliest_authoritative_checkpoint_wins(self):
        checkpoints = [
            {"event": 11, "target": "431", "resource": "tex:431",
             "status": "match", "authoritative": True},
            {"event": 291, "target": "443", "resource": "tex:443",
             "status": "divergent", "authoritative": True, "statistics": {}},
            {"event": 272, "target": "433", "resource": "tex:433",
             "status": "divergent_diagnostic", "authoritative": False},
        ]
        graph = {"resources": {"tex:443": {"producers": [
            {"event": 289, "kind": "draw", "program": 349}]}}, "events": []}
        value = gfx_independent_replay.first_divergence(
            graph, checkpoints, {"status": "divergent"}, fault={"x": 1})
        self.assertEqual(value["kind"], "intermediate")
        self.assertEqual(value["event"], 291)
        self.assertEqual(value["producer_event"], 289)

    def test_semantic_mismatch_is_the_earliest_divergence(self):
        audit = {
            "ok": False,
            "mismatches": [{
                "event": 14, "kind": "missing_texture_unit",
                "sampler": "tex7", "engine_unit": 6,
            }],
        }
        value = gfx_independent_replay.first_divergence(
            {"resources": {}, "events": []}, [], {"status": "match"},
            binding_audit=audit)
        self.assertEqual(value["kind"], "semantic")
        self.assertEqual(value["event"], 14)
        self.assertEqual(value["semantic"]["sampler"], "tex7")

    def test_final_only_divergence_is_not_attributed_to_an_input(self):
        value = gfx_independent_replay.first_divergence(
            {"resources": {}, "events": []}, [], {"status": "divergent"})
        self.assertEqual(value["kind"], "final_only")


if __name__ == "__main__":
    unittest.main()
