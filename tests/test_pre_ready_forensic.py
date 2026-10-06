#!/usr/bin/env python3
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "analysis" / "tools"))

from pre_ready_forensic import build_forensic  # noqa: E402

RUN_DIR = ROOT / "results" / "20261006T193739Z-submission-experiment"
ARTIFACT = ROOT / "analysis" / "evidence" / "pre-ready-forensic-20261006T193739Z.json"


class PreReadyForensicTests(unittest.TestCase):
    def test_future_power_leakage_would_change_stable_without_bound(self) -> None:
        sys.path.insert(0, str(ROOT / "benchmark"))
        import autonomous_runner as auto

        start = __import__("datetime").datetime(2026, 1, 1, tzinfo=__import__("datetime").timezone.utc)
        anchor = {"monotonic_ns": 0, "wall_ns": int(start.timestamp() * 1e9)}
        raw = []
        for second in range(1, 25):
            cpu = 1000.0 if second <= 15 else 9000.0
            raw.append(
                {
                    "timestamp": start + __import__("datetime").timedelta(seconds=second),
                    "tasks": [{"pid": 1, "cputime_ms_per_s": cpu}],
                }
            )
        eval_ns = 15_000_000_000
        bounded = auto.cpu_samples_between(raw, 1, anchor, 0, eval_ns)[-10:]
        all_samples = auto.cpu_samples(raw, 1, anchor, 0)
        b_vals = [v for _, v in bounded]
        u_vals = [v for when, v in all_samples if when <= eval_ns][-10:]
        leaky_vals = [v for _, v in all_samples][-10:]
        self.assertTrue(auto.stable(b_vals, 10, 0.10))
        self.assertTrue(auto.stable(u_vals, 10, 0.10))
        self.assertFalse(auto.stable(leaky_vals, 10, 0.10))

    def test_measured_193739Z_forensic_when_run_present(self) -> None:
        if not RUN_DIR.is_dir():
            self.skipTest("local 193739Z run artifacts not present")
        payload = build_forensic(RUN_DIR)
        self.assertEqual(payload["probe"]["probe_row_count"], 7)
        self.assertEqual(payload["probe"]["timeout_window_seconds"], 180.0)
        if ARTIFACT.is_file():
            on_disk = json.loads(ARTIFACT.read_text(encoding="utf-8"))
            self.assertEqual(on_disk["probe"], payload["probe"])


if __name__ == "__main__":
    unittest.main()
