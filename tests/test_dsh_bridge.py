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
from unittest import mock

from automation import dsh_bridge
from automation import dsh_campaign
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

    def test_candidate_edit_does_not_change_authority(self) -> None:
        sidecar = self.sidecar(self.campaign("smoke-campaign"))
        code, first = self.run_bridge("prepare", sidecar)
        self.assertEqual(code, dsh_bridge.EXIT_OK)
        # Editing the candidate is the normal campaign action, not drift.
        (self.workspace / "marker.txt").write_text("edited\n", encoding="utf-8")
        code, second = self.run_bridge("prepare", sidecar)
        self.assertEqual(code, dsh_bridge.EXIT_OK, second)
        self.assertNotEqual(first["identity_digest"], second["identity_digest"])
        code, context = self.run_bridge("context", sidecar)
        self.assertEqual(code, dsh_bridge.EXIT_OK, context)
        self.assertEqual(context["status"], "authorized")
        self.assertEqual(context["drift"], [])
        # A committed candidate also remains authorized; only frozen authority
        # (workspace/policy/budget/checker) refuses.
        subprocess.run(["git", "-C", str(self.workspace), "init", "-q"],
                       check=False)
        subprocess.run(["git", "-C", str(self.workspace), "add", "."],
                       check=False)
        subprocess.run(["git", "-C", str(self.workspace), "-c",
                        "user.email=t@example.invalid", "-c",
                        "user.name=Test", "commit", "-qm", "edit"],
                       check=False)
        code, third = self.run_bridge("prepare", sidecar)
        self.assertEqual(code, dsh_bridge.EXIT_OK, third)

    def test_prepare_emits_a_compact_startup_block(self) -> None:
        sidecar = self.sidecar(self.campaign("smoke-campaign"))
        code, out = self.run_bridge("prepare", sidecar)
        self.assertEqual(code, dsh_bridge.EXIT_OK)
        startup = out["startup"]
        self.assertEqual(startup["campaign_id"], out["campaign_id"])
        self.assertEqual(startup["workspace"], str(self.workspace.resolve()))
        self.assertEqual(startup["workspace_mode"], "seed")
        self.assertTrue(startup["workspace_writable"])
        self.assertIsNone(startup["workspace_head"])
        self.assertEqual(startup["checker"], out["execution_roots"]["checker"])
        self.assertEqual(startup["run_root"], str(self.run_root.resolve()))
        self.assertEqual(startup["objective_digest"],
                         out["identities"]["objective_digest"])
        self.assertEqual(startup["live_capture"]["enabled"], False)
        self.assertEqual(startup["closing"]["max_rounds"], 4)
        self.assertEqual(startup["closing"]["max_validations"], 6)
        self.assertEqual(startup["closing"]["wall_clock_seconds"], 600)
        self.assertTrue(startup["closing"]["require_checker_pass"])
        self.assertEqual(startup["allowed_paths"], ["*"])
        self.assertEqual(startup["forbidden_paths"], [])
        self.assertIn(startup["first_command"][0], ("git", "ls"))
        self.assertIn("workspace:", out["startup_text"])
        self.assertIn(str(self.workspace.resolve()), out["startup_text"])
        self.assertIn("validations used 0 of 6", out["startup_text"])
        self.assertIn("last attempt: none recorded", out["startup_text"])
        self.assertIn("case context: no locator is bound", out["startup_text"])
        self.assertTrue(out["goal_text"].startswith(out["objective"]))
        self.assertIn(out["startup_text"], out["goal_text"])
        # The writability probe must not leave candidate residue.
        self.assertEqual(
            list(self.workspace.glob(".dsh-prepare-probe-*")), [])

    def snapshot_paths(self, *roots) -> dict:
        """Every relevant path's type, mtime and content digest."""
        snapshot: dict = {}
        for root in roots:
            root = Path(root)
            candidates = [root] if root.is_file() else sorted(root.rglob("*"))
            for path in candidates:
                stat = path.lstat()
                rel = str(path)
                if path.is_dir() and not path.is_symlink():
                    snapshot[rel] = ("dir", stat.st_mtime_ns, None)
                elif path.is_file() and not path.is_symlink():
                    snapshot[rel] = (
                        "file", stat.st_mtime_ns, receipts.digest_file(path))
                else:
                    snapshot[rel] = ("link", stat.st_mtime_ns, os.readlink(path))
        return snapshot

    def test_context_is_read_only_and_matches_prepare(self) -> None:
        campaign = self.campaign("smoke-campaign")
        sidecar = self.sidecar(campaign)
        self.run_root.mkdir(parents=True)
        # Representative durable artifacts: a binding, budget/state, a lease,
        # an append-only log and the campaign lock. Context must not touch any
        # of them (or create anything new).
        for name, body in (
            ("plugin-binding.json", '{"goal_id": "g1"}'),
            ("state.json", '{"schema": "campaign-state"}'),
            ("live-launch.json", '{"used": 1}'),
            ("lease.json", '{"holder": "test"}'),
            ("validations.jsonl", '{"attempt_id": "kept"}\n'),
            ("context.log", "kept\n"),
        ):
            (self.run_root / name).write_text(body, encoding="utf-8")
        code, prepared = self.run_bridge("prepare", sidecar)
        self.assertEqual(code, dsh_bridge.EXIT_OK)
        before = self.snapshot_paths(
            self.run_root, self.workspace, sidecar, campaign)
        code, context = self.run_bridge("context", sidecar)
        self.assertEqual(code, dsh_bridge.EXIT_OK, context)
        self.assertTrue(context["anchored"])
        self.assertEqual(context["status"], "authorized")
        self.assertEqual(context["outcome"], "authorized")
        self.assertEqual(context["drift"], [])
        self.assertEqual(context["identity_digest"], prepared["identity_digest"])
        self.assertEqual(context["startup_text"], prepared["startup_text"])
        self.assertEqual(context["goal_text"], prepared["goal_text"])
        self.assertIn("identity: digest=", context["startup_text"])
        self.assertIn("policy: digest=", context["startup_text"])
        self.assertIn("verification: none", context["startup_text"])
        self.assertIn("case context: no locator is bound", context["startup_text"])
        self.assertIn("stop path:", context["startup_text"])
        self.assertEqual(len(context["startup"]["policy"]["digest"]), 64)
        code, again = self.run_bridge("context", sidecar)
        self.assertEqual(again["startup_text"], context["startup_text"])
        after = self.snapshot_paths(
            self.run_root, self.workspace, sidecar, campaign)
        self.assertEqual(before, after)

    def test_context_reports_the_stored_qualification_without_revalidating(self) -> None:
        sidecar = self.sidecar(self.campaign("pass-campaign"))
        self.run_bridge("prepare", sidecar)
        code, validated = self.run_bridge("validate", sidecar)
        self.assertEqual(code, dsh_bridge.EXIT_OK, validated)
        self.assertEqual(validated["checker_verdict"], "pass")
        before = self.snapshot_paths(self.run_root, self.workspace, sidecar)
        code, context = self.run_bridge("context", sidecar)
        self.assertEqual(code, dsh_bridge.EXIT_OK, context)
        self.assertEqual(context["verification"]["state"], "pass")
        self.assertEqual(context["verification"]["checker_verdict"], "pass")
        self.assertIn("verification: pass", context["startup_text"])
        after = self.snapshot_paths(self.run_root, self.workspace, sidecar)
        self.assertEqual(before, after, "context must not re-run or re-record the checker")

    def test_context_refuses_a_changed_budget_and_names_the_field(self) -> None:
        campaign = self.campaign("smoke-campaign")
        sidecar = self.sidecar(campaign)
        self.run_bridge("prepare", sidecar)
        data = json.loads(sidecar.read_text(encoding="utf-8"))
        data["limits"]["max_validations"] = 4
        sidecar.write_text(json.dumps(data), encoding="utf-8")
        before = self.snapshot_paths(
            self.run_root, self.workspace, sidecar, campaign)
        code, out = self.run_bridge("context", sidecar)
        self.assertEqual(code, dsh_bridge.EXIT_INTEGRITY, out)
        self.assertFalse(out["ok"])
        self.assertEqual(out["status"], "drift")
        self.assertEqual(out["outcome"], "refused")
        self.assertEqual(out["kind"], "identity_modified")
        self.assertIn("budget.max_validations", out["drift"])
        self.assertIn("budget.max_validations", out["detail"])
        self.assertNotIn("startup_text", out)
        self.assertNotIn("goal_text", out)
        after = self.snapshot_paths(
            self.run_root, self.workspace, sidecar, campaign)
        self.assertEqual(before, after)

    def test_context_refuses_a_changed_workspace_and_names_the_field(self) -> None:
        sidecar = self.sidecar(self.campaign("smoke-campaign"))
        self.run_bridge("prepare", sidecar)
        data = json.loads(sidecar.read_text(encoding="utf-8"))
        # A trusted-tree path would also fail the execution-root check; the
        # authority comparison must still name the changed workspace first.
        data["candidate"]["workspace"] = str(REPO / "dsh")
        sidecar.write_text(json.dumps(data), encoding="utf-8")
        code, out = self.run_bridge("context", sidecar)
        self.assertEqual(code, dsh_bridge.EXIT_INTEGRITY, out)
        self.assertEqual(out["status"], "drift")
        self.assertEqual(out["outcome"], "refused")
        self.assertIn("workspace", out["drift"])
        self.assertIn("workspace", out["detail"])
        self.assertNotIn("startup_text", out)
        self.assertNotIn("goal_text", out)

    def test_context_refuses_a_changed_policy_before_the_candidate_walk(self) -> None:
        sidecar = self.sidecar(
            self.campaign("smoke-campaign"),
            allowed=("marker.txt", "progress.md"))
        self.run_bridge("prepare", sidecar)
        data = json.loads(sidecar.read_text(encoding="utf-8"))
        data["candidate"]["policy"]["allowed_paths"] = ["marker.txt"]
        sidecar.write_text(json.dumps(data), encoding="utf-8")
        code, out = self.run_bridge("context", sidecar)
        self.assertEqual(code, dsh_bridge.EXIT_INTEGRITY, out)
        self.assertEqual(out["status"], "drift")
        self.assertEqual(out["outcome"], "refused")
        self.assertIn("policy.digest", out["drift"])
        self.assertIn("policy.digest", out["detail"])
        self.assertNotIn("startup_text", out)
        self.assertNotIn("goal_text", out)

    def test_context_without_an_anchor_is_an_exact_missing_artifact(self) -> None:
        sidecar = self.sidecar(self.campaign("smoke-campaign"))
        code, out = self.run_bridge("context", sidecar)
        self.assertEqual(code, dsh_bridge.EXIT_INTEGRITY, out)
        self.assertFalse(out["ok"])
        self.assertEqual(out["status"], "unprepared")
        self.assertEqual(out["outcome"], "refused")
        self.assertEqual(out["anchored"], False)
        self.assertEqual(out["kind"], "not_prepared")
        self.assertIn("no identity anchor", out["detail"])
        self.assertNotIn("startup_text", out)
        self.assertNotIn("goal_text", out)
        self.assertFalse((self.run_root / "identity.json").exists())
        self.assertFalse(self.run_root.exists())

    def test_worktree_context_authorizes_the_same_worktree_head(self) -> None:
        baseline = subprocess.run(
            ["git", "-C", str(REPO), "rev-parse", "HEAD"],
            capture_output=True, text=True, check=True,
        ).stdout.strip()
        worktree = self.root / "context-wt"
        subprocess.run(
            ["git", "-C", str(REPO), "worktree", "add", "--detach",
             str(worktree), baseline],
            capture_output=True, check=True,
        )
        self.addCleanup(
            subprocess.run,
            ["git", "-C", str(REPO), "worktree", "remove", "--force",
             "--force", str(worktree)],
            capture_output=True)
        sidecar = self.sidecar(self.campaign("smoke-campaign"))
        data = json.loads(sidecar.read_text(encoding="utf-8"))
        data["candidate"] = {
            "mode": "worktree",
            "workspace": str(worktree),
            "policy": {"allowed_paths": ["*"]},
        }
        data["candidate_baseline_commit"] = baseline
        sidecar.write_text(json.dumps(data), encoding="utf-8")
        code, prepared = self.run_bridge("prepare", sidecar)
        self.assertEqual(code, dsh_bridge.EXIT_OK, prepared)
        code, context = self.run_bridge("context", sidecar)
        self.assertEqual(code, dsh_bridge.EXIT_OK, context)
        self.assertEqual(context["status"], "authorized")
        self.assertEqual(context["startup"]["workspace_mode"], "worktree")
        self.assertEqual(context["startup"]["workspace"], str(worktree.resolve()))
        self.assertEqual(context["startup"]["workspace_head"], baseline)
        self.assertIn(f"head={baseline}", context["startup_text"])

    def test_startup_text_reports_used_and_remaining_validations(self) -> None:
        sidecar = self.sidecar(self.campaign("smoke-campaign"))
        self.run_root.mkdir(parents=True, exist_ok=True)
        rows = [{
            "attempt_id": f"val-{index}", "operation": "validate",
            "classification": "pass", "checker_verdict": "pass",
            "digest": "a" * 64, "qualified": True, "at": index + 1,
        } for index in range(2)]
        (self.run_root / "validations.jsonl").write_text(
            "\n".join(json.dumps(row) for row in rows) + "\n")
        code, out = self.run_bridge("prepare", sidecar)
        self.assertEqual(code, dsh_bridge.EXIT_OK)
        self.assertIn("validations used 2 of 6 (remaining 4)",
                      out["startup_text"])
        self.assertIn("classification=pass", out["startup_text"])

    def test_prepare_refuses_an_unwritable_workspace_before_anchoring(self) -> None:
        sidecar = self.sidecar(self.campaign("smoke-campaign"))
        self.workspace.mkdir(parents=True)
        # A pre-existing path must never be overwritten or removed by a probe.
        existing = self.workspace / ".dsh-prepare-probe-existing"
        existing.write_text("keep", encoding="utf-8")
        original_open = os.open

        def fail_probe_open(path, *args, **kwargs):
            if Path(path).name.startswith(dsh_bridge.PROBE_PREFIX):
                raise PermissionError("injected permission")
            return original_open(path, *args, **kwargs)

        with mock.patch("automation.dsh_bridge.os.open",
                        side_effect=fail_probe_open):
            code, out = self.run_bridge("prepare", sidecar)
        self.assertEqual(code, dsh_bridge.EXIT_INTEGRITY)
        self.assertFalse(out["ok"])
        self.assertEqual(out["kind"], "workspace_not_writable")
        self.assertIn("injected permission", out["detail"])
        self.assertEqual(existing.read_text(encoding="utf-8"), "keep")
        self.assertFalse((self.run_root / "identity.json").is_file())
        self.assertFalse((self.run_root / "validations.jsonl").exists())
        self.assertEqual(
            list(self.workspace.glob(".dsh-prepare-probe-*")), [existing])


    def test_probe_filters_stale_residue_from_the_candidate_identity(self) -> None:
        sidecar = self.sidecar(self.campaign("smoke-campaign"))
        self.workspace.mkdir(parents=True)
        stale = self.workspace / f"{dsh_bridge.PROBE_PREFIX}stale"
        stale.write_text("left by a killed process", encoding="utf-8")
        self.addCleanup(lambda: stale.unlink(missing_ok=True))
        dsh_bridge._register_probe(self.run_root, self.workspace, stale.name)
        code, out = self.run_bridge("prepare", sidecar)
        self.assertEqual(code, dsh_bridge.EXIT_OK, out)
        self.assertNotIn(stale.name,
                         out["identities"]["candidate"]["changed_paths"])
        self.assertFalse(stale.exists())

    def test_probe_cleanup_failure_is_a_refusal(self) -> None:
        sidecar = self.sidecar(self.campaign("smoke-campaign"))
        self.workspace.mkdir(parents=True)
        original_unlink = Path.unlink

        def fail_probe_unlink(path, *args, **kwargs):
            if path.name.startswith(dsh_bridge.PROBE_PREFIX):
                raise OSError("injected unlink failure")
            return original_unlink(path, *args, **kwargs)

        with mock.patch("pathlib.Path.unlink", new=fail_probe_unlink):
            code, out = self.run_bridge("prepare", sidecar)
        self.assertEqual(code, dsh_bridge.EXIT_INTEGRITY)
        self.assertEqual(out["kind"], "workspace_not_writable")
        self.assertIn("probe cleanup failed", out["detail"])
        self.assertFalse((self.run_root / "identity.json").is_file())
        residue = list(self.workspace.glob(f"{dsh_bridge.PROBE_PREFIX}*"))
        self.assertEqual(len(residue), 1)
        for path in residue:
            path.unlink()


    def test_nested_probe_prefixed_path_is_still_candidate_work(self) -> None:
        sidecar = self.sidecar(self.campaign("smoke-campaign"),
                               allowed=("marker.txt", "progress.md", ".local/*"))
        data = json.loads(sidecar.read_text())
        data["candidate"]["policy"]["forbidden_paths"] = [".local"]
        sidecar.write_text(json.dumps(data), encoding="utf-8")
        self.workspace.mkdir(parents=True)
        nested = self.workspace / ".local" / ".dsh-prepare-probe-cheat"
        nested.parent.mkdir()
        nested.write_text("candidate work", encoding="utf-8")
        code, out = self.run_bridge("prepare", sidecar)
        self.assertEqual(code, dsh_bridge.EXIT_INTEGRITY)
        self.assertEqual(out["kind"], "policy")
        self.assertIn("frozen path modified", out["detail"])
        self.assertTrue(nested.is_file())

    def test_freeze_ignores_root_level_probe_residue(self) -> None:
        sidecar = self.sidecar(self.campaign("smoke-campaign"))
        self.run_bridge("prepare", sidecar)
        stale = self.workspace / f"{dsh_bridge.PROBE_PREFIX}stale"
        stale.write_text("residue", encoding="utf-8")
        self.addCleanup(lambda: stale.unlink(missing_ok=True))
        dsh_bridge._register_probe(self.run_root, self.workspace, stale.name)
        loaded = dsh_campaign.load_sidecar(sidecar)
        frozen = dsh_bridge._freeze(loaded)
        self.assertEqual(frozen["mode"], "seed")
        self.assertEqual(frozen["entries"], 2)


    def test_staged_rename_into_probe_namespace_is_not_filtered(self) -> None:
        baseline = subprocess.run(
            ["git", "-C", str(REPO), "rev-parse", "HEAD"],
            capture_output=True, text=True, check=True,
        ).stdout.strip()
        worktree = self.root / "probe-rename-wt"
        subprocess.run(
            ["git", "-C", str(REPO), "worktree", "add", "--detach",
             str(worktree), baseline],
            capture_output=True, check=True,
        )
        self.addCleanup(
            subprocess.run,
            ["git", "-C", str(REPO), "worktree", "remove", "--force",
             "--force", str(worktree)],
            capture_output=True)
        subprocess.run(
            ["git", "-C", str(worktree), "mv", "docs/status.md",
             ".dsh-prepare-probe-evil"],
            capture_output=True, check=True)
        sidecar = self.sidecar(self.campaign("smoke-campaign"),
                               allowed=("docs/*",))
        data = json.loads(sidecar.read_text())
        data["candidate"] = {
            "mode": "worktree",
            "workspace": str(worktree),
            "policy": {"allowed_paths": ["docs/*"]},
        }
        data["candidate_baseline_commit"] = baseline
        sidecar.write_text(json.dumps(data), encoding="utf-8")
        code, out = self.run_bridge("prepare", sidecar)
        self.assertEqual(code, dsh_bridge.EXIT_INTEGRITY)
        self.assertEqual(out["kind"], "policy")
        self.assertIn(".dsh-prepare-probe-evil", out["detail"])
        self.assertTrue((worktree / ".dsh-prepare-probe-evil").is_file())
        self.assertFalse((worktree / "docs" / "status.md").exists())


