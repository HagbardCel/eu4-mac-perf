"""Agent-facing experiment surface: authorization, probe refusal, PNG preview."""

from __future__ import annotations

import hashlib
import io
import json
import math
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from automation import live_gfx

ROOT = Path(__file__).resolve().parents[1]


class ProbeSurfaceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.exe = self.root / "eu4"
        self.exe.write_bytes(b"pinned stub")
        self.run_root = self.root / "run"
        self.run_root.mkdir()
        self.output_root = self.run_root / "experiments"


    def test_png_preview_is_a_real_png(self) -> None:
        width = height = 2
        bgra = bytes([0, 0, 255, 255] * width * height)
        path = self.root / "preview.png"
        live_gfx._write_png(path, bgra, width, height)
        payload = path.read_bytes()
        self.assertTrue(payload.startswith(b"\x89PNG\r\n\x1a\n"))
        self.assertIn(b"IHDR", payload[:32])

    def test_status_levels_are_not_collapsed(self) -> None:
        output = self.root / "experiment"
        output.mkdir()
        raw = output / "checkpoint-after-draw-1.bgra"
        raw.write_bytes(b"\x00" * (4 * 4 * 4))
        checkpoint = {
            "checkpoint": "after-draw",
            "target": {"width": 4, "height": 4},
            "capture": {"raw_file": raw.name},
            "status": {
                "draw_reached": 1,
                "draw_encoded": 1,
                "gpu_completed": 1,
                "pixels_captured": 1,
                "presented": 0,
            },
        }
        (output / "checkpoint-after-draw-1.json").write_text(json.dumps(checkpoint))
        (output / "draw-route.json").write_text(json.dumps({"route": "live"}))
        status = live_gfx._read_status(output)
        self.assertTrue(status["draw_reached"])
        self.assertTrue(status["draw_encoded"])
        self.assertTrue(status["gpu_completed"])
        self.assertTrue(status["pixels_captured"])
        self.assertFalse(status["presented"])
        inspected = live_gfx._inspect(output)
        self.assertEqual(inspected["checkpoints"][0]["checkpoint"], "after-draw")


class ScreenshotRetryTests(unittest.TestCase):
    def test_over_threshold_capture_does_not_stop_discovery(self) -> None:
        self.assertFalse(live_gfx._window_retry_should_stop(
            {"status": "captured", "qualified": False}
        ))
        self.assertFalse(live_gfx._window_retry_should_stop({"status": "missing"}))
        self.assertTrue(live_gfx._window_retry_should_stop(
            {"status": "captured", "qualified": True}
        ))

    def test_similarity_threshold_is_explicit(self) -> None:
        self.assertTrue(live_gfx._similarity_ok(
            {"status": "compared", "mean_abs_error": 1.9}, 2.0
        ))
        self.assertFalse(live_gfx._similarity_ok(
            {"status": "compared", "mean_abs_error": 2.1}, 2.0
        ))
        self.assertFalse(live_gfx._similarity_ok(
            {"status": "unavailable", "mean_abs_error": 0.0}, 2.0
        ))


class ProbeLaunchTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.exe = self.root / "eu4"
        self.exe.write_bytes(b"pinned stub")
        self.sha = hashlib.sha256(self.exe.read_bytes()).hexdigest()
        self.run_root = self.root / "run"
        self.run_root.mkdir()
        self.output_root = self.run_root / "experiments"


    def test_probe_rejects_invalid_timeout_before_spawn(self) -> None:

        output = self.output_root / "invalid-timeout"
        for value in (math.nan, math.inf, -math.inf, 0, -1):
            stderr = io.StringIO()
            with patch("sys.stderr", stderr), patch("subprocess.Popen") as popen:
                code = live_gfx.main([
                    "probe", "--backend", "metal", "--checkpoint", "after-draw",
                    f"--timeout={value}", "--output", str(output),

                ])
            self.assertEqual(code, live_gfx.EXIT_INCOMPLETE)
            self.assertIn("--timeout", stderr.getvalue())
            popen.assert_not_called()
            self.assertFalse(output.exists())


    def test_probe_rejects_invalid_occurrence(self) -> None:
        for value in ("0", "-1", "2147483648"):
            stderr = io.StringIO()
            with patch("sys.stderr", stderr):
                code = live_gfx.main([
                    "probe", "--backend", "metal", "--checkpoint", "after-draw",
                    "--occurrence", value, "--output", str(self.output_root / "bad"),

                ])
            self.assertEqual(code, live_gfx.EXIT_INCOMPLETE)
            self.assertIn("--occurrence", stderr.getvalue())

    def test_probe_passes_executable_identity_and_caller_to_launcher(self) -> None:

        identity = {"executable": str(self.exe), "executable_sha256": self.sha}
        library = self.root / "libeu4_metal_boot.dylib"
        library.write_bytes(b"library")
        launched = []
        envs = []

        def fake_launch(output, **kwargs):
            launched.append(output)
            envs.append(dict(kwargs.get("env_overrides") or {}))
            output.mkdir(parents=True, exist_ok=True)
            raw = output / "checkpoint-after-draw-1.bgra"
            raw.write_bytes(b"\x00\x00\x00\xff")
            (output / "checkpoint-after-draw-1.json").write_text(json.dumps({
                "schema": "gfx-checkpoint/v1",
                "checkpoint": "after-draw",
                "target": {"width": 1, "height": 1},
                "capture": {"raw_file": raw.name},
                "status": {
                    "draw_reached": 1, "draw_encoded": 1, "gpu_completed": 1,
                    "pixels_captured": 1, "presented": 0,
                },
            }))
            return {"pid": 1234}

        import types
        bench = types.SimpleNamespace(
            gog_identity=lambda: identity, sha256=lambda path: "library-sha"
        )
        import sys
        benchmark = self.root / "benchmark"
        benchmark.mkdir()
        with patch("automation.test_profiles.live_allowed", return_value=True), \
             patch.dict(sys.modules, {
            "eu4_benchmark": bench,
            "eu4_metal_boot": types.SimpleNamespace(
                build_library=lambda arch="x86_64": library,
                launch_session=fake_launch,
            ),
        }):
            code = live_gfx.main([
                "probe", "--backend", "metal", "--checkpoint", "after-draw",
                "--occurrence", "1", "--timeout", "30",
                "--output", str(self.output_root / "probe"),

            ])
        self.assertEqual(code, live_gfx.EXIT_COMPLETE)
        self.assertEqual(len(launched), 1)
        self.assertEqual(envs[0]["EU4_GFX_CHECKPOINT_CALLER_RVA"], "0")
        with patch("automation.test_profiles.live_allowed", return_value=True), \
             patch.dict(sys.modules, {
            "eu4_benchmark": bench,
            "eu4_metal_boot": types.SimpleNamespace(
                build_library=lambda arch="x86_64": library,
                launch_session=fake_launch,
            ),
        }):
            second = live_gfx.main([
                "probe", "--backend", "metal", "--checkpoint", "after-draw",
                "--caller-rva", "0x1234", "--occurrence", "1", "--timeout", "30",
                "--output", str(self.output_root / "probe2"),

            ])
        self.assertEqual(second, live_gfx.EXIT_COMPLETE)
        self.assertEqual(envs[1]["EU4_GFX_CHECKPOINT_CALLER_RVA"], "0x1234")
        result = json.loads((self.output_root / "probe" / "probe-result.json").read_text())
        self.assertTrue(result["status"]["pixels_captured"])
        self.assertIsNone(result["status"]["reference_matched"])
        self.assertEqual(envs[0]["EU4_GFX_EXECUTABLE_SHA256"], self.sha)


