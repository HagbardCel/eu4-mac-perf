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

from automation import (  # noqa: E402
    gfx_frame_graph, gfx_independent_replay, gfx_prefix_check,
)

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

    def test_a_full_clear_records_proven_depth_and_stencil_coverage(self):
        self.fixture.events[0]["state"] = {
            "scissor": {"enabled": False, "box": [0, 0, WIDTH, HEIGHT]},
            "color_mask": [1, 1, 1, 1],
            "depth_mask": True,
            "stencil_mask": -1,
        }
        self.fixture.write()
        graph = gfx_frame_graph.build(self.fixture.frame)
        framebuffer = graph["resources"]["fb:0"]
        self.assertTrue(framebuffer["has_clear"])
        self.assertTrue(framebuffer["has_depth_clear"])
        self.assertTrue(framebuffer["has_stencil_clear"])
        coverage = framebuffer["producers"][0]["coverage"]
        self.assertTrue(coverage["full_extent"])
        self.assertTrue(coverage["depth_full"])
        self.assertTrue(coverage["stencil_full"])

    def test_a_scissored_clear_is_not_full_coverage(self):
        self.fixture.events[0]["state"] = {
            "scissor": {"enabled": True, "box": [0, 0, 2, 2]},
            "color_mask": [1, 1, 1, 1],
            "depth_mask": True,
            "stencil_mask": -1,
        }
        self.fixture.write()
        graph = gfx_frame_graph.build(self.fixture.frame)
        coverage = graph["resources"]["fb:0"]["producers"][0]["coverage"]
        self.assertFalse(coverage["full_extent"])
        self.assertFalse(coverage["color_full"])
        self.assertFalse(coverage["depth_full"])
        self.assertFalse(coverage["stencil_full"])
        self.assertNotIn("has_depth_clear", graph["resources"]["fb:0"])

    def test_a_clear_with_a_disabled_depth_mask_is_not_depth_coverage(self):
        self.fixture.events[0]["state"] = {
            "scissor": {"enabled": False, "box": [0, 0, WIDTH, HEIGHT]},
            "color_mask": [1, 1, 1, 1],
            "depth_mask": False,
            "stencil_mask": -1,
        }
        self.fixture.write()
        graph = gfx_frame_graph.build(self.fixture.frame)
        coverage = graph["resources"]["fb:0"]["producers"][0]["coverage"]
        self.assertTrue(coverage["color_full"])
        self.assertFalse(coverage["depth_full"])
        self.assertFalse(graph["resources"]["fb:0"].get("has_depth_clear", False))

    def test_a_clear_without_recorded_state_is_not_proven(self):
        graph = gfx_frame_graph.build(self.fixture.frame)
        coverage = graph["resources"]["fb:0"]["producers"][0]["coverage"]
        self.assertFalse(coverage["state_recorded"])
        self.assertFalse(coverage["full_extent"])
        self.assertFalse(coverage["depth_full"])
        self.assertFalse(coverage["stencil_full"])

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

    def test_copy_destination_explains_a_previously_unproven_texture(self):
        fixture = self.fixture
        copy = {
            "kind": "resolve", "operation": "glCopyTexSubImage2D",
            "is_blit": False, "source_framebuffer": 0, "read_buffer": 0x0405,
            "source": "0", "source_level": 0, "source_resolved": True,
            "destination": "10", "destination_level": 0,
            "destination_resolved": True,
            "source_rect": [0, 0, WIDTH, HEIGHT],
            "destination_offset": [0, 0], "width": WIDTH, "height": HEIGHT,
            "source_width": WIDTH, "source_height": HEIGHT,
            "source_internal_format": 0,
            "destination_width": WIDTH, "destination_height": HEIGHT,
            "internal_format": 0x8058,
        }
        # Keep only the first draw, then copy the default framebuffer into 10
        # and sample it.  Its only producer is the in-frame copy.
        fixture.events = [
            clear(None, 7),
            draw(1, None, [fixture.asset], program=1),
            copy,
            draw(2, None, [fixture.rt], program=3),
        ]
        fixture.events[1]["seed"] = None
        fixture.write()
        manifest = json.loads((fixture.frame / "frame-trace.json").read_text())
        manifest["events"] = len(fixture.events)
        (fixture.frame / "frame-trace.json").write_text(json.dumps(manifest))
        graph = gfx_frame_graph.build(fixture.frame)
        self.assertEqual(graph["status"], "complete", graph["reasons"])
        self.assertEqual(graph["counts"]["resolves"], 1)
        resource = graph["resources"]["tex:10"]
        self.assertEqual(resource["origin"], "renderer_generated")
        self.assertEqual(resource["producers"][0]["kind"], "resolve")
        self.assertEqual(resource["producers"][0]["rect"],
                         [0, 0, WIDTH, HEIGHT])
        self.assertEqual(graph["unproven"], [])

    def test_copy_source_image_becomes_a_source_checkpoint(self):
        fixture = self.fixture
        copy = {
            "kind": "resolve", "operation": "glCopyTexSubImage2D",
            "is_blit": False, "source_framebuffer": 0, "read_buffer": 0x0405,
            "source": "0", "destination": "10", "source_level": 0,
            "destination_level": 0, "source_resolved": True,
            "destination_resolved": True,
            "source_rect": [0, 0, WIDTH, HEIGHT],
            "destination_offset": [0, 0], "width": WIDTH, "height": HEIGHT,
            "source_width": WIDTH, "source_height": HEIGHT,
            "source_internal_format": 0, "destination_width": WIDTH,
            "destination_height": HEIGHT, "internal_format": 0x8058,
            "source_image": {
                "width": WIDTH, "height": HEIGHT,
                "bytes": WIDTH * HEIGHT * 4,
                "file": "copy-source.bgra",
                "sha256": sha(bytes(WIDTH * HEIGHT * 4)),
            },
        }
        fixture.events = [
            clear(None, 7),
            draw(1, None, [fixture.asset], program=1),
            copy,
            draw(2, None, [fixture.rt], program=3),
        ]
        fixture.events[1]["seed"] = None
        fixture.write_blob("copy-source.bgra",
                           bytes(WIDTH * HEIGHT * 4))
        fixture.write()
        graph = gfx_frame_graph.build(fixture.frame)
        plan = gfx_frame_graph.checkpoint_plan(graph)
        source = [item for item in plan if item["target"] == "0"]
        self.assertEqual(len(source), 1)
        self.assertEqual(source[0]["event"], 2)
        self.assertEqual(source[0]["resource"], "fb:0")
        self.assertEqual(source[0]["reference_file"], "copy-source.bgra")

    def test_unsupported_copy_variant_invalidates_the_capture(self):
        fixture = self.fixture
        fixture.events.append({
            "kind": "resolve", "operation": "glBlitFramebuffer",
            "source_framebuffer": 0, "read_buffer": 0x0405, "source": "0",
            "destination": "10", "source_level": 0, "destination_level": 0,
            "source_resolved": True, "destination_resolved": True,
            "source_rect": [0, 0, 4, 4], "destination_offset": [0, 0],
            "width": 4, "height": 4, "source_width": 4, "source_height": 4,
            "source_internal_format": 0, "destination_width": 4,
            "destination_height": 4, "internal_format": 0x8058,
        })
        fixture.write()
        manifest = json.loads((fixture.frame / "frame-trace.json").read_text())
        manifest["events"] = len(fixture.events)
        (fixture.frame / "frame-trace.json").write_text(json.dumps(manifest))
        graph = gfx_frame_graph.build(fixture.frame)
        self.assertEqual(graph["status"], "invalid")
        self.assertTrue(any("unsupported copy operation" in reason
                            for reason in graph["reasons"]), graph["reasons"])

    def test_malformed_copy_contract_is_invalid(self):
        base = {
            "kind": "resolve", "operation": "glCopyTexSubImage2D",
            "is_blit": False, "source_framebuffer": 0, "read_buffer": 0x0405,
            "source": "0", "destination": "10", "source_level": 0,
            "destination_level": 0, "source_resolved": True,
            "destination_resolved": True,
            "source_rect": [0, 0, WIDTH, HEIGHT],
            "destination_offset": [0, 0], "width": WIDTH, "height": HEIGHT,
            "source_width": WIDTH, "source_height": HEIGHT,
            "source_internal_format": 0, "destination_width": WIDTH,
            "destination_height": HEIGHT, "internal_format": 0x8058,
        }
        cases = {
            "missing source": {"source": None},
            "missing read buffer": {"read_buffer": None},
            "bad level": {"source_level": 1},
            "bad rectangle": {"source_rect": [0, 0, 0, 1]},
            "bad dimensions": {"destination_width": 0},
            "unresolved": {"source_resolved": False},
        }
        for label, override in cases.items():
            with self.subTest(label=label):
                fixture = self.fixture
                fixture.events = [
                    clear(None, 7),
                    draw(1, None, [fixture.asset], program=1),
                    dict(base, **override),
                ]
                fixture.write()
                graph = gfx_frame_graph.build(fixture.frame)
                self.assertEqual(graph["status"], "invalid", (label, graph["reasons"]))
                self.assertTrue(graph["reasons"], label)

    def test_non_default_attachment_must_match_read_buffer(self):
        fixture = self.fixture
        event = {
            "kind": "resolve", "operation": "glCopyTexSubImage2D",
            "is_blit": False, "source_framebuffer": 1, "read_buffer": 0x8CE0,
            "source": "10", "source_attachment": 0x8CE1,
            "destination": "11", "source_level": 0, "destination_level": 0,
            "source_resolved": True, "destination_resolved": True,
            "source_rect": [0, 0, 4, 4], "destination_offset": [0, 0],
            "width": 4, "height": 4, "source_width": 4, "source_height": 4,
            "source_internal_format": 0x8058, "destination_width": 4,
            "destination_height": 4, "internal_format": 0x8058,
        }
        fixture.events.append(event)
        fixture.write()
        graph = gfx_frame_graph.build(fixture.frame)
        self.assertEqual(graph["status"], "invalid")
        self.assertTrue(any("does not match read buffer" in reason
                            for reason in graph["reasons"]), graph["reasons"])

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