class ValidationBudgetTests(BridgeTestCase):
    """External sidecar limits are enforced from the bridge's attempt log."""

    def loaded(self, *, retry=None, limits=None):
        path = self.sidecar(self.campaign("smoke-campaign"))
        data = json.loads(path.read_text())
        if retry is not None:
            data["checker_retry"].update(retry)
        if limits is not None:
            data["limits"].update(limits)
        path.write_text(json.dumps(data), encoding="utf-8")
        return dsh_campaign.load_sidecar(path)

    def test_max_validations_refuses_another_run(self):
        sidecar = self.loaded(limits={"max_validations": 1})
        history = [{"at": 1.0, "classification": "verdict",
                    "checker_verdict": "fail", "digest": "a"}]
        stop = dsh_bridge._campaign_budget_stop(sidecar, history, 2.0)
        self.assertEqual(stop["kind"], "budget_exhausted")
        self.assertIn("max_validations", stop["detail"])

    def test_identical_failure_streak_requires_changed_strategy(self):
        sidecar = self.loaded(retry={"max_identical_retries": 1})
        row = {"at": 1.0, "classification": "verdict",
               "checker_verdict": "fail", "digest": "same"}
        stop = dsh_bridge._campaign_budget_stop(
            sidecar, [row, dict(row)], 2.0)
        self.assertEqual(stop["kind"], "repeated_failure")
        self.assertIn("change the diagnostic strategy", stop["detail"])

    def test_different_failures_do_not_trip_the_strategy_gate(self):
        sidecar = self.loaded(retry={"max_identical_retries": 1})
        rows = [
            {"at": 1.0, "classification": "verdict",
             "checker_verdict": "fail", "digest": "a"},
            {"at": 2.0, "classification": "verdict",
             "checker_verdict": "fail", "digest": "b"},
        ]
        self.assertIsNone(dsh_bridge._campaign_budget_stop(sidecar, rows, 3.0))

    def test_wall_clock_deadline_is_refused(self):
        sidecar = self.loaded(limits={"wall_clock_seconds": 1})
        rows = [{"at": 100.0, "classification": "verdict",
                 "checker_verdict": "fail", "digest": "a"}]
        stop = dsh_bridge._campaign_budget_stop(sidecar, rows, 200.0)
        self.assertEqual(stop["kind"], "deadline")

    def test_a_fresh_campaign_is_not_stopped(self):
        sidecar = self.loaded()
        self.assertIsNone(dsh_bridge._campaign_budget_stop(
            sidecar, [], 2.0))


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


