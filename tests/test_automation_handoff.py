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
    path_allowlisted,
    snapshot_candidate,
    verify_ref_unmodified,
    write_synthetic_rejection,
)
from automation.oracle_promote import (
    PromotionDecision,
    classify_finding,
    finding_content_digest,
    promote_grounded_findings,
)
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


class OpenCodeGateTests(unittest.TestCase):
    def test_run_opencode_requires_password(self) -> None:
        env = {k: v for k, v in os.environ.items()}
        env.pop("OPENCODE_SERVER_PASSWORD", None)
        workflow = REPO / ".local" / "qualification" / "opencode-gate-workflow.json"
        workflow.parent.mkdir(parents=True, exist_ok=True)
        workflow.write_text("{}\n", encoding="utf-8")
        campaign = REPO / "campaigns" / "eu4-a01-trusted-validation" / "campaign.json"
        ran = subprocess.run(
            [
                sys.executable,
                "-m",
                "automation.handoff_cli",
                "run-opencode",
                str(workflow),
                str(campaign),
            ],
            cwd=REPO,
            env=env,
            capture_output=True,
            text=True,
        )
        self.assertEqual(ran.returncode, 2)
        self.assertIn("OPENCODE_SERVER_PASSWORD", ran.stderr)


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
            self.assertEqual(state["remediation_campaign_id"], "impl-main-fix-r1")
            state = controller.step(state)
            self.assertEqual(state["state"], "qualified_by_checker")
            state = controller.step(state)
            self.assertEqual(state["state"], "reviewing")
            state = controller.step(state)
            self.assertEqual(state["state"], "awaiting_merge")


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


if __name__ == "__main__":
    unittest.main()
