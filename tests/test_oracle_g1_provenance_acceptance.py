"""G1 independent provenance acceptance (reviewer-owned)."""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from tests.oracle_g1_acceptance.checker import (
    check_decision_record,
    check_package_contract,
    load_census_row,
)

ROOT = Path(__file__).resolve().parents[1]


class G1ProvenanceCheckerTests(unittest.TestCase):
    def test_census_binary_matches_target_pin(self):
        target = json.loads((ROOT / "reverse_engineering/target.json").read_text())
        summary = json.loads(
            (ROOT / "analysis/renderer-corpus/scope/v1/manifest-summary.json"
             ).read_text(encoding="utf-8"))
        self.assertEqual(summary["binary_sha256"], target["sha256"])

    def test_gfx_draw_indexed_row_is_unique(self):
        row = load_census_row("_GfxDrawIndexed", "0x00000000015eaa77")
        self.assertIsNotNone(row)

    def test_negative_wrong_entry_rva(self):
        with tempfile.TemporaryDirectory() as tmp:
            contract = Path(tmp) / "contract.md"
            contract.write_text(
                "---\n"
                "function_mangled: _GfxDrawIndexed\n"
                "entry_rva: 0x0000000000000001\n"
                "gate_verdict: PASS\n"
                "blockers: []\n"
                "---\n\n"
                "## Preconditions\n## Postconditions\n## Modified state\n"
                "## Dependencies\n## Unobserved\n",
                encoding="utf-8",
            )
            errors = check_package_contract(contract, ROOT)
            self.assertTrue(any("census" in e for e in errors))

    def test_decision_record_fail_is_valid(self):
        with tempfile.TemporaryDirectory() as tmp:
            decision = Path(tmp) / "second-function-re0-decision.md"
            decision.write_text(
                "---\n"
                "gate_verdict: FAIL\n"
                "candidates_considered: _GfxDrawIndexed, InternalSetTextureAndSamplerState\n"
                "---\n\n"
                "## Summary\nAll candidates failed RE0 gate.\n",
                encoding="utf-8",
            )
            self.assertEqual(check_decision_record(decision, ROOT), [])


if __name__ == "__main__":
    unittest.main()
