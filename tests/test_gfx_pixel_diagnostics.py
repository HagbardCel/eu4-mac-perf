"""Pixel-level first-divergence localization used by M7 diagnostics."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from automation import gfx_frame_pixels  # noqa: E402


class PixelLocationTests(unittest.TestCase):
    def test_first_differing_pixel_and_bounds_are_reported(self):
        width, height = 8, 6
        reference = bytes(4) * (width * height)
        candidate = bytearray(reference)
        # Two differing pixels: (x=5, y=1) and (x=2, y=4).
        for x, y in ((5, 1), (2, 4)):
            offset = (y * width + x) * 4
            candidate[offset:offset + 4] = bytes((10, 20, 30, 255))
        statistics = gfx_frame_pixels.compare_images(
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
        statistics = gfx_frame_pixels.compare_images(body, body, 4, 4)
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
        statistics = gfx_frame_pixels.compare_images(
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
                start, span = gfx_frame_pixels._grid_span(index, size, 8)
                covered.extend(range(start, start + span))
            self.assertEqual(covered, list(range(size)),
                             f"size {size} is not partitioned by the grid")

    def test_exact_corner_rectangles_follow_the_bin_assignment(self):
        """Literals for a non-multiple size: each corner has its exact bin."""
        width, height = 17, 11
        cases = {
            "top-left": ((0, 0), (0, 0, 3, 2)),
            "top-right": ((width - 1, 0), (15, 0, 2, 2)),
            "bottom-left": ((0, height - 1), (0, 10, 3, 1)),
            "bottom-right": ((width - 1, height - 1), (15, 10, 2, 1)),
        }
        for label, ((x, y), expected) in cases.items():
            with self.subTest(edge=label):
                statistics = self._compare_single(width, height, x, y)[0]
                self.assertEqual(
                    statistics["differing_bounds"],
                    {"x": x, "y": y, "width": 1, "height": 1})
                region = self._region_containing(statistics, x, y)
                self.assertEqual(
                    (region["x"], region["y"], region["width"],
                     region["height"]), expected)
                # The reported rectangle is the inverse of the pixel's bin.
                self.assertEqual(region["column"], x * 8 // width)
                self.assertEqual(region["row"], y * 8 // height)
                self.assertEqual(
                    (region["x"], region["width"]),
                    gfx_frame_pixels._grid_span(region["column"], width, 8))
                self.assertEqual(
                    (region["y"], region["height"]),
                    gfx_frame_pixels._grid_span(region["row"], height, 8))
                # Bin membership is monotonic, so the rectangle's centre also
                # belongs to it; an off-by-one span would move the centre.
                centre_x = region["x"] + region["width"] // 2
                centre_y = region["y"] + region["height"] // 2
                self.assertEqual(centre_x * 8 // width, region["column"])
                self.assertEqual(centre_y * 8 // height, region["row"])

    def test_one_pixel_frame_has_one_exact_region(self):
        reference = bytes(4)
        candidate = bytes((9, 8, 7, 3))
        statistics = gfx_frame_pixels.compare_images(
            reference, candidate, 1, 1)
        self.assertEqual(statistics["pixels"], 1)
        self.assertEqual(statistics["mismatched_pixels"], 1)
        self.assertEqual(statistics["alpha_different_pixels"], 1)
        self.assertEqual(statistics["differing_bounds"],
                         {"x": 0, "y": 0, "width": 1, "height": 1})
        self.assertEqual(len(statistics["regions"]), 1)
        self.assertEqual(
            (statistics["regions"][0]["x"], statistics["regions"][0]["y"],
             statistics["regions"][0]["width"],
             statistics["regions"][0]["height"]), (0, 0, 1, 1))
        self.assertEqual(statistics["regions"][0]["mean_max_channel_error"], 9.0)

    def test_zero_differing_pixels_has_no_bounds_or_failing_region(self):
        width, height = 17, 11
        reference = bytes(4) * (width * height)
        statistics = gfx_frame_pixels.compare_images(
            reference, reference, width, height)
        self.assertEqual(statistics["mismatched_pixels"], 0)
        self.assertEqual(statistics["alpha_different_pixels"], 0)
        self.assertEqual(statistics["max_channel_error"], 0)
        self.assertIsNone(statistics["first_differing_pixel"])
        self.assertIsNone(statistics["differing_bounds"])
        self.assertTrue(all(region["mean_max_channel_error"] == 0
                            for region in statistics["regions"]))
        self.assertIsNone(gfx_frame_pixels._first_differing_region(
            statistics["regions"]))

    def test_alpha_only_difference_is_counted_and_localized(self):
        """An alpha-only delta must not disappear behind equal RGB channels."""
        width, height = 17, 11
        x, y = width - 1, height - 1
        reference = bytes(4) * (width * height)
        candidate = bytearray(reference)
        offset = (y * width + x) * 4
        # BGR unchanged; alpha differs by three, exactly like the pilot's
        # RGB-only false negative.
        candidate[offset:offset + 4] = bytes((0, 0, 0, 3))
        statistics = gfx_frame_pixels.compare_images(
            reference, bytes(candidate), width, height)
        self.assertEqual(statistics["mismatched_pixels"], 1)
        self.assertEqual(statistics["alpha_different_pixels"], 1)
        self.assertEqual(statistics["max_channel_error"], 3)
        self.assertEqual(statistics["mean_abs_error"],
                         [0.0, 0.0, 0.0, round(3 / (width * height), 9)])
        self.assertEqual(statistics["differing_bounds"],
                         {"x": x, "y": y, "width": 1, "height": 1})
        region = self._region_containing(statistics, x, y)
        self.assertEqual(region["column"], x * 8 // width)
        self.assertEqual(region["row"], y * 8 // height)
        self.assertEqual(
            (region["x"], region["y"], region["width"], region["height"]),
            (15, 10, 2, 1))
        # The two-pixel bin's mean includes its unchanged neighbour.
        self.assertEqual(region["mean_max_channel_error"], 1.5)

    def test_every_returned_region_is_the_inverse_of_its_bin(self):
        for width, height in ((1, 1), (7, 3), (9, 9), (17, 11), (64, 64)):
            with self.subTest(width=width, height=height):
                body = bytes(4) * (width * height)
                statistics = gfx_frame_pixels.compare_images(
                    body, body, width, height)
                regions = {
                    (region["row"], region["column"]): region
                    for region in statistics["regions"]}
                self.assertEqual(len(regions), len(statistics["regions"]),
                                 "a bin was reported twice")
                occupied = {
                    (y * 8 // height, x * 8 // width)
                    for y in range(height) for x in range(width)}
                self.assertEqual(set(regions), occupied)
                for (row, column), region in regions.items():
                    self.assertEqual(
                        (region["x"], region["width"]),
                        gfx_frame_pixels._grid_span(column, width, 8))
                    self.assertEqual(
                        (region["y"], region["height"]),
                        gfx_frame_pixels._grid_span(row, height, 8))
                    for x in range(region["x"],
                                   region["x"] + region["width"]):
                        self.assertEqual(x * 8 // width, column)
                    for y in range(region["y"],
                                   region["y"] + region["height"]):
                        self.assertEqual(y * 8 // height, row)
                # Spans partition each axis, so rectangles never overlap.
                ordered = sorted(statistics["regions"],
                                 key=lambda item: (item["x"], item["y"]))
                for index, first in enumerate(ordered):
                    for second in ordered[index + 1:]:
                        overlap_x = (first["x"] < second["x"] + second["width"]
                                     and second["x"] < first["x"] + first["width"])
                        overlap_y = (first["y"] < second["y"] + second["height"]
                                     and second["y"] < first["y"] + first["height"])
                        self.assertFalse(overlap_x and overlap_y,
                                         (first, second))


if __name__ == "__main__":
    unittest.main()
