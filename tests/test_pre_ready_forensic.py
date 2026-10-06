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
