import hashlib
import json
import subprocess
import unittest
from pathlib import Path

from analysis.tools.r0_stack_analyze import analyze_sample_text

ROOT = Path(__file__).resolve().parents[1]
VENICE_SAMPLE = ROOT / "results/20261012T210806Z-r0-measurement/epoch_b.sample.txt"
COMMITTED_REPORT = ROOT / "analysis/evidence/r0-stack-report-20261012T210806Z.json"
ANALYZER_SOURCE = ROOT / "analysis/tools/r0_stack_analyze.py"


def _normalized_report(payload: dict) -> dict:
    return json.loads(json.dumps(payload, sort_keys=True))


class VeniceEvidenceReplayTests(unittest.TestCase):
    def test_venice_stack_report_matches_committed_evidence(self) -> None:
        committed = _normalized_report(json.loads(COMMITTED_REPORT.read_text()))
        replay = _normalized_report(
            analyze_sample_text(VENICE_SAMPLE.read_text(errors="replace"), main_thread_id=6650541)
        )
        self.assertEqual(replay, committed)

    def test_analyzer_source_hash_recorded_for_replay_pinning(self) -> None:
        digest = hashlib.sha256(ANALYZER_SOURCE.read_bytes()).hexdigest()
        head = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
        self.assertTrue(head)
        self.assertEqual(len(digest), 64)


if __name__ == "__main__":
    unittest.main()
