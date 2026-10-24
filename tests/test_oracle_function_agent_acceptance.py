"""Independent G3a acceptance (black-box). Implementation agents must not edit."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from oracle.function_agent import catalog, seam
from oracle.slice import controller, receipts
from oracle.slice.adapters import StubAdapter

from tests.test_oracle_function_agent_seam import _run


class G3aAcceptanceTests(unittest.TestCase):
    def test_synth_apply_end_to_end_sealed_receipt(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = _run(Path(tmp), StubAdapter("repair"))
            self.assertEqual(result.status, controller.STATUS_CERTIFIED)
            self.assertIsNotNone(result.receipt)
            receipts.verify(result.receipt, expected_sha256=result.receipt["sha256"])

    def test_certify_tool_request_is_refused(self):
        fake_tools = type("T", (), {"call": lambda self, m, a=None: {"tool": m}})()
        bound = catalog.CatalogBoundTools(fake_tools,
                                            allowed=catalog.AGENT_VISIBLE_TOOLS)
        with self.assertRaises(catalog.CatalogError):
            bound.call("certify", {})

    def test_claim_denies_arbitrary_cpp(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = _run(Path(tmp), StubAdapter("repair"))
            self.assertFalse(result.claim["arbitrary_cpp_supported"])
            self.assertFalse(result.claim["generic_workspace"])


class DefectiveSeamStandIn:
    """Schema-valid success without a sealed receipt."""

    def run(self, **_kwargs):
        return seam.SeamResult(
            status=controller.STATUS_CERTIFIED,
            claim={"function_id": "synth:apply", "synthetic": True,
                   "arbitrary_cpp_supported": False, "generic_workspace": False},
            run=None,
        )


class G3aNegativeControlTests(unittest.TestCase):
    def test_defective_stand_in_fails_receipt_requirement(self):
        result = DefectiveSeamStandIn().run()
        self.assertIsNone(result.receipt)
        with self.assertRaises(receipts.ReceiptError):
            receipts.verify(result.receipt)


if __name__ == "__main__":
    unittest.main()
