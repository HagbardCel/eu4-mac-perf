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

    def test_exact_scaled_difference_is_still_a_difference(self):
        reference = self.image(30, width=4, height=4)
        candidate = np.repeat(np.repeat(reference, 2, axis=0), 2, axis=1)
        candidate[0, 0, :3] = 200
        result = gfx_frame_compare.compare_payloads(
            bgra(reference), {"width": 4, "height": 4, "origin": "lower_left"},
            candidate.tobytes(), {"width": 8, "height": 8, "origin": "top_left"},
            policy=self.policy(), regions={},
        )
        self.assertEqual(result["status"], "fail")
        self.assertEqual(result["pixel_verdict"], "different")
        self.assertFalse(result["exact_qualified"])

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

    def test_region_policy_can_reject_a_small_text_difference(self):
        reference = self.image(0, width=32, height=16)
        candidate = reference.copy()
        # A tiny glyph region differs while the global fraction stays low.
        candidate[2:6, 2:10, :3] = 200
        policy = self.policy(policy="tolerant", pixel_tolerance=0,
                             max_mismatched_fraction=0.10,
                             max_mean_abs_error=255.0)
        regions = {
            "text": {"rect": [0, 8, 0, 16], "policy": "tolerant",
                     "max_mismatched_fraction": 0.0},
            "map": {"rect": [8, 16, 0, 32], "policy": "ignore"},
        }
        result = gfx_frame_compare.compare_frames(
            reference, candidate, policy=policy, regions=regions)
        self.assertEqual(result["status"], "fail")
        self.assertTrue(any("text" in failure for failure in result["region_failures"]))

    def test_structural_region_failure_is_reported(self):
        reference = self.image(0, width=16, height=16)
        candidate = np.zeros_like(reference)
        result = gfx_frame_compare.compare_frames(
            reference, candidate,
            policy=self.policy(policy="tolerant", pixel_tolerance=255,
                               max_mismatched_fraction=1.0,
                               max_mean_abs_error=255.0),
            regions={"map": {"rect": [0, 16, 0, 16], "policy": "structural",
                             "min_structural_correlation": 0.9}},
        )
        self.assertEqual(result["status"], "fail")
        self.assertIn("map", " ".join(result["region_failures"]))

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


