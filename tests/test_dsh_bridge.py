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
        if operation == "prepare":
            # Only the launcher creates the anchor; the plugin's prepare verifies.
            argv.append("--create-anchor")
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
        # The unified vocabulary: infrastructure failures ask for native pause.
        self.assertEqual(out["goal_policy"], dsh_bridge.GOAL_POLICY["unavailable"])
        self.assertEqual(out["goal_policy"], "pause")
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
        dsh_bridge._trusted_modules_digest = lambda *a, **k: ("0" * 64, [])
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


class TrustedClosureTests(BridgeTestCase):
    def _campaign_importing(self, module: str) -> Path:
        campaign = self.campaign("pass-campaign")
        checker = campaign / "accept.py"
        checker.write_text(
            checker.read_text(encoding="utf-8")
            + f"\nfrom automation import {module}  # noqa: F401,E402\n",
            encoding="utf-8",
        )
        return campaign

    def test_the_closure_follows_transitive_checker_imports(self) -> None:
        """A checker that imports one module also executes what *it* imports."""
        campaign = self._campaign_importing("gfx_draw_accept_v2")
        code, out = self.run_bridge("prepare", self.sidecar(campaign))
        self.assertEqual(code, dsh_bridge.EXIT_OK, out)
        names = {row["module"] for row in out["identities"]["trusted_modules"]}
        # None of these are in the fixed list; they are reached through the
        # checker's own import graph.
        for expected in ("gfx_draw_accept_v2", "gfx_checkpoint", "gfx_contracts",
                         "gfx_evidence_policy", "launch_ledger"):
            self.assertIn(expected, names)
            self.assertNotIn(expected, dsh_bridge.TRUSTED_MODULES)

    def test_editing_a_transitive_dependency_changes_the_digest(self) -> None:
        campaign = self._campaign_importing("gfx_draw_accept_v2")
        checker = campaign / "accept.py"
        before = dsh_bridge._trusted_modules_digest(checker)[0]
        target = REPO / "automation" / "gfx_evidence_policy.py"
        original = target.read_bytes()
        try:
            target.write_bytes(original + b"\n# probe\n")
            after = dsh_bridge._trusted_modules_digest(checker)[0]
        finally:
            target.write_bytes(original)
        self.assertNotEqual(before, after)

    def test_an_unresolvable_checker_import_refuses_rather_than_anchoring(self) -> None:
        campaign = self.campaign("pass-campaign")
        checker = campaign / "accept.py"
        checker.write_text(
            checker.read_text(encoding="utf-8")
            + "\nimport automation.no_such_module  # noqa: F401,E402\n",
            encoding="utf-8",
        )
        code, out = self.run_bridge("prepare", self.sidecar(campaign))
        self.assertEqual(code, dsh_bridge.EXIT_INTEGRITY, out)
        self.assertEqual(out["kind"], "checker_dependency_unresolved")


class GoalPolicyContractTests(unittest.TestCase):
    """The plugin must act on the bridge's vocabulary, not a guessed one."""

    def test_the_plugin_handles_exactly_the_terminal_bridge_policies(self) -> None:
        source = (REPO / "dsh" / "eu4-goal-plugin" / "lib" / "index.js").read_text(
            encoding="utf-8"
        )
        terminal = {
            dsh_bridge.GOAL_POLICY["blocked"],
            dsh_bridge.GOAL_POLICY["unavailable"],
        }
        self.assertEqual(terminal, {"block", "pause"})
        for value in terminal:
            self.assertIn(f"policy !== '{value}'", source)
        # The non-terminal policies must not be treated as terminal.
        for verdict in ("pass", "fail", "inconclusive"):
            self.assertNotIn(
                f"policy !== '{dsh_bridge.GOAL_POLICY[verdict]}'", source
            )


