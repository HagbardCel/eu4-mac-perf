import os
import tempfile
import unittest
from pathlib import Path

from reverse_engineering.graphics_agent.model_budget import (
    BudgetReservationError,
    authorization_remaining_micro_usd,
    compute_reservation_micro_usd,
    paid_model_requests_allowed,
    release_paid_run,
    reserve_paid_run,
    resolve_spend_authorization_id,
    settle_paid_run,
)
from reverse_engineering.graphics_agent.model_registry import profile_for_mode, resolve_effective_profile
from reverse_engineering.graphics_agent.research_store import connect


class ModelBudgetTests(unittest.TestCase):
    def test_reservation_positive(self) -> None:
        profile = profile_for_mode("discovery")
        effective = resolve_effective_profile(profile)
        from reverse_engineering.graphics_agent.model_budget import RunSpendLimits

        reserved = compute_reservation_micro_usd(
            effective, RunSpendLimits(4, 1000, 500, 500_000)
        )
        self.assertGreater(reserved, 0)

    def test_reserve_requires_authorization(self) -> None:
        os.environ["ALLOW_MODEL_REQUESTS"] = "true"
        os.environ.pop("EU4_AGENT_AUTHORIZE_PAID_RUN", None)
        self.assertFalse(paid_model_requests_allowed())

    def test_reserve_settle_release(self) -> None:
        os.environ["ALLOW_MODEL_REQUESTS"] = "true"
        os.environ["EU4_AGENT_AUTHORIZE_PAID_RUN"] = "1"
        with tempfile.TemporaryDirectory() as tmp:
            conn = connect(Path(tmp) / "r.sqlite")
            profile = profile_for_mode("verification")
            ctx = reserve_paid_run(conn, "inv", "verify", profile, {"model_spend": {"investigation_budget_usd": 5}})
            settle_paid_run(conn, ctx.run_id, None)
            row = conn.execute("SELECT state FROM model_spend_ledger WHERE run_id = ?", (ctx.run_id,)).fetchone()
            self.assertEqual(row[0], "settled")

    def test_second_reserve_rejected(self) -> None:
        os.environ["ALLOW_MODEL_REQUESTS"] = "true"
        os.environ["EU4_AGENT_AUTHORIZE_PAID_RUN"] = "1"
        with tempfile.TemporaryDirectory() as tmp:
            conn = connect(Path(tmp) / "r.sqlite")
            profile = profile_for_mode("verification")
            cfg = {"model_spend": {"investigation_budget_usd": 5, "per_run_cap_usd": 0.5}}
            reserve_paid_run(conn, "inv", "verify", profile, cfg)
            with self.assertRaises(BudgetReservationError):
                reserve_paid_run(conn, "inv", "verify", profile, cfg)
            release_paid_run(conn, conn.execute("SELECT run_id FROM model_spend_ledger").fetchone()[0])

    def test_shared_spend_authorization_across_investigations(self) -> None:
        os.environ["ALLOW_MODEL_REQUESTS"] = "true"
        os.environ["EU4_AGENT_AUTHORIZE_PAID_RUN"] = "1"
        os.environ.pop("EU4_AGENT_SPEND_AUTHORIZATION_ID", None)
        cfg = {
            "model_spend": {
                "spend_authorization_id": "canary-bundle-test",
                "investigation_budget_usd": 0.2,
                "per_run_cap_usd": 0.2,
            }
        }
        auth = resolve_spend_authorization_id("inv-a", cfg)
        self.assertEqual(auth, "canary-bundle-test")
        with tempfile.TemporaryDirectory() as tmp:
            conn = connect(Path(tmp) / "r.sqlite")
            profile = profile_for_mode("verification")
            reserve_paid_run(conn, "inv-a", "verify", profile, cfg)
            settle_paid_run(conn, conn.execute("SELECT run_id FROM model_spend_ledger").fetchone()[0], None)
            self.assertEqual(authorization_remaining_micro_usd(conn, auth), 0)
            with self.assertRaises(BudgetReservationError):
                reserve_paid_run(conn, "inv-b", "verify", profile, cfg)


if __name__ == "__main__":
    unittest.main()