class DefaultCheckpointGraphTests(unittest.TestCase):
    """Additive selected-event metadata feeds the existing checkpoint plan."""

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.fixture = FrameFixture(Path(self.temporary.name))
        self.payload = bytes(range(WIDTH * HEIGHT * 4))

    def tearDown(self):
        self.temporary.cleanup()

    def attach(self, event_index=4, payload=None, **overrides):
        payload = self.payload if payload is None else payload
        name = "default-checkpoint-test.bin"
        (self.fixture.frame / "blobs" / name).write_bytes(payload)
        value = {
            "schema": "gfx-default-framebuffer-checkpoint/v1",
            "event": event_index, "target": "0", "resource": "fb:0",
            "width": WIDTH, "height": HEIGHT, "bytes": len(payload),
            "file": name, "sha256": sha(payload),
            "pixel_format": "BGRA8", "origin": "lower_left",
            "timing": "pre_event", "status": "captured",
        }
        value.update(overrides)
        self.fixture.events[event_index]["default_checkpoint"] = value
        self.fixture.write()
        return value

    def attach_many(self, events=(1, 3, 4)):
        """Attach several checkpoints and declare the matching selection."""
        for event_index in events:
            self.attach(event_index)
        manifest = json.loads(
            (self.fixture.frame / "frame-trace.json").read_text())
        manifest["default_checkpoint_events"] = list(events)
        manifest["default_checkpoint_count"] = len(events)
        (self.fixture.frame / "frame-trace.json").write_text(
            json.dumps(manifest))

    def redeclare(self, events):
        manifest = json.loads(
            (self.fixture.frame / "frame-trace.json").read_text())
        manifest["default_checkpoint_events"] = list(events)
        manifest["default_checkpoint_count"] = len(events)
        (self.fixture.frame / "frame-trace.json").write_text(
            json.dumps(manifest))

    def test_captured_checkpoint_joins_the_deterministic_request(self):
        self.attach(4)
        graph = gfx_frame_graph.build(self.fixture.frame)
        self.assertEqual(graph["status"], "complete", graph["reasons"])
        self.assertEqual(len(graph["default_checkpoints"]), 1)
        checkpoint = graph["default_checkpoints"][0]
        self.assertEqual(checkpoint["event"], 4)
        self.assertEqual(checkpoint["resource"], "fb:0")
        plan = gfx_frame_graph.checkpoint_plan(graph)
        selected = [item for item in plan
                    if item["event"] == 4 and item["target"] == "0"]
        self.assertEqual(len(selected), 1)
        self.assertEqual(selected[0]["reference_sha256"], sha(self.payload))
        request = gfx_frame_graph.checkpoint_request(graph)
        item = next(item for item in request["checkpoints"]
                    if item["event"] == 4 and item["target"] == "0")
        self.assertEqual(item["resource"], "fb:0")
        self.assertEqual(item["file"], "intermediate-event4-tex0.bgra")
        self.assertEqual(item["reference_file"],
                         "default-checkpoint-test.bin")
        self.assertEqual(item["reference_sha256"], sha(self.payload))
        self.assertEqual(item["reference_width"], WIDTH)
        self.assertEqual(item["reference_height"], HEIGHT)

    def test_legacy_request_serialization_is_unchanged_without_identity(self):
        self.attach(4)
        graph = gfx_frame_graph.build(self.fixture.frame)
        request = gfx_frame_graph.checkpoint_request(graph)
        item = next(item for item in request["checkpoints"]
                    if item["event"] == 4 and item["target"] == "0")
        # Byte-identical legacy key order plus the additive writer authority;
        # no capture identity keys.
        self.assertEqual(list(item), [
            "event", "target", "resource", "file", "reference_file",
            "reference_sha256", "reference_width", "reference_height",
            "producer_events", "writer_authority"])
        self.assertIn(item["writer_authority"], ("graph", "unknown"))
        self.assertIsInstance(item["producer_events"], list)
        self.assertNotIn("session_id", item)
        self.assertNotIn("capture_frame_id", item)

    def test_request_serialization_adds_capture_identity_when_present(self):
        self.attach(4, session_id="session-a", capture_frame_id=1)
        graph = gfx_frame_graph.build(self.fixture.frame)
        request = gfx_frame_graph.checkpoint_request(graph)
        item = next(item for item in request["checkpoints"]
                    if item["event"] == 4 and item["target"] == "0")
        self.assertEqual(item["session_id"], "session-a")
        self.assertEqual(item["capture_frame_id"], 1)
        self.assertEqual(list(item)[-2:], ["session_id", "capture_frame_id"])

    def test_checkpoint_request_is_stable_and_ordered(self):
        self.attach(4)
        graph = gfx_frame_graph.build(self.fixture.frame)
        first = gfx_frame_graph.checkpoint_request(graph)
        second = gfx_frame_graph.checkpoint_request(graph)
        self.assertEqual(first, second)
        keys = [(item["event"], item["target"])
                for item in first["checkpoints"]]
        self.assertEqual(keys, sorted(keys))

    def test_checkpoint_naming_a_different_event_is_invalid(self):
        self.attach(4, event=3)
        graph = gfx_frame_graph.build(self.fixture.frame)
        self.assertEqual(graph["status"], "invalid")
        self.assertTrue(any("names event" in reason
                            for reason in graph["reasons"]), graph["reasons"])

    def test_checkpoint_for_another_resource_is_invalid(self):
        self.attach(4, target="10", resource="tex:10")
        graph = gfx_frame_graph.build(self.fixture.frame)
        self.assertEqual(graph["status"], "invalid")
        self.assertTrue(any("default framebuffer" in reason
                            for reason in graph["reasons"]), graph["reasons"])

    def test_multiple_selected_checkpoints_are_accepted_in_order(self):
        self.attach_many((1, 3, 4))
        graph = gfx_frame_graph.build(self.fixture.frame)
        self.assertEqual(graph["status"], "complete", graph["reasons"])
        self.assertEqual(
            [item["event"] for item in graph["default_checkpoints"]],
            [1, 3, 4])
        plan = gfx_frame_graph.checkpoint_plan(graph)
        selected = [(item["event"], item["target"]) for item in plan
                    if item["resource"] == "fb:0"]
        self.assertEqual(selected, [(1, "0"), (3, "0"), (4, "0")])
        request = gfx_frame_graph.checkpoint_request(graph)
        self.assertEqual(
            [item["event"] for item in request["checkpoints"]
             if item["resource"] == "fb:0"],
            [1, 3, 4])

    def test_declared_selection_must_match_attached_checkpoints(self):
        self.attach_many((1, 3, 4))
        self.redeclare((1, 3))
        graph = gfx_frame_graph.build(self.fixture.frame)
        self.assertEqual(graph["status"], "invalid")
        self.assertTrue(any("declares default checkpoints" in reason
                            for reason in graph["reasons"]), graph["reasons"])

    def test_declared_count_must_match_the_declared_selection(self):
        self.attach_many((1, 3, 4))
        manifest = json.loads(
            (self.fixture.frame / "frame-trace.json").read_text())
        manifest["default_checkpoint_count"] = 2
        (self.fixture.frame / "frame-trace.json").write_text(
            json.dumps(manifest))
        graph = gfx_frame_graph.build(self.fixture.frame)
        self.assertEqual(graph["status"], "invalid")
        self.assertTrue(any("but lists" in reason
                            for reason in graph["reasons"]), graph["reasons"])

    def test_checkpoints_from_two_sessions_are_invalid(self):
        self.attach(1, session_id="session-a", capture_frame_id=1)
        self.attach(3, session_id="session-b", capture_frame_id=1)
        self.redeclare((1, 3))
        graph = gfx_frame_graph.build(self.fixture.frame)
        self.assertEqual(graph["status"], "invalid")
        self.assertTrue(any("more than one capture session" in reason
                            for reason in graph["reasons"]), graph["reasons"])

    def test_checkpoints_from_two_capture_frames_are_invalid(self):
        self.attach(1, session_id="session-a", capture_frame_id=1)
        self.attach(3, session_id="session-a", capture_frame_id=2)
        self.redeclare((1, 3))
        graph = gfx_frame_graph.build(self.fixture.frame)
        self.assertEqual(graph["status"], "invalid")
        self.assertTrue(any("more than one capture frame" in reason
                            for reason in graph["reasons"]), graph["reasons"])

    def test_malformed_capture_identity_is_invalid(self):
        self.attach(1, session_id="")
        graph = gfx_frame_graph.build(self.fixture.frame)
        self.assertEqual(graph["status"], "invalid")
        self.assertTrue(any("session_id" in reason
                            for reason in graph["reasons"]), graph["reasons"])

    def test_missing_checkpoint_blob_is_invalid(self):
        value = self.attach(4)
        (self.fixture.frame / "blobs" / value["file"]).unlink()
        graph = gfx_frame_graph.build(self.fixture.frame)
        self.assertEqual(graph["status"], "invalid")
        self.assertTrue(any("missing blob" in reason or "blob is absent" in reason
                            for reason in graph["reasons"]), graph["reasons"])

    def test_checkpoint_blob_digest_mismatch_is_invalid(self):
        self.attach(4, sha256="0" * 64)
        graph = gfx_frame_graph.build(self.fixture.frame)
        self.assertEqual(graph["status"], "invalid")
        self.assertTrue(any("digest mismatch" in reason
                            for reason in graph["reasons"]), graph["reasons"])

    def test_checkpoint_byte_count_mismatch_is_invalid(self):
        self.attach(4, bytes=1)
        graph = gfx_frame_graph.build(self.fixture.frame)
        self.assertEqual(graph["status"], "invalid")
        self.assertTrue(any("byte count" in reason
                            for reason in graph["reasons"]), graph["reasons"])


