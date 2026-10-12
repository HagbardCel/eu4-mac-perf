import json
import unittest
from pathlib import Path

from analysis.tools.frame_evolution_analyze import (
    FrameRecord,
    accepted_census_frames,
    build_report,
    classify_scenario,
    consecutive_equal_runs,
    evaluate_positive_control_windows,
    positive_control_segments,
    tile_mask_from_pixels,
)


class FrameEvolutionAnalyzeTests(unittest.TestCase):
    def test_consecutive_equal_runs(self) -> None:
        frames = [
            FrameRecord(1, 1, 0, True, True, 64, 64, 0, True, 1, flush_ok=True),
            FrameRecord(2, 1, 0, True, True, 64, 64, 1, True, 1, flush_ok=True),
            FrameRecord(3, 1, 0, True, True, 64, 64, 1, True, 1, flush_ok=True),
            FrameRecord(4, 1, 0, True, True, 64, 64, 0, True, 2, flush_ok=True),
        ]
        runs = consecutive_equal_runs(frames)
        self.assertEqual(len(runs), 1)
        self.assertEqual(runs[0]["length"], 2)

    def test_classify_static(self) -> None:
        frames = [
            FrameRecord(
                i, 1, 0, True, True, 64, 64, 1 if i > 1 else 0, True, 1, uptime_ns=i * 10_000_000
            )
            for i in range(1, 300)
        ]
        summary = classify_scenario(frames, min_frames=120, min_seconds=2.0, fps_hint=60.0)
        self.assertEqual(summary["classification"], "static")

    def test_positive_control_requires_pixels(self) -> None:
        before = [FrameRecord(1, 1, 1, True, True, 64, 64, 0, True, 10)]
        after = [FrameRecord(2, 1, 1, True, True, 64, 64, 0, True, 20)]
        result = evaluate_positive_control_windows(before, after, stable_tail=1, stable_head=1)
        self.assertEqual(result["status"], "insufficient_evidence")

    def test_positive_control_uptime_events(self) -> None:
        frames = [
            FrameRecord(1, 1, 1, True, True, 64, 64, 0, True, 10, uptime_ns=100),
            FrameRecord(2, 1, 1, True, True, 64, 64, 0, True, 11, uptime_ns=200),
            FrameRecord(3, 1, 1, True, True, 64, 64, 0, True, 20, uptime_ns=400),
        ]
        events = [
            {"event": "pre_window_end", "event_uptime_ns": 250},
            {"event": "post_window_start", "event_uptime_ns": 350},
        ]
        segments = positive_control_segments(frames, events)
        self.assertIsNotNone(segments)
        self.assertEqual(len(segments["before"]), 2)
        self.assertEqual(len(segments["after"]), 1)

    def test_panel_roi_detects_change(self) -> None:
        width, height = 64, 64
        before = bytearray(width * height * 4)
        after = bytearray(before)
        roi_x = int(0.72 * width)
        roi_y = int(0.02 * height)
        after[roi_y * width * 4 + roi_x * 4] = 255
        panel = tile_mask_from_pixels(bytes(before), bytes(after), width, height)
        self.assertGreater(panel["changed_fraction"], 0.0)

    def test_build_report_writes_candidates(self) -> None:
        log = Path(self._testMethodName) / "probe.log"
        log.parent.mkdir(parents=True, exist_ok=True)
        lines = []
        for i in range(1, 301):
            eq = 1 if i > 1 else 0
            lines.append(f"F,{i},1,0,1,1,0,64,64,{eq},1,42,1,{i * 10_000_000},0,1,0,99,7,0\n")
        log.write_text("".join(lines))
        roi = Path(self._testMethodName) / "roi.json"
        roi.write_text(
            json.dumps(
                {
                    "verified": True,
                    "panel_roi_opengl_bottom_left": [0.5, 0.5, 0.12, 0.12],
                }
            )
        )
        width, height = 64, 64
        before = bytearray(width * height * 4)
        after = bytearray(before)
        for y in range(32, 39):
            for x in range(32, 39):
                after[y * width * 4 + x * 4] = 255
        positive = {
            "before": [FrameRecord(1, 1, 0, True, True, 64, 64, 0, True, 10, uptime_ns=1)],
            "after": [FrameRecord(2, 1, 1, True, True, 64, 64, 0, True, 20, uptime_ns=2)],
            "pre_generation": 8,
            "post_generation": 9,
        }
        report = build_report(
            log_path=log,
            positive_control=positive,
            pre_pixels=bytes(before),
            post_pixels=bytes(after),
            capture_width=width,
            capture_height=height,
            roi_config=roi,
            require_roi_verified=True,
        )
        self.assertTrue(report["candidate_for_lossless_skip"])
        json.dumps(report)

    def test_outside_roi_change_rejected(self) -> None:
        width, height = 64, 64
        roi = (0.5, 0.5, 0.12, 0.12)
        before = bytearray(width * height * 4)
        after = bytearray(before)
        after[0] = 255
        panel = tile_mask_from_pixels(bytes(before), bytes(after), width, height, roi=roi)
        self.assertEqual(panel["changed_fraction"], 0.0)


if __name__ == "__main__":
    unittest.main()
