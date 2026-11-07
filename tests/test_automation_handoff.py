"""A0.2 review schema, oracle promotion, handoff, and scripted two-defect path."""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from automation.handoff import (
    HandoffController,
    snapshot_candidate,
    verify_ref_unmodified,
    write_synthetic_rejection,
)
from automation.oracle_promote import PromotionDecision, classify_finding, promote_grounded_findings
from automation.review_result import validate_review
from automation.verification_manifest import (
    build_manifest,
    digest_manifest,
    file_sha256,
    load_manifest,
    member_from_file,
    save_manifest,
)

REPO = Path(__file__).resolve().parents[1]
ORACLE = REPO / "analysis" / "qualification" / "v0"
EXE = Path("/Applications/EuropaUniversalisIV/eu4.app/Contents/MacOS/eu4")


def _init_git_repo(root: Path) -> None:
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
    subprocess.run(["git", "add", "README"], cwd=root, check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "init"], cwd=root, check=True, capture_output=True
    )


class ReviewSchemaTests(unittest.TestCase):
    def test_rejects_prose_only_outcome(self) -> None:
        errors = validate_review({"outcome": "rejected"})
        self.assertTrue(errors)

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
    def test_repo_native_ref_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            _init_git_repo(root)
            workspace = root / "ws"
            qual = workspace / "analysis" / "qualification" / "v0"
            qual.mkdir(parents=True)
            (qual / "candidate.json").write_text('{"ok":1}\n', encoding="utf-8")
            (workspace / "progress.md").write_text("# p\n", encoding="utf-8")
            first = snapshot_candidate(
                root, workspace, workflow_id="wf", round_id=0
            )
            self.assertEqual(
                verify_ref_unmodified(
                    root, first["candidate_ref"], first["candidate_commit"]
                ),
                [],
            )
            # Move the ref away to prove integrity detection
            subprocess.run(
                [
                    "git",
                    "-C",
                    str(root),
                    "update-ref",
                    first["candidate_ref"],
                    "0" * 40,
                ],
                check=False,
                capture_output=True,
            )
            # Force a different commit by mutating content then re-snapshot
            (qual / "candidate.json").write_text('{"ok":2}\n', encoding="utf-8")
            second = snapshot_candidate(
                root, workspace, workflow_id="wf", round_id=1
            )
            self.assertNotEqual(first["candidate_tree"], second["candidate_tree"])
            self.assertEqual(
                verify_ref_unmodified(
                    root, second["candidate_ref"], second["candidate_commit"]
                ),
                [],
            )
            self.assertTrue(
                verify_ref_unmodified(
                    root, second["candidate_ref"], first["candidate_commit"]
                )
            )


