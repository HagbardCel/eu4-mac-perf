"""PR 4 historical regression cases as semantic/resource tests.

Each case is a small synthetic operation or lineage, so the regression does not
require another EU4 launch.  The cases mirror the defects identified during M6.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from automation import gfx_semantic  # noqa: E402


def draw(**overrides):
    value = {
        "kind": "draw",
        "draw_id": "draw-1",
        "caller_rva": "0xedfcd1",
        "target": "main-scene",
        "target_size": [1728, 1117],
        "viewport": [0, 0, 1728, 1117],
        "scissor": [0, 0, 1728, 1117],
        "shader": {"identity": "simple-lua-Simple/asset-maincode-v0",
                   "vertex": "vs-hash", "pixel": "ps-hash", "linked": True},
        "topology": 5,
        "samplers": {
            "tex0": {"binding": 0, "texture": "atlas", "digest": "a" * 64},
            "tex7": {"binding": 7, "texture": "terrain", "digest": "b" * 64},
        },
        "textures": {"terrain": "b" * 64},
        "uniforms": {"projection": "u-hash", "tint": [1, 1, 1, 1]},
        "raster": {"cull_enabled": True, "cull_face": "back", "front_face": "ccw"},
        "depth": {"test": True, "write": True, "func": "less"},
        "blend": {"enabled": True, "source": "src_alpha",
                  "destination": "one_minus_src_alpha"},
        "gpu": {"completed": True, "refused": False},
    }
    value.update(overrides)
    return value


class SamplerRegressionTests(unittest.TestCase):
    def test_tex7_bound_to_unit_6_is_the_first_divergence(self):
        reference = draw()
        candidate = draw(samplers={
            "tex0": {"binding": 0, "texture": "atlas", "digest": "a" * 64},
            "tex7": {"binding": 6, "texture": "terrain", "digest": "b" * 64},
        })
        result = gfx_semantic.first_divergence([reference], [candidate])
        self.assertEqual(result["status"], gfx_semantic.DIVERGED)
        self.assertEqual(result["first"]["field"], "samplers.tex7.unit")
        self.assertEqual(result["first"]["reference"], 7)
        self.assertEqual(result["first"]["candidate"], 6)

    def test_sampler_contract_reports_a_missing_texN_declaration(self):
        samplers = gfx_semantic.normalize_samplers({
            "tex7": {"binding": 6},
            "tex8": {"binding": 8},
        })
        violations = gfx_semantic.sampler_contract(samplers)
        self.assertEqual(len(violations), 1)
        self.assertEqual(violations[0]["sampler"], "tex7")
        self.assertEqual(violations[0]["expected_unit"], 7)

    def test_missing_tex6_does_not_renumber_tex7(self):
        # The map-base shader omits tex6.  Sequential binding would move tex7 to
        # unit 6; the declared engine name must keep it on unit 7.
        reference = draw()
        candidate = draw(samplers={
            "tex0": {"binding": 0, "texture": "atlas", "digest": "a" * 64},
            "tex7": {"binding": 7, "texture": "terrain", "digest": "b" * 64},
        })
        self.assertEqual(
            gfx_semantic.first_divergence([reference], [candidate])["status"],
            gfx_semantic.MATCH,
        )


class ExecutionRegressionTests(unittest.TestCase):
    def test_rejected_draw_is_an_execution_divergence(self):
        reference = draw()
        candidate = draw(gpu={"completed": False, "refused": True,
                              "refusal_reason": "invalid sampler binding"})
        result = gfx_semantic.first_divergence([reference], [candidate])
        self.assertEqual(result["status"], gfx_semantic.DIVERGED)
        self.assertEqual(result["first"]["field"], "gpu.completed")

    def test_clear_semantics_flags_are_compared(self):
        reference = draw(kind="clear", clear={"flags": 7, "color": [0, 0, 0, 0]})
        candidate = draw(kind="clear", clear={"flags": 1, "color": [0, 0, 0, 0]})
        result = gfx_semantic.first_divergence([reference], [candidate])
        self.assertEqual(result["first"]["field"], "clear.flags")
        self.assertEqual(result["first"]["reference"], 7)
        self.assertEqual(result["first"]["candidate"], 1)

    def test_incorrect_cull_enable_decoding(self):
        reference = draw(raster={"cull_enabled": True, "cull_face": "back",
                                 "front_face": "ccw"})
        candidate = draw(raster={"cull_enabled": False, "cull_face": "back",
                                 "front_face": "ccw"})
        result = gfx_semantic.first_divergence([reference], [candidate])
        self.assertEqual(result["first"]["field"], "raster.cull_enabled")

    def test_offscreen_viewport_scaled_by_backbuffer_hidpi_ratio(self):
        reference = draw(target="bloom", target_size=[864, 558],
                         viewport=[0, 0, 864, 558], scissor=[0, 0, 864, 558])
        candidate = draw(target="bloom", target_size=[864, 558],
                         viewport=[0, 0, 432, 279], scissor=[0, 0, 432, 279])
        result = gfx_semantic.first_divergence([reference], [candidate])
        self.assertEqual(result["first"]["field"], "viewport")
        self.assertEqual(result["first"]["reference"], [0, 0, 864, 558])
        self.assertEqual(result["first"]["candidate"], [0, 0, 432, 279])

    def test_missing_resolve_orientation_flip(self):
        reference = draw(kind="resolve", resolve={
            "source": "game-color", "destination": "main-scene",
            "source_size": [3456, 2234], "destination_size": [1728, 1117],
            "flip_v": True, "scaled": True, "status": "scaled_ok",
        })
        candidate = draw(kind="resolve", resolve={
            "source": "game-color", "destination": "main-scene",
            "source_size": [3456, 2234], "destination_size": [1728, 1117],
            "flip_v": False, "scaled": True, "status": "scaled_ok",
        })
        result = gfx_semantic.first_divergence([reference], [candidate])
        self.assertEqual(result["first"]["field"], "resolve.flip_v")

    def test_observer_state_corruption_is_an_instrumentation_divergence(self):
        reference = draw(gpu={"completed": True, "observer_state_restored": True})
        candidate = draw(gpu={"completed": True, "observer_state_restored": False})
        result = gfx_semantic.first_divergence([reference], [candidate])
        self.assertEqual(result["first"]["field"], "gpu.observer_state_restored")


class SequenceTests(unittest.TestCase):
    def test_operation_count_mismatch_is_reported(self):
        result = gfx_semantic.first_divergence([draw(), draw()], [draw()])
        self.assertEqual(result["status"], gfx_semantic.DIVERGED)
        self.assertEqual(result["first"]["field"], "operation_count")

    def test_missing_side_is_insufficient(self):
        result = gfx_semantic.first_divergence([], [draw()])
        self.assertEqual(result["status"], gfx_semantic.INSUFFICIENT)

    def test_identical_sequence_matches(self):
        result = gfx_semantic.first_divergence([draw()], [draw()])
        self.assertEqual(result["status"], gfx_semantic.MATCH)


class ResourceLineageTests(unittest.TestCase):
    def test_wrong_generation_resource_usage(self):
        lineage = gfx_semantic.ResourceLineage()
        lineage.observe({"key": "season-map", "action": "create", "sequence": 1,
                         "dimensions": [864, 558], "format": "BGRA8"})
        lineage.observe({"key": "season-map", "action": "upload", "sequence": 2,
                         "digest": "a" * 64, "writer": "draw-10"})
        lineage.observe({"key": "season-map", "action": "sample", "sequence": 3,
                         "generation": 1, "consumer": "draw-11"})
        lineage.observe({"key": "season-map", "action": "upload", "sequence": 4,
                         "digest": "b" * 64, "writer": "draw-12"})
        lineage.observe({"key": "season-map", "action": "sample", "sequence": 5,
                         "generation": 1, "consumer": "draw-13"})
        summary = lineage.summary()
        self.assertIn("sampled_generation_1_current_2",
                      summary["uncertain"]["season-map"])

    def test_sampling_before_upload_is_preserved_as_uncertainty(self):
        lineage = gfx_semantic.ResourceLineage()
        lineage.observe({"key": "atlas", "action": "create", "sequence": 1})
        lineage.observe({"key": "atlas", "action": "sample", "sequence": 2,
                         "generation": 0, "consumer": "draw-1"})
        summary = lineage.summary()
        self.assertIn("sampled_before_upload", summary["uncertain"]["atlas"])

    def test_replay_pre_image_ordering_violation(self):
        """An upload after a synthetic clear means the draw starts black."""
        lineage = gfx_semantic.ResourceLineage()
        lineage.observe({"key": "frame", "action": "create", "sequence": 1})
        lineage.observe({"key": "frame", "action": "clear", "sequence": 2})
        lineage.observe({"key": "frame", "action": "upload", "sequence": 3,
                         "digest": "pre-image"})
        lineage.observe({"key": "frame", "action": "sample", "sequence": 4,
                         "generation": 2, "consumer": "draw-1"})
        summary = lineage.summary()
        record = summary["resources"]["frame"]
        self.assertEqual(record["last_write_at"], 3)
        self.assertEqual(record["generation"], 2)
        # The pre-image upload is generation 2, not the final content: the
        # lineage makes the ordering violation explicit rather than hiding it.
        self.assertIn("frame", summary["resources"])

    def test_lineage_tracks_writer_and_consumers(self):
        lineage = gfx_semantic.ResourceLineage()
        lineage.observe({"key": "tex", "action": "create", "sequence": 1})
        lineage.observe({"key": "tex", "action": "upload", "sequence": 2,
                         "digest": "c" * 64, "writer": "copy-1"})
        lineage.observe({"key": "tex", "action": "sample", "sequence": 3,
                         "generation": 1, "consumer": "draw-4"})
        record = lineage.summary()["resources"]["tex"]
        self.assertEqual(record["last_writer"], "copy-1")
        self.assertEqual(record["last_write_digest"], "c" * 64)
        self.assertEqual(record["consumers"], ["draw-4"])


if __name__ == "__main__":
    unittest.main()
