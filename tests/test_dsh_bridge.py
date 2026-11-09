"""Trusted bridge behaviour: retry policy, classification, integrity, idempotency."""

import contextlib
import io
import json
import os
import shutil
import subprocess
import sys
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
            flag = "--" + key.replace("_", "-")
            if value is True:
                argv.append(flag)
            elif value in (False, None):
                continue
            else:
                argv.extend([flag, str(value)])
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


class ExecutionRootTests(BridgeTestCase):
    def test_the_primary_checkout_is_not_an_acceptable_candidate(self) -> None:
        sidecar = self.sidecar(
            self.campaign("smoke-campaign"),
            # Rewrite the candidate to point at the checkout itself.
        )
        data = json.loads(sidecar.read_text(encoding="utf-8"))
        data["candidate"] = {"mode": "worktree", "workspace": str(REPO),
                             "policy": {"allowed_paths": ["*"]}}
        data["candidate_baseline_commit"] = "6fa1888c8d1cf0f3a6e8205a03ae171d4ce1ba36"
        sidecar.write_text(json.dumps(data), encoding="utf-8")
        code, out = self.run_bridge("prepare", sidecar)
        self.assertEqual(code, dsh_bridge.EXIT_INTEGRITY)
        self.assertEqual(out["kind"], "execution_root")
        self.assertIn("nested", out["detail"])

    def test_danger_full_access_cannot_run_qualification(self) -> None:
        sidecar = self.sidecar(self.campaign("smoke-campaign"))
        os.environ["EU4_DSH_SANDBOX_MODE"] = "danger-full-access"
        self.addCleanup(os.environ.pop, "EU4_DSH_SANDBOX_MODE", None)
        code, out = self.run_bridge("prepare", sidecar)
        self.assertEqual(code, dsh_bridge.EXIT_INTEGRITY)
        self.assertEqual(out["kind"], "execution_root")
        self.assertIn("danger-full-access", out["detail"])

    def test_a_declared_workspace_mismatch_is_refused(self) -> None:
        sidecar = self.sidecar(self.campaign("smoke-campaign"))
        os.environ["EU4_DSH_WORKSPACE"] = str(self.root / "elsewhere")
        self.addCleanup(os.environ.pop, "EU4_DSH_WORKSPACE", None)
        code, out = self.run_bridge("prepare", sidecar)
        self.assertEqual(code, dsh_bridge.EXIT_INTEGRITY)
        self.assertIn("not the campaign workspace", out["detail"])

    def test_a_matching_declared_workspace_is_accepted(self) -> None:
        sidecar = self.sidecar(self.campaign("smoke-campaign"))
        os.environ["EU4_DSH_WORKSPACE"] = str(self.workspace)
        self.addCleanup(os.environ.pop, "EU4_DSH_WORKSPACE", None)
        code, out = self.run_bridge("prepare", sidecar)
        self.assertEqual(code, dsh_bridge.EXIT_OK, out)


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


class SnapshotOpTests(BridgeTestCase):
    def test_snapshot_freezes_a_seed_candidate(self) -> None:
        sidecar = self.sidecar(self.campaign("smoke-campaign"))
        self.run_bridge("prepare", sidecar)
        code, out = self.run_bridge("snapshot", sidecar)
        self.assertEqual(code, dsh_bridge.EXIT_OK, out)
        self.assertEqual(out["candidate"]["mode"], "seed")
        self.assertEqual(len(out["candidate"]["digest"]), 64)
        kinds = [row["kind"] for row in receipts.load_journal(self.run_root)]
        self.assertIn("snapshot", kinds)