class ResourceNamespaceTests(unittest.TestCase):
    def test_texture_and_renderbuffer_names_do_not_alias(self):
        events = [
            {"kind": "clear", "target": {
                "framebuffer": 6,
                "color_attachment": {"type": "texture", "name": 5,
                                     "level": 0, "internal_format": "RGBA8"}}},
            {"kind": "clear", "target": {
                "framebuffer": 7,
                "color_attachment": {"type": "renderbuffer", "name": 5}}},
        ]
        resources = {
            "tex:5": {"producers": [{"event": 0}], "consumers": [],
                      "origin": "renderer_generated"},
            "fb:7": {"producers": [{"event": 1}], "consumers": [],
                     "origin": "renderer_generated"},
        }
        trace = {"_prefix": {
            "mode": "selected", "status": "complete", "end": None,
            "events": [0, 1], "initial": None,
            "post_events": [
                {"event": 0, "timing": "post_event", "kind": "clear",
                 "resource": "tex:5", "target_name": "5",
                 "pixel_format": "RGBA8", "alpha_applicable": True,
                 "target_identity": {"internal_format": 0x8058,
                                     "alpha_bits": 8},
                 "color": {"status": "captured", "file": "a.bgra",
                           "sha256": "0" * 64, "bytes": 64,
                           "pixel_format": "RGBA8"}},
                {"event": 1, "timing": "post_event", "kind": "clear",
                 "resource": "fb:7", "target_name": "7",
                 "color": {"status": "unsupported_format"}},
            ],
        }}
        coverage = gfx_frame_graph.prefix_coverage(trace, events, resources)
        self.assertEqual(coverage["status"], "selected", coverage["problems"])
        self.assertEqual(coverage["excluded_events"], [1])
        self.assertNotIn("fb:5", resources)
        self.assertNotIn("tex:7", resources)
        # A forged texture identity for the renderbuffer event is rejected.
        forged = json.loads(json.dumps(trace))
        forged["_prefix"]["post_events"][1]["resource"] = "tex:5"
        forged["_prefix"]["post_events"][1]["target_name"] = "5"
        broken = gfx_frame_graph.prefix_coverage(forged, events, resources)
        self.assertEqual(broken["status"], "invalid")
        self.assertTrue(any("fb:7" in problem
                            for problem in broken["problems"]),
                        broken["problems"])


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

    def test_event_zero_consumer_index_is_preserved(self):
        graph = {
            "status": "complete",
            "resources": {
                "tex:10": {
                    "origin": "external",
                    "consumers": [{"event": 0, "sampler": "tex10"}],
                    "consumer_generations": [
                        {"event": 0, "required_generation": 0}
                    ],
                }
            },
            "renderer_generated_sampled": [],
            "boundary": {},
        }
        replay = fake_replay()
        replay["_provenance_samples"] = [{
            "event": 0, "texture": "10", "sampler": "tex10", "unit": 10,
            "source": "external_capture", "generation": -1,
            "expected_texture": "10", "selected_texture": "10",
        }]
        value = gfx_frame_graph.evaluate_replay(graph, replay)
        self.assertEqual(value["status"], "complete", value["reasons"])
        self.assertEqual(
            value["consumer_validation"]["problems"], [])

    def test_duplicate_provenance_records_are_rejected(self):
        replay = fake_replay()
        rows = replay_provenance()
        rows.append(dict(rows[1]))  # same (event=4, sampler=tex10)
        replay["_provenance_samples"] = rows
        value = gfx_frame_graph.evaluate_replay(self.graph, replay)
        self.assertEqual(value["status"], "invalid")
        kinds = [item["kind"]
                 for item in value["consumer_validation"]["problems"]]
        self.assertIn("duplicate_consumer", kinds)

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

    def test_binding_audit_reports_missing_engine_unit_zero(self):
        rows = [
            {"event": 14, "texture": "429", "sampler": "tex7", "unit": 0,
             "fault_shader_unit": 0, "expected_texture": "429",
             "selected_texture": "", "fault_sampler_shift": 1,
             "fault_missing_engine_unit": 0},
        ]
        audit = gfx_independent_replay.binding_audit(
            fake_replay(_provenance_samples=rows))
        missing = [item for item in audit["mismatches"]
                   if item["kind"] == "missing_texture_unit"]
        self.assertTrue(missing)
        self.assertEqual(missing[0]["engine_unit"], 0)

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


