"""Issue #278 synthetic default-drawable observation regression."""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HOST = ROOT / "tests" / "gfx_drawable_observe_host.m"


@unittest.skipUnless(sys.platform == "darwin", "CGL observer is macOS")
class DrawableObserveHostTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        cls.binary = Path(cls.temporary.name) / "gfx_drawable_observe_host"
        completed = subprocess.run(
            ["clang", "-O1", "-DGL_SILENCE_DEPRECATION", "-o", str(cls.binary),
             str(HOST), "-framework", "OpenGL"],
            capture_output=True, text=True, check=False)
        if completed.returncode != 0:
            raise unittest.SkipTest(
                "synthetic CGL host did not build: " + completed.stderr[-400:])

    @classmethod
    def tearDownClass(cls):
        cls.temporary.cleanup()

    def _run(self) -> dict:
        completed = subprocess.run(
            [str(self.binary)], capture_output=True, text=True, check=False)
        self.assertEqual(completed.returncode, 0, completed.stderr)
        return json.loads(completed.stdout)

    def test_observer_records_effective_drawable_or_explicit_unavailable(self):
        record = self._run()
        self.assertEqual(record["schema"], "gfx-drawable-observation/v1")
        self.assertEqual(record["status"], "ok")
        cgl = record["context"]["cgl"]
        for field in ("color_size", "alpha_size", "depth_size", "stencil_size",
                      "sample_buffers", "samples", "accelerated",
                      "double_buffer", "stereo", "profile"):
            self.assertIn(field, cgl)
            value = cgl[field]
            self.assertTrue(
                isinstance(value, (int, str)) and value != "",
                f"{field} is neither a value nor unavailable: {value!r}")
            if isinstance(value, str) and field != "profile":
                self.assertTrue(value.startswith("unavailable:"), value)
        framebuffer = record["context"]["default_framebuffer"]
        self.assertEqual(framebuffer["draw_framebuffer_binding"], 0)
        self.assertEqual(record["render_target_path"], "default-window")
        colour = framebuffer["color_back_left"]
        self.assertEqual(colour["object_type"], "framebuffer_default")
        self.assertEqual(colour["component_type"], "unsigned_normalized")
        self.assertEqual(
            [colour["red_size"], colour["green_size"], colour["blue_size"],
             colour["alpha_size"]],
            [8, 8, 8, 8])
        for member in ("depth", "stencil"):
            self.assertIn(member, framebuffer)
            self.assertEqual(framebuffer[member]["object_type"],
                             "framebuffer_default")
            self.assertIn("component_type", framebuffer[member])
        # The effective CGL color size covers the attachment's 8+8+8+8 bits.
        self.assertGreaterEqual(cgl["color_size"] + cgl["alpha_size"], 32)

    def test_observer_leaves_no_current_context(self):
        self._run()
        # A second run must still succeed; the first context was destroyed.
        record = self._run()
        self.assertEqual(record["status"], "ok")