class ExitPolicyTests(BridgeTestCase):
    """Every bridge exit says what the goal should do, in GOAL_POLICY's words."""

    def test_a_terminal_refusal_asks_for_pause(self) -> None:
        campaign = self.campaign("pass-campaign")
        sidecar = self.sidecar(campaign)
        self.run_bridge("prepare", sidecar)
        # Tamper with the checker after the anchor exists.
        checker = campaign / "accept.py"
        checker.write_text(checker.read_text(encoding="utf-8") + "\n# edited\n",
                           encoding="utf-8")
        code, out = self.run_bridge("validate", sidecar)
        self.assertEqual(code, dsh_bridge.EXIT_INTEGRITY)
        self.assertEqual(out["kind"], "identity_modified")
        self.assertEqual(out["goal_policy"], dsh_bridge.GOAL_POLICY["unavailable"])
        self.assertEqual(out["goal_policy"], "pause")

    def test_a_remediable_refusal_does_not_pause(self) -> None:
        sidecar = self.sidecar(self.campaign("pass-campaign"))
        self.run_bridge("prepare", sidecar)
        # Finalizing before any validation: nothing is wrong with the campaign,
        # the candidate simply has not been checked yet.
        code, out = self.run_bridge("finalize", sidecar)
        self.assertEqual(code, dsh_bridge.EXIT_INTEGRITY)
        self.assertEqual(out["kind"], "not_qualified")
        self.assertEqual(out["goal_policy"], dsh_bridge.GOAL_POLICY["fail"])
        self.assertNotEqual(out["goal_policy"], "pause")


