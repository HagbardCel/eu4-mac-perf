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

    @staticmethod
    def _region_containing(statistics: dict, x: int, y: int) -> dict:
        for region in statistics["regions"]:
            if (region["x"] <= x < region["x"] + region["width"]
                    and region["y"] <= y < region["y"] + region["height"]):
                return region
        raise AssertionError(
            f"no returned region contains ({x}, {y}): "
            f"{statistics['regions']}")

    def _compare_single(self, width: int, height: int,
                        x: int, y: int) -> tuple[dict, int]:
        reference = bytes(4) * (width * height)
        candidate = bytearray(reference)
        offset = (y * width + x) * 4
        candidate[offset:offset + 4] = bytes((9, 8, 7, 0))
        statistics = m6_compare.compare_images(
            reference, bytes(candidate), width, height)
        # The exact global statistics are independent of localization.
        self.assertEqual(statistics["pixels"], width * height)
        self.assertEqual(statistics["mismatched_pixels"], 1)
        self.assertEqual(statistics["max_channel_error"], 9)
        return statistics, offset

    def test_odd_width_last_column_region_contains_the_pixel(self):
        statistics, _ = self._compare_single(9, 1, 8, 0)
        region = self._region_containing(statistics, 8, 0)
        self.assertEqual(
            (region["x"], region["y"], region["width"], region["height"]),
            (8, 0, 1, 1))
        self.assertEqual(statistics["differing_bounds"],
                         {"x": 8, "y": 0, "width": 1, "height": 1})

    def test_odd_height_last_row_region_contains_the_pixel(self):
        statistics, _ = self._compare_single(1, 9, 0, 8)
        region = self._region_containing(statistics, 0, 8)
        self.assertEqual(
            (region["x"], region["y"], region["width"], region["height"]),
            (0, 8, 1, 1))
        self.assertEqual(statistics["differing_bounds"],
                         {"x": 0, "y": 8, "width": 1, "height": 1})

    def test_bottom_edge_region_contains_the_pixel_at_full_height(self):
        width, height = 1728, 1117
        statistics, _ = self._compare_single(width, height, width - 1,
                                             height - 1)
        region = self._region_containing(statistics, width - 1, height - 1)
        self.assertGreater(region["width"], 0)
        self.assertGreater(region["height"], 0)
        self.assertEqual(statistics["differing_bounds"],
                         {"x": width - 1, "y": height - 1,
                          "width": 1, "height": 1})

    def test_grid_spans_partition_every_coordinate(self):
        for size in (1, 7, 9, 1117):
            covered = []
            for index in range(8):
                start, span = m6_compare._grid_span(index, size, 8)
                covered.extend(range(start, start + span))
            self.assertEqual(covered, list(range(size)),
                             f"size {size} is not partitioned by the grid")


if __name__ == "__main__":
    unittest.main()
