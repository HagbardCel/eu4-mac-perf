"""Trusted bridge behaviour: retry policy, classification, integrity, idempotency."""

import contextlib
import io
import json
import shutil
import tempfile
import unittest
import uuid
from pathlib import Path

from automation import dsh_bridge
from automation import dsh_receipts as receipts

REPO = Path(__file__).resolve().parents[1]


class BridgeTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.run_root = self.root / "run"
        self.workspace = self.root / "ws"

    def campaign(self, fixture: str) -> Path:
        """Copy a fixture campaign under the checkout.

        The campaign root must live inside the trusted checkout, so fixtures are
        copied into gitignored .local/ rather than mutated in place.
        """
        target = REPO / ".local" / "dsh-test" / f"{fixture}-{uuid.uuid4().hex[:8]}"
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(REPO / "tests" / "fixtures" / "dsh" / fixture, target)
        self.addCleanup(shutil.rmtree, target, ignore_errors=True)
        return target

    def sidecar(self, campaign: Path, *, retry=("stateful", 1), allowed=("*",),
                manifest=None) -> Path:
        data = {
            "schema": "eu4-dsh-campaign/v1",
            "campaign_id": f"dsh-test-{uuid.uuid4().hex[:6]}",
            "source_campaign": str(campaign / "campaign.json"),
            "run_root": str(self.run_root),
            "candidate": {
                "mode": "seed",
                "workspace": str(self.workspace),
                "policy": {"allowed_paths": list(allowed)},
            },
            "checker_retry": {"mode": retry[0], "confirmation_rounds": retry[1]},
            "goal": {"max_rounds": 4},
            "limits": {"max_validations": 6, "validation_timeout_seconds": 30,
                       "wall_clock_seconds": 600},
            "live_capture": {"enabled": False},
        }
        path = self.root / "sidecar.json"
        path.write_text(json.dumps(data), encoding="utf-8")
        return path

    def run_bridge(self, operation: str, sidecar: Path, **extra):
        argv = [operation, "--sidecar", str(sidecar)]
        for key, value in extra.items():
            argv.extend(["--" + key.replace("_", "-"), str(value)])
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            code = dsh_bridge.main(argv)
        text = buffer.getvalue().strip()
        return code, (json.loads(text) if text else None)


class PrepareTests(BridgeTestCase):
    def test_prepare_seeds_the_workspace_and_records_identity(self) -> None:
        sidecar = self.sidecar(self.campaign("smoke-campaign"))
        code, out = self.run_bridge("prepare", sidecar)
        self.assertEqual(code, dsh_bridge.EXIT_OK)
        self.assertTrue(out["ok"])
        self.assertEqual((self.workspace / "marker.txt").read_text().strip(), "ready")
        self.assertTrue((self.run_root / "identity.json").is_file())
        self.assertEqual(
            out["execution_roots"]["candidate_root"], str(self.workspace.resolve())
        )
        self.assertEqual(len(out["identity_digest"]), 64)


class RetryPolicyTests(BridgeTestCase):
    def test_unchanged_correct_candidate_reaches_its_confirmation_pass(self) -> None:
        """The stateful case: a first-run failure must not become a dead end."""
        sidecar = self.sidecar(self.campaign("smoke-campaign"), retry=("stateful", 1))
        self.run_bridge("prepare", sidecar)

        code, first = self.run_bridge("validate", sidecar)
        self.assertEqual(code, dsh_bridge.EXIT_OK)
        self.assertEqual(first["classification"], "verdict")
        self.assertEqual(first["checker_verdict"], "fail")

        code, second = self.run_bridge("validate", sidecar)
        self.assertEqual(code, dsh_bridge.EXIT_OK)
        self.assertEqual(second["classification"], "verdict")
        self.assertEqual(second["checker_verdict"], "pass")
        self.assertEqual(second["goal_policy"], "qualified")
        self.assertEqual(
            first["candidate_digest"], second["candidate_digest"],
            "the acceptance case requires an unchanged candidate",
        )

        counters = receipts.load_counters(self.run_root)
        self.assertEqual(counters["validations"], 2)
        receipt = receipts.load_current_receipt(self.run_root)
        self.assertEqual(receipt["outcome"]["checker_verdict"], "pass")

    def test_deterministic_mode_refuses_an_identical_failure(self) -> None:
        sidecar = self.sidecar(self.campaign("smoke-campaign"),
                               retry=("deterministic", 0))
        self.run_bridge("prepare", sidecar)
        _, first = self.run_bridge("validate", sidecar)
        self.assertEqual(first["checker_verdict"], "fail")

        code, second = self.run_bridge("validate", sidecar)
        self.assertEqual(code, dsh_bridge.EXIT_OK)
        self.assertEqual(second["classification"], "policy_stop")
        self.assertEqual(second["kind"], "no_progress")
        self.assertEqual(second["goal_policy"], "unavailable")
        self.assertNotIn("blocked", json.dumps(second))
        self.assertEqual(receipts.load_counters(self.run_root)["validations"], 1)


