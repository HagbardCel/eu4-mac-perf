#!/usr/bin/env python3
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "analysis" / "tools"))

from border_loop_roi import (  # noqa: E402
    estimated_production_eliminations_per_second,
    eliminations_per_frame,
    validate_roi_readiness_payload,
)


class BorderLoopRoiTests(unittest.TestCase):
    def test_readiness_artifact(self) -> None:
        path = ROOT / "analysis" / "evidence" / "border-loop-head-roi-readiness-20261005.json"
        payload = json.loads(path.read_text())
        validate_roi_readiness_payload(payload)

    def test_per_frame_rate(self) -> None:
        self.assertEqual(eliminations_per_frame(400, 100), 4.0)
        self.assertIsNone(eliminations_per_frame(1, 0))

    def test_production_rate_uses_baseline_fps(self) -> None:
        est = estimated_production_eliminations_per_second(4.0, fps_baseline=55.0)
        self.assertEqual(est, 220.0)


if __name__ == "__main__":
    unittest.main()
