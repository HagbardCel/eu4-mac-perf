#!/usr/bin/env python3
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "analysis" / "tools"))

from border_loop_evidence import (  # noqa: E402
    CRITERION_KEYS,
    derive_static_feasibility,
    derive_static_item_lists,
    validate_evidence_payload,
)


class BorderLoopEvidenceTests(unittest.TestCase):
    def test_derive_pending(self) -> None:
        criteria = {k: "PENDING" for k in CRITERION_KEYS}
        self.assertEqual(derive_static_feasibility(criteria), "PENDING_STATIC_RE")

    def test_derive_conditional_go(self) -> None:
        criteria = {k: "PASS" for k in CRITERION_KEYS}
        self.assertEqual(derive_static_feasibility(criteria), "GO_CONDITIONAL_RUNTIME_GATES")

    def test_derive_no_go(self) -> None:
        criteria = {k: "PASS" for k in CRITERION_KEYS}
        criteria["9_detour_relocation"] = "FAIL"
        self.assertEqual(derive_static_feasibility(criteria), "NO_GO")

    def test_committed_evidence_json_consistent(self) -> None:
        path = ROOT / "analysis" / "evidence" / "border-loop-head-feasibility-20261004.json"
        payload = json.loads(path.read_text())
        validate_evidence_payload(payload)

    def test_open_and_failed_lists(self) -> None:
        criteria = {k: "PASS" for k in CRITERION_KEYS}
        criteria["5_helper_side_effect_equivalence"] = "PENDING"
        criteria["6_cpu_continuation"] = "FAIL"
        open_items, failed = derive_static_item_lists(criteria)
        self.assertEqual(open_items, ["5_helper_side_effect_equivalence"])
        self.assertEqual(failed, ["6_cpu_continuation"])


if __name__ == "__main__":
    unittest.main()