class ReplayTests(BridgeTestCase):
    """Finalization replays the frozen candidate over its pinned baseline."""

    def test_replay_keeps_head_at_the_pin_for_a_baseline_sensitive_checker(self) -> None:
        baseline = subprocess.run(
            ["git", "-C", str(REPO), "rev-parse", "HEAD"],
            capture_output=True, text=True, check=True,
        ).stdout.strip()
        campaign = REPO / ".local" / "dsh-test" / f"replay-{uuid.uuid4().hex[:8]}"
        (campaign / "seed").mkdir(parents=True)
        (campaign / "baseline.txt").write_text(baseline + "\n", encoding="utf-8")
        (campaign / "objective.md").write_text("# Objective\n\nready\n", encoding="utf-8")
        (campaign / "campaign.json").write_text(json.dumps({
            "id": f"replay-{uuid.uuid4().hex[:6]}",
            "objective": "objective.md",
            "workspace": "seed",
            "accept": ["python3", "accept.py"],
            "accept_timeout_seconds": 60,
        }), encoding="utf-8")
        # A checker in the shape of the real reconstruction ones: it insists the
        # worktree HEAD is still the pinned commit, that the reconstruction is
        # uncommitted work, and that its research checkpoint sits beside the
        # workspace -- the invariant that made the first replay revision fail.
        (campaign / "accept.py").write_text(
            """#!/usr/bin/env python3
import json, subprocess, sys
from pathlib import Path

workspace = Path(sys.argv[1])
expected = (Path(__file__).parent / "baseline.txt").read_text().strip()
head = subprocess.run(["git", "-C", str(workspace), "rev-parse", "HEAD"],
                      capture_output=True, text=True).stdout.strip()
dirty = subprocess.run(["git", "-C", str(workspace), "status", "--porcelain"],
                       capture_output=True, text=True).stdout.strip()
if head != expected:
    print(json.dumps({"verdict": "fail",
                      "diagnostics": f"worktree HEAD is not the pinned commit: {head}"}))
    raise SystemExit(0)
checkpoint = Path(workspace).resolve().parent / "research-checkpoint.md"
if not checkpoint.is_file():
    print(json.dumps({"verdict": "fail",
                      "diagnostics": "replacement edited before the research checkpoint"}))
    raise SystemExit(0)
if not dirty:
    print(json.dumps({"verdict": "fail", "diagnostics": "no uncommitted reconstruction"}))
    raise SystemExit(0)
print(json.dumps({"verdict": "pass", "diagnostics": "pinned baseline, uncommitted delta"}))
""",
            encoding="utf-8",
        )
        self.addCleanup(shutil.rmtree, campaign, ignore_errors=True)

        worktree = self.root / "candidate-wt"
        subprocess.run(
            ["git", "-C", str(REPO), "worktree", "add", "--detach",
             str(worktree), baseline],
            capture_output=True, check=True,
        )
        self.addCleanup(subprocess.run, ["git", "-C", str(REPO), "worktree",
                                         "remove", "--force", "--force", str(worktree)],
                        capture_output=True)
        (worktree / "candidate.txt").write_text("ready\n", encoding="utf-8")
        (self.root / "research-checkpoint.md").write_text(
            "# Research\n\nverified observations\n", encoding="utf-8"
        )

        sidecar = self.sidecar(campaign, allowed=("candidate.txt",))
        data = json.loads(sidecar.read_text(encoding="utf-8"))
        data["candidate"] = {
            "mode": "worktree",
            "workspace": str(worktree),
            "policy": {"allowed_paths": ["candidate.txt"]},
        }
        data["candidate_baseline_commit"] = baseline
        data["replay_inputs"] = ["research-checkpoint.md"]
        sidecar.write_text(json.dumps(data), encoding="utf-8")

        code, prepared = self.run_bridge("prepare", sidecar)
        self.assertEqual(code, dsh_bridge.EXIT_OK, prepared)
        code, validated = self.run_bridge("validate", sidecar)
        self.assertEqual(code, dsh_bridge.EXIT_OK, validated)
        self.assertEqual(validated["checker_verdict"], "pass")

        code, final = self.run_bridge("finalize", sidecar)
        self.assertEqual(code, dsh_bridge.EXIT_OK, final)
        self.assertEqual(final.get("checker_verdict"), "pass", final)
        self.assertEqual(final.get("replay", {}).get("verdict"), "pass", final)
        self.assertEqual(final["replay"]["head"], baseline)
        self.assertEqual(final["replay"]["baseline"], baseline)
        self.assertEqual(
            [row["path"] for row in final["replay"]["inputs"]],
            ["research-checkpoint.md"],
        )


