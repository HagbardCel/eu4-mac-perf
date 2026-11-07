"""A0.2 review schema, oracle promotion, and handoff state machine."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from automation.handoff import (
    HandoffController,
    snapshot_candidate,
    verify_snapshot_unmodified,
    write_synthetic_rejection,
)
from automation.oracle_promote import promote_grounded_findings
from automation.review_result import validate_review, write_review
from automation.verification_manifest import (
    build_manifest,
    digest_manifest,
    member_from_file,
    save_manifest,
)

REPO = Path(__file__).resolve().parents[1]


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


class HandoffIdempotencyTests(unittest.TestCase):
    def test_snapshot_reuse_and_integrity(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            workspace = root / "ws"
            workspace.mkdir()
            (workspace / "file.txt").write_text("one\n", encoding="utf-8")
            snap = root / "snap"
            first = snapshot_candidate(workspace, snap)
            second = snapshot_candidate(workspace, snap)
            self.assertEqual(first["candidate_commit"], second["candidate_commit"])
            self.assertEqual(
                verify_snapshot_unmodified(snap, first["candidate_commit"]), []
            )
            (snap / "file.txt").write_text("mutated\n", encoding="utf-8")
            errors = verify_snapshot_unmodified(snap, first["candidate_commit"])
            self.assertTrue(errors)

    def test_rejection_creates_new_remediation_campaign_id(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            oracle = root / "oracle"
            oracle.mkdir()
            helper = root / "helper.py"
            helper.write_text("ok\n", encoding="utf-8")
            checker = root / "accept.py"
            checker.write_text("ok\n", encoding="utf-8")
            # Seed empty prior manifest
            members = [member_from_file(root, "helper.py", "trusted_helper")]
            members.append(member_from_file(root, "accept.py", "checker"))
            save_manifest(oracle / "verification-manifest.json", build_manifest(members=members))

            workspace = root / "ws"
            workspace.mkdir()
            (workspace / "cand.txt").write_text("ok\n", encoding="utf-8")
            calls = {"implementer": 0, "reviewer": 0}

            def run_implementer(campaign_id: str) -> dict:
                calls["implementer"] += 1
                return {
                    "status": "complete",
                    "last_verdict": "pass",
                    "workspace": str(workspace),
                }

            def run_reviewer(state: dict) -> Path:
                calls["reviewer"] += 1
                path = root / f"review-{state['round']}.json"
                repro = root / "repro.json"
                repro.write_text(
                    json.dumps(
                        {
                            "expected_provenance": "instruction_static",
                            "expected": {"ok": True},
                            "sequence": ["create", "link"],
                        }
                    ),
                    encoding="utf-8",
                )
                # Copy repro into repo-relative layout for promotion
                # Use absolute path as reproducer for this temp test via rewrite
                write_synthetic_rejection(
                    path,
                    candidate_commit=state["candidate_commit"],
                    oracle_manifest_sha256=state["oracle_manifest_sha256"],
                    reproducer_relpath=str(repro),
                )
                # Patch review to absolute path accepted by promote (repo=root)
                data = json.loads(path.read_text(encoding="utf-8"))
                data["findings"][0]["reproducer"] = "repro.json"
                path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
                return path

            workflow = root / "workflow.json"
            controller = HandoffController(
                repo=root,
                workflow_path=workflow,
                run_implementer=run_implementer,
                run_reviewer=run_reviewer,
                oracle_dir=oracle,
                trusted_helpers=["helper.py"],
                checker_relpath="accept.py",
            )
            state = controller.load_or_init("impl-main", "c" * 40)
            # implement → qualify
            state = controller.step(state)
            self.assertEqual(state["state"], "qualified_by_checker")
            # begin review
            state = controller.step(state)
            self.assertEqual(state["state"], "reviewing")
            self.assertTrue(state.get("review_path"))
            # finish review → rejected
            state = controller.step(state)
            self.assertEqual(state["state"], "review_rejected")
            # remediate → new campaign id
            state = controller.step(state)
            self.assertEqual(state["state"], "remediating")
            self.assertEqual(state["remediation_campaign_id"], "impl-main-fix-r1")
            self.assertEqual(state["round"], 1)
            # Idempotent: stepping remediation runs implementer again
            state = controller.step(state)
            self.assertEqual(state["state"], "qualified_by_checker")
            self.assertGreaterEqual(calls["implementer"], 2)


class OraclePromotionTests(unittest.TestCase):
    def test_unestablished_not_promoted(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            oracle = root / "oracle"
            oracle.mkdir()
            (root / "h.py").write_text("1\n", encoding="utf-8")
            (root / "c.py").write_text("1\n", encoding="utf-8")
            save_manifest(
                oracle / "verification-manifest.json",
                build_manifest(
                    members=[
                        member_from_file(root, "h.py", "trusted_helper"),
                        member_from_file(root, "c.py", "checker"),
                    ]
                ),
            )
            review = {
                "schema": "eu4-review/v1",
                "candidate_commit": "a" * 40,
                "oracle_manifest_sha256": "b" * 64,
                "outcome": "rejected",
                "findings": [
                    {
                        "id": "gap",
                        "severity": "P1",
                        "kind": "behavioral_counterexample",
                        "reproducer": "missing.json",
                        "expected_provenance": "unestablished",
                    }
                ],
            }
            result = promote_grounded_findings(
                repo=root,
                review=review,
                oracle_dir=oracle,
                trusted_helpers=["h.py"],
                checker_relpath="c.py",
            )
            self.assertIsNone(result)


if __name__ == "__main__":
    unittest.main()
