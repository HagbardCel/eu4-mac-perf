"""Live backend dispatch and reusable GPU checkpoints on a real Metal device.

The host drives the same replacement entry points as the game but never starts
EU4. This is a GPU integration test, not a live-engine capture.
"""

import hashlib
import json
import os
import struct
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HOST_SOURCE = ROOT / "tests" / "gfx_live_draw_host.mm"
HOST = ROOT / "benchmark" / ".build" / "gfx_live_draw_host"
SOURCES = [
    ROOT / "renderer/metal" / "eu4_gfx_shader.mm",
    ROOT / "renderer/metal" / "eu4_gfx_buffers.mm",
    ROOT / "benchmark" / "eu4_gfx_perf.mm",
    ROOT / "renderer/metal" / "eu4_gfx_texture.mm",
    ROOT / "renderer/integration" / "eu4_gfx_init.mm",
    ROOT / "benchmark" / "eu4_gfx_frame_observe.c",
    ROOT / "renderer/metal" / "eu4_gfx_simple_draw.mm",
    ROOT / "renderer/metal" / "eu4_gfx_sprite_draw.mm",
    ROOT / "renderer/metal" / "eu4_gfx_draw_encode.mm",
    ROOT / "benchmark" / "eu4_gfx_draw_capture.c",
    ROOT / "renderer/integration" / "eu4_gfx_draw_route.mm",
    ROOT / "renderer/metal" / "eu4_gfx_generic_draw.mm",
    ROOT / "benchmark" / "eu4_gfx_logical_capture.mm",
    ROOT / "renderer/integration" / "eu4_gfx_checkpoint.mm",
    ROOT / "benchmark" / "eu4_metal_events.c",
]