class InputBindingTests(BridgeTestCase):
    def test_inputs_that_changed_after_validation_refuse_finalization(self) -> None:
        """Generated inputs are bound at validation and compared at finalization."""
        sidecar = self.sidecar(self.campaign("pass-campaign"))
        self.run_bridge("prepare", sidecar)
        self.assertEqual(self.run_bridge("validate", sidecar)[0], dsh_bridge.EXIT_OK)

        original = dsh_bridge.snapshot.ignored_digest
        values = iter(["a" * 64, "b" * 64])
        dsh_bridge.snapshot.ignored_digest = lambda workspace: next(values, "b" * 64)
        self.addCleanup(setattr, dsh_bridge.snapshot, "ignored_digest", original)

        code, out = self.run_bridge("finalize", sidecar)
        self.assertEqual(code, dsh_bridge.EXIT_INTEGRITY, out)
        self.assertEqual(out["kind"], "inputs_changed")


class IncompleteInventoryTests(BridgeTestCase):
    def test_a_truncated_inventory_refuses_a_pass(self) -> None:
        sidecar = self.sidecar(self.campaign("pass-campaign"))
        self.run_bridge("prepare", sidecar)
        original = dsh_bridge.snapshot.ignored_inputs
        dsh_bridge.snapshot.ignored_inputs = lambda ws: {"entries": [], "truncated": True}
        self.addCleanup(setattr, dsh_bridge.snapshot, "ignored_inputs", original)
        code, out = self.run_bridge("validate", sidecar)
        self.assertEqual(code, dsh_bridge.EXIT_INTEGRITY, out)
        self.assertEqual(out["kind"], "inputs_incomplete")

    def test_the_campaign_can_accept_an_incomplete_inventory(self) -> None:
        sidecar = self.sidecar(self.campaign("pass-campaign"))
        data = json.loads(sidecar.read_text(encoding="utf-8"))
        data["allow_incomplete_inputs"] = True
        sidecar.write_text(json.dumps(data), encoding="utf-8")
        self.run_bridge("prepare", sidecar)
        original = dsh_bridge.snapshot.ignored_inputs
        dsh_bridge.snapshot.ignored_inputs = lambda ws: {"entries": [], "truncated": True}
        self.addCleanup(setattr, dsh_bridge.snapshot, "ignored_inputs", original)
        code, out = self.run_bridge("validate", sidecar)
        self.assertEqual(code, dsh_bridge.EXIT_OK, out)
        self.assertEqual(out["checker_verdict"], "pass")


