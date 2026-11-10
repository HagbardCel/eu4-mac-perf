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
        self.assertIn("equals the infrastructure root", out["detail"])
        self.assertIn("could be shadowed", out["detail"])

    def test_a_candidate_under_local_is_allowed(self) -> None:
        """The agent's sandbox is the session workspace, so the candidate must be
        inside the checkout -- but only under .local/, never in the trusted tree."""
        sidecar = self.sidecar(self.campaign("smoke-campaign"))
        data = json.loads(sidecar.read_text(encoding="utf-8"))
        run_root = REPO / ".local" / "dsh-test" / f"roots-{uuid.uuid4().hex[:8]}"
        data["run_root"] = str(run_root)
        # Drop the helper's absolute workspace so it defaults to run_root/workspace.
        data["candidate"].pop("workspace", None)
        sidecar.write_text(json.dumps(data), encoding="utf-8")
        self.addCleanup(shutil.rmtree, run_root, True)
        code, out = self.run_bridge("prepare", sidecar)
        self.assertEqual(code, dsh_bridge.EXIT_OK, out)
        self.assertTrue(Path(out["workspace"]).is_dir())
        self.assertTrue(
            str(out["workspace"]).startswith(str(REPO / ".local")),
            out["workspace"],
        )

    def test_a_candidate_elsewhere_in_the_checkout_is_refused(self) -> None:
        sidecar = self.sidecar(self.campaign("smoke-campaign"))
        data = json.loads(sidecar.read_text(encoding="utf-8"))
        data["candidate"] = {"mode": "seed", "workspace": str(REPO / "dsh"),
                             "policy": {"allowed_paths": ["*"]}}
        data["candidate_baseline_commit"] = "6fa1888c8d1cf0f3a6e8205a03ae171d4ce1ba36"
        sidecar.write_text(json.dumps(data), encoding="utf-8")
        code, out = self.run_bridge("prepare", sidecar)
        self.assertEqual(code, dsh_bridge.EXIT_INTEGRITY)
        self.assertEqual(out["kind"], "execution_root")
        self.assertIn("must live under .local/", out["detail"])

    def test_prepare_does_not_re_anchor_a_changed_checker(self) -> None:
        """The plugin prepares before every check, so prepare must verify, not rewrite."""
        campaign = self.campaign("smoke-campaign")
        sidecar = self.sidecar(campaign)
        code, first = self.run_bridge("prepare", sidecar)
        self.assertEqual(code, dsh_bridge.EXIT_OK, first)
        self.assertTrue(first["anchored_now"])

        checker = campaign / "accept.py"
        checker.write_text(checker.read_text(encoding="utf-8") + "\n# edited\n",
                           encoding="utf-8")

        code, second = self.run_bridge("prepare", sidecar)
        self.assertEqual(code, dsh_bridge.EXIT_INTEGRITY, second)
        self.assertEqual(second["kind"], "identity_modified")
        self.assertIn("checker_sha256", second["detail"])

    def test_editing_a_trusted_module_is_refused(self) -> None:
        """What the checker path executes is digested, not just the commit."""
        sidecar = self.sidecar(self.campaign("pass-campaign"))
        self.run_bridge("prepare", sidecar)
        original = dsh_bridge._trusted_modules_digest
        dsh_bridge._trusted_modules_digest = lambda: "0" * 64
        self.addCleanup(setattr, dsh_bridge, "_trusted_modules_digest", original)
        code, out = self.run_bridge("validate", sidecar)
        self.assertEqual(code, dsh_bridge.EXIT_INTEGRITY, out)
        self.assertEqual(out["kind"], "identity_modified")
        self.assertIn("trusted_modules_digest", out["detail"])

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

    def _forge_record(self) -> dict:
        """Rewrite the qualification to pass and re-hash it, so it verifies."""
        path = receipts.paths(self.run_root).qualification
        body = json.loads(path.read_text(encoding="utf-8"))
        body["outcome"]["checker_verdict"] = "pass"
        body.pop("receipt_digest", None)
        body["receipt_digest"] = receipts.digest_text(receipts.canonical_json(body))
        path.write_text(json.dumps(body), encoding="utf-8")
        self.assertEqual(receipts.verify_receipt_digest(body), [],
                         "the forgery must look self-consistent")
        return body

    def test_a_rehashed_forged_pass_cannot_authorize_completion(self) -> None:
        """A stored pass is not authority: the confirmation run decides."""
        sidecar = self.sidecar(self.campaign("failing-campaign"))
        self.run_bridge("prepare", sidecar)
        self.run_bridge("validate", sidecar)
        self._forge_record()
        code, out = self.run_bridge("finalize", sidecar)
        self.assertEqual(code, dsh_bridge.EXIT_INTEGRITY, out)
        self.assertEqual(out["kind"], "fresh_validation_failed")
        self.assertFalse((self.run_root / "finalization.json").exists())

    def test_finalize_detects_an_inconsistent_record(self) -> None:
        sidecar = self.sidecar(self.campaign("failing-campaign"))
        self.run_bridge("prepare", sidecar)
        self.run_bridge("validate", sidecar)
        path = receipts.paths(self.run_root).qualification
        body = json.loads(path.read_text(encoding="utf-8"))
        body["outcome"]["checker_verdict"] = "pass"
        path.write_text(json.dumps(body), encoding="utf-8")
        code, out = self.run_bridge("finalize", sidecar)
        self.assertEqual(code, dsh_bridge.EXIT_INTEGRITY)
        self.assertEqual(out["kind"], "receipt_corrupt")


class ConfirmationRoundTests(BridgeTestCase):
    def test_an_unchanged_correct_candidate_fails_once_then_passes(self) -> None:
        """The smoke checker fails once by design; asking again must work.

        There is no retry policy or budget: each validate simply runs it.
        """
        sidecar = self.sidecar(self.campaign("smoke-campaign"))
        self.run_bridge("prepare", sidecar)

        code, first = self.run_bridge("validate", sidecar)
        self.assertEqual(code, dsh_bridge.EXIT_OK)
        self.assertEqual(first["checker_verdict"], "fail")

        code, second = self.run_bridge("validate", sidecar)
        self.assertEqual(code, dsh_bridge.EXIT_OK)
        self.assertEqual(second["checker_verdict"], "pass")
        self.assertEqual(second["goal_policy"], "qualified")
        self.assertEqual(first["candidate_digest"], second["candidate_digest"])

        record = receipts.load_current_receipt(self.run_root)
        self.assertEqual(record["outcome"]["checker_verdict"], "pass")

    def test_two_concurrent_validations_leave_one_record(self) -> None:
        """Real processes: the run-root lock must serialize contenders."""
        sidecar = self.sidecar(self.campaign("pass-campaign"))
        self.run_bridge("prepare", sidecar)

        env = {**os.environ, "PYTHONPATH": str(REPO), "PYTHONNOUSERSITE": "1"}
        command = [sys.executable, "-m", "automation.dsh_bridge",
                   "validate", "--sidecar", str(sidecar)]
        processes = [
            subprocess.Popen(command, cwd=str(REPO), env=env,
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            for _ in range(2)
        ]
        outputs = [json.loads(process.communicate(timeout=120)[0]) for process in processes]

        busy = [out for out in outputs if out.get("kind") == "busy"]
        self.assertLessEqual(len(busy), 1, "the run-root lock must serialize runs")
        record = receipts.load_current_receipt(self.run_root)
        self.assertIsNotNone(record)
        self.assertEqual(receipts.verify_receipt_digest(record), [])
        self.assertEqual(len(list(Path(self.run_root).glob("*.json"))), 2)  # identity + record
