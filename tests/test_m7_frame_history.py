"""M7 PR4 history-aware dependency graph and carried-resource provenance.

These are offline contract tests: they synthesize captured frames plus a
``gfx-frame-sequence.json`` history record and a Metal result document.  No
GPU, EU4 process or second capture is involved.
"""
from __future__ import annotations

import copy
import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from automation import gfx_frame_graph, m6_frame  # noqa: E402

WIDTH = HEIGHT = 4


def sha(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def target(name=None, *, framebuffer=None):
    if name is None:
        return {"framebuffer": 0, "width": WIDTH, "height": HEIGHT,
                "color_attachment": None}
    return {
        "framebuffer": framebuffer or 1, "width": WIDTH, "height": HEIGHT,
        "color_attachment": {
            "type": "texture", "name": name, "level": 0,
            "internal_format": "RGBA8", "width": WIDTH, "height": HEIGHT,
        },
    }


def texture(name, *, width=WIDTH, height=HEIGHT, payload=b"t" * 64):
    return {
        "name": name, "sampler_name": f"tex{name}", "unit": int(name),
        "width": width, "height": height, "internal_format": "RGBA8",
        "levels": [{
            "level": 0, "width": width, "height": height,
            "pixel_format": "RGBA8", "sha256": sha(payload),
            "file": f"tex-{name}.bin", "_payload": payload,
        }],
    }


def draw(index, samples, *, target_value=None, program=2):
    return {
        "kind": "draw", "index": index,
        "draw": {"entry": "_GfxDraw", "mode": 4, "count": 3,
                 "caller_rva": "0x1000"},
        "state": {"viewport": [0, 0, WIDTH, HEIGHT],
                  "blend": {"enabled": False},
                  "depth": {"test": False, "write": False},
                  "cull": {"enabled": False},
                  "color_mask": [True, True, True, True],
                  "scissor": {"enabled": False},
                  "dither": False, "multisample": False},
        "program": {"name": program, "uniforms": [], "shaders": [
            {"stage": "vertex", "sha256": sha(b"vertex"),
             "file": "vertex.metal"},
            {"stage": "pixel", "sha256": sha(b"pixel"),
             "file": "pixel.metal"},
        ]},
        "target": target_value or target(),
        "textures": samples,
        "attributes": [{
            "location": 0, "name": "in_POSITION", "components": 2,
            "gl_type": "0x1406", "stride": 8,
            "data": "verts.bin", "sha256": sha(b"verts!"),
        }],
    }


def clear(index, target_value, color=(1, 0, 0, 1)):
    return {
        "kind": "clear", "flags": 1, "color": list(color),
        "depth": 1, "stencil": 0, "target": target_value,
    }


class FrameFactory:
    def __init__(self, root: Path):
        self.root = root
        self.counter = 0

    def frame(self, events, *, sequence_index, previous=None, carried=(),
              render_targets=(), lifecycle=(), session="session-h",
              write_history=True) -> Path:
        self.counter += 1
        frame = self.root / f"frame-{self.counter}"
        (frame / "blobs").mkdir(parents=True)
        reference = bytes((index * 7) & 0xFF
                          for index in range(WIDTH * HEIGHT * 4))
        (frame / "frame-reference.bgra").write_bytes(reference)
        serializable = copy.deepcopy(events)
        for event in serializable:
            for sampled in event.get("textures") or []:
                for level in sampled.get("levels") or []:
                    level.pop("_payload", None)
        (frame / "blobs" / "verts.bin").write_bytes(b"verts!")
        (frame / "blobs" / "vertex.metal").write_bytes(b"vertex")
        (frame / "blobs" / "pixel.metal").write_bytes(b"pixel")
        (frame / "frame-events.jsonl").write_text(
            "\n".join(json.dumps(event) for event in serializable) + "\n")
        manifest = {
            "schema": "gfx-frame-trace/v1", "complete": True,
            "reason": "scene_present", "events": len(events),
            "frame": {
                "width": WIDTH, "height": HEIGHT, "origin": "lower_left",
                "pixel_format": "BGRA8", "row_stride": WIDTH * 4,
                "reference": "frame-reference.bgra",
                "reference_bytes": len(reference),
                "reference_sha256": sha(reference),
            },
            "seed": {"width": 0, "height": 0},
        }
        (frame / "frame-trace.json").write_text(json.dumps(manifest))
        for event in events:
            for sampled in event.get("textures") or []:
                for level in sampled.get("levels") or []:
                    (frame / "blobs" / level["file"]).write_bytes(
                        level.get("_payload")
                        or b"x" * (level.get("width", WIDTH)
                                   * level.get("height", HEIGHT) * 4))
        if write_history:
            document = {
                "schema": "gfx-frame-sequence/v1",
                "session_id": session, "pid": 1,
                "sequence_index": sequence_index,
                "campaign_frames": 4,
                "capture_frame_id": sequence_index,
                "complete": True, "boundary": "scene",
                "reason": "scene_present",
                "previous": None,
                "render_targets": list(render_targets),
                "carried_renderer_outputs": list(carried),
                "lifecycle": list(lifecycle),
                "reference_sha256": sha(reference),
                "manifest_sha256": sha(
                    (frame / "frame-trace.json").read_bytes()),
                "event_stream_sha256": sha(
                    (frame / "frame-events.jsonl").read_bytes()),
                "operations": len(events), "draws": 0, "clears": 0,
                "copies": 0,
            }
            if previous is not None:
                document["previous"] = {
                    "capture_frame_id": sequence_index - 1,
                    "dir": str(previous),
                    "manifest_sha256": sha(
                        (previous / "frame-trace.json").read_bytes()),
                    "event_stream_sha256": sha(
                        (previous / "frame-events.jsonl").read_bytes()),
                }
            (frame / "frame-sequence.json").write_text(json.dumps(document))
        return frame

    def graph(self, frame: Path) -> dict:
        trace = m6_frame.load_trace(frame, verify_blobs=True)
        trace["_frame_dir"] = str(frame)
        return gfx_frame_graph.build(frame, trace=trace)


def replay_document(*, generation: int, history_generation: int | None = 1,
                    carried: bool = True, source="metal_render_target",
                    history_generation_value: int | None = None,
                    history=None) -> dict:
    sample = {
        "event": 0, "texture": "10", "sampler": "tex10", "unit": 10,
        "source": source, "generation": generation,
        "expected_texture": "10", "selected_texture": "10",
    }
    if carried:
        sample["carried"] = True
        if history_generation is not None:
            sample["history_generation"] = history_generation
    return {
        "status": "ok", "events": 1, "encoded": 1, "failed": 0,
        "command_status": 4, "readback": True,
        "_provenance_samples": [sample],
        "transfers": [],
        "provenance": {
            "bindings": 1, "metal_render_target_bindings": 1,
            "external_capture_bindings": 0,
            "first_sample_before_any_write": [],
        },
        "history": history if history is not None else {
            "status": "ok", "sequence": 2,
            "resources": [{
                "name": "10", "kind": "renderer_generated",
                "generation": (generation if history_generation_value is None
                               else history_generation_value),
                "last_frame": 1,
                "width": WIDTH, "height": HEIGHT, "format": "RGBA8",
            }],
        },
    }


class HistoryAwareGraphTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.factory = FrameFactory(Path(self.temporary.name))

    def tearDown(self):
        self.temporary.cleanup()

    def _carried_pair(self, *, carried=("10",), sample_width=WIDTH,
                      sample_height=HEIGHT, lifecycle=(), write_history=True):
        frame1 = self.factory.frame(
            [clear(0, target("10")), clear(1, target())],
            sequence_index=1, render_targets=("10",))
        frame2 = self.factory.frame(
            [draw(0, [texture("10", width=sample_width,
                              height=sample_height)])],
            sequence_index=2, previous=frame1, carried=carried,
            lifecycle=lifecycle, write_history=write_history)
        return frame1, frame2

    def test_full_extent_carried_texture_is_prior_frame_metal_output(self):
        _, frame2 = self._carried_pair()
        graph = self.factory.graph(frame2)
        self.assertEqual(graph["history"]["status"], "verified")
        resource = graph["resources"]["tex:10"]
        self.assertEqual(resource["origin"], gfx_frame_graph.BOUNDARY)
        self.assertTrue(resource["history"]["verified"])
        self.assertNotIn("tex:10", graph["unproven"])
        self.assertNotIn("tex:10", graph["external_inputs"])
        self.assertIn("tex:10", graph["carried_resources"])

    def test_smaller_carried_texture_is_not_reclassified_as_external(self):
        _, frame2 = self._carried_pair(sample_width=2, sample_height=2)
        graph = self.factory.graph(frame2)
        self.assertEqual(graph["resources"]["tex:10"]["origin"],
                         gfx_frame_graph.BOUNDARY)
        self.assertNotIn("tex:10", graph["external_inputs"])

    def test_absent_carry_record_leaves_the_resource_unproven(self):
        _, frame2 = self._carried_pair(carried=())
        graph = self.factory.graph(frame2)
        self.assertEqual(graph["history"]["status"], "verified")
        self.assertEqual(graph["resources"]["tex:10"]["origin"],
                         gfx_frame_graph.UNPROVEN)
        self.assertIn("tex:10", graph["unproven"])

    def test_invalid_previous_frame_link_fails_closed(self):
        frame1, frame2 = self._carried_pair()
        document = json.loads(
            (frame2 / "frame-sequence.json").read_text())
        document["previous"]["manifest_sha256"] = "0" * 64
        (frame2 / "frame-sequence.json").write_text(json.dumps(document))
        graph = self.factory.graph(frame2)
        self.assertEqual(graph["history"]["status"], "invalid")
        self.assertEqual(graph["resources"]["tex:10"]["origin"],
                         gfx_frame_graph.UNPROVEN)
        self.assertTrue(any("previous-frame manifest" in reason
                            for reason in graph["reasons"]))

    def test_carried_declaration_without_previous_identity_is_invalid(self):
        _, frame2 = self._carried_pair()
        document = json.loads(
            (frame2 / "frame-sequence.json").read_text())
        document["previous"] = None
        (frame2 / "frame-sequence.json").write_text(json.dumps(document))
        graph = self.factory.graph(frame2)
        self.assertEqual(graph["history"]["status"], "invalid")
        self.assertEqual(graph["resources"]["tex:10"]["origin"],
                         gfx_frame_graph.UNPROVEN)

    def test_cpu_mutation_before_the_draw_invalidates_carried_history(self):
        _, frame2 = self._carried_pair(
            lifecycle=({"kind": "mutation", "event": 0,
                        "operation": "glTexSubImage2D", "names": ("10",)},))
        graph = self.factory.graph(frame2)
        resource = graph["resources"]["tex:10"]
        self.assertEqual(resource["origin"], gfx_frame_graph.UNPROVEN)
        self.assertIn("mutation", resource.get("unproven_reason", ""))
        self.assertNotIn("tex:10", graph["carried_resources"])

    def test_cpu_mutation_after_the_draw_still_invalidates_carried_history(self):
        _, frame2 = self._carried_pair(
            lifecycle=({"kind": "mutation", "event": 9,
                        "operation": "glTexImage2D", "names": ("10",)},))
        graph = self.factory.graph(frame2)
        self.assertEqual(graph["resources"]["tex:10"]["origin"],
                         gfx_frame_graph.UNPROVEN)

    def test_subresource_mutation_is_treated_as_unreproduced(self):
        _, frame2 = self._carried_pair(
            lifecycle=({"kind": "mutation", "event": 0, "level": 0,
                        "operation": "glTexSubImage2D", "names": ("10",)},))
        graph = self.factory.graph(frame2)
        self.assertEqual(graph["history"]["mutation_events"], {"10": [0]})
        self.assertEqual(graph["resources"]["tex:10"]["origin"],
                         gfx_frame_graph.UNPROVEN)

    def test_verified_carried_sample_is_a_complete_replay(self):
        _, frame2 = self._carried_pair()
        graph = self.factory.graph(frame2)
        value = gfx_frame_graph.evaluate_replay(
            graph, replay_document(generation=1, history_generation=1))
        self.assertEqual(value["status"], "complete", value["reasons"])
        self.assertEqual(value["consumer_validation"]["problems_count"], 0)

    def test_carried_generation_mismatch_is_invalid(self):
        _, frame2 = self._carried_pair()
        graph = self.factory.graph(frame2)
        value = gfx_frame_graph.evaluate_replay(
            graph, replay_document(generation=2, history_generation=2,
                                   history_generation_value=1))
        self.assertEqual(value["status"], "invalid")
        kinds = [row["kind"] for row in
                 value["consumer_validation"]["problems"]]
        self.assertIn("stale_carried_generation", kinds)

    def test_carried_resource_bound_from_external_capture_is_invalid(self):
        _, frame2 = self._carried_pair()
        graph = self.factory.graph(frame2)
        value = gfx_frame_graph.evaluate_replay(
            graph, replay_document(generation=1, history_generation=1,
                                   carried=False,
                                   source="external_capture"))
        self.assertEqual(value["status"], "invalid")
        kinds = [row["kind"] for row in
                 value["consumer_validation"]["problems"]]
        self.assertIn("carried_resource_external_substitution", kinds)

    def test_newer_unrelated_generation_is_not_accepted(self):
        frame1 = self.factory.frame(
            [clear(0, target("10")), clear(1, target())],
            sequence_index=1, render_targets=("10",))
        frame2 = self.factory.frame(
            [clear(0, target("10")), draw(1, [texture("10")])],
            sequence_index=2, previous=frame1, carried=(),
            render_targets=("10",))
        graph = self.factory.graph(frame2)
        self.assertEqual(graph["resources"]["tex:10"]["origin"],
                         gfx_frame_graph.GENERATED)
        history = {
            "status": "ok", "sequence": 2,
            "resources": [{
                "name": "10", "kind": "renderer_generated",
                "generation": 5, "last_frame": 2,
                "width": WIDTH, "height": HEIGHT, "format": "RGBA8",
            }],
        }

        def document(generation):
            value = replay_document(generation=generation,
                                    history_generation=None, carried=False,
                                    history=history)
            value["events"] = 2
            value["encoded"] = 2
            value["_provenance_samples"] = [{
                "event": 1, "texture": "10", "sampler": "tex10", "unit": 10,
                "source": "metal_render_target", "generation": generation,
                "expected_texture": "10", "selected_texture": "10",
            }]
            return value

        good = gfx_frame_graph.evaluate_replay(graph, document(5))
        self.assertEqual(good["status"], "complete", good["reasons"])
        newer = gfx_frame_graph.evaluate_replay(graph, document(6))
        self.assertEqual(newer["status"], "invalid")
        self.assertIn("stale_generation",
                      [row["kind"] for row in
                       newer["consumer_validation"]["problems"]])


if __name__ == "__main__":
    unittest.main()
