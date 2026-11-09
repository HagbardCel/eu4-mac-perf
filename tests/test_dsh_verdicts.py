"""Every row of the verdict table, and both budget stops.

The plan's contract is that the checker's verdict is passed through unchanged and
that the bridge's own classification is a separate axis. The branches that steer
agent behaviour most -- inconclusive, blocked, unavailable -- had no coverage, nor
did the validation and finalization budgets.
"""

import json
import unittest
from pathlib import Path

from automation import dsh_bridge
from automation import dsh_receipts as receipts
from tests.test_dsh_bridge import BridgeTestCase


class ClassifyTests(unittest.TestCase):
    def classify(self, **outcome):
        return dsh_bridge.classify(outcome)

    def test_a_checker_verdict_is_passed_through(self) -> None:
        for verdict in ("pass", "fail", "inconclusive", "blocked", "unavailable"):
            with self.subTest(verdict=verdict):
                self.assertEqual(
                    self.classify(ok=True, verdict=verdict, exit_code=0),
                    ("verdict", None, verdict),
                )

    def test_pass_with_a_nonzero_exit_is_an_infrastructure_error(self) -> None:
        self.assertEqual(
            self.classify(ok=True, verdict="pass", exit_code=3),
            ("infrastructure_error", "exit_code", None),
        )

    def test_named_infrastructure_failures_keep_their_kind(self) -> None:
        for kind in ("timeout", "truncated", "unreadable"):
            with self.subTest(kind=kind):
                self.assertEqual(
                    self.classify(ok=False, verdict=None, exit_code=None, diagnostics=kind),
                    ("infrastructure_error", kind, None),
                )

    def test_a_nonzero_exit_without_a_named_reason_is_exit_code(self) -> None:
        self.assertEqual(
            self.classify(ok=False, verdict=None, exit_code=2,
                          diagnostics="Traceback: boom"),
            ("infrastructure_error", "exit_code", None),
        )

    def test_a_clean_exit_without_a_verdict_is_unreadable(self) -> None:
        self.assertEqual(
            self.classify(ok=False, verdict=None, exit_code=0, diagnostics=""),
            ("infrastructure_error", "unreadable", None),
        )


class GoalPolicyTests(BridgeTestCase):
    def verdict_sidecar(self, *, max_validations: int = 6) -> Path:
        sidecar = self.sidecar(self.campaign("verdict-campaign"))
        data = json.loads(sidecar.read_text(encoding="utf-8"))
        data["limits"]["max_validations"] = max_validations
        sidecar.write_text(json.dumps(data), encoding="utf-8")
        return sidecar

    def request(self, verdict: str) -> None:
        (self.root / "verdict.txt").write_text(verdict + "\n", encoding="utf-8")

    def test_every_verdict_reaches_the_goal_policy(self) -> None:
        expected = {
            "pass": "qualified",
            "fail": "remediate",
            "inconclusive": "investigate",
            "blocked": "block",
            "unavailable": "pause",
        }
        for verdict, policy in expected.items():
            with self.subTest(verdict=verdict):
                self.setUp()
                sidecar = self.verdict_sidecar()
                self.run_bridge("prepare", sidecar)
                self.request(verdict)
                code, out = self.run_bridge("validate", sidecar)
                self.assertEqual(code, dsh_bridge.EXIT_OK, out)
                self.assertEqual(out["checker_verdict"], verdict)
                self.assertEqual(out["classification"], "verdict")
                self.assertEqual(out["goal_policy"], policy)
                stored = receipts.load_current_receipt(self.run_root)
                self.assertEqual(stored["outcome"]["checker_verdict"], verdict)

    def test_the_validation_budget_stops_instead_of_running_again(self) -> None:
        sidecar = self.verdict_sidecar(max_validations=1)
        self.run_bridge("prepare", sidecar)
        self.request("inconclusive")
        first = self.run_bridge("validate", sidecar)
        self.assertEqual(first[1]["checker_verdict"], "inconclusive")
        code, second = self.run_bridge("validate", sidecar)
        self.assertEqual(code, dsh_bridge.EXIT_OK)
        self.assertEqual(second["classification"], "policy_stop")
        self.assertEqual(second["kind"], "validation_budget")
        self.assertEqual(second["goal_policy"], "unavailable")

    def test_the_finalization_budget_stops_instead_of_confirming_again(self) -> None:
        sidecar = self.sidecar(self.campaign("pass-campaign"))
        self.run_bridge("prepare", sidecar)
        self.run_bridge("validate", sidecar)
        first = self.run_bridge("finalize", sidecar)
        self.assertEqual(first[1]["checker_verdict"], "pass")
        second = self.run_bridge("finalize", sidecar)
        self.assertEqual(second[1]["finalizations_used"], 2)
        code, third = self.run_bridge("finalize", sidecar)
        self.assertEqual(code, dsh_bridge.EXIT_OK)
        self.assertEqual(third["classification"], "policy_stop")
        self.assertEqual(third["kind"], "finalize_budget")


if __name__ == "__main__":
    unittest.main()