class CheckerAttemptAccountingTests(BridgeTestCase):
    """Every checker execution is a durable attempt with a terminal result."""

    def logged(self) -> tuple[list[dict], list[dict]]:
        rows = dsh_bridge._validation_history(self.run_root)
        return rows, dsh_bridge._validation_attempts(rows)

    def sidecar_with_limit(self, fixture: str, max_validations: int) -> Path:
        sidecar = self.sidecar(self.campaign(fixture))
        data = json.loads(sidecar.read_text(encoding="utf-8"))
        data["limits"]["max_validations"] = max_validations
        sidecar.write_text(json.dumps(data), encoding="utf-8")
        return sidecar

    def test_a_normal_pass_records_one_distinct_attempt(self) -> None:
        sidecar = self.sidecar(self.campaign("pass-campaign"))
        self.run_bridge("prepare", sidecar)
        code, out = self.run_bridge("validate", sidecar)
        self.assertEqual(code, dsh_bridge.EXIT_OK, out)
        rows, attempts = self.logged()
        self.assertEqual([row.get("phase") for row in rows],
                         ["start", "result"])
        self.assertEqual(len({row["attempt_id"] for row in rows}), 1)
        self.assertEqual(len(attempts), 1)
        self.assertTrue(attempts[0]["terminal"])
        self.assertTrue(attempts[0]["qualified"])
        self.assertEqual(attempts[0]["checker_verdict"], "pass")

    def test_a_checker_failure_is_a_classified_terminal_attempt(self) -> None:
        sidecar = self.sidecar(self.campaign("smoke-campaign"))
        self.run_bridge("prepare", sidecar)
        code, out = self.run_bridge("validate", sidecar)
        self.assertEqual(code, dsh_bridge.EXIT_OK, out)
        self.assertEqual(out["checker_verdict"], "fail")
        _, attempts = self.logged()
        self.assertEqual(len(attempts), 1)
        self.assertTrue(attempts[0]["terminal"])
        self.assertFalse(attempts[0]["qualified"])
        self.assertEqual(attempts[0]["classification"], "verdict")
        self.assertEqual(attempts[0]["checker_verdict"], "fail")

    def test_a_checker_exception_is_recorded_and_counted(self) -> None:
        sidecar = self.sidecar(self.campaign("pass-campaign"))
        self.run_bridge("prepare", sidecar)
        with mock.patch.object(dsh_bridge, "run_accept",
                               side_effect=RuntimeError("checker exploded")):
            code, out = self.run_bridge("validate", sidecar)
        self.assertEqual(code, dsh_bridge.EXIT_OK, out)
        self.assertEqual(out["classification"], "infrastructure_error")
        self.assertEqual(out["kind"], "checker_exception")
        _, attempts = self.logged()
        self.assertEqual(len(attempts), 1)
        self.assertTrue(attempts[0]["terminal"])
        self.assertFalse(attempts[0]["qualified"])
        self.assertEqual(attempts[0]["kind"], "checker_exception")

    def test_identity_drift_after_the_checker_is_recorded(self) -> None:
        sidecar = self.sidecar(self.campaign("pass-campaign"))
        self.run_bridge("prepare", sidecar)
        original = dsh_bridge._identity
        calls: list[dict] = []

        def drifting(sidecar_arg, infra_commit):
            value = original(sidecar_arg, infra_commit)
            calls.append(value)
            if len(calls) == 2:
                value = json.loads(json.dumps(value))
                value["candidate"]["digest"] = "0" * 64
            return value

        with mock.patch.object(dsh_bridge, "_identity", side_effect=drifting):
            code, out = self.run_bridge("validate", sidecar)
        self.assertEqual(code, dsh_bridge.EXIT_INTEGRITY, out)
        self.assertEqual(out["kind"], "identity_drift")
        _, attempts = self.logged()
        self.assertEqual(len(attempts), 1)
        self.assertEqual(attempts[0]["classification"], "identity_drift")
        self.assertFalse(attempts[0]["qualified"])

    def test_incomplete_inputs_after_a_pass_are_recorded(self) -> None:
        sidecar = self.sidecar(self.campaign("pass-campaign"))
        self.run_bridge("prepare", sidecar)
        original = dsh_bridge.snapshot.ignored_inputs
        dsh_bridge.snapshot.ignored_inputs = lambda ws: {
            "entries": [], "truncated": True}
        self.addCleanup(setattr, dsh_bridge.snapshot, "ignored_inputs", original)
        code, out = self.run_bridge("validate", sidecar)
        self.assertEqual(code, dsh_bridge.EXIT_INTEGRITY, out)
        self.assertEqual(out["kind"], "inputs_incomplete")
        _, attempts = self.logged()
        self.assertEqual(len(attempts), 1)
        self.assertEqual(attempts[0]["classification"], "inputs_incomplete")
        self.assertFalse(attempts[0]["qualified"])

    def test_an_interrupted_attempt_counts_against_max_validations(self) -> None:
        sidecar = self.sidecar_with_limit("pass-campaign", 1)
        self.run_bridge("prepare", sidecar)
        # A crash between the start row and the checker result leaves exactly
        # this durable record behind; a restarted bridge must still count it.
        dsh_bridge._append_validation(self.run_root, {
            "attempt_id": "interrupted-attempt",
            "phase": "start",
            "at": 1.0,
            "operation": "validate",
        })
        with mock.patch.object(dsh_bridge, "run_accept",
                               side_effect=AssertionError(
                                   "the checker must not start")):
            code, out = self.run_bridge("validate", sidecar)
        self.assertEqual(code, dsh_bridge.EXIT_OK, out)
        self.assertEqual(out["classification"], "policy_stop")
        self.assertEqual(out["kind"], "budget_exhausted")
        self.assertEqual(out["validations"], 1)
        _, attempts = self.logged()
        self.assertEqual(len(attempts), 1)
        self.assertFalse(attempts[0]["terminal"])

    def test_distinct_attempts_not_log_rows_are_counted(self) -> None:
        sidecar = dsh_campaign.load_sidecar(
            self.sidecar_with_limit("pass-campaign", 2))
        rows = [
            {"attempt_id": "one", "phase": "start", "at": 1.0},
            {"attempt_id": "one", "phase": "result", "at": 1.1,
             "classification": "verdict", "checker_verdict": "pass",
             "qualified": True},
        ]
        self.assertIsNone(dsh_bridge._campaign_budget_stop(sidecar, rows, 2.0))
        rows.extend([
            {"attempt_id": "two", "phase": "start", "at": 3.0},
            {"attempt_id": "two", "phase": "result", "at": 3.1,
             "classification": "verdict", "checker_verdict": "pass",
             "qualified": True},
        ])
        stop = dsh_bridge._campaign_budget_stop(sidecar, rows, 4.0)
        self.assertEqual(stop["kind"], "budget_exhausted")

    def test_an_exhausted_budget_refuses_before_the_checker_starts(self) -> None:
        sidecar = self.sidecar_with_limit("pass-campaign", 1)
        self.run_bridge("prepare", sidecar)
        self.assertEqual(self.run_bridge("validate", sidecar)[0],
                         dsh_bridge.EXIT_OK)
        rows_before = dsh_bridge._validation_history(self.run_root)
        with mock.patch.object(dsh_bridge, "run_accept",
                               side_effect=AssertionError(
                                   "the checker must not start")):
            code, out = self.run_bridge("validate", sidecar)
        self.assertEqual(code, dsh_bridge.EXIT_OK, out)
        self.assertEqual(out["kind"], "budget_exhausted")
        self.assertEqual(dsh_bridge._validation_history(self.run_root),
                         rows_before)

    def test_finalization_fresh_checker_execution_is_accounted(self) -> None:
        sidecar = self.sidecar(self.campaign("pass-campaign"))
        self.run_bridge("prepare", sidecar)
        self.assertEqual(self.run_bridge("validate", sidecar)[0],
                         dsh_bridge.EXIT_OK)
        code, out = self.run_bridge("finalize", sidecar)
        self.assertEqual(code, dsh_bridge.EXIT_OK, out)
        self.assertTrue((self.run_root / "finalization.json").is_file())
        _, attempts = self.logged()
        self.assertEqual([row["operation"] for row in attempts],
                         ["validate", "finalize"])
        self.assertTrue(all(row["qualified"] for row in attempts))
        self.assertEqual(len({row["attempt_id"] for row in attempts}), 2)

    def test_finalization_refuses_before_a_fresh_checker_when_exhausted(self) -> None:
        sidecar = self.sidecar_with_limit("pass-campaign", 1)
        self.run_bridge("prepare", sidecar)
        self.assertEqual(self.run_bridge("validate", sidecar)[0],
                         dsh_bridge.EXIT_OK)
        rows_before = dsh_bridge._validation_history(self.run_root)
        with mock.patch.object(dsh_bridge, "run_accept",
                               side_effect=AssertionError(
                                   "the finalization checker must not start")):
            code, out = self.run_bridge("finalize", sidecar)
        self.assertEqual(code, dsh_bridge.EXIT_OK, out)
        self.assertEqual(out["classification"], "policy_stop")
        self.assertEqual(out["kind"], "budget_exhausted")
        self.assertFalse((self.run_root / "finalization.json").exists())
        self.assertEqual(dsh_bridge._validation_history(self.run_root),
                         rows_before)

    def test_legacy_rows_remain_readable_and_count_as_attempts(self) -> None:
        sidecar = dsh_campaign.load_sidecar(
            self.sidecar_with_limit("pass-campaign", 3))
        rows = [
            {"at": 1.0, "classification": "verdict",
             "checker_verdict": "fail", "digest": "a"},
            {"at": 2.0, "classification": "verdict",
             "checker_verdict": "pass", "digest": "b"},
        ]
        attempts = dsh_bridge._validation_attempts(rows)
        self.assertEqual(len(attempts), 2)
        self.assertTrue(all(row["terminal"] for row in attempts))
        self.assertFalse(attempts[0]["qualified"])
        self.assertTrue(attempts[1]["qualified"])
        self.assertIsNone(dsh_bridge._campaign_budget_stop(sidecar, rows, 3.0))
        self.assertEqual(
            dsh_bridge._campaign_budget_stop(sidecar, rows + [rows[-1]],
                                            4.0)["kind"],
            "budget_exhausted")
