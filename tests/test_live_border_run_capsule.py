#!/usr/bin/env python3
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "analysis" / "tools"))

from live_border_run_capsule import SCHEMA_V6_CENSUS_KEYS, build_capsule  # noqa: E402


class LiveBorderRunCapsuleTests(unittest.TestCase):
    def test_build_capsule_v5_end_omits_census_keys_and_preserves_offline_commit(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp)
            v5_end = {
                "counter_schema_version": 5,
                "border_loop_head_entries": 100,
                "candidate_swaps": 10,
                "border_structural_draws": 0,
                "border_runtime_context_checked": 1,
                "border_runtime_context_multidraw_supported": 1,
            }
            manifest = {
                "experiment": "submission_border_multidraw_v1",
                "status": "complete",
                "git": {"commit": "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb", "dirty": False},
                "phases": [
                    {
                        "name": "a1",
                        "control_validation": {"end": v5_end},
                    }
                ],
            }
            (run_dir / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
            (run_dir / "events.jsonl").write_text("{}\n", encoding="utf-8")
            (run_dir / "submission_control.bin").write_bytes(b"\x00" * 64)

            capsule = build_capsule(
                run_dir,
                evidence_kind="test_capsule",
                registered_provenance_commit="aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
            )
            self.assertEqual(capsule["run_git_commit"], "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb")
            self.assertEqual(
                capsule["offline_verified_code_commit"], "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
            )
            phase = capsule["phases"][0]
            self.assertEqual(phase["run_git_commit"], "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb")
            self.assertEqual(
                phase["offline_verified_code_commit"], "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
            )
            counters = phase["counters"]
            for key in SCHEMA_V6_CENSUS_KEYS:
                self.assertNotIn(key, counters)
            self.assertEqual(counters["border_loop_head_entries"], 100)
            self.assertIsNotNone(capsule["source_hashes"]["manifest.json"])
            self.assertIsNotNone(capsule["source_hashes"]["events.jsonl"])
            self.assertIsNotNone(capsule["source_hashes"]["submission_control.bin"])


if __name__ == "__main__":
    unittest.main()