def copy_provenance_graph(*, source="tex:A", destination="tex:B",
                          include_source_sampler=False,
                          source_kind="draw", source_origin="renderer_generated",
                          source_event=1, copy_event=2):
    source_consumers = [{
        "event": copy_event, "unit": None, "sampler": "resolve",
        "operation": "glCopyTexSubImage2D", "destination": destination,
        "source_framebuffer": 0 if source == "fb:0" else 0,
        "source_attachment": 0,
    }]
    if include_source_sampler:
        source_consumers.append({"event": 3, "unit": 1, "sampler": "tex1"})
    resources = {
        source: {
            "origin": source_origin,
            "consumers": source_consumers,
            "consumer_generations": [
                {"event": row["event"],
                 "required_generation": 1 if source != "fb:0" else 0}
                for row in source_consumers
            ],
            "producers": [{"event": source_event, "kind": source_kind}],
        },
        destination: {
            "origin": "renderer_generated",
            "consumers": [{"event": 3, "unit": 0, "sampler": "tex0"}],
            "consumer_generations": [{"event": 3, "required_generation": 1}],
            "producers": [{"event": copy_event, "kind": "resolve"}],
        },
    }
    sampled = [destination]
    if include_source_sampler:
        sampled.append(source)
    return {
        "status": "complete", "resources": resources,
        "renderer_generated_sampled": sampled,
        "events": [], "boundary": {},
    }