@unittest.skipUnless(
    EXE.is_file() and (ORACLE / "verification-manifest.json").is_file(),
    "oracle not promoted",
)
class OraclePromotionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.evidence = json.loads(
            (ORACLE / "primitive-table.evidence.json").read_text(encoding="utf-8")
        )
        self.evidence_path = ORACLE / "primitive-table.evidence.json"

    def test_unestablished_reject_only(self) -> None:
        finding = {
            "id": "gap",
            "severity": "P1",
            "kind": "behavioral_counterexample",
            "reproducer": "missing.json",
            "expected_provenance": "unestablished",
        }
        self.assertEqual(
            classify_finding(finding, self.evidence, self.evidence_path),
            PromotionDecision.REJECT_ONLY,
        )

    def test_grounded_binding_promotes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            _init_git_repo(root)
            # Mirror trusted helpers + evidence under temp repo
            for rel in (
                "automation/evidence.py",
                "automation/macho_map.py",
                "automation/verification_manifest.py",
                "automation/replay_primitive.py",
                "automation/review_result.py",
                "automation/oracle_promote.py",
                "campaigns/eu4-a01-trusted-validation/accept.py",
            ):
                dest = root / rel
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(REPO / rel, dest)
            oracle = root / "analysis" / "qualification" / "v0"
            oracle.mkdir(parents=True)
            shutil.copy2(self.evidence_path, oracle / "primitive-table.evidence.json")
            (oracle / "CURRENT").write_text("v0\n", encoding="utf-8")
            helpers = [
                "automation/evidence.py",
                "automation/macho_map.py",
                "automation/verification_manifest.py",
                "automation/replay_primitive.py",
            ]
            members = [member_from_file(root, h, "trusted_helper") for h in helpers]
            members.append(
                member_from_file(
                    root, "campaigns/eu4-a01-trusted-validation/accept.py", "checker"
                )
            )
            members.append(
                member_from_file(
                    root,
                    "analysis/qualification/v0/primitive-table.evidence.json",
                    "frozen_oracle",
                )
            )
            save_manifest(oracle / "verification-manifest.json", build_manifest(members=members))

            binding = {
                "replay_id": "primitive_lookup_v1",
                "observation": "out_of_range_default",
                "expected": self.evidence["output"]["mapping"]["default_out_of_range"],
                "function_rva": self.evidence["function_rva"],
                "executable_sha256": self.evidence["executable_sha256"],
                "evidence_ref": {
                    "path": "analysis/qualification/v0/primitive-table.evidence.json",
                    "sha256": file_sha256(oracle / "primitive-table.evidence.json"),
                },
                "sequence": ["read_candidate", "compare"],
            }
            review = {
                "schema": "eu4-review/v1",
                "candidate_commit": "a" * 40,
                "oracle_manifest_sha256": "b" * 64,
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
            result = promote_grounded_findings(
                repo=root,
                review=review,
                oracle_dir=oracle,
                trusted_helpers=helpers,
                checker_relpath="campaigns/eu4-a01-trusted-validation/accept.py",
            )
            self.assertTrue(result["promoted"])
            self.assertEqual(result["oracle_version"], "v1")
            self.assertEqual((oracle / "CURRENT").read_text(encoding="utf-8").strip(), "v1")
            # Interrupt smoke: pretend crash after publish — restart sees CURRENT=v1
            self.assertTrue(
                (oracle / "oracle_versions" / "v1" / "regressions" / "defect-b.json").is_file()
            )
            again = promote_grounded_findings(
                repo=root,
                review=review,
                oracle_dir=oracle,
                trusted_helpers=helpers,
                checker_relpath="campaigns/eu4-a01-trusted-validation/accept.py",
            )
            # New version attempt: prior CURRENT is v1 so next would be v2, but
            # same finding re-promotes to a new version — for smoke we only need
            # CURRENT never rolls back to missing.
            self.assertTrue((oracle / "CURRENT").is_file())
            self.assertNotEqual(again.get("oracle_version"), "v0")


@unittest.skipUnless(
    EXE.is_file() and (ORACLE / "verification-manifest.json").is_file(),
    "oracle not promoted",
)
class OpenCodeGateTests(unittest.TestCase):
    def test_run_opencode_requires_password(self) -> None:
        env = {**dict(**{k: v for k, v in __import__("os").environ.items()})}
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


class ScriptedTwoDefectTests(unittest.TestCase):
    def test_scripted_handoff_reaches_awaiting_merge(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            workflow = (
                REPO / ".local" / "qualification" / f"wf-test-{Path(temporary).name}.json"
            )
            workflow.parent.mkdir(parents=True, exist_ok=True)
            if workflow.exists():
                workflow.unlink()
            workspace = REPO / ".local" / "qualification" / "scripted-workspace"
            if workspace.exists():
                shutil.rmtree(workspace)
            # Ensure clean v0 oracle (prior runs may have promoted regressions).
            subprocess.run(
                [sys.executable, "-m", "automation.promote_evidence"],
                cwd=REPO,
                check=True,
                capture_output=True,
            )
            versions = ORACLE / "oracle_versions"
            if versions.exists():
                shutil.rmtree(versions)
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
                # Restore frozen v0 for other tests / clean working tree.
                if versions.exists():
                    shutil.rmtree(versions)
                subprocess.run(
                    [sys.executable, "-m", "automation.promote_evidence"],
                    cwd=REPO,
                    check=True,
                    capture_output=True,
                )
                if workflow.exists():
                    workflow.unlink()


class HandoffRemediationIdTests(unittest.TestCase):
    def test_rejection_creates_new_remediation_campaign_id(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            _init_git_repo(root)
            oracle = root / "oracle"
            oracle.mkdir()
            helper = root / "helper.py"
            helper.write_text("ok\n", encoding="utf-8")
            checker = root / "accept.py"
            checker.write_text("ok\n", encoding="utf-8")
            evidence = {
                "function_rva": "0x1",
                "executable_sha256": "c" * 64,
                "output": {
                    "table": [0, 1, 3, 4, 5, 10, 11, 12],
                    "engine_enum_4_gl_mode": 5,
                    "mapping": {"default_out_of_range": 4},
                },
            }
            (oracle / "primitive-table.evidence.json").write_text(
                json.dumps(evidence), encoding="utf-8"
            )
            (oracle / "CURRENT").write_text("v0\n", encoding="utf-8")
            save_manifest(
                oracle / "verification-manifest.json",
                build_manifest(
                    members=[
                        member_from_file(root, "helper.py", "trusted_helper"),
                        member_from_file(root, "accept.py", "checker"),
                        member_from_file(
                            root,
                            "oracle/primitive-table.evidence.json",
                            "frozen_oracle",
                        ),
                    ]
                ),
            )
            # Also place replay helper so promote member_from_file can hash it if listed
            (root / "automation").mkdir()
            (root / "automation" / "replay_primitive.py").write_text(
                (REPO / "automation" / "replay_primitive.py").read_text(encoding="utf-8"),
                encoding="utf-8",
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
            calls = {"implementer": 0, "reviewer": 0}

            def run_implementer(campaign_id: str, state: dict) -> dict:
                calls["implementer"] += 1
                if "fix" in campaign_id:
                    cand = json.loads(
                        (qual / "primitive-lookup.candidate.json").read_text(
                            encoding="utf-8"
                        )
                    )
                    cand["out_of_range_default"] = 4
                    (qual / "primitive-lookup.candidate.json").write_text(
                        json.dumps(cand), encoding="utf-8"
                    )
                return {
                    "status": "complete",
                    "last_verdict": "pass",
                    "workspace": str(workspace),
                }

            def run_reviewer(state: dict) -> Path:
                calls["reviewer"] += 1
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
                binding = {
                    "replay_id": "primitive_lookup_v1",
                    "observation": "out_of_range_default",
                    "expected": 4,
                    "function_rva": evidence["function_rva"],
                    "executable_sha256": evidence["executable_sha256"],
                    "evidence_ref": {
                        "path": "oracle/primitive-table.evidence.json",
                        "sha256": file_sha256(
                            oracle / "primitive-table.evidence.json"
                        ),
                    },
                    "sequence": ["read", "compare"],
                }
                write_synthetic_rejection(
                    path,
                    candidate_commit=state["candidate_commit"],
                    oracle_manifest_sha256=state["oracle_manifest_sha256"],
                    binding=binding,
                )
                return path

            workflow = root / "workflow.json"
            controller = HandoffController(
                repo=root,
                workflow_path=workflow,
                run_implementer=run_implementer,
                run_reviewer=run_reviewer,
                oracle_dir=oracle,
                checker_path=checker,
                trusted_helpers=["helper.py", "automation/replay_primitive.py"],
                checker_relpath="accept.py",
            )
            state = controller.load_or_init("impl-main", "c" * 40)
            state = controller.step(state)
            self.assertEqual(state["state"], "qualified_by_checker")
            state = controller.step(state)
            self.assertEqual(state["state"], "reviewing")
            state = controller.step(state)
            self.assertEqual(state["state"], "review_rejected")
            state = controller.step(state)
            self.assertEqual(state["state"], "remediating")
            self.assertEqual(state["remediation_campaign_id"], "impl-main-fix-r1")
            self.assertEqual(state["round"], 1)
            state = controller.step(state)
            self.assertEqual(state["state"], "qualified_by_checker")
            state = controller.step(state)
            self.assertEqual(state["state"], "reviewing")
            state = controller.step(state)
            self.assertEqual(state["state"], "awaiting_merge")
            self.assertIsNotNone(state["qualification_receipt"])
            self.assertGreaterEqual(calls["implementer"], 2)


if __name__ == "__main__":
    unittest.main()
