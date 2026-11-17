"""Pixel-level first-divergence localization used by M7 diagnostics."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from automation import m6_compare  # noqa: E402


class PixelLocationTests(unittest.TestCase):
    def test_first_differing_pixel_and_bounds_are_reported(self):
        width, height = 8, 6
        reference = bytes(4) * (width * height)
        candidate = bytearray(reference)
        # Two differing pixels: (x=5, y=1) and (x=2, y=4).
        for x, y in ((5, 1), (2, 4)):
            offset = (y * width + x) * 4
            candidate[offset:offset + 4] = bytes((10, 20, 30, 255))
        statistics = m6_compare.compare_images(
            reference, bytes(candidate), width, height)
        self.assertEqual(statistics["mismatched_pixels"], 2)
        self.assertEqual(
            statistics["first_differing_pixel"],
            {"x": 5, "y": 1, "offset": 1 * width + 5})
        self.assertEqual(
            statistics["differing_bounds"],
            {"x": 2, "y": 1, "width": 4, "height": 4})

    def test_matching_images_have_no_pixel_location(self):
        body = bytes(4) * 16
        statistics = m6_compare.compare_images(body, body, 4, 4)
        self.assertEqual(statistics["mismatched_pixels"], 0)
        self.assertIsNone(statistics["first_differing_pixel"])
        self.assertIsNone(statistics["differing_bounds"])


if __name__ == "__main__":
    unittest.main()
