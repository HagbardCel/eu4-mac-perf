import json
import unittest
from pathlib import Path

from reverse_engineering.reconcile_gfx import gfx_helper_rvas, reconcile_draw_sites

FIXTURE = Path(__file__).resolve().parent / "re_fixtures" / "export_mini"


class ReconcileGfxTests(unittest.TestCase):
    def test_gfx_helper_rva_map(self) -> None:
        functions = {
            "0x0000000000005000": {"name_mangled": "_GfxDrawIndexed"},
            "0x0000000000006000": {"name_mangled": "other"},
        }
        helpers = gfx_helper_rvas(functions)
        self.assertEqual(helpers.get("GfxDrawIndexed"), "0x0000000000005000")

    def test_reconcile_runs_on_fixture(self) -> None:
        draw = {
            "direct_sites": [
                {
                    "callsite_offset": 0x3000,
                    "operation": "GfxDrawIndexed",
                    "owner_symbol": "owner",
                }
            ]
        }
        report = reconcile_draw_sites(draw, FIXTURE / "inventory")
        self.assertEqual(report["draw_callers_count"], 1)
        self.assertIn(report["sites"][0]["status"], ("matched", "missing_callsite", "target_mismatch", "unresolved"))


if __name__ == "__main__":
    unittest.main()
