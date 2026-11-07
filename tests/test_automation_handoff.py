"""A0.2 portable tests: oracle promotion, snapshots, scripted handoff (no EU4 binary)."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from automation.handoff import (
    HandoffController,
    ensure_reviewer_worktree,
    path_allowlisted,
    quarantine_report,
    recover_completed_reviewer_receipt,
    reviewer_invocation_path_for,
    reviewer_receipt_path_for,
    reviewer_worktree_path,
    run_root_for,
    snapshot_candidate,
    verify_ref_unmodified,
    verify_reviewer_checkout,
    write_reviewer_invocation,
    write_reviewer_receipt,
    write_synthetic_rejection,
)
from automation.handoff_cli import (
    build_remediation_campaign,
    legacy_workflow_layout,
    materialize_reviewer_campaign,
    reviewer_report_path,
)
from automation.review_result import write_review
from automation.oracle_promote import (
    PromotionDecision,
    classify_finding,
    finding_content_digest,
    promote_grounded_findings,
)
from automation.state import load_campaign
from automation.review_result import validate_review
from automation.verification_manifest import (
    EU4_EXECUTABLE_SHA256,
    REPO_BASELINE_COMMIT,
    build_manifest,
    digest_manifest,
    file_sha256,
    load_manifest,
    member_from_file,
    save_manifest,
)

REPO = Path(__file__).resolve().parents[1]
FIXTURE = REPO / "tests" / "fixtures" / "qualification"
EXE = Path("/Applications/EuropaUniversalisIV/eu4.app/Contents/MacOS/eu4")


def _init_git_repo(root: Path) -> str:
    subprocess.run(["git", "init"], cwd=root, check=True, capture_output=True)
    subprocess.run(
        ["git", "config", "user.email", "test@local"],
        cwd=root,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "test"],
        cwd=root,
        check=True,
        capture_output=True,
    )
    (root / "README").write_text("x\n", encoding="utf-8")
    (root / "automation").mkdir()
    (root / "automation" / "replay_primitive.py").write_text(
        (REPO / "automation" / "replay_primitive.py").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    for name in (
        "evidence.py",
        "macho_map.py",
        "verification_manifest.py",
        "review_result.py",
        "oracle_promote.py",
    ):
        shutil.copy2(REPO / "automation" / name, root / "automation" / name)
    checker = root / "accept.py"
    checker.write_text("# checker\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=root, check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "init"], cwd=root, check=True, capture_output=True
    )
    return subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=root,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()


def _seed_oracle(root: Path, oracle: Path) -> dict:
    oracle.mkdir(parents=True, exist_ok=True)
    shutil.copy2(
        FIXTURE / "primitive-table.evidence.json",
        oracle / "primitive-table.evidence.json",
    )
    helpers = [
        "automation/evidence.py",
        "automation/macho_map.py",
        "automation/verification_manifest.py",
        "automation/replay_primitive.py",
    ]
    members = [member_from_file(root, h, "trusted_helper") for h in helpers]
    members.append(member_from_file(root, "accept.py", "checker"))
    members.append(
        member_from_file(
            root,
            str((oracle / "primitive-table.evidence.json").relative_to(root)),
            "frozen_oracle",
        )
    )
    manifest = build_manifest(
        members=members,
        repo_baseline_commit=REPO_BASELINE_COMMIT,
        eu4_executable_sha256=EU4_EXECUTABLE_SHA256,
        extra={"oracle_version_name": "v0"},
    )
    save_manifest(oracle / "verification-manifest.json", manifest)
    (oracle / "CURRENT").write_text("v0\n", encoding="utf-8")
    return manifest


def _binding(evidence: dict, evidence_path: Path) -> dict:
    return {
        "replay_id": "primitive_lookup_v1",
        "observation": "out_of_range_default",
        "expected": evidence["output"]["mapping"]["default_out_of_range"],
        "function_rva": evidence["function_rva"],
        "executable_sha256": evidence["executable_sha256"],
        "evidence_ref": {
            "path": "analysis/qualification/v0/primitive-table.evidence.json",
            "sha256": file_sha256(evidence_path),
        },
        "sequence": ["read", "compare"],
    }


def _rejection(commit: str, oracle_sha: str, binding: dict) -> dict:
    return {
        "schema": "eu4-review/v1",
        "candidate_commit": commit,
        "oracle_manifest_sha256": oracle_sha,
        "outcome": "rejected",
        "findings": [
            {
                "id": "defect-b",
                "severity": "P1",
                "kind": "behavioral_counterexample",
                "reproducer": "analysis/qualification/v0/primitive-lookup.candidate.json",
                "expected_provenance": "instruction_static",
                "binding": binding,
            }
        ],
    }


class ReviewSchemaTests(unittest.TestCase):
    def test_rejects_prose_only_outcome(self) -> None:
        self.assertTrue(validate_review({"outcome": "rejected"}))

    def test_valid_rejection(self) -> None:
        review = {
            "schema": "eu4-review/v1",
            "candidate_commit": "a" * 40,
            "oracle_manifest_sha256": "b" * 64,
            "outcome": "rejected",
            "findings": [
                {
                    "id": "x",
                    "severity": "P1",
                    "kind": "behavioral_counterexample",
                    "reproducer": "fixtures/x.json",
                    "expected_provenance": "instruction_static",
                }
            ],
        }
        self.assertEqual(validate_review(review), [])


class SnapshotTests(unittest.TestCase):
    def test_complete_snapshot_parented_and_allowlisted(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            pin = _init_git_repo(root)
            # Baseline must contain a tracked file outside allowlist
            (root / "automation" / "keep.txt").write_text("keep\n", encoding="utf-8")
            subprocess.run(["git", "add", "."], cwd=root, check=True, capture_output=True)
            subprocess.run(
                ["git", "commit", "-m", "baseline"],
                cwd=root,
                check=True,
                capture_output=True,
            )
            pin = subprocess.run(
                ["git", "rev-parse", "HEAD"],
                cwd=root,
                capture_output=True,
                text=True,
                check=True,
            ).stdout.strip()
            workspace = root / "ws"
            qual = workspace / "analysis" / "qualification" / "v0"
            qual.mkdir(parents=True)
            (qual / "primitive-lookup.candidate.json").write_text(
                '{"ok":1}\n', encoding="utf-8"
            )
            (workspace / "progress.md").write_text("# p\n", encoding="utf-8")
            snap = snapshot_candidate(
                root,
                workspace,
                workflow_id="wf",
                round_id=0,
                parent_commit=pin,
            )
            self.assertEqual(snap["candidate_parent"], pin)
            # Full tree includes baseline automation
            names = subprocess.run(
                ["git", "-C", str(root), "ls-tree", "-r", "--name-only", snap["candidate_commit"]],
                capture_output=True,
                text=True,
                check=True,
            ).stdout.splitlines()
            self.assertIn("automation/keep.txt", names)
            self.assertIn(
                "analysis/qualification/v0/primitive-lookup.candidate.json", names
            )
            for path in snap["changed_paths"]:
                self.assertTrue(path_allowlisted(path), path)
            self.assertEqual(
                verify_ref_unmodified(
                    root, snap["candidate_ref"], snap["candidate_commit"]
                ),
                [],
            )

    def test_snapshot_ignores_workspace_protected_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            _init_git_repo(root)
            baseline_qual = root / "analysis" / "qualification" / "v0"
            baseline_qual.mkdir(parents=True)
            (baseline_qual / "primitive-table.evidence.json").write_text(
                '{"trusted":true}\n', encoding="utf-8"
            )
            subprocess.run(["git", "add", "."], cwd=root, check=True, capture_output=True)
            subprocess.run(
                ["git", "commit", "-m", "evidence"],
                cwd=root,
                check=True,
                capture_output=True,
            )
            pin = subprocess.run(
                ["git", "rev-parse", "HEAD"],
                cwd=root,
                capture_output=True,
                text=True,
                check=True,
            ).stdout.strip()
            workspace = root / "ws"
            qual = workspace / "analysis" / "qualification" / "v0"
            qual.mkdir(parents=True)
            (qual / "primitive-lookup.candidate.json").write_text(
                '{"ok":1}\n', encoding="utf-8"
            )
            # Divergent workspace-local evidence must not enter the snapshot overlay
            (qual / "primitive-table.evidence.json").write_text(
                '{"trusted":false,"tampered":1}\n', encoding="utf-8"
            )
            (workspace / "progress.md").write_text("# p\n", encoding="utf-8")
            snap = snapshot_candidate(
                root,
                workspace,
                workflow_id="wf-ev",
                round_id=0,
                parent_commit=pin,
            )
            evidence_blob = subprocess.run(
                [
                    "git",
                    "-C",
                    str(root),
                    "show",
                    f"{snap['candidate_commit']}:analysis/qualification/v0/primitive-table.evidence.json",
                ],
                capture_output=True,
                text=True,
                check=True,
            ).stdout
            self.assertEqual(evidence_blob, '{"trusted":true}\n')
            self.assertNotIn(
                "analysis/qualification/v0/primitive-table.evidence.json",
                snap["changed_paths"],
            )


class OraclePromotionTests(unittest.TestCase):
    def test_unestablished_reject_only(self) -> None:
        evidence = json.loads(
            (FIXTURE / "primitive-table.evidence.json").read_text(encoding="utf-8")
        )
        finding = {
            "id": "gap",
            "severity": "P1",
            "kind": "behavioral_counterexample",
            "reproducer": "missing.json",
            "expected_provenance": "unestablished",
        }
        self.assertEqual(
            classify_finding(
                finding, evidence, FIXTURE / "primitive-table.evidence.json"
            ),
            PromotionDecision.REJECT_ONLY,
        )

    def test_append_only_and_already_applied_after_manifest_crash(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            _init_git_repo(root)
            oracle = root / "oracle"
            _seed_oracle(root, oracle)
            evidence = json.loads(
                (oracle / "primitive-table.evidence.json").read_text(encoding="utf-8")
            )
            helpers = [
                "automation/evidence.py",
                "automation/macho_map.py",
                "automation/verification_manifest.py",
                "automation/replay_primitive.py",
            ]
            binding = _binding(evidence, oracle / "primitive-table.evidence.json")
            review = _rejection("a" * 40, "b" * 64, binding)
            review_path = root / "review.json"
            review_path.write_text(json.dumps(review), encoding="utf-8")

            first = promote_grounded_findings(
                repo=root,
                review=review,
                oracle_dir=oracle,
                trusted_helpers=helpers,
                checker_relpath="accept.py",
                review_path=review_path,
            )
            self.assertTrue(first["promoted"])
            self.assertFalse(first["reused"])
            v1 = first["oracle_version"]
            self.assertTrue(
                (oracle / "oracle_versions" / v1 / "regressions" / "defect-b.json").is_file()
            )

            # Simulate crash after manifest publish: tip already at v1, workflow forgot
            tip = load_manifest(oracle / "verification-manifest.json")
            self.assertEqual(tip["oracle_version_name"], v1)
            # CURRENT missing (crash before CURRENT update)
            (oracle / "CURRENT").unlink(missing_ok=True)

            second = promote_grounded_findings(
                repo=root,
                review=review,
                oracle_dir=oracle,
                trusted_helpers=helpers,
                checker_relpath="accept.py",
                review_path=review_path,
            )
            self.assertTrue(second["promoted"])
            self.assertTrue(second["reused"])
            self.assertEqual(second["oracle_version"], v1)
            # Still a single version directory for this finding
            versions = [
                p.name
                for p in (oracle / "oracle_versions").iterdir()
                if p.is_dir() and not p.name.startswith(".")
            ]
            self.assertEqual(versions, [v1])

            # CURRENT recoverable from tip
            self.assertEqual(
                (oracle / "CURRENT").read_text(encoding="utf-8").strip(), v1
            )

            # Second distinct finding → new version that still contains defect-b
            review2 = json.loads(json.dumps(review))
            review2["findings"][0]["id"] = "defect-b2"
            review2["findings"][0]["binding"] = dict(binding)
            # same observation content but new id — actually same binding digest
            # differs by id in finding_content_digest
            review2_path = root / "review2.json"
            review2_path.write_text(json.dumps(review2), encoding="utf-8")
            third = promote_grounded_findings(
                repo=root,
                review=review2,
                oracle_dir=oracle,
                trusted_helpers=helpers,
                checker_relpath="accept.py",
                review_path=review2_path,
            )
            self.assertTrue(third["promoted"])
            self.assertFalse(third["reused"])
            v2 = third["oracle_version"]
            self.assertNotEqual(v2, v1)
            regs = list(
                (oracle / "oracle_versions" / v2 / "regressions").glob("*.json")
            )
            ids = {p.stem for p in regs}
            self.assertEqual(ids, {"defect-b", "defect-b2"})

            # Tampering CURRENT alone must not hide regressions from tip manifest
            (oracle / "CURRENT").write_text("v0\n", encoding="utf-8")
            tip = load_manifest(oracle / "verification-manifest.json")
            from automation.replay_primitive import resolve_regression_root

            root_regs = resolve_regression_root(oracle, tip)
            self.assertIsNotNone(root_regs)
            self.assertEqual(root_regs.parent.name, v2)


class ScriptedTwoDefectTests(unittest.TestCase):
    def test_scripted_handoff_reaches_awaiting_merge(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            workflow = (
                REPO
                / ".local"
                / "qualification"
                / f"wf-test-{Path(temporary).name}.json"
            )
            workflow.parent.mkdir(parents=True, exist_ok=True)
            if workflow.exists():
                workflow.unlink()
            # Isolate: wipe prior scripted workspace
            workspace = REPO / ".local" / "qualification" / "scripted-workspace"
            if workspace.exists():
                shutil.rmtree(workspace)
            # Snapshot tracked oracle tip before run
            tip_before = None
            tip_path = REPO / "analysis/qualification/v0/verification-manifest.json"
            if tip_path.is_file():
                tip_before = tip_path.read_bytes()
            try:
                ran = subprocess.run(
                    [
                        sys.executable,
                        "-m",
                        "automation.handoff_cli",
                        "run-scripted",
                        str(workflow),
                    ],
                    cwd=REPO,
                    capture_output=True,
                    text=True,
                    env={**os.environ, "EU4_A01_FIXTURE_MODE": "1"},
                )
                self.assertEqual(ran.returncode, 0, ran.stdout + ran.stderr)
                payload = json.loads(ran.stdout)
                self.assertEqual(payload["state"], "awaiting_merge")
                receipt = payload["receipt"]
                self.assertTrue(receipt["candidate_commit"])
                self.assertTrue(receipt["oracle_digest"])
                self.assertTrue(receipt["checker_digest"])
                self.assertTrue(receipt["review_digest"])
            finally:
                if tip_before is not None:
                    self.assertEqual(tip_path.read_bytes(), tip_before)
                if workflow.exists():
                    workflow.unlink()
                oracle_local = workflow.parent / "oracle"
                if oracle_local.exists():
                    shutil.rmtree(oracle_local)


class HandoffRemediationIdTests(unittest.TestCase):
    def test_rejection_creates_new_remediation_from_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            pin = _init_git_repo(root)
            oracle = root / "oracle"
            _seed_oracle(root, oracle)
            evidence = json.loads(
                (oracle / "primitive-table.evidence.json").read_text(encoding="utf-8")
            )
            workspace = root / "ws"
            qual = workspace / "analysis" / "qualification" / "v0"
            qual.mkdir(parents=True)
            (qual / "primitive-lookup.candidate.json").write_text(
                json.dumps(
                    {
                        "schema": "eu4-primitive-lookup/v1",
                        "table": evidence["output"]["table"],
                        "engine_enum_4_gl_mode": 5,
                        "out_of_range_default": 0,
                    }
                ),
                encoding="utf-8",
            )
            (workspace / "progress.md").write_text("# ok\n", encoding="utf-8")

            def run_implementer(campaign_id: str, state: dict) -> dict:
                if state.get("state") == "remediating" and state.get(
                    "rejected_candidate_commit"
                ):
                    # Must see A-fixed candidate from snapshot export semantics
                    cand = json.loads(
                        (qual / "primitive-lookup.candidate.json").read_text(
                            encoding="utf-8"
                        )
                    )
                    self.assertEqual(cand["engine_enum_4_gl_mode"], 5)
                    cand["out_of_range_default"] = 4
                    (qual / "primitive-lookup.candidate.json").write_text(
                        json.dumps(cand), encoding="utf-8"
                    )
                    brief = workspace / "objective-remediation.md"
                    brief.write_text(
                        f"repair out_of_range_default oracle={state.get('oracle_version')}\n",
                        encoding="utf-8",
                    )
                return {
                    "status": "complete",
                    "last_verdict": "pass",
                    "workspace": str(workspace),
                    "session_id": f"ses-{campaign_id}",
                    "verification_manifest_sha256": state[
                        "oracle_manifest_sha256"
                    ],
                }

            def run_reviewer(state: dict) -> Path:
                path = root / f"review-{state['round']}.json"
                if state["round"] >= 1:
                    from automation.review_result import write_review

                    write_review(
                        path,
                        {
                            "schema": "eu4-review/v1",
                            "candidate_commit": state["candidate_commit"],
                            "oracle_manifest_sha256": state["oracle_manifest_sha256"],
                            "outcome": "qualified",
                            "findings": [],
                        },
                    )
                    return path
                write_synthetic_rejection(
                    path,
                    candidate_commit=state["candidate_commit"],
                    oracle_manifest_sha256=state["oracle_manifest_sha256"],
                    binding=_binding(
                        evidence, oracle / "primitive-table.evidence.json"
                    ),
                )
                return path

            workflow = root / "workflow.json"
            controller = HandoffController(
                repo=root,
                workflow_path=workflow,
                run_implementer=run_implementer,
                run_reviewer=run_reviewer,
                oracle_dir=oracle,
                checker_path=root / "accept.py",
                trusted_helpers=[
                    "automation/evidence.py",
                    "automation/macho_map.py",
                    "automation/verification_manifest.py",
                    "automation/replay_primitive.py",
                ],
                checker_relpath="accept.py",
            )
            state = controller.load_or_init("impl-main", pin)
            state = controller.step(state)
            self.assertEqual(state["state"], "qualified_by_checker")
            self.assertTrue(state["candidate_parent"])
            state = controller.step(state)
            self.assertEqual(state["state"], "reviewing")
            state = controller.step(state)
            self.assertEqual(state["state"], "review_rejected")
            self.assertTrue(state.get("rejected_candidate_commit"))
            state = controller.step(state)
            self.assertEqual(state["state"], "remediating")
            self.assertEqual(
                state["remediation_campaign_id"],
                f"impl-main-fix-r1-{state['workflow_id']}",
            )
            state = controller.step(state)
            self.assertEqual(state["state"], "qualified_by_checker")
            state = controller.step(state)
            self.assertEqual(state["state"], "reviewing")
            state = controller.step(state)
            self.assertEqual(state["state"], "awaiting_merge")


class RemediationBuilderTests(unittest.TestCase):
    def test_build_remediation_campaign_absolute_pin(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            pin = _init_git_repo(root)
            # Parent tree with editable candidate so export has a blob
            qual = root / "analysis" / "qualification" / "v0"
            qual.mkdir(parents=True)
            (qual / "primitive-lookup.candidate.json").write_text(
                '{"schema":"eu4-primitive-lookup/v1","table":[],'
                '"engine_enum_4_gl_mode":5,"out_of_range_default":0}\n',
                encoding="utf-8",
            )
            (root / "progress.md").write_text("# ok\n", encoding="utf-8")
            subprocess.run(["git", "add", "."], cwd=root, check=True, capture_output=True)
            subprocess.run(
                ["git", "commit", "-m", "cand"],
                cwd=root,
                check=True,
                capture_output=True,
            )
            cand = subprocess.run(
                ["git", "rev-parse", "HEAD"],
                cwd=root,
                capture_output=True,
                text=True,
                check=True,
            ).stdout.strip()
            oracle = root / "oracle"
            _seed_oracle(root, oracle)
            # Publish a version-local pin path like promote would
            version_dir = oracle / "oracle_versions" / "v_test"
            version_dir.mkdir(parents=True)
            tip = oracle / "verification-manifest.json"
            version_pin = version_dir / "verification-manifest.json"
            version_pin.write_bytes(tip.read_bytes())
            camp_dir = root / "campaigns" / "eu4-a01-trusted-validation"
            camp_dir.mkdir(parents=True)
            (camp_dir / "objective.md").write_text("# base\n", encoding="utf-8")
            (camp_dir / "seed").mkdir()
            (camp_dir / "campaign.json").write_text(
                json.dumps(
                    {
                        "id": "eu4-a01-trusted-validation",
                        "objective": "objective.md",
                        "runtime": "opencode",
                        "model": "test/model",
                        "opencode_version": "0.0.0",
                        "workspace": "seed",
                        "accept": ["python3", "accept.py"],
                        "max_rounds": 2,
                        "max_seconds": 60,
                        "accept_timeout_seconds": 30,
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            state = {
                "workflow_id": "wf_test",
                "remediation_campaign_id": "eu4-a01-trusted-validation-fix-r1",
                "rejected_candidate_commit": cand,
                "oracle_manifest_path": str(
                    version_pin.relative_to(root)
                ),
                "oracle_manifest_sha256": file_sha256(version_pin),
                "oracle_version": "v_test",
                "last_review": {
                    "findings": [
                        {
                            "id": "defect-b",
                            "binding": {
                                "observation": "out_of_range_default",
                                "expected": 4,
                            },
                        }
                    ]
                },
            }
            built = build_remediation_campaign(
                root, camp_dir / "campaign.json", state
            )
            data = json.loads(built.read_text(encoding="utf-8"))
            self.assertTrue(Path(data["verification_manifest"]).is_absolute())
            self.assertTrue(Path(data["verification_manifest"]).is_file())
            self.assertEqual(
                Path(data["verification_manifest"]).resolve(),
                version_pin.resolve(),
            )
            # Relative accept needs a local checker file for load_campaign
            (built.parent / "accept.py").write_text("# checker\n", encoding="utf-8")
            campaign = load_campaign(built)
            self.assertEqual(
                campaign.verification_manifest.resolve(),
                version_pin.resolve(),
            )
            objective = (built.parent / "objective.md").read_text(encoding="utf-8")
            self.assertIn("out_of_range_default", objective)
            self.assertIn("defect-b", objective)
            # Same base campaign, different workflow: never share a seed dir.
            other_state = {**state, "workflow_id": "wf_other"}
            other = build_remediation_campaign(
                root, camp_dir / "campaign.json", other_state
            )
            self.assertNotEqual(other.parent, built.parent)


class ReviewerWorktreeTests(unittest.TestCase):
    def test_restart_reuses_registered_worktree(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            pin = _init_git_repo(root)
            wt = ensure_reviewer_worktree(
                root,
                workflow_id="wf1",
                round_id=0,
                candidate_commit=pin,
            )
            self.assertTrue(wt.is_dir())
            head = subprocess.run(
                ["git", "-C", str(wt), "rev-parse", "HEAD"],
                capture_output=True,
                text=True,
                check=True,
            ).stdout.strip()
            self.assertEqual(head, pin)
            again = ensure_reviewer_worktree(
                root,
                workflow_id="wf1",
                round_id=0,
                candidate_commit=pin,
            )
            self.assertEqual(again.resolve(), wt.resolve())

    def test_progress_md_dirty_is_allowed(self) -> None:
        from automation.handoff import verify_worktree_clean

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            pin = _init_git_repo(root)
            wt = ensure_reviewer_worktree(
                root,
                workflow_id="wf-prog",
                round_id=0,
                candidate_commit=pin,
            )
            (wt / "progress.md").write_text("# reviewer notes\n", encoding="utf-8")
            self.assertEqual(verify_worktree_clean(root, wt), [])
            (wt / "README").write_text("tamper\n", encoding="utf-8")
            errs = verify_worktree_clean(root, wt)
            self.assertTrue(any("README" in e for e in errs))


class LiveReviewerReceiptTests(unittest.TestCase):
    def test_preexisting_rejection_without_receipt_blocked(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            pin = _init_git_repo(root)
            oracle = root / "oracle"
            manifest = _seed_oracle(root, oracle)
            evidence = json.loads(
                (oracle / "primitive-table.evidence.json").read_text(encoding="utf-8")
            )
            workspace = root / "ws"
            qual = workspace / "analysis" / "qualification" / "v0"
            qual.mkdir(parents=True)
            (qual / "primitive-lookup.candidate.json").write_text(
                json.dumps(
                    {
                        "schema": "eu4-primitive-lookup/v1",
                        "table": evidence["output"]["table"],
                        "engine_enum_4_gl_mode": 5,
                        "out_of_range_default": 0,
                    }
                ),
                encoding="utf-8",
            )
            (workspace / "progress.md").write_text("# ok\n", encoding="utf-8")
            reports = root / "reports"
            reports.mkdir()
            planted = reports / "review-r0.json"

            def run_implementer(campaign_id: str, state: dict) -> dict:
                return {
                    "status": "complete",
                    "last_verdict": "pass",
                    "workspace": str(workspace),
                    "session_id": f"impl-{campaign_id}",
                    "verification_manifest_sha256": state[
                        "oracle_manifest_sha256"
                    ],
                }

            def run_reviewer(state: dict) -> dict:
                # Plant a valid rejection but never emit a completed receipt —
                # simulates empty/unverified reviewer session.
                wt = ensure_reviewer_worktree(
                    root,
                    workflow_id=state["workflow_id"],
                    round_id=state["round"],
                    candidate_commit=state["candidate_commit"],
                )
                write_synthetic_rejection(
                    planted,
                    candidate_commit=state["candidate_commit"],
                    oracle_manifest_sha256=state["oracle_manifest_sha256"],
                    binding=_binding(
                        evidence, oracle / "primitive-table.evidence.json"
                    ),
                )
                return {
                    "path": planted,
                    "session_id": "empty-reviewer-session",
                    "worktree": str(wt),
                    # deliberately omit receipt_path
                }

            workflow = root / "workflow.json"
            controller = HandoffController(
                repo=root,
                workflow_path=workflow,
                run_implementer=run_implementer,
                run_reviewer=run_reviewer,
                oracle_dir=oracle,
                checker_path=root / "accept.py",
                trusted_helpers=[
                    "automation/evidence.py",
                    "automation/macho_map.py",
                    "automation/verification_manifest.py",
                    "automation/replay_primitive.py",
                ],
                checker_relpath="accept.py",
            )
            state = controller.load_or_init("impl-live", pin)
            state["require_live_sessions"] = True
            state = controller.step(state)
            self.assertEqual(state["state"], "qualified_by_checker")
            state = controller.step(state)
            self.assertEqual(state["state"], "reviewing")
            state = controller.step(state)
            self.assertEqual(state["state"], "blocked")
            self.assertIn("live_reviewer_receipt", state["block_reason"])

    def test_matching_receipt_allows_rejection(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            pin = _init_git_repo(root)
            oracle = root / "oracle"
            _seed_oracle(root, oracle)
            evidence = json.loads(
                (oracle / "primitive-table.evidence.json").read_text(encoding="utf-8")
            )
            workspace = root / "ws"
            qual = workspace / "analysis" / "qualification" / "v0"
            qual.mkdir(parents=True)
            (qual / "primitive-lookup.candidate.json").write_text(
                json.dumps(
                    {
                        "schema": "eu4-primitive-lookup/v1",
                        "table": evidence["output"]["table"],
                        "engine_enum_4_gl_mode": 5,
                        "out_of_range_default": 0,
                    }
                ),
                encoding="utf-8",
            )
            (workspace / "progress.md").write_text("# ok\n", encoding="utf-8")
            reports = root / "reports"
            reports.mkdir()

            def run_implementer(campaign_id: str, state: dict) -> dict:
                return {
                    "status": "complete",
                    "last_verdict": "pass",
                    "workspace": str(workspace),
                    "session_id": f"impl-{campaign_id}",
                    "verification_manifest_sha256": state[
                        "oracle_manifest_sha256"
                    ],
                }

            def run_reviewer(state: dict) -> dict:
                wt = ensure_reviewer_worktree(
                    root,
                    workflow_id=state["workflow_id"],
                    round_id=state["round"],
                    candidate_commit=state["candidate_commit"],
                )
                path = reports / f"review-r{state['round']}.json"
                write_synthetic_rejection(
                    path,
                    candidate_commit=state["candidate_commit"],
                    oracle_manifest_sha256=state["oracle_manifest_sha256"],
                    binding=_binding(
                        evidence, oracle / "primitive-table.evidence.json"
                    ),
                )
                receipt = reviewer_receipt_path_for(path)
                write_reviewer_receipt(
                    receipt,
                    campaign_id=state["reviewer_campaign_id"],
                    candidate_commit=state["candidate_commit"],
                    oracle_digest=state["oracle_manifest_sha256"],
                    reviewer_session_id="reviewer-ses-1",
                    runner_status="complete",
                    report_sha256=file_sha256(path),
                    qualification_mode="live",
                )
                return {
                    "path": path,
                    "session_id": "reviewer-ses-1",
                    "receipt_path": str(receipt),
                    "worktree": str(wt),
                    "qualification_mode": "live",
                }

            workflow = root / "workflow.json"
            controller = HandoffController(
                repo=root,
                workflow_path=workflow,
                run_implementer=run_implementer,
                run_reviewer=run_reviewer,
                oracle_dir=oracle,
                checker_path=root / "accept.py",
                trusted_helpers=[
                    "automation/evidence.py",
                    "automation/macho_map.py",
                    "automation/verification_manifest.py",
                    "automation/replay_primitive.py",
                ],
                checker_relpath="accept.py",
            )
            state = controller.load_or_init("impl-live2", pin)
            state["require_live_sessions"] = True
            state = controller.step(state)
            state = controller.step(state)
            state = controller.step(state)
            self.assertEqual(state["state"], "review_rejected")

    def test_quarantine_preexisting_report(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            report = root / "review.json"
            report.write_text('{"schema":"eu4-review/v1"}\n', encoding="utf-8")
            moved = quarantine_report(report)
            self.assertIsNotNone(moved)
            self.assertFalse(report.is_file())
            self.assertTrue(moved.is_file())


class OraclePublishOrderTests(unittest.TestCase):
    def test_version_local_manifest_written_and_recovered(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            _init_git_repo(root)
            oracle = root / "oracle"
            _seed_oracle(root, oracle)
            evidence = json.loads(
                (oracle / "primitive-table.evidence.json").read_text(encoding="utf-8")
            )
            helpers = [
                "automation/evidence.py",
                "automation/macho_map.py",
                "automation/verification_manifest.py",
                "automation/replay_primitive.py",
            ]
            binding = _binding(evidence, oracle / "primitive-table.evidence.json")
            review = _rejection("a" * 40, "b" * 64, binding)
            review_path = root / "review.json"
            review_path.write_text(json.dumps(review), encoding="utf-8")
            first = promote_grounded_findings(
                repo=root,
                review=review,
                oracle_dir=oracle,
                trusted_helpers=helpers,
                checker_relpath="accept.py",
                review_path=review_path,
            )
            v1 = first["oracle_version"]
            pin = oracle / "oracle_versions" / v1 / "verification-manifest.json"
            self.assertTrue(pin.is_file())
            tip_bytes = (oracle / "verification-manifest.json").read_bytes()
            self.assertEqual(pin.read_bytes(), tip_bytes)
            # Delete version-local pin; reuse must recreate after tip validation
            pin.unlink()
            second = promote_grounded_findings(
                repo=root,
                review=review,
                oracle_dir=oracle,
                trusted_helpers=helpers,
                checker_relpath="accept.py",
                review_path=review_path,
            )
            self.assertTrue(second["reused"])
            self.assertTrue(pin.is_file())
            self.assertEqual(pin.read_bytes(), tip_bytes)


class ReviewerObjectiveTests(unittest.TestCase):
    def test_live_objective_omits_defect_b_hint(self) -> None:
        text = (
            REPO / "campaigns/eu4-a02-reviewer/objective.md"
        ).read_text(encoding="utf-8")
        self.assertNotIn("out_of_range_default", text)


@unittest.skipUnless(EXE.is_file(), "pinned EU4 executable not installed")
class LiveEu4IntegrationTests(unittest.TestCase):
    """macOS-only: real binary drift check against tracked tip oracle."""

    def test_tracked_accept_seed_fails(self) -> None:
        seed = REPO / "campaigns/eu4-a01-trusted-validation/seed"
        with tempfile.TemporaryDirectory() as temporary:
            ws = Path(temporary) / "ws"
            shutil.copytree(seed, ws)
            ran = subprocess.run(
                [
                    sys.executable,
                    str(REPO / "campaigns/eu4-a01-trusted-validation/accept.py"),
                    str(ws),
                ],
                cwd=REPO,
                capture_output=True,
                text=True,
            )
            self.assertEqual(json.loads(ran.stdout)["verdict"], "fail")


def _candidate_workspace(root: Path, evidence: dict) -> Path:
    workspace = root / "ws"
    qual = workspace / "analysis" / "qualification" / "v0"
    qual.mkdir(parents=True)
    (qual / "primitive-lookup.candidate.json").write_text(
        json.dumps(
            {
                "schema": "eu4-primitive-lookup/v1",
                "table": evidence["output"]["table"],
                "engine_enum_4_gl_mode": 5,
                "out_of_range_default": 0,
            }
        ),
        encoding="utf-8",
    )
    (workspace / "progress.md").write_text("# ok\n", encoding="utf-8")
    return workspace


def _live_controller(
    root: Path,
    oracle: Path,
    workspace: Path,
    run_reviewer,
    implementer_digest: str | None = None,
) -> HandoffController:
    def run_implementer(campaign_id: str, state: dict) -> dict:
        return {
            "status": "complete",
            "last_verdict": "pass",
            "workspace": str(workspace),
            "session_id": f"impl-{campaign_id}",
            "verification_manifest_sha256": (
                implementer_digest
                if implementer_digest is not None
                else state["oracle_manifest_sha256"]
            ),
        }

    return HandoffController(
        repo=root,
        workflow_path=root / "workflow.json",
        run_implementer=run_implementer,
        run_reviewer=run_reviewer,
        oracle_dir=oracle,
        checker_path=root / "accept.py",
        trusted_helpers=[
            "automation/evidence.py",
            "automation/macho_map.py",
            "automation/verification_manifest.py",
            "automation/replay_primitive.py",
        ],
        checker_relpath="accept.py",
    )


def _qualified_reviewer(root: Path, reports: Path):
    def run_reviewer(state: dict) -> dict:
        wt = ensure_reviewer_worktree(
            root,
            workflow_id=state["workflow_id"],
            round_id=state["round"],
            candidate_commit=state["candidate_commit"],
        )
        path = reviewer_report_path(reports, state["workflow_id"], state["round"])
        write_review(
            path,
            {
                "schema": "eu4-review/v1",
                "candidate_commit": state["candidate_commit"],
                "oracle_manifest_sha256": state["oracle_manifest_sha256"],
                "outcome": "qualified",
                "findings": [],
            },
        )
        receipt = reviewer_receipt_path_for(path)
        write_reviewer_receipt(
            receipt,
            campaign_id=state["reviewer_campaign_id"],
            candidate_commit=state["candidate_commit"],
            oracle_digest=state["oracle_manifest_sha256"],
            reviewer_session_id="reviewer-ses-1",
            runner_status="complete",
            report_sha256=file_sha256(path),
            qualification_mode="live",
        )
        return {
            "path": path,
            "session_id": "reviewer-ses-1",
            "receipt_path": str(receipt),
            "worktree": str(wt),
            "qualification_mode": "live",
        }

    return run_reviewer


class WorkflowNamespaceTests(unittest.TestCase):
    def test_run_roots_and_reports_are_workflow_scoped(self) -> None:
        repo = Path("/repo")
        self.assertNotEqual(
            run_root_for(repo, "wf_a", "base"), run_root_for(repo, "wf_b", "base")
        )
        self.assertEqual(
            run_root_for(repo, "wf_a", "base"), run_root_for(repo, "wf_a", "base")
        )
        reports = repo / "reports"
        self.assertNotEqual(
            reviewer_report_path(reports, "wf_a", 0),
            reviewer_report_path(reports, "wf_b", 0),
        )
        self.assertNotEqual(
            reviewer_worktree_path(repo, "wf_a", 0),
            reviewer_worktree_path(repo, "wf_b", 0),
        )


class ReviewerRecoveryTests(unittest.TestCase):
    CAMPAIGN = "base-review-r0-wf_x"
    CANDIDATE = "c" * 40
    ORACLE = "d" * 64

    def _fixture(self, root: Path) -> tuple[Path, Path, Path]:
        report = reviewer_report_path(root / "reports", "wf_x", 0)
        report.parent.mkdir(parents=True)
        evidence_path = FIXTURE / "primitive-table.evidence.json"
        evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
        write_synthetic_rejection(
            report,
            candidate_commit=self.CANDIDATE,
            oracle_manifest_sha256=self.ORACLE,
            binding=_binding(evidence, evidence_path),
        )
        invocation = reviewer_invocation_path_for(report)
        run_root = run_root_for(root, "wf_x", self.CAMPAIGN)
        run_root.mkdir(parents=True)
        (run_root / "state.json").write_text(
            json.dumps(
                {
                    "campaign_id": self.CAMPAIGN,
                    "status": "complete",
                    "last_verdict": "pass",
                    "session_id": "reviewer-ses-1",
                    "verification_manifest_sha256": self.ORACLE,
                    "workspace": str(root / "wt"),
                }
            )
            + "\n",
            encoding="utf-8",
        )
        return report, invocation, run_root

    def _recover(self, root, report, invocation, run_root):
        return recover_completed_reviewer_receipt(
            root,
            run_root=run_root,
            report_path=report,
            invocation_path=invocation,
            campaign_id=self.CAMPAIGN,
            candidate_commit=self.CANDIDATE,
            oracle_digest=self.ORACLE,
            worktree=root / "wt",
        )

    def test_recovers_completed_reviewer_without_receipt(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            report, invocation, run_root = self._fixture(root)
            write_reviewer_invocation(
                invocation,
                campaign_id=self.CAMPAIGN,
                candidate_commit=self.CANDIDATE,
                oracle_digest=self.ORACLE,
                report_path=report,
                worktree=root / "wt",
            )
            result = self._recover(root, report, invocation, run_root)
            self.assertIsNotNone(result)
            self.assertEqual(result["session_id"], "reviewer-ses-1")
            self.assertTrue(reviewer_receipt_path_for(report).is_file())

    def test_planted_report_with_completed_session_not_attributed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            report, invocation, run_root = self._fixture(root)
            # No launch record: the report may predate the session.
            self.assertIsNone(self._recover(root, report, invocation, run_root))
            self.assertFalse(reviewer_receipt_path_for(report).is_file())

    def test_runner_oracle_mismatch_not_recovered(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            report, invocation, run_root = self._fixture(root)
            write_reviewer_invocation(
                invocation,
                campaign_id=self.CAMPAIGN,
                candidate_commit=self.CANDIDATE,
                oracle_digest=self.ORACLE,
                report_path=report,
                worktree=root / "wt",
            )
            saved = json.loads((run_root / "state.json").read_text(encoding="utf-8"))
            saved["verification_manifest_sha256"] = "e" * 64
            (run_root / "state.json").write_text(json.dumps(saved), encoding="utf-8")
            self.assertIsNone(self._recover(root, report, invocation, run_root))


class ReviewerCheckoutRecoveryTests(unittest.TestCase):
    def test_modified_worktree_blocks_qualification(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            pin = _init_git_repo(root)
            oracle = root / "oracle"
            _seed_oracle(root, oracle)
            evidence = json.loads(
                (oracle / "primitive-table.evidence.json").read_text(encoding="utf-8")
            )
            workspace = _candidate_workspace(root, evidence)
            reports = root / "reports"
            reports.mkdir()
            controller = _live_controller(
                root, oracle, workspace, _qualified_reviewer(root, reports)
            )
            state = controller.load_or_init("impl-live", pin)
            state["require_live_sessions"] = True
            state = controller.step(state)
            self.assertEqual(state["state"], "qualified_by_checker")
            state = controller.step(state)
            self.assertEqual(state["state"], "reviewing")
            self.assertTrue(state["reviewer_worktree"])
            # Crash before review_path/worktree are persisted.
            state["review_path"] = None
            state["reviewer_worktree"] = None
            original = controller.run_reviewer

            def reviewer_without_worktree(state_: dict) -> dict:
                result = original(state_)
                result.pop("worktree", None)  # simulates the lost state field
                return result

            controller.run_reviewer = reviewer_without_worktree
            wt = reviewer_worktree_path(root, state["workflow_id"], state["round"])
            (wt / "README").write_text("tamper\n", encoding="utf-8")
            state = controller.step(state)
            state = controller.step(state)
            self.assertEqual(state["state"], "blocked")
            self.assertIn("reviewer_worktree", state["block_reason"])


class OracleIdentityTests(unittest.TestCase):
    def test_missing_oracle_path_backfilled_when_digest_matches(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            pin = _init_git_repo(root)
            oracle = root / "oracle"
            _seed_oracle(root, oracle)
            evidence = json.loads(
                (oracle / "primitive-table.evidence.json").read_text(encoding="utf-8")
            )
            workspace = _candidate_workspace(root, evidence)
            controller = _live_controller(
                root, oracle, workspace, lambda state: Path("unused")
            )
            state = controller.load_or_init("impl-live", pin)
            state.pop("oracle_manifest_path")
            (root / "workflow.json").write_text(
                json.dumps(state), encoding="utf-8"
            )
            reloaded = controller.load_or_init("impl-live", pin)
            self.assertEqual(
                reloaded["oracle_manifest_path"],
                str(oracle / "verification-manifest.json"),
            )

    def test_runner_digest_must_match_workflow_pin(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            pin = _init_git_repo(root)
            oracle = root / "oracle"
            _seed_oracle(root, oracle)
            evidence = json.loads(
                (oracle / "primitive-table.evidence.json").read_text(encoding="utf-8")
            )
            workspace = _candidate_workspace(root, evidence)
            controller = _live_controller(
                root, oracle, workspace, lambda state: Path("unused"),
                implementer_digest="f" * 64,
            )
            state = controller.load_or_init("impl-live", pin)
            state = controller.step(state)
            self.assertEqual(state["state"], "blocked")
            self.assertIn("oracle_pin_mismatch", state["block_reason"])

    def test_oracle_advance_does_not_relabel_qualification(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            pin = _init_git_repo(root)
            oracle = root / "oracle"
            manifest = _seed_oracle(root, oracle)
            pinned = digest_manifest(manifest)
            evidence = json.loads(
                (oracle / "primitive-table.evidence.json").read_text(encoding="utf-8")
            )
            workspace = _candidate_workspace(root, evidence)
            controller = _live_controller(
                root, oracle, workspace, lambda state: Path("unused")
            )
            state = controller.load_or_init("impl-live", pin)
            # Another workflow promotes a new oracle while this run is in flight.
            advanced = dict(manifest)
            advanced["oracle_version_name"] = "v_next"
            save_manifest(oracle / "verification-manifest.json", advanced)
            self.assertNotEqual(digest_manifest(advanced), pinned)
            state = controller.step(state)
            self.assertEqual(state["state"], "qualified_by_checker")
            self.assertEqual(state["oracle_manifest_sha256"], pinned)


class ReviewerCampaignTests(unittest.TestCase):
    def test_campaign_pins_oracle_and_workflow(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            reviewer_src = root / "campaigns" / "eu4-a02-reviewer"
            reviewer_src.mkdir(parents=True)
            (reviewer_src / "objective.md").write_text("review\n", encoding="utf-8")
            (reviewer_src / "accept.py").write_text("# checker\n", encoding="utf-8")
            base = root / "base"
            (base / "seed").mkdir(parents=True)
            (base / "accept.py").write_text("# checker\n", encoding="utf-8")
            (base / "campaign.json").write_text(
                json.dumps(
                    {
                        "id": "base",
                        "objective": "objective.md",
                        "runtime": "opencode",
                        "model": "test/model",
                        "opencode_version": "0.0.0",
                        "workspace": "seed",
                        "accept": ["python3", "accept.py"],
                        "max_rounds": 2,
                        "max_seconds": 60,
                        "accept_timeout_seconds": 30,
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            (base / "objective.md").write_text("base\n", encoding="utf-8")
            pin = root / "oracle" / "verification-manifest.json"
            pin.parent.mkdir()
            pin.write_text("{}\n", encoding="utf-8")
            state = {
                "workflow_id": "wf_x",
                "reviewer_campaign_id": "base-review-r0-wf_x",
                "candidate_commit": "c" * 40,
            }
            built = materialize_reviewer_campaign(
                root,
                state,
                campaign_path=base / "campaign.json",
                worktree=root / "wt",
                report_path=reviewer_report_path(root / "reports", "wf_x", 0),
                pin=pin,
            )
            data = json.loads(built.read_text(encoding="utf-8"))
            self.assertEqual(data["id"], "base-review-r0-wf_x")
            self.assertEqual(Path(data["verification_manifest"]), pin)
            self.assertEqual(data["workspace"]["commit"], "c" * 40)


class LegacyLayoutTests(unittest.TestCase):
    def test_unfinished_legacy_workflow_blocks(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            legacy_root = root / ".local" / "automation" / "base"
            legacy_root.mkdir(parents=True)
            reports = root / "reports"
            reports.mkdir()
            state = {
                "state": "reviewing",
                "workflow_id": "wf_new",
                "round": 0,
                "implementer_campaign_id": "base",
                "reviewer_campaign_id": "base-review-r0",
            }
            self.assertEqual(
                legacy_workflow_layout(root, state, reports), legacy_root
            )
            state["state"] = "awaiting_merge"
            self.assertIsNone(legacy_workflow_layout(root, state, reports))

    def test_legacy_report_blocks_before_new_session(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            reports = root / "reports"
            reports.mkdir()
            planted = reports / "review-r0.json"
            planted.write_text("{}\n", encoding="utf-8")
            state = {
                "state": "reviewing",
                "workflow_id": "wf_new",
                "round": 0,
                "implementer_campaign_id": "base",
                "reviewer_campaign_id": "base-review-r0",
            }
            self.assertEqual(
                legacy_workflow_layout(root, state, reports), planted
            )

    def test_scoped_workflow_ignores_another_workflows_flat_report(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            reports = root / "reports"
            reports.mkdir()
            (reports / "review-r1.json").write_text("{}\n", encoding="utf-8")
            (root / ".local" / "automation" / "wf_new" / "base").mkdir(parents=True)
            state = {
                "state": "reviewing",
                "workflow_id": "wf_new",
                "round": 1,
                "implementer_campaign_id": "base",
                "reviewer_campaign_id": "base-review-r1-wf_new",
            }
            self.assertIsNone(legacy_workflow_layout(root, state, reports))


if __name__ == "__main__":
    unittest.main()