class ContractPackageTests(BridgeTestCase):
    def test_the_closure_covers_the_contract_package_modules(self) -> None:
        """`gfx_contracts` picks a validator version at runtime."""
        campaign = self.campaign("pass-campaign")
        checker = campaign / "accept.py"
        checker.write_text(
            checker.read_text(encoding="utf-8")
            + "\nfrom automation.gfx_contracts.api import *  # noqa: F401,F403,E402\n",
            encoding="utf-8",
        )
        code, out = self.run_bridge("prepare", self.sidecar(campaign))
        self.assertEqual(code, dsh_bridge.EXIT_OK, out)
        names = {row["module"] for row in out["identities"]["trusted_modules"]}
        self.assertIn("gfx_contracts.api", names)
        self.assertIn("gfx_contracts.v0", names)

    def test_editing_a_dynamically_loaded_contract_changes_the_digest(self) -> None:
        campaign = self.campaign("pass-campaign")
        checker = campaign / "accept.py"
        checker.write_text(
            checker.read_text(encoding="utf-8")
            + "\nimport automation.gfx_contracts  # noqa: F401,E402\n",
            encoding="utf-8",
        )
        before = dsh_bridge._trusted_modules_digest(checker)[0]
        target = REPO / "automation" / "gfx_contracts" / "v0.py"
        original = target.read_bytes()
        try:
            target.write_bytes(original + b"\n# probe\n")
            after = dsh_bridge._trusted_modules_digest(checker)[0]
        finally:
            target.write_bytes(original)
        self.assertNotEqual(before, after)


class CredentialIsolationTests(BridgeTestCase):
    """A checker, and the tests it runs, must not see provider credentials."""

    def test_a_checker_cannot_read_provider_credentials(self) -> None:
        import os
        from unittest import mock

        campaign = self.campaign("pass-campaign")
        observed = campaign / "observed-env.json"
        checker = campaign / "accept.py"
        checker.write_text(
            "#!/usr/bin/env python3\n"
            "import json, os, sys\n"
            "from pathlib import Path\n"
            "keys = sorted(os.environ)\n"
            "Path(__file__).with_name('observed-env.json').write_text(json.dumps(keys))\n"
            "print(json.dumps({'verdict': 'pass', 'diagnostics': 'env recorded'}))\n",
            encoding="utf-8",
        )
        sidecar = self.sidecar(campaign)
        self.run_bridge("prepare", sidecar)
        with mock.patch.dict(os.environ, {
            "OPENCODE_GO_API_KEY": "secret-value",
            "DEEPSEEK_API_KEY": "secret-value",
            "SOME_SERVICE_TOKEN": "secret-value",
        }):
            code, out = self.run_bridge("validate", sidecar)
        self.assertEqual(code, dsh_bridge.EXIT_OK, out)
        seen = json.loads(observed.read_text(encoding="utf-8"))
        for name in ("OPENCODE_GO_API_KEY", "DEEPSEEK_API_KEY", "SOME_SERVICE_TOKEN"):
            self.assertNotIn(name, seen)
        # Toolchain settings still pass through.
        self.assertIn("PATH", seen)


class InfrastructurePolicyTests(BridgeTestCase):
    """The policy the plugin acts on must come from real bridge responses."""

    def _policy_for(self, checker_body: str, *, timeout: int = 30) -> dict:
        campaign = self.campaign("pass-campaign")
        (campaign / "accept.py").write_text(checker_body, encoding="utf-8")
        sidecar = self.sidecar(campaign)
        data = json.loads(sidecar.read_text(encoding="utf-8"))
        data["limits"]["validation_timeout_seconds"] = timeout
        sidecar.write_text(json.dumps(data), encoding="utf-8")
        self.run_bridge("prepare", sidecar)
        code, out = self.run_bridge("validate", sidecar)
        self.assertEqual(code, dsh_bridge.EXIT_OK, out)
        return out

    def test_unreadable_output_requests_pause(self) -> None:
        out = self._policy_for(
            "#!/usr/bin/env python3\nprint('this is not json')\n"
        )
        self.assertEqual(out["goal_policy"], "pause")
        self.assertEqual(out["classification"], "infrastructure_error")

    def test_a_timeout_requests_pause(self) -> None:
        out = self._policy_for(
            "#!/usr/bin/env python3\nimport time\ntime.sleep(10)\n", timeout=1
        )
        self.assertEqual(out["goal_policy"], "pause")
        self.assertIn("timeout", out["kind"])

    def test_a_blocked_verdict_requests_block(self) -> None:
        out = self._policy_for(
            "#!/usr/bin/env python3\nimport json\n"
            "print(json.dumps({'verdict': 'blocked', 'diagnostics': 'pin mismatch'}))\n"
        )
        self.assertEqual(out["goal_policy"], "block")

    def test_a_failing_verdict_stays_remediable(self) -> None:
        out = self._policy_for(
            "#!/usr/bin/env python3\nimport json\n"
            "print(json.dumps({'verdict': 'fail', 'diagnostics': 'not ready'}))\n"
        )
        self.assertEqual(out["goal_policy"], "remediate")


