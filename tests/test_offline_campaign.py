"""End-to-end offline 100-frame campaign with a controlled defect (PR 3/4).

No EU4 launch: the fixture produces the same logical observations, normalized
frames and difference a live campaign would, so alignment, comparison, first
failure preservation and the diagnostic package can be exercised deterministically.
"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from automation import gfx_align, gfx_case, gfx_frame_compare  # noqa: E402

try:
    import numpy as np
except Exception:  # noqa: BLE001
    np = None

WIDTH = 64
HEIGHT = 48
DEFECT_FRAME = 57


@unittest.skipUnless(np is not None, "numpy is required")
class OfflineCampaignTests(unittest.TestCase):
    def setUp(self):
        self._temporary = tempfile.TemporaryDirectory()
        self.root = Path(self._temporary.name)

    def tearDown(self):
        self._temporary.cleanup()

    def frame_image(self, index: int) -> "np.ndarray":
        image = np.zeros((HEIGHT, WIDTH, 4), dtype=np.uint8)
        image[:, :, 0] = (index * 3) % 255
        image[:, :, 1] = (index * 5) % 255
        image[:, :, 2] = 40
        image[:, :, 3] = 255
        # A small asymmetrical "glyph" region whose orientation matters.
        image[4:8, 2:10, :3] = 220
        image[8:11, 2:5, :3] = 160
        return image

    def observations(self, count: int) -> list[dict]:
        return [
            {
                "frame_index": index,
                "width": WIDTH,
                "height": HEIGHT,
                "origin": "top_left",
                "caller_signature": "0xabc",
                "draws": 20,
                "sha256": f"{index:064x}",
                "scene": "title",
            }
            for index in range(1, count + 1)
        ]

    def test_one_hundred_aligned_frames_and_first_defect_at_57(self):
        reference_observations = self.observations(100)
        candidate_observations = self.observations(100)
        alignment = gfx_align.align_sequences(reference_observations,
                                              candidate_observations)
        self.assertEqual(alignment["aligned"], 100)
        self.assertEqual(alignment["ambiguous"], 0)

        policy = {
            "policy": "tolerant",
            "pixel_tolerance": 8,
            "max_mismatched_fraction": 0.005,
            "max_mean_abs_error": 1.0,
            "allow_scale_normalization": True,
        }
        regions = {"text": {"rect": [0, 16, 0, 24], "policy": "structural"}}
        compared = 0
        first_failure = None
        for index in range(1, 101):
            reference = self.frame_image(index)
            candidate = reference.copy()
            if index == DEFECT_FRAME:
                # Mirrored glyph region: the historical Credits/Options defect.
                candidate[0:16, 0:24, :3] = reference[0:16, 0:24, :3][:, ::-1, :]
            result = gfx_frame_compare.compare_frames(
                reference, candidate, policy=policy, regions=regions)
            compared += 1
            if result["status"] == "fail":
                first_failure = (index, result)
                break
        self.assertEqual(compared, DEFECT_FRAME)
        self.assertIsNotNone(first_failure)
        index, result = first_failure
        self.assertEqual(index, DEFECT_FRAME)
        self.assertIn("text", result["mirrored_regions"])
        self.assertTrue(result["regions"]["text"]["mirror"]["mirrored"])

    def test_first_defect_builds_a_replayable_case(self):
        run = self.root / "run"
        (run / "reference").mkdir(parents=True)
        (run / "candidate").mkdir(parents=True)
        reference = self.frame_image(DEFECT_FRAME)
        candidate = reference.copy()
        candidate[0:16, 0:24, :3] = reference[0:16, 0:24, :3][:, ::-1, :]
        reference_path = run / "reference" / f"frame-{DEFECT_FRAME}.bgra"
        reference_path.write_bytes(reference[::-1, :, [2, 1, 0, 3]].tobytes())
        candidate_path = run / "candidate" / f"frame-{DEFECT_FRAME}.bgra"
        candidate_path.write_bytes(candidate[:, :, [2, 1, 0, 3]].tobytes())
        reference_meta = {
            "backend": "opengl", "frame_index": DEFECT_FRAME, "width": WIDTH,
            "height": HEIGHT, "origin": "lower_left", "pixel_format": "BGRA8",
            "raw_file": str(reference_path), "sha256": "a" * 64,
            "caller_signature": "0xabc", "draws": 20,
        }
        candidate_meta = {
            "backend": "metal", "frame_index": DEFECT_FRAME, "width": WIDTH,
            "height": HEIGHT, "origin": "top_left", "pixel_format": "BGRA8",
            "raw_file": str(candidate_path), "sha256": "b" * 64,
            "caller_signature": "0xabc", "draws": 20,
        }
        comparison = gfx_frame_compare.compare_payloads(
            reference_path.read_bytes(), reference_meta,
            candidate_path.read_bytes(), candidate_meta,
            policy={"policy": "tolerant", "pixel_tolerance": 8,
                    "max_mismatched_fraction": 0.005,
                    "max_mean_abs_error": 1.0,
                    "allow_scale_normalization": True},
            regions={"text": {"rect": [0, 16, 0, 24], "policy": "structural"}},
        )
        pair = {
            "frame_index": DEFECT_FRAME,
            "candidate_frame_index": DEFECT_FRAME,
            "status": "aligned",
            "dumps": {
                "reference": {"found": {str(DEFECT_FRAME): reference_meta}},
                "candidate": {"found": {str(DEFECT_FRAME): candidate_meta}},
            },
            "comparison": comparison,
        }
        result = {
            "schema": "gfx-dual-run/v1",
            "session_id": "offline-100",
            "scenario": {"name": "offline", "digest": "d" * 64,
                         "comparison": {"policy": "tolerant",
                                        "max_channel_error": 8,
                                        "max_mismatched_fraction": 0.005,
                                        "max_mean_abs_error": 1.0,
                                        "regions": {"text": {"rect": [0, 16, 0, 24]}}}},
            "health": {"verdict": "unknown", "first_failure": None,
                       "instrumentation_failures": []},
            "frames": {"comparison": {"status": "fail", "first_failure": pair},
                       "last_pair": pair, "aligned": 100, "paired": 100},
            "stop": {"reason": "frame_difference"},
            "artifacts": {"bytes": 100, "max_bytes": 1000, "dropped": 0},
            "processes": {}, "ledger": {}, "preflight": {}, "terminal": {},
        }
        (run / "result.json").write_text(json.dumps(result))
        (run / "session.json").write_text(json.dumps({"session_id": "offline-100"}))
        packaged = gfx_case.build_case(run)
        self.assertEqual(packaged["classification"], gfx_case.CONFIRMED_RENDERING)
        replay = gfx_case.compare_case(Path(packaged["case_dir"]))
        self.assertEqual(replay["status"], "fail")
        analysis = (Path(packaged["case_dir"]) / "analysis.md").read_text()
        self.assertIn("horizontal mirroring", analysis.lower())


if __name__ == "__main__":
    unittest.main()