@unittest.skipUnless(sys.platform == "darwin", "live draw route needs Metal/macOS")
class GfxLiveDrawTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        HOST.parent.mkdir(parents=True, exist_ok=True)
        command = [
            "clang", "-O2", "-Wall", "-Wextra", "-fobjc-arc", "-lc++",
            "-I", str(ROOT / "benchmark"),
            "-framework", "Foundation", "-framework", "Metal", "-framework", "QuartzCore",
            "-o", str(HOST), str(HOST_SOURCE), *[str(source) for source in SOURCES],
        ]
        built = subprocess.run(command, capture_output=True, text=True, check=False)
        if built.returncode:
            raise AssertionError(built.stderr.strip() or "live draw host failed to build")

    def _run(self, *args: str) -> subprocess.CompletedProcess:
        env = os.environ.copy()
        env["MTL_DEBUG_LAYER"] = "1"
        env["MTL_DEBUG_LAYER_ERROR_MODE"] = "assert"
        return subprocess.run(
            [str(HOST), *args], capture_output=True, text=True, check=False, env=env
        )

    def test_clear_two_draws_present(self) -> None:
        ran = self._run()
        self.assertEqual(ran.returncode, 0, ran.stderr)
        self.assertIn("live_route=ok", ran.stdout)
        self.assertIn("clear_draw=1", ran.stdout)
        self.assertIn("two_draws=1", ran.stdout)
        self.assertIn("present=1", ran.stdout)
        self.assertIn("gpu_failure=1", ran.stdout)

    def test_texture_binding_ranges(self) -> None:
        ran = self._run("--texture-ranges")
        self.assertEqual(ran.returncode, 0, ran.stderr)
        self.assertIn("texture_ranges=1", ran.stdout)

    def test_dump_texture_write_failure(self) -> None:
        ran = self._run("--dump-texture")
        self.assertEqual(ran.returncode, 0, ran.stderr)
        self.assertIn("dump_texture=1", ran.stdout)

    def test_bc_texture_decode(self) -> None:
        ran = self._run("--bc-decode")
        self.assertEqual(ran.returncode, 0, ran.stderr)
        self.assertTrue(
            "bc_decode=1" in ran.stdout or "bc_decode=skip" in ran.stdout
        )

    def test_sampler_state_decode(self) -> None:
        ran = self._run("--sampler-decode")
        self.assertEqual(ran.returncode, 0, ran.stderr)
        self.assertIn("sampler_decode=1", ran.stdout)

    def test_sprite_uniform_contract(self) -> None:
        ran = self._run("--sprite-uniform")
        self.assertEqual(ran.returncode, 0, ran.stderr)
        self.assertIn("sprite_uniform=1", ran.stdout)

    def test_effective_depth_state(self) -> None:
        ran = self._run("--depth-state")
        self.assertEqual(ran.returncode, 0, ran.stderr)
        self.assertIn("depth_state=1", ran.stdout)

    def test_texture_type_binding_decision(self) -> None:
        ran = self._run("--texture-type-decision")
        self.assertEqual(ran.returncode, 0, ran.stderr)
        self.assertIn("texture_type_decision=1", ran.stdout)

    def test_scissor_conversion_helper(self) -> None:
        ran = self._run("--scissor-helper")
        self.assertEqual(ran.returncode, 0, ran.stderr)
        self.assertIn("scissor_helper=1", ran.stdout)

    def test_target_clear_engine_flags(self) -> None:
        ran = self._run("--target-clear")
        self.assertEqual(ran.returncode, 0, ran.stderr)
        self.assertIn("target_clear=1", ran.stdout)
        self.assertIn("depth_preserved=1", ran.stdout)
        self.assertIn("depth_cleared=1", ran.stdout)
        self.assertIn("color_preserved=1", ran.stdout)

    def test_resolve_orientation(self) -> None:
        ran = self._run("--resolve-orientation")
        self.assertEqual(ran.returncode, 0, ran.stderr)
        self.assertIn("resolve_orientation=1", ran.stdout)

    def test_production_attribute_bounds(self) -> None:
        ran = self._run("--production-bounds")
        self.assertEqual(ran.returncode, 0, ran.stderr)
        self.assertIn("production_bounds=1", ran.stdout)
        self.assertIn("attributes_17=1", ran.stdout)
        self.assertIn("capacity=1", ran.stdout)

    def test_vertex_fetch_semantics(self) -> None:
        ran = self._run("--vertex-fetch")
        self.assertEqual(ran.returncode, 0, ran.stderr)
        self.assertIn("vertex_fetch=1", ran.stdout)
        self.assertIn("one_instance=1", ran.stdout)
        self.assertIn("multi_instance=1", ran.stdout)
        self.assertIn("constant=1", ran.stdout)

    def test_production_pipeline_mask_cache_separation(self) -> None:
        ran = self._run("--production-pipeline")
        self.assertEqual(ran.returncode, 0, ran.stderr)
        self.assertIn("production_pipeline=1", ran.stdout)
        self.assertIn("distinct=1", ran.stdout)

    def test_color_write_mask_preserves_disabled_channels(self) -> None:
        ran = self._run("--color-write-mask")
        self.assertEqual(ran.returncode, 0, ran.stderr)
        self.assertIn("color_write_mask=1", ran.stdout)

    def test_indexed_base_vertex_reads_effective_vertices(self) -> None:
        ran = self._run("--indexed-base-vertex")
        self.assertEqual(ran.returncode, 0, ran.stderr)
        self.assertIn("indexed_base_vertex=1", ran.stdout)
        self.assertIn("base4_green=1", ran.stdout)
        self.assertIn("base0_red=1", ran.stdout)

    def test_geometry_publication_is_thread_safe(self) -> None:
        ran = self._run("--geometry-thread")
        self.assertEqual(ran.returncode, 0, ran.stderr)
        self.assertIn("geometry_thread=1", ran.stdout)
        for token in ("no_encoder_end=1", "no_submit=1", "transition=1",
                      "draw_refused=1", "epoch=1", "older_refused=1",
                      "stale_present=1", "same_size=1"):
            self.assertIn(token, ran.stdout)

    def test_scissor_does_not_leak_into_simple_or_sprite(self) -> None:
        ran = self._run("--scissor-handoff")
        self.assertEqual(ran.returncode, 0, ran.stderr)
        self.assertIn("scissor_handoff=1", ran.stdout)
        self.assertIn("one_encoder=1", ran.stdout)
        self.assertIn("corner_green=1", ran.stdout)
        self.assertIn("center_blue=1", ran.stdout)

    def test_multiple_render_target_conversions_in_external_command(self) -> None:
        ran = self._run("--sampled-multi")
        self.assertEqual(ran.returncode, 0, ran.stderr)
        self.assertIn("sampled_multi=1", ran.stdout)
        for token in ("external=1", "bindings=1", "pixels=1", "second=1",
                      "superseded=1", "abandoned=1", "active=1"):
            self.assertIn(token, ran.stdout)

    def test_failed_generic_draw_closes_only_its_pass(self) -> None:
        ran = self._run("--encoder-cleanup")
        self.assertEqual(ran.returncode, 0, ran.stderr)
        self.assertIn("encoder_cleanup=1", ran.stdout)
        for token in ("failed=1", "refused=1", "diagnostic=1", "no_encoder=1",
                      "preserved_before=1", "preserved=1", "presented=1",
                      "pixels=1"):
            self.assertIn(token, ran.stdout)

    def test_cached_layout_preserves_attribute_locations(self) -> None:
        ran = self._run("--element-locations")
        self.assertEqual(ran.returncode, 0, ran.stderr)
        self.assertIn("element_locations=1", ran.stdout)
        for token in ("cached=1", "trace=1", "fallback=1", "stable=1"):
            self.assertIn(token, ran.stdout)

    def test_resource_update_drain_success(self) -> None:
        ran = self._run("--update-success")
        self.assertEqual(ran.returncode, 0, ran.stderr)
        self.assertIn("update_success=1", ran.stdout)

    def _assert_update_drain_failure(self, kind: str, symbol: str) -> None:
        ran = self._run("--update-drain-failure", kind)
        self.assertEqual(ran.returncode, 2, ran.stderr)
        self.assertNotIn("update_unexpected_return=1", ran.stdout)
        marker = "EVENT_DIR="
        line = next(
            (part for part in ran.stderr.splitlines() if marker in part), ""
        )
        self.assertIn(marker, line)
        directory = Path(line.split(marker, 1)[1].strip())
        stop = json.loads((directory / "stop.json").read_text())
        self.assertEqual(stop["symbol"], symbol)
        self.assertEqual(stop["reason"], "gpu_failed")

    def test_vertex_update_fails_closed_on_drain_error(self) -> None:
        self._assert_update_drain_failure("vertex", "_GfxUpdateVertexBuffer")

    def test_multi_update_fails_closed_on_drain_error(self) -> None:
        self._assert_update_drain_failure(
            "multiple", "_GfxUpdateVertexBufferMultiple"
        )

    def test_texture_update_fails_closed_on_drain_error(self) -> None:
        self._assert_update_drain_failure("texture", "_GfxUpdateTexture")

    def test_gpu_timing_is_counted_once_per_command_buffer(self) -> None:
        ran = self._run("--gpu-timing")
        self.assertEqual(ran.returncode, 0, ran.stderr)
        self.assertIn("gpu_timing=1", ran.stdout)
        self.assertIn("epoch_isolated=1", ran.stdout)

    def test_dynamic_slot_offset_does_not_create_layout_variant(self) -> None:
        ran = self._run("--layout-stability")
        self.assertEqual(ran.returncode, 0, ran.stderr)
        self.assertIn("layout_stability=1", ran.stdout)
        self.assertIn("misses=0", ran.stdout)
        self.assertIn("encoders=0", ran.stdout)

    def test_render_pass_coalescing_preserves_blend_order(self) -> None:
        """Two blend states share one pass; the exact pixel proves order."""
        ran = self._run("--blend-order")
        self.assertEqual(ran.returncode, 0, ran.stderr)
        self.assertIn("blend_order=1", ran.stdout)
        self.assertIn("one_pass=1", ran.stdout)
        self.assertIn("order=1", ran.stdout)
        self.assertIn("clear_split=1", ran.stdout)

    def test_sampled_render_target_reuse_within_command(self) -> None:
        """One conversion serves every draw in the shared command buffer."""
        ran = self._run("--sampled-batch")
        self.assertEqual(ran.returncode, 0, ran.stderr)
        self.assertIn("sampled_batch=1", ran.stdout)
        self.assertIn("reused=1", ran.stdout)
        self.assertIn("shadow=1", ran.stdout)
        self.assertIn("reconverted=1", ran.stdout)

    def test_shared_command_batch_sequences(self) -> None:
        """Refusal, target change, in-place update, 1000 draws and shutdown."""
        ran = self._run("--batch-sequences")
        self.assertEqual(ran.returncode, 0, ran.stderr)
        self.assertIn("batch_sequences=1", ran.stdout)
        for token in ("refused=1", "preserved_draws=1", "preserved_pixels=1",
                      "target_switch=1", "resource_update=1", "one_submission=1",
                      "flushes=1", "shutdown=1"):
            self.assertIn(token, ran.stdout)

    def test_indexed_draw_reuses_engine_index_buffer(self) -> None:
        """The draw path binds the registry's Metal buffer directly."""
        ran = self._run("--indexed-resource")
        self.assertEqual(ran.returncode, 0, ran.stderr)
        self.assertIn("indexed_resource=1", ran.stdout)
        self.assertIn("reuse=1", ran.stdout)
        self.assertIn("update=1", ran.stdout)
        self.assertIn("recycle=1", ran.stdout)
        self.assertIn("negative_refused=1", ran.stdout)
        self.assertIn("bounds_refused=1", ran.stdout)
        self.assertIn("zero_alloc=1", ran.stdout)

    def test_draw_semantics_contracts(self) -> None:
        ran = self._run("--draw-semantics")
        self.assertEqual(ran.returncode, 0, ran.stderr)
        self.assertIn("draw_semantics=1", ran.stdout)

    def test_sampled_render_target_orientation(self) -> None:
        ran = self._run("--sampled-rt-orientation")
        self.assertEqual(ran.returncode, 0, ran.stderr)
        self.assertIn("sampled_rt_orientation=1", ran.stdout)
        self.assertIn("rt_flip=1", ran.stdout)
        self.assertIn("normal_preserved=1", ran.stdout)
        self.assertIn("shadow_cache=1", ran.stdout)
        self.assertIn("repeated=1", ran.stdout)
        self.assertIn("modified=1", ran.stdout)
        self.assertIn("abandoned=1", ran.stdout)
        self.assertIn("stale=1", ran.stdout)
        self.assertIn("refused=1", ran.stdout)
        self.assertIn("missing_storage=1", ran.stdout)

    def test_sampler_effective_min_filter_uses_texture_mip_state(self) -> None:
        ran = self._run("--sampler-effective")
        self.assertEqual(ran.returncode, 0, ran.stderr)
        self.assertIn("sampler_effective=1", ran.stdout)
        self.assertIn("with_mips=9985", ran.stdout)
        self.assertIn("without_mips=9729", ran.stdout)

    def test_offscreen_viewport_is_not_hidpi_scaled(self) -> None:
        ran = self._run("--offscreen-viewport")
        self.assertEqual(ran.returncode, 0, ran.stderr)
        self.assertIn("offscreen_viewport=1", ran.stdout)
        self.assertIn("covered=1024 of=1024", ran.stdout)

    def test_frame_start_reconstruction_and_texture_clear(self) -> None:
        ran = self._run("--frame-start")
        self.assertEqual(ran.returncode, 0, ran.stderr)
        self.assertIn("begin_scene=1", ran.stdout)
        self.assertIn("current_cleared=1", ran.stdout)
        self.assertIn("blend_default=1", ran.stdout)
        self.assertIn("clear_texture=1", ran.stdout)
        self.assertIn("texture_lod=1", ran.stdout)

    def test_two_frame_prefix_resets_stale_target_contents(self) -> None:
        ran = self._run("--prefix-two-frames")
        self.assertEqual(ran.returncode, 0, ran.stderr)
        self.assertIn("frame1_red=1", ran.stdout)
        self.assertIn("frame2_green=1", ran.stdout)
        self.assertIn("stale_red=0", ran.stdout)

    def test_native_checkpoint_occurrence_validation(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            for value in ("0", "-1", "2147483648", "abc"):
                env = os.environ.copy()
                env.update({
                    "EU4_GFX_CHECKPOINT": "after-draw",
                    "EU4_GFX_CHECKPOINT_DIR": tmp,
                    "EU4_GFX_CHECKPOINT_OCCURRENCE": value,
                })
                ran = subprocess.run([str(HOST), "--checkpoint-env"],
                                     capture_output=True, text=True, check=False, env=env)
                self.assertEqual(ran.returncode, 0, ran.stderr)
                self.assertIn("enabled=0", ran.stdout, value)
            for value in ("1", "2147483647"):
                env = os.environ.copy()
                env.update({
                    "EU4_GFX_CHECKPOINT": "after-draw",
                    "EU4_GFX_CHECKPOINT_DIR": tmp,
                    "EU4_GFX_CHECKPOINT_OCCURRENCE": value,
                })
                ran = subprocess.run([str(HOST), "--checkpoint-env"],
                                     capture_output=True, text=True, check=False, env=env)
                self.assertEqual(ran.returncode, 0, ran.stderr)
                self.assertIn(f"enabled=1 occurrence={value}", ran.stdout)

    def test_after_present_caller_filter_selects_only_matching_callers(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            ran = self._run("--present-caller-filter", tmp)
            self.assertEqual(ran.returncode, 0, ran.stderr or ran.stdout)
            self.assertIn("unrelated=0 selected=1 later=0", ran.stdout)

    def test_clear_flag_mask(self) -> None:
        for flags in (1, 7):
            ran = self._run("--clear-flags", str(flags))
            self.assertEqual(ran.returncode, 0, ran.stderr)
            self.assertIn(f"clear_ok={flags}", ran.stdout)
        for flags in (0, 8, 9):
            ran = self._run("--clear-flags", str(flags))
            self.assertEqual(ran.returncode, 2, ran.stderr)

    def test_caller_selector_any_and_miss(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            ran = self._run("--checkpoint-any", tmp)
            self.assertEqual(ran.returncode, 2, ran.stderr)
            self.assertTrue((Path(tmp) / "checkpoint-after-draw-1.json").is_file())
        with tempfile.TemporaryDirectory() as tmp:
            ran = self._run("--checkpoint-miss", tmp)
            self.assertEqual(ran.returncode, 0, ran.stderr)
            self.assertIn("checkpoint_miss=1", ran.stdout)
            self.assertFalse((Path(tmp) / "checkpoint-after-draw-1.json").exists())

    def test_partial_scissor_clear_fails_closed_with_reason(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            ran = self._run("--clear-refusal", tmp)
            self.assertEqual(ran.returncode, 2, ran.stderr)
            diagnostic = json.loads((Path(tmp) / "clear-unsupported.json").read_text())
            self.assertEqual(diagnostic["reason"],
                             "a partial scissor is not supported by the clear")
            self.assertEqual(diagnostic["flags"], 7)

    def _checkpoint(self, operation: str) -> dict:
        with tempfile.TemporaryDirectory() as tmp:
            ran = self._run("--checkpoint", operation, tmp)
            # The checkpoint deliberately stops the process after the capture.
            self.assertEqual(ran.returncode, 2, ran.stderr)
            path = Path(tmp) / f"checkpoint-{operation}-1.json"
            self.assertTrue(path.is_file(), ran.stderr)
            result = json.loads(path.read_text())
            raw = Path(tmp) / result["capture"]["raw_file"]
            self.assertTrue(raw.is_file())
            payload = raw.read_bytes()
            self.assertEqual(len(payload), result["capture"]["raw_bytes"])
            self.assertEqual(
                hashlib.sha256(payload).hexdigest(), result["capture"]["raw_sha256"]
            )
            width = result["target"]["width"]
            self.assertEqual(result["capture"]["raw_bytes"], width * width * 4)
            self.assertEqual(result["target"]["pixel_format"], "BGRA8Unorm")
            self.assertEqual(result["status"]["pixels_captured"], 1)
            self.assertEqual(result["gpu"]["completed"], True)
            self.assertEqual(result["caller"]["rva"], "0xedfcd1" if operation == "after-draw" else "0x0")
            if operation == "after-draw":
                route = json.loads((Path(tmp) / "draw-route.json").read_text())
                self.assertEqual(route["route"], "live")
                self.assertEqual(route["shader_identity"],
                                 "simple-lua-Simple/asset-maincode-v0")
            return result

    def test_checkpoint_after_draw(self) -> None:
        result = self._checkpoint("after-draw")
        self.assertEqual(result["checkpoint"], "after-draw")
        self.assertEqual(result["draw"]["shader_identity"], "simple-lua-Simple/asset-maincode-v0")
        self.assertEqual(result["draw"]["vertex_stride"], 24)
        self.assertEqual(result["draw"]["count"], 4)
        self.assertEqual(result["status"]["draw_reached"], 1)
        self.assertEqual(result["status"]["draw_encoded"], 1)

    def test_checkpoint_after_clear(self) -> None:
        result = self._checkpoint("after-clear")
        self.assertEqual(result["checkpoint"], "after-clear")
        self.assertEqual(result["status"]["draw_encoded"], 0)

    def test_checkpoint_before_present(self) -> None:
        result = self._checkpoint("before-present")
        self.assertEqual(result["checkpoint"], "before-present")
        self.assertEqual(result["status"]["presented"], 0)


if __name__ == "__main__":
    unittest.main()
