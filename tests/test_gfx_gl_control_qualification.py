"""Original-API GL control qualification before any Metal blame."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from automation import gfx_gl_control_qualification as qualification


class ControlQualificationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.live = self.root / "live.bgra"
        self.control = self.root / "control.bgra"
        self.base = bytes([10, 20, 30, 255]) * (8 * 8)
        self.live.write_bytes(self.base)
        self.control.write_bytes(self.base)
        self.drawable = {"observed": True, "samples": 0,
                         "sample_buffers": 0, "depth_bits": 24,
                         "stencil_bits": 8, "red_bits": 8, "green_bits": 8,
                         "blue_bits": 8, "alpha_bits": 8}
        self.prestate = {"depth_clear": 1.0, "stencil_clear": 0,
                         "color": [0, 0, 0, 1]}

    def tearDown(self):
        self.temporary.cleanup()

    def test_exact_control_is_qualified_and_can_blame_metal(self):
        value = qualification.qualify(
            self.live, self.control, 8, 8, drawable=self.drawable,
            prestate=self.prestate)
        self.assertEqual(value["status"], "qualified")
        self.assertTrue(value["qualified"])
        self.assertTrue(value["metal_blame_allowed"])
        self.assertEqual(value["statistics"]["mismatched_pixels"], 0)
        self.assertEqual(value["missing_observation"], [])
        self.assertTrue(qualification.attribution(value)["allowed"])

    def test_four_pixel_residual_is_not_parity(self):
        body = bytearray(self.base)
        for offset in (0, 4, 8 * 8 * 4 - 4, 8 * 8 * 4 - 8):
            body[offset] ^= 1
        self.control.write_bytes(bytes(body))
        value = qualification.qualify(
            self.live, self.control, 8, 8, drawable=self.drawable,
            prestate=self.prestate)
        self.assertEqual(value["status"], "unqualified")
        self.assertEqual(value["statistics"]["mismatched_pixels"], 4)
        self.assertFalse(value["metal_blame_allowed"])
        verdict = qualification.attribution(value)
        self.assertFalse(verdict["allowed"])
        self.assertIn("not exact", verdict["reason"])

    def test_alpha_only_residual_is_unqualified(self):
        body = bytearray(self.base)
        body[3] = 254
        self.control.write_bytes(bytes(body))
        value = qualification.qualify(
            self.live, self.control, 8, 8, drawable=self.drawable,
            prestate=self.prestate)
        self.assertFalse(value["qualified"])
        self.assertEqual(value["statistics"]["alpha_different_pixels"], 1)

    def test_missing_drawable_and_prestate_are_named(self):
        body = bytearray(self.base)
        body[0] ^= 1
        self.control.write_bytes(bytes(body))
        value = qualification.qualify(self.live, self.control, 8, 8)
        self.assertFalse(value["qualified"])
        self.assertTrue(any("drawable" in item
                            for item in value["missing_observation"]))
        self.assertTrue(any("pre-draw" in item
                            for item in value["missing_observation"]))
        self.assertIn("drawable", value["next_discriminator"])

    def test_size_mismatch_fails_closed(self):
        self.control.write_bytes(b"\x00" * 4)
        with self.assertRaises(qualification.QualificationError):
            qualification.qualify(self.live, self.control, 8, 8)

    def test_cli_reports_unqualified_with_a_nonzero_exit(self):
        body = bytearray(self.base)
        body[0] ^= 1
        self.control.write_bytes(bytes(body))
        output = self.root / "result.json"
        code = qualification.main([
            str(self.live), str(self.control), "--width", "8",
            "--height", "8", "--out", str(output)])
        self.assertEqual(code, 1)
        value = json.loads(output.read_text())
        self.assertEqual(value["status"], "unqualified")


if __name__ == "__main__":
    unittest.main()
