#!/usr/bin/env python3
import json
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EVIDENCE = ROOT / "analysis" / "evidence"


class BorderEvidenceIntegrityTests(unittest.TestCase):
    def test_census_provenance_delta_matches_readiness_mirror(self) -> None:
        census = json.loads(
            (EVIDENCE / "border-mode-census-live-20261007T074355Z.json").read_text()
        )
        readiness = json.loads(
            (EVIDENCE / "border-loop-head-roi-readiness-20261005.json").read_text()
        )
        dc = readiness["domain_census_live"]

        self.assertEqual(census["run_provenance_delta"], dc["run_provenance_delta"])
        self.assertEqual(census["run"], dc["run"])
        self.assertEqual(census["run_git_commit"], dc["run_git_commit"])
        self.assertEqual(census["offline_verified_code_commit"], dc["offline_verified_commit"])


if __name__ == "__main__":
    unittest.main()