class ClassificationTests(BridgeTestCase):
    def test_unreadable_checker_output_is_an_infrastructure_error(self) -> None:
        sidecar = self.sidecar(self.campaign("broken-campaign"))
        self.run_bridge("prepare", sidecar)
        code, out = self.run_bridge("validate", sidecar)
        self.assertEqual(code, dsh_bridge.EXIT_OK)
        self.assertEqual(out["classification"], "infrastructure_error")
        self.assertEqual(out["kind"], "unreadable")
        self.assertIsNone(out["checker_verdict"])
        self.assertEqual(out["goal_policy"], "unavailable")
        # The run is recorded for audit, but it never becomes a usable pass:
        # the receipt carries no checker verdict for anything to qualify on.
        stored = receipts.load_current_receipt(self.run_root)
        self.assertEqual(stored["outcome"]["checker_verdict"], None)
        self.assertEqual(
            stored["outcome"]["bridge_classification"], "infrastructure_error"
        )


class IntegrityTests(BridgeTestCase):
    def test_a_modified_checker_fails_closed(self) -> None:
        campaign = self.campaign("smoke-campaign")
        sidecar = self.sidecar(campaign)
        self.run_bridge("prepare", sidecar)
        checker = campaign / "accept.py"
        checker.write_text(
            checker.read_text(encoding="utf-8") + "\n# tampered\n", encoding="utf-8"
        )
        code, out = self.run_bridge("validate", sidecar)
        self.assertEqual(code, dsh_bridge.EXIT_INTEGRITY)
        self.assertEqual(out["classification"], "integrity_refusal")
        self.assertEqual(out["kind"], "identity_modified")
        self.assertIn("checker_sha256", out["detail"])

    def test_a_candidate_outside_the_allowlist_is_refused(self) -> None:
        sidecar = self.sidecar(self.campaign("smoke-campaign"),
                               allowed=("marker.txt", "progress.md"))
        self.run_bridge("prepare", sidecar)
        (self.workspace / "smuggled.txt").write_text("x\n", encoding="utf-8")
        code, out = self.run_bridge("validate", sidecar)
        self.assertEqual(code, dsh_bridge.EXIT_INTEGRITY)
        self.assertEqual(out["kind"], "policy")
        self.assertIn("smuggled.txt", out["detail"])

    def test_validate_requires_prepare(self) -> None:
        sidecar = self.sidecar(self.campaign("smoke-campaign"))
        code, out = self.run_bridge("validate", sidecar)
        self.assertEqual(code, dsh_bridge.EXIT_INTEGRITY)
        self.assertEqual(out["kind"], "not_prepared")


class IdempotencyTests(BridgeTestCase):
    def test_an_identical_verified_pass_is_not_rerun(self) -> None:
        sidecar = self.sidecar(self.campaign("pass-campaign"))
        self.run_bridge("prepare", sidecar)
        _, first = self.run_bridge("validate", sidecar)
        self.assertEqual(first["checker_verdict"], "pass")
        _, second = self.run_bridge("validate", sidecar)
        self.assertTrue(second.get("reused"))
        self.assertEqual(second["checker_verdict"], "pass")
        counter = self.root / "invocations.txt"
        self.assertEqual(len(counter.read_text(encoding="utf-8").splitlines()), 1)


class StatusTests(BridgeTestCase):
    def test_status_is_current_then_stale_after_a_candidate_change(self) -> None:
        sidecar = self.sidecar(self.campaign("pass-campaign"))
        self.run_bridge("prepare", sidecar)
        self.run_bridge("validate", sidecar)
        _, fresh = self.run_bridge("status", sidecar)
        self.assertTrue(fresh["current"])
        self.assertEqual(fresh["stale"], [])

        (self.workspace / "marker.txt").write_text("ready-ish\n", encoding="utf-8")
        _, stale = self.run_bridge("status", sidecar)
        self.assertFalse(stale["current"])
        self.assertIn("candidate.digest", stale["stale"])


