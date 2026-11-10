"""Launch-safety facts that the infrastructure must not overstate.

`automation.launch_ledger` is authoritative *accounting* for how many original-
EU4 launches a workflow may make. It is not mutual exclusion: `claim()` refuses
only when the budget is spent, so two callers under a limit of three both get
valid reservations. These tests pin that down so no part of the DSH path can
describe the ledger as a concurrency control.
"""

import json
import tempfile
import unittest
from pathlib import Path

from automation import launch_ledger


class LedgerSemanticsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.ledger = self.root / "ledger.json"
        launch_ledger.create_ledger(
            self.ledger, workflow_id="wf-test", limit=3, deadline_seconds=120
        )

    def claim(self, campaign: str) -> str:
        return launch_ledger.claim(
            self.ledger,
            campaign_id=campaign,
            run_root=self.root,
            output=self.root / f"{campaign}.out",
            targets=["capture"],
            requested_timeout=30,
        )

    def test_the_budget_is_enforced(self) -> None:
        for index in range(3):
            self.claim(f"c{index}")
        with self.assertRaises(launch_ledger.LedgerError) as caught:
            self.claim("overflow")
        self.assertEqual(caught.exception.kind, "exhausted")

    def test_accounting_is_not_mutual_exclusion(self) -> None:
        """Two live claims coexist while budget remains: no admission control."""
        first = self.claim("a")
        second = self.claim("b")
        value = launch_ledger.load_ledger(self.ledger)
        statuses = {
            receipt["token"]: receipt["status"] for receipt in value["launches"]
        }
        self.assertEqual(statuses[first], "reserved")
        self.assertEqual(statuses[second], "reserved")
        self.assertEqual(value["used"], 2)

    def test_a_reservation_survives_until_reconciled_and_stays_charged(self) -> None:
        token = self.claim("a")
        launch_ledger.start(self.ledger, token, pid=4242)
        launch_ledger.reconcile(self.ledger)
        value = launch_ledger.load_ledger(self.ledger)
        self.assertEqual(value["launches"][0]["status"], "started")
        self.assertEqual(value["used"], 1)


class DshCaptureRefusalTests(unittest.TestCase):
    def test_a_sidecar_cannot_enable_live_capture(self) -> None:
        from automation.dsh_campaign import SidecarError, load_sidecar

        repo = Path(__file__).resolve().parents[1]
        document = json.loads(
            (repo / "dsh" / "campaigns" / "offline-smoke.json").read_text(
                encoding="utf-8"
            )
        )
        document["live_capture"] = {"enabled": True}
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "sidecar.json"
            path.write_text(json.dumps(document), encoding="utf-8")
            with self.assertRaises(SidecarError) as caught:
                load_sidecar(path)
        self.assertIn("exclusivity", str(caught.exception))

    def test_the_bridge_imports_no_launch_path(self) -> None:
        """The DSH bridge must not be able to start an original-EU4 launch."""
        import automation.dsh_bridge as bridge

        source = Path(bridge.__file__).read_text(encoding="utf-8")
        for forbidden in ("live_gfx", "launch_ledger", "launch_counter_path"):
            self.assertNotIn(
                forbidden,
                source,
                f"the bridge must not reference the launch path ({forbidden})",
            )


if __name__ == "__main__":
    unittest.main()
