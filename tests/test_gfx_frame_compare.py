"""PR 3: semantic alignment and normalized pixel/region comparison."""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from automation import gfx_align, gfx_frame_compare  # noqa: E402

try:
    import numpy as np
except Exception:  # noqa: BLE001
    np = None


def bgra(image: "np.ndarray") -> bytes:
    """Encode top-left RGBA as lower-left BGRA (the GL on-disk convention)."""
    rgba = image[::-1, :, :]
    return rgba[:, :, [2, 1, 0, 3]].tobytes()


def observation(index: int, *, signature: str = "0xabc", draws: int = 2,
                width: int = 8, height: int = 6, scene: str | None = None,
                origin: str = "top_left") -> dict:
    value = {
        "frame_index": index,
        "width": width,
        "height": height,
        "origin": origin,
        "caller_signature": signature,
        "draws": draws,
        "sha256": f"{index:064x}",
    }
    if scene:
        value["scene"] = scene
    return value


@unittest.skipUnless(np is not None, "numpy is required")
class PixelComparisonTests(unittest.TestCase):
    def image(self, value=20, *, width=8, height=6):
        return np.full((height, width, 4), value, dtype=np.uint8)

    def policy(self, **overrides):
        value = {
            "policy": "exact",
            "pixel_tolerance": 0,
            "max_mismatched_fraction": 0.0,
            "max_mean_abs_error": 0.0,
            "allow_scale_normalization": True,
        }
        value.update(overrides)
        return value

    def test_identical_frames_pass_exact(self):
        image = self.image()
        result = gfx_frame_compare.compare_frames(
            image, image.copy(), policy=self.policy(), regions={}
        )
        self.assertEqual(result["status"], "pass")
        self.assertEqual(result["statistics"]["mismatched_pixels"], 0)

    def test_single_pixel_difference_is_reported(self):
        reference = self.image()
        candidate = reference.copy()
        candidate[2, 3, 1] = 40
        result = gfx_frame_compare.compare_frames(
            reference, candidate, policy=self.policy(), regions={}
        )
        self.assertEqual(result["status"], "fail")
        self.assertEqual(result["statistics"]["mismatched_pixels"], 1)
        self.assertEqual(result["statistics"]["max_channel_error"], 20)
        self.assertEqual(result["statistics"]["difference_bbox"], [2, 3, 3, 4])

    def test_tolerant_policy_accepts_a_small_numeric_difference(self):
        reference = self.image()
        candidate = reference.copy()
        candidate[2, 3, 1] = 22
        result = gfx_frame_compare.compare_frames(
            reference, candidate,
            policy=self.policy(policy="tolerant", pixel_tolerance=4,
                               max_mismatched_fraction=0.01, max_mean_abs_error=1.0),
            regions={},
        )
        self.assertEqual(result["status"], "pass")
        self.assertEqual(result["statistics"]["max_channel_error"], 2)

    def test_diagnostic_policy_reports_without_failing(self):
        reference = self.image()
        candidate = reference.copy()
        candidate[:, :, 0] = 200
        result = gfx_frame_compare.compare_frames(
            reference, candidate, policy=self.policy(policy="diagnostic"), regions={}
        )
        self.assertEqual(result["status"], "diagnostic")
        self.assertTrue(result["would_fail"])

    def test_horizontal_mirroring_is_detected_in_a_text_region(self):
        reference = self.image(0)
        # An asymmetric "glyph" shape that is only plausible when mirrored.
        reference[1:3, 1:3, :3] = 255
        reference[3:5, 1:2, :3] = 180
        candidate = reference[:, ::-1, :].copy()
        regions = {"text": {"rect": [0, 6, 0, 8], "policy": "structural"}}
        result = gfx_frame_compare.compare_frames(
            reference, candidate,
            policy=self.policy(policy="tolerant", pixel_tolerance=0,
                               max_mismatched_fraction=1.0, max_mean_abs_error=255.0),
            regions=regions,
        )
        self.assertEqual(result["status"], "fail")
        self.assertIn("text", result["mirrored_regions"])
        self.assertTrue(result["regions"]["text"]["mirror"]["mirrored"])

    def test_scale_two_candidate_is_normalized_and_recorded(self):
        reference = self.image(30, width=4, height=4)
        divided = reference.reshape(2, 2, 2, 2, 4)  # block-constant source
        candidate = np.repeat(np.repeat(reference, 2, axis=0), 2, axis=1)
        result = gfx_frame_compare.compare_payloads(
            bgra(reference), {"width": 4, "height": 4, "origin": "lower_left",
                              "pixel_format": "BGRA8", "sha256": "a" * 64},
            candidate.copy().tobytes(),
            {"width": 8, "height": 8, "origin": "top_left",
             "pixel_format": "RGBA8", "sha256": "b" * 64},
            policy=self.policy(policy="tolerant", pixel_tolerance=0,
                               max_mismatched_fraction=0.0, max_mean_abs_error=0.0),
            regions={},
        )
        self.assertEqual(result["normalization"]["scale"], 2)
        self.assertEqual(result["normalization"]["method"], "area_average_downscale_x2")
        self.assertEqual(result["statistics"]["mismatched_pixels"], 0)
        self.assertEqual(result["status"], "pass")

    def test_exact_policy_does_not_pass_a_scaled_candidate_as_bit_identical(self):
        reference = self.image(30, width=4, height=4)
        candidate = np.repeat(np.repeat(reference, 2, axis=0), 2, axis=1)
        result = gfx_frame_compare.compare_frames(
            reference, gfx_frame_compare.downscale_area(candidate, 2),
            policy=self.policy(), regions={}, candidate_meta={"scale": 2},
        )
        # The arrays are identical after downscale, so this helper passes; the
        # payload-level path records the scale and marks exact unverified.
        payload = gfx_frame_compare.compare_payloads(
            bgra(reference), {"width": 4, "height": 4, "origin": "lower_left"},
            candidate.tobytes(), {"width": 8, "height": 8, "origin": "top_left"},
            policy=self.policy(), regions={},
        )
        self.assertEqual(payload["status"], "unverified")
        self.assertIn("bit-identical", " ".join(payload["reasons"]))

    def test_alpha_only_difference_is_visible(self):
        reference = self.image(0)
        candidate = reference.copy()
        candidate[0, 0, 3] = 99
        result = gfx_frame_compare.compare_frames(
            reference, candidate, policy=self.policy(), regions={}
        )
        self.assertEqual(result["statistics"]["alpha_mismatched_pixels"], 1)
        self.assertEqual(result["status"], "fail")

    def test_region_metrics_include_structure_and_mirror(self):
        reference = self.image(0)
        candidate = self.image(0)
        regions = {"ui": {"rect": [0, 3, 0, 8]}, "map": {"rect": [3, 6, 0, 8]}}
        result = gfx_frame_compare.compare_frames(
            reference, candidate, policy=self.policy(), regions=regions
        )
        self.assertEqual(set(result["regions"]), {"ui", "map"})
        self.assertIn("structural_correlation", result["regions"]["ui"])

    def test_non_integer_scale_is_unverified(self):
        reference = self.image(width=4, height=4)
        candidate = self.image(width=6, height=6)
        with self.assertRaises(gfx_frame_compare.CompareError):
            gfx_frame_compare.normalize_candidate(
                reference, candidate, policy=self.policy(), candidate_meta={}
            )

    def test_png_writer_emits_a_valid_header(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "difference.png"
            gfx_frame_compare.write_png(path, self.image())
            header = path.read_bytes()[:8]
        self.assertEqual(header, b"\x89PNG\r\n\x1a\n")


class AlignmentTests(unittest.TestCase):
    def sequences(self, count=4, *, signature="0xabc", draws=6, width=8, height=6):
        return (observation(index, signature=signature, draws=draws,
                            width=width, height=height)
                for index in range(1, count + 1))

    def test_identical_sequences_are_aligned(self):
        reference = list(self.sequences())
        candidate = list(self.sequences())
        result = gfx_align.align_sequences(reference, candidate)
        self.assertEqual(result["status"], gfx_align.ALIGNED)
        self.assertEqual(len(result["pairs"]), 4)
        self.assertTrue(all(pair["status"] == gfx_align.ALIGNED for pair in result["pairs"]))

    def test_different_scene_is_not_aligned(self):
        reference = list(self.sequences())
        candidate = [observation(index, signature="0xabc", draws=6,
                                 width=8, height=6, scene="menu")
                     for index in range(1, 5)]
        reference = [dict(row, scene="loading") for row in reference]
        result = gfx_align.align_sequences(reference, candidate)
        self.assertTrue(all(pair["status"] != gfx_align.ALIGNED for pair in result["pairs"]))

    def test_weak_match_is_ambiguous_not_aligned(self):
        reference = [observation(1, signature="0x111", draws=4, width=8, height=6)]
        candidate = [observation(1, signature="0x222", draws=4, width=8, height=6)]
        result = gfx_align.align_sequences(reference, candidate)
        self.assertEqual(result["pairs"][0]["status"], gfx_align.AMBIGUOUS)

    def test_unrelated_extent_is_not_aligned(self):
        reference = [observation(1, signature="0xabc", draws=4, width=8, height=6)]
        candidate = [observation(1, signature="0xabc", draws=4, width=9, height=7)]
        result = gfx_align.align_sequences(reference, candidate)
        self.assertEqual(result["pairs"][0]["status"], gfx_align.NOT_ALIGNED)

    def test_missing_side_is_reported(self):
        result = gfx_align.align_sequences([], [observation(1)])
        self.assertEqual(result["status"], gfx_align.MISSING)

    def test_scale_two_is_accepted_as_a_normalized_extent(self):
        reference = [observation(1, width=8, height=6)]
        candidate = [observation(1, width=16, height=12)]
        result = gfx_align.align_sequences(reference, candidate)
        self.assertEqual(result["pairs"][0]["extent"]["scale"], 2)
        self.assertEqual(result["pairs"][0]["status"], gfx_align.ALIGNED)

    def test_same_composition_is_a_consistency_case(self):
        # OpenGL-vs-OpenGL / Metal-vs-Metal: identical observations must align
        # and compare exactly.
        reference = list(self.sequences(5))
        candidate = list(self.sequences(5))
        alignment = gfx_align.align_sequences(reference, candidate)
        self.assertEqual(alignment["aligned"], 5)
        self.assertEqual(alignment["ambiguous"], 0)
        if np is not None:
            image = np.full((6, 8, 4), 40, dtype=np.uint8)
            diff = gfx_frame_compare.compare_frames(
                image, image.copy(), policy={"policy": "exact"}, regions={}
            )
            self.assertEqual(diff["status"], "pass")


if __name__ == "__main__":
    unittest.main()
