"""PR 4: bounded flight recorder and offline diagnostic case packages."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from automation import gfx_case, gfx_flight  # noqa: E402

try:
    import numpy as np
except Exception:  # noqa: BLE001
    np = None


class FlightRecorderTests(unittest.TestCase):
    def test_bounds_and_freeze(self):
        recorder = gfx_flight.FlightRecorder(max_frames=2, max_events=2)
        for index in range(4):
            recorder.frame({"frame_index": index})
        for index in range(4):
            recorder.event("frame", "observed", index=index)
        snapshot = recorder.snapshot()
        self.assertEqual([row["frame_index"] for row in snapshot["frames"]], [2, 3])
        self.assertEqual(len(snapshot["events"]), 2)
        self.assertGreater(snapshot["dropped_frames"], 0)
        recorder.freeze("frame_difference")
        recorder.frame({"frame_index": 99})
        self.assertTrue(recorder.snapshot()["frozen"])
        self.assertEqual(recorder.snapshot()["freeze_reason"], "frame_difference")
        self.assertEqual(recorder.snapshot()["dropped_frames"], snapshot["dropped_frames"] + 1)

    def test_write_round_trip(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "flight.json"
            recorder = gfx_flight.FlightRecorder()
            recorder.event("health", "ok")
            recorder.write(path)
            value = json.loads(path.read_text())
        self.assertEqual(value["schema"], gfx_flight.SCHEMA)
        self.assertEqual(value["events"][0]["kind"], "health")


@unittest.skipUnless(np is not None, "numpy is required")
class CaseBuilderTests(unittest.TestCase):
    def setUp(self):
        self._temporary = tempfile.TemporaryDirectory()
        self.run = Path(self._temporary.name) / "run"
        (self.run / "reference").mkdir(parents=True)
        (self.run / "candidate").mkdir(parents=True)
        reference = np.full((6, 8, 4), 20, dtype=np.uint8)
        reference[0:2, 0:2, :3] = 200
        candidate = np.repeat(np.repeat(reference, 2, axis=0), 2, axis=1)
        self.reference_path = self.run / "reference" / "frame-4.bgra"
        self.reference_path.write_bytes(reference[::-1, :, [2, 1, 0, 3]].tobytes())
        self.candidate_path = self.run / "candidate" / "frame-4.bgra"
        self.candidate_path.write_bytes(candidate.tobytes())
        self.reference_meta = {
            "backend": "opengl", "frame_index": 4, "width": 8, "height": 6,
            "origin": "lower_left", "pixel_format": "BGRA8",
            "raw_file": str(self.reference_path), "sha256": "a" * 64,
            "caller_signature": "0xabc", "draws": 20,
        }
        self.candidate_meta = {
            "backend": "metal", "frame_index": 4, "width": 16, "height": 12,
            "origin": "top_left", "pixel_format": "BGRA8",
            "raw_file": str(self.candidate_path), "sha256": "b" * 64,
            "caller_signature": "0xabc", "draws": 20,
        }
        self.pair = {
            "frame_index": 4,
            "candidate_frame_index": 4,
            "status": "aligned",
            "dumps": {
                "reference": {"found": {"4": self.reference_meta}, "missing": []},
                "candidate": {"found": {"4": self.candidate_meta}, "missing": []},
            },
            "comparison": {
                "status": "fail",
                "reasons": ["max_channel_error 9 > 8"],
                "alignment": {"status": "aligned", "evidence": ["caller_signature"]},
                "statistics": {"pixels": 48, "mismatched_pixels": 0,
                               "mismatched_fraction": 0.0, "max_channel_error": 9,
                               "mean_abs_error": 1.0, "difference_bbox": None},
                "regions": {},
                "mirrored_regions": [],
            },
        }
        self.result = {
            "schema": "gfx-dual-run/v1",
            "session_id": "session-test",
            "scenario": {"name": "test", "digest": "d" * 64,
                         "comparison": {"policy": "tolerant", "max_channel_error": 8,
                                        "max_mismatched_fraction": 0.01,
                                        "max_mean_abs_error": 1.5, "regions": {}}},
            "build": {"candidate_library_sha256": "c" * 64},
            "processes": {"reference": {"pid": 1}, "candidate": {"pid": 2}},
            "health": {"verdict": "unknown", "first_failure": None,
                       "instrumentation_failures": []},
            "frames": {
                "comparison": {"status": "fail", "first_failure": self.pair},
                "last_pair": self.pair,
            },
            "stop": {"reason": "frame_difference", "trigger": "frame 4"},
            "artifacts": {"bytes": 100, "max_bytes": 1000, "dropped": 0},
            "ledger": {"tokens": ["a", "b"]},
            "preflight": {"writable": True},
            "terminal": {"status": "closed"},
        }
        (self.run / "result.json").write_text(json.dumps(self.result))
        (self.run / "session.json").write_text(json.dumps({"session_id": "session-test",
                                                           "profile": "targeted"}))

    def tearDown(self):
        self._temporary.cleanup()

    def test_build_and_replay(self):
        packaged = gfx_case.build_case(self.run)
        case_dir = Path(packaged["case_dir"])
        self.assertEqual(packaged["classification"], gfx_case.CONFIRMED_RENDERING)
        for name in ("manifest.json", "result.json", "analysis.md",
                     "frames/reference-frame-4.bgra",
                     "frames/candidate-frame-4.bgra",
                     "difference/difference.png",
                     "gpu/health.json", "repro/repro.json"):
            self.assertTrue((case_dir / name).is_file(), name)
        analysis = (case_dir / "analysis.md").read_text()
        self.assertIn("Confirmed observations", analysis)
        self.assertIn("Excluded explanations", analysis)
        self.assertIn("Remaining hypotheses", analysis)
        self.assertIn("Missing evidence", analysis)
        self.assertIn("Another EU4 launch", analysis)
        replay = gfx_case.compare_case(case_dir)
        self.assertIn(replay["status"], ("pass", "fail", "unverified"))
        inspected = gfx_case.inspect_case(case_dir)
        self.assertEqual(inspected["classification"], gfx_case.CONFIRMED_RENDERING)
        self.assertGreater(packaged["total_bytes"], 0)

    def test_tampered_packaged_frame_is_not_replayed_as_valid(self):
        packaged = gfx_case.build_case(self.run)
        case_dir = Path(packaged["case_dir"])
        frame = case_dir / "frames" / "reference-frame-4.bgra"
        payload = bytearray(frame.read_bytes())
        payload[0] ^= 0xFF
        frame.write_bytes(bytes(payload))
        replay = gfx_case.compare_case(case_dir)
        self.assertEqual(replay["status"], gfx_case.INCOMPLETE)
        self.assertIn("digest", replay["reason"])

    def test_pair_index_is_not_silently_ignored(self):
        with self.assertRaises(ValueError):
            gfx_case.build_case(self.run, pair_index=3)

    def test_missing_raw_frames_are_incomplete_evidence(self):
        pair = dict(self.pair, dumps={"reference": {"found": {}}, "candidate": {"found": {}}})
        self.result["frames"] = {"comparison": {"first_failure": pair}, "last_pair": pair}
        (self.run / "result.json").write_text(json.dumps(self.result))
        packaged = gfx_case.build_case(self.run)
        self.assertEqual(packaged["classification"], gfx_case.INCOMPLETE)

    def test_gpu_failure_is_classified_before_pixels(self):
        self.result["health"] = {"verdict": "failed",
                                 "first_failure": {"kind": "draw_refused"},
                                 "instrumentation_failures": []}
        (self.run / "result.json").write_text(json.dumps(self.result))
        packaged = gfx_case.build_case(self.run)
        self.assertEqual(packaged["classification"], gfx_case.GPU_FAILURE)

    def test_unaligned_pair_is_not_a_rendering_difference(self):
        pair = dict(self.pair, comparison={"status": "not_compared",
                                           "reason": "alignment is ambiguous"})
        self.result["frames"] = {"comparison": {"first_failure": None}, "last_pair": pair}
        (self.run / "result.json").write_text(json.dumps(self.result))
        packaged = gfx_case.build_case(self.run)
        self.assertEqual(packaged["classification"], gfx_case.UNALIGNED)


if __name__ == "__main__":
    unittest.main()