def copy_provenance_replay(*, graph, source="A", destination="B",
                           source_generation=1, transfer=True,
                           seed_source=False):
    samples = [{
        "event": 3, "texture": destination.split(":")[-1],
        "sampler": "tex0", "unit": 0, "source": "metal_render_target",
        "generation": 1, "expected_texture": destination.split(":")[-1],
        "selected_texture": destination.split(":")[-1],
    }]
    if seed_source:
        samples.append({
            "event": 1, "texture": source, "sampler": "tex0", "unit": 0,
            "source": "external_capture", "generation": -1,
            "expected_texture": source, "selected_texture": source,
        })
    transfers = []
    if transfer:
        transfers.append({
            "event": 2, "kind": "resolve",
            "operation": "glCopyTexSubImage2D",
            "source": "0" if source == "fb:0" else source,
            "destination": destination.split(":")[-1],
            "source_generation": source_generation,
            "source_kind": "metal_default" if source == "fb:0"
            else "metal_render_target",
            "destination_generation_before": 0,
            "destination_generation_after": 1,
            "status": "ok",
        })
    return {
        "status": "ok", "events": 4, "encoded": 4, "failed": 0,
        "command_status": 4, "readback": True,
        "provenance": {
            "bindings": len(samples), "metal_render_target_bindings": 1,
            "external_capture_bindings": 1 if seed_source else 0,
            "render_target_textures": [destination.split(":")[-1]],
            "external_textures": [source] if seed_source else [],
            "first_sample_before_any_write": [],
        },
        "_provenance_samples": samples,
        "transfers": transfers,
    }


class CopyProvenanceTests(unittest.TestCase):
    def test_copy_only_source_does_not_require_sampler_binding(self):
        graph = copy_provenance_graph()
        value = gfx_frame_graph.evaluate_replay(
            graph, copy_provenance_replay(graph=graph))
        self.assertEqual(value["status"], "complete", value["reasons"])
        self.assertEqual(value["transfer_validation"]["problems_count"], 0)

    def test_source_and_destination_both_sampled(self):
        graph = copy_provenance_graph(include_source_sampler=True)
        replay = copy_provenance_replay(graph=graph)
        replay["_provenance_samples"].append({
            "event": 3, "texture": "A", "sampler": "tex1", "unit": 1,
            "source": "metal_render_target", "generation": 1,
            "expected_texture": "A", "selected_texture": "A",
        })
        value = gfx_frame_graph.evaluate_replay(graph, replay)
        self.assertEqual(value["status"], "complete", value["reasons"])

    def test_stale_source_generation_is_invalid(self):
        graph = copy_provenance_graph()
        value = gfx_frame_graph.evaluate_replay(
            graph, copy_provenance_replay(graph=graph, source_generation=0))
        self.assertEqual(value["status"], "invalid")
        self.assertIn("stale_source_generation",
                      [row["kind"] for row in
                       value["transfer_validation"]["problems"]])

    def test_wrong_source_resource_is_invalid(self):
        graph = copy_provenance_graph()
        value = gfx_frame_graph.evaluate_replay(
            graph, copy_provenance_replay(graph=graph, source="C"))
        self.assertEqual(value["status"], "invalid")
        self.assertIn("wrong_source",
                      [row["kind"] for row in
                       value["transfer_validation"]["problems"]])

    def test_missing_transfer_is_invalid(self):
        graph = copy_provenance_graph()
        value = gfx_frame_graph.evaluate_replay(
            graph, copy_provenance_replay(graph=graph, transfer=False))
        self.assertEqual(value["status"], "invalid")
        self.assertIn("missing_transfer",
                      [row["kind"] for row in
                       value["transfer_validation"]["problems"]])

    def test_default_framebuffer_copy_is_valid(self):
        graph = copy_provenance_graph(source="fb:0", source_origin="renderer_generated")
        graph["resources"]["fb:0"]["consumer_generations"] = [
            {"event": 2, "required_generation": 0}]
        value = gfx_frame_graph.evaluate_replay(
            graph, copy_provenance_replay(graph=graph, source="fb:0",
                                          source_generation=-1))
        self.assertEqual(value["status"], "complete", value["reasons"])

    def test_default_framebuffer_source_kind_is_required(self):
        graph = copy_provenance_graph(source="fb:0", source_origin="renderer_generated")
        graph["resources"]["fb:0"]["consumer_generations"] = [
            {"event": 2, "required_generation": 0}]
        replay = copy_provenance_replay(
            graph=graph, source="fb:0", source_generation=-1)
        replay["transfers"][0]["source_kind"] = "external_capture"
        value = gfx_frame_graph.evaluate_replay(graph, replay)
        self.assertEqual(value["status"], "invalid")
        self.assertIn("source_category",
                      [row["kind"] for row in
                       value["transfer_validation"]["problems"]])

    def test_default_framebuffer_wrong_source_category_is_invalid(self):
        graph = copy_provenance_graph(source="fb:0", source_origin="renderer_generated")
        graph["resources"]["fb:0"]["consumer_generations"] = [
            {"event": 2, "required_generation": 0}]
        replay = copy_provenance_replay(
            graph=graph, source="fb:0", source_generation=-1)
        replay["transfers"][0]["source_kind"] = "metal_render_target"
        value = gfx_frame_graph.evaluate_replay(graph, replay)
        self.assertEqual(value["status"], "invalid")
        self.assertIn("source_category",
                      [row["kind"] for row in
                       value["transfer_validation"]["problems"]])

    def test_invalid_transfer_is_not_downgraded_by_seeded_resource(self):
        graph = copy_provenance_graph()
        replay = copy_provenance_replay(graph=graph, transfer=False)
        replay["_provenance_samples"].append({
            "event": 1, "texture": "A", "sampler": "tex0", "unit": 0,
            "source": "external_capture", "generation": -1,
            "expected_texture": "A", "selected_texture": "A",
        })
        value = gfx_frame_graph.evaluate_replay(graph, replay)
        self.assertEqual(value["status"], "invalid", value["reasons"])
        self.assertIn("missing_transfer",
                      [row["kind"] for row in
                       value["transfer_validation"]["problems"]])

    def test_captured_source_substitution_is_invalid(self):
        graph = copy_provenance_graph()
        replay = copy_provenance_replay(graph=graph)
        replay["transfers"][0]["source_kind"] = "external_capture"
        value = gfx_frame_graph.evaluate_replay(graph, replay)
        self.assertEqual(value["status"], "invalid")


