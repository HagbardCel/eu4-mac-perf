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
    ROOT / "benchmark" / "eu4_gfx_shader.mm",
    ROOT / "benchmark" / "eu4_gfx_buffers.mm",
    ROOT / "benchmark" / "eu4_gfx_texture.mm",
    ROOT / "benchmark" / "eu4_gfx_init.mm",
    ROOT / "benchmark" / "eu4_gfx_simple_draw.mm",
    ROOT / "benchmark" / "eu4_gfx_sprite_draw.mm",
    ROOT / "benchmark" / "eu4_gfx_draw_encode.mm",
    ROOT / "benchmark" / "eu4_gfx_draw_capture.c",
    ROOT / "benchmark" / "eu4_gfx_draw_route.mm",
    ROOT / "benchmark" / "eu4_gfx_logical_capture.mm",
    ROOT / "benchmark" / "eu4_gfx_checkpoint.mm",
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

    def test_frame_start_reconstruction_and_texture_clear(self) -> None:
        ran = self._run("--frame-start")
        self.assertEqual(ran.returncode, 0, ran.stderr)
        self.assertIn("begin_scene=1", ran.stdout)
        self.assertIn("current_cleared=1", ran.stdout)
        self.assertIn("blend_default=1", ran.stdout)
        self.assertIn("clear_texture=1", ran.stdout)

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