@unittest.skipUnless(np is not None, "numpy is required")
class TemporalDiagnosticsTests(unittest.TestCase):
    """Phase 3: reusable consecutive-frame and cross-process diagnostics."""

    def frame(self, index: int, image, *, role: str = "reference") -> dict:
        return {
            "frame_index": index,
            "image": image,
            "sha256": f"{role}{index:060d}",
            "identity": {"role": role, "game_day": 56456976,
                         "update_identity": "0xd09cad25b3166452"},
        }

    def images(self):
        base = np.full((6, 8, 4), 20, dtype=np.uint8)
        base[:, :, 3] = 255
        # Reference 74 -> 75 changes 12 of 48 pixels.
        ref75 = base.copy()
        for column in range(4):
            ref75[0, column, 0] = 200
        for column in range(4):
            ref75[5, column, 0] = 200
        for column in range(4):
            ref75[2, column, 1] = 200
        # Reference 75 -> 76 changes a further 30 pixels.
        ref76 = ref75.copy()
        for row in range(3):
            for column in range(8):
                ref76[row + 3, column, 2] = 180
        # Candidate 74 differs from reference 74 on 4 pixels, all of which are
        # part of the reference's own 74 -> 75 variation.
        cand74 = base.copy()
        for column in range(4):
            cand74[0, column, 0] = 160
        # Candidate 74 -> 75 changes 8 more pixels.
        cand75 = cand74.copy()
        for column in range(4):
            cand75[1, column, 1] = 200
        return base, ref75, ref76, cand74, cand75

    def test_consecutive_reference_frames_can_differ_more_than_cross_process(self):
        base, ref75, ref76, cand74, cand75 = self.images()
        policy = {"policy": "tolerant", "pixel_tolerance": 8,
                  "max_mismatched_fraction": 1.0, "max_mean_abs_error": 255.0}
        value = gfx_frame_compare.compare_temporal(
            [self.frame(74, base), self.frame(75, ref75), self.frame(76, ref76)],
            [self.frame(74, cand74, role="candidate"),
             self.frame(75, cand75, role="candidate")],
            policy=policy, cross_pairs=[(74, 74)],
        )
        self.assertEqual(value["schema"], gfx_frame_compare.TEMPORAL_SCHEMA)
        self.assertEqual(value["status"], "completed")
        relations = [pair["relation"] for pair in value["pairs"]]
        self.assertEqual(relations, ["reference_temporal", "reference_temporal",
                                     "candidate_temporal", "cross_process"])
        by_relation = {(pair["relation"],
                        pair["reference"]["frame_index"],
                        pair["candidate"]["frame_index"]): pair
                       for pair in value["pairs"]}
        reference_74_75 = by_relation[("reference_temporal", 74, 75)]
        cross = by_relation[("cross_process", 74, 74)]
        self.assertEqual(reference_74_75["statistics"]["mismatched_pixels"], 12)
        self.assertEqual(cross["statistics"]["mismatched_pixels"], 4)
        self.assertGreater(reference_74_75["statistics"]["mismatched_fraction"],
                           cross["statistics"]["mismatched_fraction"])
        self.assertEqual(cross["statistics"]["difference_bbox"], [0, 1, 0, 4])
        # 74 -> 76 is not a required comparison; only consecutive pairs are.
        self.assertNotIn(("reference_temporal", 74, 76), by_relation)

    def test_cross_process_difference_overlaps_temporal_variability(self):
        base, ref75, _ref76, cand74, cand75 = self.images()
        policy = {"policy": "tolerant", "pixel_tolerance": 8,
                  "max_mismatched_fraction": 1.0, "max_mean_abs_error": 255.0}
        value = gfx_frame_compare.compare_temporal(
            [self.frame(74, base), self.frame(75, ref75)],
            [self.frame(74, cand74, role="candidate"),
             self.frame(75, cand75, role="candidate")],
            policy=policy, cross_pairs=[(74, 74)],
        )
        overlap = value["overlap"]
        self.assertEqual(overlap["cross"]["mask_pixels"], 4)
        reference_overlap = next(
            row for row in overlap["temporal"]
            if row["relation"] == "reference_temporal")
        self.assertEqual(reference_overlap["intersection_pixels"], 4)
        self.assertEqual(reference_overlap["cross_also_temporal_fraction"], 1.0)
        self.assertEqual(reference_overlap["jaccard"], 0.333333)

    def test_artifacts_are_optional_and_bounded(self):
        base, ref75, _ref76, cand74, cand75 = self.images()
        policy = {"policy": "diagnostic", "pixel_tolerance": 8}
        value = gfx_frame_compare.compare_temporal(
            [self.frame(74, base), self.frame(75, ref75)],
            [self.frame(74, cand74, role="candidate")],
            policy=policy, cross_pairs=[(74, 74)],
        )
        self.assertEqual(value["artifacts"], [])
        with tempfile.TemporaryDirectory() as temporary:
            value = gfx_frame_compare.compare_temporal(
                [self.frame(74, base), self.frame(75, ref75)],
                [self.frame(74, cand74, role="candidate")],
                policy=policy, cross_pairs=[(74, 74)],
                artifacts={"dir": Path(temporary) / "diff", "amplification": 4},
            )
            written = sorted((Path(temporary) / "diff").glob("*.png"))
            self.assertEqual(len(written), len(value["pairs"]))
            self.assertTrue(all(path.stat().st_size > 0 for path in written))

    def test_missing_cross_frame_is_explicit_not_invented(self):
        base, ref75, _ref76, cand74, _cand75 = self.images()
        value = gfx_frame_compare.compare_temporal(
            [self.frame(74, base), self.frame(75, ref75)],
            [self.frame(74, cand74, role="candidate")],
            policy={"policy": "tolerant", "pixel_tolerance": 8},
            cross_pairs=[(74, 999)],
        )
        self.assertEqual(value["status"], "incomplete")
        self.assertFalse(any(pair["relation"] == "cross_process"
                             for pair in value["pairs"]))
        self.assertTrue(any("missing" in entry["reason"]
                            for entry in value["skipped"]))
        self.assertIsNone(value["overlap"])

    def test_pair_bound_is_reported(self):
        base, ref75, ref76, cand74, cand75 = self.images()
        value = gfx_frame_compare.compare_temporal(
            [self.frame(74, base), self.frame(75, ref75), self.frame(76, ref76)],
            [self.frame(74, cand74, role="candidate"),
             self.frame(75, cand75, role="candidate")],
            policy={"policy": "diagnostic"}, max_pairs=2)
        self.assertEqual(len(value["pairs"]), 2)
        self.assertEqual(value["bounded"]["max_pairs"], 2)
        self.assertTrue(value["skipped"])

    def test_preview_hashes_cannot_establish_staticity(self):
        base = np.zeros((16, 16, 4), dtype=np.uint8)
        changed = base.copy()
        # One sub-threshold pixel per 8x8 preview block: the area average
        # rounds to the same 8-bit value, so the downscaled preview is
        # identical while the full-resolution frame is not.
        changed[0, 0, 0] = 4
        changed[8, 8, 0] = 4
        preview_base = gfx_frame_compare.downscale_area(base, 8)
        preview_changed = gfx_frame_compare.downscale_area(changed, 8)
        self.assertTrue(np.array_equal(preview_base, preview_changed))
        mask = gfx_frame_compare.difference_mask(base, changed, 0)
        self.assertEqual(int(mask.sum()), 2)
        # The staticity predicate reads full-frame digests, never previews.
        self.assertNotIn("preview_sha256", gfx_align.staticity_evidence({
            "reference": {"captured": [{"frame_index": 1, "sha256": "a"},
                                       {"frame_index": 2, "sha256": "a"}]},
            "candidate": {"captured": [{"frame_index": 1, "sha256": "b"},
                                       {"frame_index": 2, "sha256": "b"}]},
        }))