class ExitCodePolicyTests(unittest.TestCase):
    def outcome(self, **overrides):
        value = {"execution": "ok", "coverage": "complete",
                 "pixels": "match", "semantic": "match",
                 "state_coverage": "complete",
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


class ProductionBuildIdentityTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.fixture = FrameFixture(Path(self.temporary.name))

    def tearDown(self):
        self.temporary.cleanup()

    def add_checkpoint(self):
        self.fixture.events[3]["default_checkpoint"] = {
            "schema": "gfx-default-framebuffer-checkpoint/v1",
            "status": "captured",
            "event": 3,
            "target": "0",
            "resource": "fb:0",
            "pixel_format": "BGRA8",
            "origin": "lower_left",
            "timing": "pre_event",
            "file": "cp.bin",
            "sha256": sha(self.fixture.reference),
            "bytes": len(self.fixture.reference),
            "width": WIDTH,
            "height": HEIGHT,
            "session_id": "session-checkpoint",
            "capture_frame_id": 2,
        }
        self.fixture.write()
        self.fixture.write_blob("cp.bin", self.fixture.reference)

    def write_seal(self):
        events = self.fixture.frame / "frame-events.jsonl"
        reference = self.fixture.frame / "frame-reference.bgra"
        manifest = self.fixture.frame / "frame-trace.json"
        seal = {
            "schema": "gfx-frame-seal/v1", "sealed": True, "complete": True,
            "struct_size": 2256, "abi_version": 2,
            "capture_frame_id": 3, "session_id": "session-seal", "pid": 123,
            "boundary": "scene", "present_index": 904,
            "operation_count": len(self.fixture.events), "draw_count": 0,
            "clear_count": 0, "copy_count": 0,
            "event_stream_file": events.name,
            "event_stream_bytes": events.stat().st_size,
            "event_stream_sha256": sha(events.read_bytes()),
            "event_stream_verified": True,
            "width": WIDTH, "height": HEIGHT, "pixel_format": "BGRA8",
            "trace_complete": True, "trace_integrity": True,
            "readback_ok": True,
            "reference_file": reference.name,
            "reference_bytes": reference.stat().st_size,
            "reference_sha256": sha(reference.read_bytes()),
            "manifest_file": manifest.name,
            "manifest_sha256": sha(manifest.read_bytes()),
            "capture_dir": str(self.fixture.frame.resolve()),
            "gpu_status": "ok",
        }
        (self.fixture.frame / "frame-seal.json").write_text(json.dumps(seal))

    def test_build_projects_checkpoint_capture_identity(self):
        self.add_checkpoint()
        graph = gfx_frame_graph.build(self.fixture.frame)
        self.assertEqual(graph["capture"]["session_id"],
                         "session-checkpoint")
        self.assertEqual(graph["capture"]["capture_frame_id"], 2)
        self.assertEqual(graph["capture"]["source"], "checkpoints")
        rows = gfx_frame_graph.provenance_matrix(graph, [{
            "event": 4, "target": "0", "resource": "fb:0"}])
        self.assertEqual(rows[0]["frame"]["session_id"],
                         "session-checkpoint")
        self.assertEqual(rows[0]["frame"]["capture_frame_id"], 2)

    def test_build_projects_seal_stream_identity(self):
        self.write_seal()
        graph = gfx_frame_graph.build(self.fixture.frame)
        self.assertEqual(graph["capture"]["source"], "seal")
        self.assertEqual(graph["capture"]["session_id"], "session-seal")
        self.assertEqual(graph["capture"]["capture_frame_id"], 3)
        self.assertEqual(graph["capture"]["present_index"], 904)
        events = self.fixture.frame / "frame-events.jsonl"
        self.assertEqual(graph["capture"]["event_stream_sha256"],
                         sha(events.read_bytes()))
        rows = gfx_frame_graph.provenance_matrix(graph, [{
            "event": 4, "target": "0", "resource": "fb:0"}])
        self.assertEqual(rows[0]["frame"]["event_stream_sha256"],
                         sha(events.read_bytes()))

    def test_present_but_invalid_seal_is_not_treated_as_absent(self):
        self.write_seal()
        seal_path = self.fixture.frame / "frame-seal.json"
        seal = json.loads(seal_path.read_text())
        seal["manifest_sha256"] = "0" * 64
        seal_path.write_text(json.dumps(seal))
        graph = gfx_frame_graph.build(self.fixture.frame)
        self.assertEqual(graph["status"], "invalid")
        self.assertEqual(graph["capture"]["source"], "invalid_seal")
        self.assertTrue(any("seal is present but invalid" in reason
                            for reason in graph["reasons"]))

    def test_legacy_nonempty_producer_history_is_validated(self):
        graph = {
            "events": [
                {"event": 0, "kind": "clear"},
                {"event": 2, "kind": "draw", "entry": "_GfxDrawIndexed"},
            ],
            "resources": {"fb:0": {"producers": [
                {"event": 0, "kind": "clear"}]}},
            "default_checkpoints": [{
                "event": 2, "target": "0", "resource": "fb:0",
                "reference_file": "ref.bin", "reference_sha256": "c" * 64,
                "reference_width": 8, "reference_height": 8,
            }],
        }
        expected = gfx_frame_graph.checkpoint_request(graph)["checkpoints"]
        item = dict(expected[0])
        item.pop("writer_authority")
        item["producer_events"] = [99]
        problems = (
            gfx_independent_replay._checkpoint_request_plan_problems(
                graph, [item]))
        self.assertTrue(any(problem["kind"] in
                            ("substituted_request", "malformed_request")
                            for problem in problems))
        # A legitimately empty legacy list remains accepted for fb:0.
        item["producer_events"] = []
        problems = (
            gfx_independent_replay._checkpoint_request_plan_problems(
                graph, [item]))
        self.assertEqual(problems, [])
        # Sampled textures always carried producer history; erasing it must
        # not hide a writer.
        texture_fixture = FrameFixture(self.fixture.root / "texture-graph")
        texture_graph = gfx_frame_graph.build(texture_fixture.frame)
        expected_texture = gfx_frame_graph.checkpoint_request(
            texture_graph)["checkpoints"]
        texture_item = next(
            (dict(item) for item in expected_texture
             if item["resource"].startswith("tex:")), None)
        self.assertIsNotNone(texture_item)
        texture_item.pop("writer_authority")
        texture_item["producer_events"] = []
        problems = (
            gfx_independent_replay._checkpoint_request_plan_problems(
                texture_graph, [texture_item]))
        self.assertTrue(any("texture" in problem["detail"]
                            for problem in problems))


class ProvenanceMatrixTests(unittest.TestCase):
    def graph(self):
        return {
            "capture": {
                "session_id": "session-a", "capture_frame_id": 1,
                "event_stream_sha256": "a" * 64,
                "reference_sha256": "b" * 64,
                "source": "seal",
            },
            "events": [
                {"event": 0, "kind": "clear", "operation": "glClear"},
                {"event": 1, "kind": "draw", "operation": "_GfxDraw",
                 "entry": "_GfxDraw", "program": "300",
                 "caller_rva": "0x100"},
                {"event": 2, "kind": "draw", "operation": "_GfxDrawIndexed",
                 "entry": "_GfxDrawIndexed", "program": "301",
                 "caller_rva": "0x10d4e50"},
            ],
            "resources": {
                "fb:0": {
                    "producers": [
                        {"event": 0, "kind": "clear", "writes": ["color"]},
                        {"event": 1, "kind": "draw", "entry": "_GfxDraw",
                         "program": "300", "caller_rva": "0x100"},
                    ],
                    "consumer_generations": [
                        {"event": 2, "required_generation": 2}],
                },
            },
        }

    def test_writers_come_from_the_graph_not_from_event_minus_one(self):
        rows = gfx_frame_graph.provenance_matrix(self.graph(), [{
            "event": 2, "target": "0", "resource": "fb:0",
            "status": "divergent", "authoritative": True,
            "statistics": {"mismatched_pixels": 607, "max_channel_error": 2},
        }])
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row["observation"]["event"], 2)
        self.assertEqual(row["observation"]["phase"], "pre_event")
        self.assertEqual(row["observation"]["target"], "0")
        self.assertEqual(row["writer_events"], [0, 1])
        self.assertEqual(row["producer_event"], 1)
        self.assertEqual(row["writer_authority"], "graph")
        self.assertEqual([writer["entry"] for writer in row["writers"]],
                         [None, "_GfxDraw"])
        # The operation at the observation event is reported separately and
        # never presented as the writer of the observed pre-event content.
        self.assertEqual(row["upcoming_event"]["entry"], "_GfxDrawIndexed")
        self.assertIn("not", row["upcoming_event"]["note"])
        self.assertEqual(row["generation"]["required"], 2)
        self.assertEqual(row["frame"]["session_id"], "session-a")
        self.assertEqual(row["frame"]["capture_frame_id"], 1)
        self.assertEqual(row["statistics"]["mismatched_pixels"], 607)

    def test_no_same_resource_writer_is_explicit_unknown(self):
        graph = self.graph()
        graph["resources"]["fb:0"]["producers"] = []
        rows = gfx_frame_graph.provenance_matrix(graph, [{
            "event": 2, "target": "0", "resource": "fb:0",
        }])
        self.assertEqual(rows[0]["writer_events"], [])
        self.assertIsNone(rows[0]["producer_event"])
        self.assertEqual(rows[0]["writer_authority"], "unknown")

    def test_default_checkpoint_request_uses_graph_producer_history(self):
        graph = self.graph()
        graph["default_checkpoints"] = [{
            "event": 2, "target": "0", "reference_file": "ref.bin",
            "reference_sha256": "c" * 64, "reference_width": 8,
            "reference_height": 8,
        }]
        request = gfx_frame_graph.checkpoint_request(graph)
        item = next(item for item in request["checkpoints"]
                    if item["event"] == 2 and item["target"] == "0")
        self.assertEqual(item["producer_events"], [0, 1])
        self.assertEqual(item["writer_authority"], "graph")


