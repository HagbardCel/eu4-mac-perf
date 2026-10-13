import json
import tempfile
import unittest
from pathlib import Path

from r0_capture_provenance import verify_capture_provenance
from r0_crc64 import crc64_ecma


class CaptureProvenanceTests(unittest.TestCase):
    def test_exact_frame_index_required(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            pixels = bytes(64 * 64 * 4)
            crc = crc64_ecma(pixels)
            sidecar = {
                "write_ok": True,
                "frame_index": 5,
                "control_generation": 9,
                "width": 64,
                "height": 64,
                "crc64": crc,
                "scenario_id": 0,
                "uptime_ns": 2,
            }
            (root / "capture_9.json").write_text(json.dumps(sidecar))
            (root / "capture_9.rgba").write_bytes(pixels)
            frames = [
                {
                    "frame_index": 4,
                    "control_generation": 9,
                    "census_attempted": 1,
                    "census_ok": 1,
                    "pause_verified": 1,
                    "width": 64,
                    "height": 64,
                    "crc64": crc,
                    "scenario_id": 0,
                    "flush_ok": 1,
                    "gl_error_observed": 0,
                    "uptime_ns": 1,
                },
                {
                    "frame_index": 5,
                    "control_generation": 9,
                    "census_attempted": 1,
                    "census_ok": 1,
                    "pause_verified": 1,
                    "width": 64,
                    "height": 64,
                    "crc64": crc + 1,
                    "scenario_id": 0,
                    "flush_ok": 1,
                    "gl_error_observed": 0,
                    "uptime_ns": 2,
                },
            ]
            wrong = verify_capture_provenance(root, 9, frames)
            self.assertEqual(wrong["status"], "failed")
            sidecar["frame_index"] = 5
            sidecar["crc64"] = crc
            sidecar["uptime_ns"] = 2
            (root / "capture_9.json").write_text(json.dumps(sidecar))
            frames[1]["crc64"] = crc
            ok = verify_capture_provenance(root, 9, frames)
            self.assertEqual(ok["status"], "verified")

    def test_missing_pre_window_event_fails(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            pixels = bytes(64 * 64 * 4)
            crc = crc64_ecma(pixels)
            sidecar = {
                "write_ok": True,
                "frame_index": 1,
                "control_generation": 1,
                "width": 64,
                "height": 64,
                "crc64": crc,
                "scenario_id": 0,
                "uptime_ns": 100,
            }
            (root / "capture_1.json").write_text(json.dumps(sidecar))
            (root / "capture_1.rgba").write_bytes(pixels)
            frames = [
                {
                    "frame_index": 1,
                    "control_generation": 1,
                    "census_attempted": 1,
                    "census_ok": 1,
                    "pause_verified": 1,
                    "width": 64,
                    "height": 64,
                    "crc64": crc,
                    "scenario_id": 0,
                    "flush_ok": 1,
                    "gl_error_observed": 0,
                    "uptime_ns": 100,
                }
            ]
            events = [{"event": "post_window_start", "event_uptime_ns": 50}]
            result = verify_capture_provenance(root, 1, frames, events=events, window="pre")
            self.assertEqual(result["status"], "failed")
            self.assertEqual(result["reason"], "missing_pre_window_end")


if __name__ == "__main__":
    unittest.main()