class RecoveryTests(BridgeTestCase):
    def test_an_interrupted_validation_is_quarantined_and_never_passes(self) -> None:
        sidecar = self.sidecar(self.campaign("smoke-campaign"))
        self.run_bridge("prepare", sidecar)
        validation = receipts.paths(self.run_root).validation
        validation.mkdir(parents=True, exist_ok=True)
        (validation / "r1-deadbeef.intent.json").write_text("{}", encoding="utf-8")

        code, out = self.run_bridge("recover", sidecar)
        self.assertEqual(code, dsh_bridge.EXIT_OK, out)
        self.assertEqual(out["quarantined"], ["r1-deadbeef.intent.json"])
        self.assertFalse(out["receipt_current"])
        self.assertIsNone(receipts.load_current_receipt(self.run_root))
        quarantined = receipts.paths(self.run_root).quarantine
        self.assertTrue((quarantined / "r1-deadbeef.intent.json").is_file())

    def test_a_completed_validation_leaves_no_intent(self) -> None:
        sidecar = self.sidecar(self.campaign("smoke-campaign"), retry=("stateful", 1))
        self.run_bridge("prepare", sidecar)
        self.run_bridge("validate", sidecar)
        leftovers = list(receipts.paths(self.run_root).validation.glob("*.intent.json"))
        self.assertEqual(leftovers, [])

    def test_recover_reports_a_current_receipt_after_a_pass(self) -> None:
        sidecar = self.sidecar(self.campaign("pass-campaign"))
        self.run_bridge("prepare", sidecar)
        self.run_bridge("validate", sidecar)
        code, out = self.run_bridge("recover", sidecar)
        self.assertEqual(code, dsh_bridge.EXIT_OK, out)
        self.assertTrue(out["receipt_current"])
        self.assertEqual(out["quarantined"], [])
        self.assertEqual(out["journal_problems"], [])

    def test_plugin_invocation_avoids_flags_missing_before_311(self) -> None:
        """-P is Python 3.11+; the CI matrix includes 3.10, where it is unknown."""
        source = (REPO / "dsh" / "eu4-goal-plugin" / "lib" / "index.js").read_text(
            encoding="utf-8"
        )
        self.assertNotIn("'-P'", source)
        self.assertNotIn("'--safe-path'", source)

    def test_two_concurrent_validations_yield_one_receipt(self) -> None:
        """Real processes: the run-root lock must serialize actual contenders."""
        sidecar = self.sidecar(self.campaign("pass-campaign"))
        self.run_bridge("prepare", sidecar)

        env = {
            **os.environ,
            "PYTHONPATH": str(REPO),
            "PYTHONNOUSERSITE": "1",
        }
        # sys.executable, not a repo-local venv: this test also runs on CI,
        # where there is no .venv.
        command = [sys.executable, "-m", "automation.dsh_bridge",
                   "validate", "--sidecar", str(sidecar)]
        processes = [
            subprocess.Popen(command, cwd=str(REPO), env=env,
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            for _ in range(2)
        ]
        outputs = []
        for process in processes:
            stdout, _stderr = process.communicate(timeout=120)
            outputs.append(json.loads(stdout))

        busy = [out for out in outputs if out.get("kind") == "busy"]
        self.assertLessEqual(len(busy), 1, "the run-root lock must serialize runs")
        receipts_dir = receipts.paths(self.run_root).receipts
        written = list(receipts_dir.glob("*.json"))
        self.assertGreaterEqual(len(written), 1)
        for path in written:
            json.loads(path.read_text(encoding="utf-8"))
        pointer = json.loads(
            receipts.paths(self.run_root).current.read_text(encoding="utf-8")
        )
        self.assertTrue((receipts_dir / pointer["receipt"]).is_file())
        self.assertEqual(receipts.verify_journal(self.run_root), [])


class CheckerCompatTests(BridgeTestCase):
    def test_a_remediable_baseline_is_compatible(self) -> None:
        sidecar = self.sidecar(self.campaign("smoke-campaign"))
        code, out = self.run_bridge("checker-compat", sidecar)
        self.assertEqual(code, dsh_bridge.EXIT_OK, out)
        self.assertEqual(out["compat_verdict"], "ok")
        self.assertEqual(out["baseline"]["verdict"], "fail")
        self.assertTrue(all(row.get("inside_infrastructure", True)
                            for row in out["imports"]))

    def test_a_checker_that_passes_its_own_baseline_is_unsupported(self) -> None:
        sidecar = self.sidecar(self.campaign("pass-campaign"))
        code, out = self.run_bridge("checker-compat", sidecar)
        self.assertEqual(code, dsh_bridge.EXIT_INTEGRITY)
        self.assertEqual(out["compat_verdict"], "unsupported")
        self.assertTrue(any("already passes" in p for p in out["problems"]))

    def test_a_checker_without_a_verdict_is_unsupported(self) -> None:
        sidecar = self.sidecar(self.campaign("broken-campaign"))
        code, out = self.run_bridge("checker-compat", sidecar)
        self.assertEqual(code, dsh_bridge.EXIT_INTEGRITY)
        self.assertEqual(out["compat_verdict"], "unsupported")
        self.assertTrue(any("no verdict" in p for p in out["problems"]))


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

    def _forge_receipt(self) -> dict:
        """Rewrite the current receipt to pass and re-hash it, so it verifies."""
        pointer = receipts.load_current(self.run_root)
        path = receipts.paths(self.run_root).receipts / pointer["receipt"]
        body = json.loads(path.read_text(encoding="utf-8"))
        body["outcome"]["checker_verdict"] = "pass"
        body.pop("receipt_digest", None)
        body["receipt_digest"] = receipts.digest_text(receipts.canonical_json(body))
        path.write_text(json.dumps(body), encoding="utf-8")
        self.assertEqual(receipts.verify_receipt_digest(body), [],
                         "the forgery must look self-consistent")
        return body

    def _rechain_journal(self, receipt_name: str, receipt_digest: str) -> None:
        """Rewrite the whole journal so its links are valid again."""
        path = receipts.paths(self.run_root).journal
        rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()
                if line.strip()]
        previous = None
        rebuilt = []
        for index, row in enumerate(rows, start=1):
            row.pop("link", None)
            row["seq"] = index
            row["prev"] = previous
            if row.get("kind") == "receipt" and row.get("name") == receipt_name:
                row["receipt_digest"] = receipt_digest
            row["link"] = receipts.digest_text(receipts.canonical_json(row))
            previous = row["link"]
            rebuilt.append(row)
        path.write_text(
            "\n".join(json.dumps(row, sort_keys=True) for row in rebuilt) + "\n",
            encoding="utf-8",
        )
        self.assertEqual(receipts.verify_journal(self.run_root), [])

    def test_a_rehashed_forged_pass_is_caught_by_the_journal(self) -> None:
        sidecar = self.sidecar(self.campaign("failing-campaign"))
        self.run_bridge("prepare", sidecar)
        self.run_bridge("validate", sidecar)
        self._forge_receipt()
        code, out = self.run_bridge("finalize", sidecar)
        self.assertEqual(code, dsh_bridge.EXIT_INTEGRITY)
        self.assertEqual(out["kind"], "journal_mismatch")
        self.assertFalse((self.run_root / "finalization.json").exists())

    def test_a_forged_receipt_and_journal_still_cannot_authorize_completion(self) -> None:
        """Amendment B: with tamper evidence defeated, the fresh run decides."""
        sidecar = self.sidecar(self.campaign("failing-campaign"))
        self.run_bridge("prepare", sidecar)
        self.run_bridge("validate", sidecar)
        body = self._forge_receipt()
        self._rechain_journal(
            receipts.load_current(self.run_root)["receipt"], body["receipt_digest"]
        )

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