if __name__ == "__main__":
    unittest.main()


class AttachmentNamespaceTests(unittest.TestCase):
    def test_texture_and_renderbuffer_names_do_not_alias(self):
        texture_target = {
            "framebuffer": 9,
            "color_attachment": {"type": "texture", "name": 7},
        }
        renderbuffer_target = {
            "framebuffer": 9,
            "color_attachment": {"type": "renderbuffer", "name": 7},
        }
        tex_key, tex_attachment = gfx_frame_graph._target_resource(
            texture_target)
        rb_key, rb_attachment = gfx_frame_graph._target_resource(
            renderbuffer_target)
        self.assertEqual(tex_key, "tex:7")
        self.assertEqual(tex_attachment["name"], 7)
        self.assertEqual(rb_key, "fb:9")
        self.assertEqual(rb_attachment, {})


class ClearCoverageEndpointTests(unittest.TestCase):
    """A scissored clear covers the target only when its endpoints do."""

    def _coverage(self, scissor, width=8, height=8):
        event = {
            "kind": "clear",
            "flags": 7,
            "color": [0, 0, 0, 1],
            "depth": 1,
            "stencil": 0,
            "state": {
                "scissor": scissor,
                "color_mask": [1, 1, 1, 1],
                "depth_mask": True,
                "stencil_mask": -1,
            },
        }
        return gfx_frame_graph._clear_coverage(
            event, ["color", "depth", "stencil"], width, height)

    def test_a_negative_origin_that_shortens_the_far_edge_is_partial(self):
        # Width equals the target, but x+w == 6 of 8 columns.
        coverage = self._coverage({"enabled": True, "box": [-2, 0, 8, 8]})
        self.assertFalse(coverage["full_extent"])
        self.assertFalse(coverage["color_full"])
        self.assertFalse(coverage["depth_full"])
        self.assertFalse(coverage["stencil_full"])

    def test_a_negative_origin_that_still_reaches_the_far_edge_covers(self):
        coverage = self._coverage({"enabled": True, "box": [-2, -2, 10, 10]})
        self.assertTrue(coverage["full_extent"])
        self.assertTrue(coverage["color_full"])
        self.assertTrue(coverage["depth_full"])
        self.assertTrue(coverage["stencil_full"])

    def test_a_positive_offset_is_partial(self):
        coverage = self._coverage({"enabled": True, "box": [1, 0, 8, 8]})
        self.assertFalse(coverage["full_extent"])

    def test_a_width_that_stops_short_is_partial(self):
        coverage = self._coverage({"enabled": True, "box": [0, 0, 7, 8]})
        self.assertFalse(coverage["full_extent"])

    def test_exact_endpoint_coverage_is_full(self):
        coverage = self._coverage({"enabled": True, "box": [0, 0, 8, 8]})
        self.assertTrue(coverage["full_extent"])
        self.assertTrue(coverage["color_full"])

    def test_an_unscissored_clear_is_full(self):
        coverage = self._coverage({"enabled": False, "box": [0, 0, 1, 1]})
        self.assertTrue(coverage["full_extent"])
        self.assertTrue(coverage["color_full"])

    def test_non_integer_boxes_are_not_full_coverage(self):
        for box in (["0", "0", "8", "8"], [0.0, 0.0, 8, 8],
                    [0, 0, 8.0, 8]):
            with self.subTest(box=box):
                coverage = self._coverage({"enabled": True, "box": box})
                self.assertFalse(coverage["full_extent"])