class GoalPromptTests(BridgeTestCase):
    def test_the_prompt_states_the_completion_rule(self) -> None:
        sidecar = self.sidecar(self.campaign("smoke-campaign"))
        code, out = self.run_bridge("goal-prompt", sidecar)
        self.assertEqual(code, dsh_bridge.EXIT_OK)
        self.assertEqual(out["max_goal_rounds"], 4)
        self.assertIn("eu4_finalize_candidate", out["objective"])
        self.assertIn("denied", out["objective"])


if __name__ == "__main__":
    unittest.main()


class FinalizeTests(BridgeTestCase):
    def test_finalize_confirms_with_a_fresh_run_and_records_a_freeze(self) -> None:
        sidecar = self.sidecar(self.campaign("pass-campaign"))
        self.run_bridge("prepare", sidecar)
        self.run_bridge("validate", sidecar)
        code, out = self.run_bridge("finalize", sidecar)
        self.assertEqual(code, dsh_bridge.EXIT_OK, out)
        self.assertEqual(out["checker_verdict"], "pass")
        self.assertEqual(out["goal_policy"], "qualified")
        self.assertTrue((self.run_root / "finalization.json").is_file())
        self.assertEqual(len(out["frozen_candidate"]["digest"]), 64)

    def test_finalize_refuses_when_the_candidate_moved(self) -> None:
        sidecar = self.sidecar(self.campaign("pass-campaign"))
        self.run_bridge("prepare", sidecar)
        self.run_bridge("validate", sidecar)
        (self.workspace / "marker.txt").write_text("moved\n", encoding="utf-8")
        code, out = self.run_bridge("finalize", sidecar)
        self.assertEqual(code, dsh_bridge.EXIT_INTEGRITY)
        self.assertEqual(out["kind"], "stale")

    def test_finalize_refuses_without_a_passing_validation(self) -> None:
        sidecar = self.sidecar(self.campaign("smoke-campaign"), retry=("stateful", 1))
        self.run_bridge("prepare", sidecar)
        _, first = self.run_bridge("validate", sidecar)
        self.assertEqual(first["checker_verdict"], "fail")
        code, out = self.run_bridge("finalize", sidecar)
        self.assertEqual(code, dsh_bridge.EXIT_INTEGRITY)
        self.assertEqual(out["kind"], "not_qualified")

    def test_a_rehashed_forged_pass_receipt_cannot_authorize_completion(self) -> None:
        """Amendment B: the receipt is a record, the fresh run is the authority."""
        sidecar = self.sidecar(self.campaign("failing-campaign"))
        self.run_bridge("prepare", sidecar)
        _, first = self.run_bridge("validate", sidecar)
        self.assertEqual(first["checker_verdict"], "fail")

        # Forge a pass and re-hash it, so the tamper is invisible to any check
        # that trusts the stored receipt or its self-digest.
        pointer = receipts.load_current(self.run_root)
        path = receipts.paths(self.run_root).receipts / pointer["receipt"]
        body = json.loads(path.read_text(encoding="utf-8"))
        body["outcome"]["checker_verdict"] = "pass"
        body.pop("receipt_digest", None)
        body["receipt_digest"] = receipts.digest_text(receipts.canonical_json(body))
        path.write_text(json.dumps(body), encoding="utf-8")
        self.assertEqual(receipts.verify_receipt_digest(body), [],
                         "the forgery must look self-consistent")

        code, out = self.run_bridge("finalize", sidecar)
        self.assertEqual(code, dsh_bridge.EXIT_INTEGRITY, out)
        self.assertEqual(out["kind"], "fresh_validation_failed")
        self.assertFalse((self.run_root / "finalization.json").exists())

    def test_finalize_detects_an_inconsistent_receipt(self) -> None:
        sidecar = self.sidecar(self.campaign("failing-campaign"))
        self.run_bridge("prepare", sidecar)
        self.run_bridge("validate", sidecar)
        pointer = receipts.load_current(self.run_root)
        path = receipts.paths(self.run_root).receipts / pointer["receipt"]
        body = json.loads(path.read_text(encoding="utf-8"))
        body["outcome"]["checker_verdict"] = "pass"
        path.write_text(json.dumps(body), encoding="utf-8")
        code, out = self.run_bridge("finalize", sidecar)
        self.assertEqual(code, dsh_bridge.EXIT_INTEGRITY)
        self.assertEqual(out["kind"], "receipt_corrupt")