class ReplayInputBindingTests(BridgeTestCase):
    def _fixture(self) -> Path:
        baseline = subprocess.run(
            ["git", "-C", str(REPO), "rev-parse", "HEAD"],
            capture_output=True, text=True, check=True,
        ).stdout.strip()
        campaign = REPO / ".local" / "dsh-test" / f"replayin-{uuid.uuid4().hex[:8]}"
        (campaign / "seed").mkdir(parents=True)
        (campaign / "objective.md").write_text("# Objective\n\nready\n", encoding="utf-8")
        (campaign / "campaign.json").write_text(json.dumps({
            "id": f"replayin-{uuid.uuid4().hex[:6]}",
            "objective": "objective.md",
            "workspace": "seed",
            "accept": ["python3", "accept.py"],
            "accept_timeout_seconds": 60,
        }), encoding="utf-8")
        (campaign / "accept.py").write_text(
            "#!/usr/bin/env python3\n"
            "import json, sys\n"
            "from pathlib import Path\n"
            "checkpoint = Path(sys.argv[1]).resolve().parent / 'research-checkpoint.md'\n"
            "if not checkpoint.is_file():\n"
            "    print(json.dumps({'verdict': 'fail', 'diagnostics': 'no checkpoint'}))\n"
            "    raise SystemExit(0)\n"
            "print(json.dumps({'verdict': 'pass', 'diagnostics': 'checkpoint present'}))\n",
            encoding="utf-8",
        )
        self.addCleanup(shutil.rmtree, campaign, ignore_errors=True)

        worktree = self.root / "candidate-wt"
        subprocess.run(
            ["git", "-C", str(REPO), "worktree", "add", "--detach", str(worktree), baseline],
            capture_output=True, check=True,
        )
        self.addCleanup(subprocess.run, ["git", "-C", str(REPO), "worktree", "remove",
                                         "--force", "--force", str(worktree)],
                        capture_output=True)
        (worktree / "candidate.txt").write_text("ready\n", encoding="utf-8")
        (self.root / "research-checkpoint.md").write_text("# Research\n", encoding="utf-8")

        sidecar = self.sidecar(campaign, allowed=("candidate.txt",))
        data = json.loads(sidecar.read_text(encoding="utf-8"))
        data["candidate"] = {"mode": "worktree", "workspace": str(worktree),
                             "policy": {"allowed_paths": ["candidate.txt"]}}
        data["candidate_baseline_commit"] = baseline
        data["replay_inputs"] = ["research-checkpoint.md"]
        sidecar.write_text(json.dumps(data), encoding="utf-8")
        return sidecar

    def test_a_checkpoint_changed_after_validation_refuses_finalization(self) -> None:
        sidecar = self._fixture()
        self.assertEqual(self.run_bridge("prepare", sidecar)[0], dsh_bridge.EXIT_OK)
        code, out = self.run_bridge("validate", sidecar)
        self.assertEqual(code, dsh_bridge.EXIT_OK, out)
        self.assertEqual(out["checker_verdict"], "pass")

        (self.root / "research-checkpoint.md").write_text("# Research\n\nrewritten\n",
                                                          encoding="utf-8")
        code, out = self.run_bridge("finalize", sidecar)
        self.assertEqual(code, dsh_bridge.EXIT_INTEGRITY, out)
        self.assertEqual(out["kind"], "replay_inputs_changed")