class FormatScopeTests(unittest.TestCase):
    def test_legacy_default_drawable_keeps_four_channel_scope(self):
        scope = gfx_frame_graph.format_scope({
            "pixel_format": "BGRA8",
            "target_identity": {"internal_format": 0, "component_type": 0,
                                "red_bits": 0, "green_bits": 0,
                                "blue_bits": 0, "alpha_bits": 0},
        })
        self.assertIs(scope["alpha_applicable"], True)
        self.assertEqual(scope["compared_channels"], "RGBA")
        self.assertEqual(scope["problems"], [])

    def test_default_drawable_without_alpha_is_rgb_scope(self):
        scope = gfx_frame_graph.format_scope({
            "pixel_format": "BGRA8",
            "alpha_applicable": False,
            "target_identity": {"internal_format": 0,
                                "red_bits": 8, "green_bits": 8,
                                "blue_bits": 8, "alpha_bits": 0},
        })
        self.assertIs(scope["alpha_applicable"], False)
        self.assertEqual(scope["compared_channels"], "RGB")
        self.assertEqual(scope["problems"], [])

    def test_default_framebuffer_alpha_is_applicable(self):
        scope = gfx_frame_graph.format_scope({
            "pixel_format": "BGRA8",
            "target_identity": {"internal_format": 0, "alpha_bits": 8},
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
                                    "alpha_bits": 8},
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
                "target_identity": {"internal_format": 0, "alpha_bits": 8},
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
                "target_identity": {"internal_format": 0, "alpha_bits": 8},
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
                 "target_identity": {"internal_format": 0, "alpha_bits": 8},
                 "color": {"status": "captured", "file": "x.bin",
                           "sha256": "0" * 64, "bytes": 64,
                           "pixel_format": "BGRA8"}},
                {"event": 1, "timing": "post_event", "kind": "draw",
                 "resource": "fb:0", "target_name": "0",
                 "pixel_format": "BGRA8", "alpha_applicable": True,
                 "target_identity": {"internal_format": 0, "alpha_bits": 8},
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
        self.assertEqual(result["verdict"], "INCOMPLETE_EVIDENCE")
        self.assertIsNone(result["first_divergence"])
        self.assertEqual(result["unverified_event"], 0)

    def test_invalid_event_before_divergence_is_not_first_divergence(self):
        rows = [{"event": event, "status": "match"} for event in range(6)]
        rows[3] = {"event": 3, "status": "invalid"}
        rows[5] = {"event": 5, "status": "divergent"}
        result = gfx_prefix_check.classify_prefix(
            rows, problems=[], end=20, require_end=20)
        self.assertEqual(result["verdict"], "INCOMPLETE_EVIDENCE")
        self.assertIsNone(result["first_divergence"])

    def test_problem_invalidates_first_divergence(self):
        rows = [{"event": event, "status": "match"} for event in range(6)]
        rows[1] = {"event": 1, "status": "divergent"}
        result = gfx_prefix_check.classify_prefix(
            rows, problems=["foreign receipt"], end=20, require_end=20)
        self.assertEqual(result["verdict"], "INCOMPLETE_EVIDENCE")
        self.assertIsNone(result["first_divergence"])


class ExcludedAuxiliaryTests(unittest.TestCase):
    def test_isolated_unsupported_aux_clear_is_not_event20_exact(self):
        rows = [{"event": event, "status": "match"} for event in range(21)]
        rows[11] = {"event": 11, "status": "unsupported_auxiliary"}
        result = gfx_prefix_check.classify_prefix(
            rows, problems=[], end=20, require_end=20)
        self.assertEqual(result["verdict"],
                         "INCOMPLETE_EVIDENCE")
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
                 "pixel_format": "BGRA8", "alpha_applicable": True,
                 "target_identity": {"internal_format": 0, "alpha_bits": 8,
                                     "red_bits": 8, "green_bits": 8,
                                     "blue_bits": 8},
                 "color": {"status": "captured", "file": "x.bin",
                           "sha256": "0" * 64, "bytes": 64,
                           "pixel_format": "BGRA8"}},
                {"event": 2, "timing": "post_event", "kind": "draw",
                 "resource": "fb:0", "target_name": "0",
                 "pixel_format": "BGRA8", "alpha_applicable": True,
                 "target_identity": {"internal_format": 0, "alpha_bits": 8,
                                     "red_bits": 8, "green_bits": 8,
                                     "blue_bits": 8},
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
                 "pixel_format": "BGRA8", "alpha_applicable": True,
                 "target_identity": {"internal_format": 0, "alpha_bits": 8,
                                     "red_bits": 8, "green_bits": 8,
                                     "blue_bits": 8},
                 "color": {"status": "captured", "file": "x.bin",
                           "sha256": "0" * 64, "bytes": 64,
                           "pixel_format": "BGRA8"}},
                {"event": 2, "timing": "post_event", "kind": "draw",
                 "resource": "fb:0", "target_name": "0",
                 "pixel_format": "BGRA8", "alpha_applicable": True,
                 "target_identity": {"internal_format": 0, "alpha_bits": 8,
                                     "red_bits": 8, "green_bits": 8,
                                     "blue_bits": 8},
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