class ReferenceEqualityTests(unittest.TestCase):
    def setUp(self) -> None:
        try:
            from PIL import Image  # noqa: F401
        except Exception:  # noqa: BLE001
            self.skipTest("PIL is not installed")
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.width = 4
        self.height = 4
        self.bgra = bytes([0, 0, 255, 255] * self.width * self.height)
        self.raw = self.root / "checkpoint-after-draw-1.bgra"
        self.raw.write_bytes(self.bgra)
        self.checkpoint = {
            "checkpoint": "after-draw",
            "target": {"width": self.width, "height": self.height},
            "capture": {"raw_file": self.raw.name},
        }

    def test_pixel_equality_is_not_opengl_equivalence(self) -> None:
        reference = self.root / "identical.png"
        live_gfx._write_png(reference, self.bgra, self.width, self.height)
        result = live_gfx._compare_reference(reference, self.checkpoint, self.root)
        self.assertEqual(result["status"], "compared")
        self.assertTrue(result["comparable"])
        self.assertTrue(result["pixels_identical"])
        self.assertFalse(result["authenticated_opengl"])
        self.assertEqual(result["mismatched_pixels"], 0)
        self.assertNotIn("reference_matched", result)

    def test_alpha_only_difference_is_not_identical(self) -> None:
        from PIL import Image
        reference = self.root / "alpha-different.png"
        image = Image.frombytes(
            "RGBA", (self.width, self.height),
            live_gfx._bgra_to_rgba(self.bgra),
        )
        pixels = list(image.getdata())
        pixels[0] = (pixels[0][0], pixels[0][1], pixels[0][2], 0)
        image.putdata(pixels)
        image.save(reference)
        result = live_gfx._compare_reference(reference, self.checkpoint, self.root)
        self.assertEqual(result["status"], "compared")
        self.assertFalse(result["pixels_identical"])
        self.assertEqual(result["mismatched_pixels"], 1)
        self.assertEqual(result["maximum_channel_error"], 255)
        self.assertFalse(result["authenticated_opengl"])
        diff = Image.open(result["difference_image"]).convert("RGBA")
        diff_pixels = list(diff.getdata())
        self.assertIn((255, 0, 255, 255), diff_pixels)
        self.assertNotEqual(diff_pixels[0], (0, 0, 0, 255))

    def test_differing_reference_reports_metrics(self) -> None:
        from PIL import Image
        reference = self.root / "different.png"
        image = Image.frombytes(
            "RGBA", (self.width, self.height),
            live_gfx._bgra_to_rgba(self.bgra),
        )
        pixels = list(image.getdata())
        pixels[0] = (0, 255, 0, 255)
        image.putdata(pixels)
        image.save(reference)
        result = live_gfx._compare_reference(reference, self.checkpoint, self.root)
        self.assertEqual(result["status"], "compared")
        self.assertFalse(result["pixels_identical"])
        self.assertGreater(result["mismatched_pixels"], 0)
        self.assertGreater(result["maximum_channel_error"], 0)
        self.assertTrue(Path(result["difference_image"]).is_file())


if __name__ == "__main__":
    unittest.main()
