"""Independent review: host-mediated channel, outcomes and the completion gate.

The reviewer is never a role switch: the host runs a separate process in a
detached worktree at the frozen candidate commit, parses its output, validates
it and writes the receipt itself. These tests drive that with a scripted
reviewer process so the pipeline is exercised without spending a model call;
a scripted receipt is marked as such and can never authorize completion.
"""

import json
import shutil
import subprocess
import unittest
from pathlib import Path

from automation import dsh_bridge
from automation import dsh_receipts as receipts
from automation.dsh_review import extract_review_payload, session_id_from
from tests.test_dsh_bridge import BridgeTestCase, REPO


class ExtractionTests(unittest.TestCase):
    def test_payload_is_recovered_from_nested_event_text(self) -> None:
        payload = {"schema": "eu4-review/v1", "outcome": "qualified",
                   "candidate_commit": "a" * 40, "oracle_manifest_sha256": "b" * 64,
                   "findings": []}
        stream = "\n".join([
            json.dumps({"type": "session", "sessionId": "s-1"}),
            json.dumps({"type": "message", "text": json.dumps(payload)}),
        ])
        self.assertEqual(extract_review_payload(stream)["outcome"], "qualified")
        self.assertEqual(session_id_from(stream), "s-1")

    def test_absent_payload_is_none(self) -> None:
        self.assertIsNone(extract_review_payload('{"type":"message","text":"hello"}'))
        self.assertIsNone(session_id_from('{"type":"message","text":"hello"}'))

    def test_session_id_survives_a_non_json_line(self) -> None:
        self.assertEqual(
            session_id_from('noise\n{"session_id": "abc-2", "type": "x"}'), "abc-2"
        )


class ReviewCase(BridgeTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.base = subprocess.run(
            ["git", "-C", str(REPO), "rev-parse", "HEAD"],
            capture_output=True, text=True, check=True,
        ).stdout.strip()
        self.worktree = self.root / "wt"
        subprocess.run(
            ["git", "-C", str(REPO), "worktree", "add", "--detach",
             str(self.worktree), self.base],
            capture_output=True, check=True,
        )
        self.addCleanup(self._drop_worktree)
        # The bridge creates its own reviewer worktree under the run root; it is
        # a real git worktree, so the test must deregister it too.
        self.addCleanup(self._drop_review_worktree)
        (self.worktree / "candidate.txt").write_text("candidate\n", encoding="utf-8")

    def _drop_review_worktree(self) -> None:
        worktree = Path(self.run_root) / "review" / "worktree"
        if worktree.exists():
            subprocess.run(
                ["git", "-C", str(REPO), "worktree", "remove", "--force",
                 "--force", str(worktree)],
                capture_output=True,
            )
            shutil.rmtree(worktree, ignore_errors=True)

    def _drop_worktree(self) -> None:
        subprocess.run(
            ["git", "-C", str(REPO), "worktree", "remove", "--force",
             "--force", str(self.worktree)],
            capture_output=True,
        )
        shutil.rmtree(self.worktree, ignore_errors=True)

    def review_sidecar(self, *, review_required: bool) -> Path:
        sidecar = self.sidecar(self.campaign("pass-campaign"))
        data = json.loads(sidecar.read_text(encoding="utf-8"))
        data["candidate"] = {
            "mode": "worktree",
            "workspace": str(self.worktree),
            "policy": {"allowed_paths": ["candidate.txt"]},
        }
        data["candidate_baseline_commit"] = self.base
        data["goal"] = {"max_rounds": 4, "require_checker_pass": True,
                        "require_independent_review": review_required}
        sidecar.write_text(json.dumps(data), encoding="utf-8")
        return sidecar

    def reviewer(self, *, outcome: str, findings=None, session: str | None = "rev-1",
                 commit: str, oracle: str) -> str:
        payload = {
            "schema": "eu4-review/v1",
            "candidate_commit": commit,
            "oracle_manifest_sha256": oracle,
            "outcome": outcome,
            "findings": findings or [],
        }
        lines = []
        if session:
            lines.append({"type": "session", "sessionId": session})
        lines.append({"type": "message", "text": json.dumps(payload)})
        stream = self.root / "payload.ndjson"
        stream.write_text("\n".join(json.dumps(line) for line in lines) + "\n",
                          encoding="utf-8")
        script = self.root / "reviewer.sh"
        script.write_text(f'#!/bin/sh\ncat "{stream}"\n', encoding="utf-8")
        script.chmod(0o755)
        return str(script)


class ReviewPipelineTests(ReviewCase):
    def test_a_seeded_defect_is_rejected_then_a_clean_candidate_qualifies(self) -> None:
        sidecar = self.review_sidecar(review_required=True)
        self.assertEqual(self.run_bridge("prepare", sidecar)[0], dsh_bridge.EXIT_OK)
        code, validated = self.run_bridge("validate", sidecar)
        self.assertEqual(code, dsh_bridge.EXIT_OK, validated)
        self.assertEqual(validated["checker_verdict"], "pass")

        code, preflight = self.run_bridge("review-preflight", sidecar)
        self.assertEqual(code, dsh_bridge.EXIT_OK, preflight)
        commit = preflight["frozen"]["commit"]
        oracle = preflight["oracle_digest"]
        self.assertEqual(len(commit), 40)

        # Round 1: the reviewer rejects a seeded defect.
        script = self.reviewer(
            outcome="rejected",
            findings=[{"id": "F1", "severity": "P1", "kind": "evidence_gap"}],
            commit=commit, oracle=oracle,
        )
        code, run = self.run_bridge("review-run", sidecar, reviewer_cmd=script)
        self.assertEqual(code, dsh_bridge.EXIT_OK, run)
        self.assertEqual(run["outcome"], "rejected")
        self.assertFalse(run["qualified"])
        self.assertEqual(run["qualification_mode"], "scripted")

        code, verify = self.run_bridge("review-verify", sidecar, allow_scripted=True)
        self.assertEqual(code, dsh_bridge.EXIT_INTEGRITY)
        self.assertFalse(verify["qualified"])
        self.assertTrue(any("rejected" in problem for problem in verify["problems"]))

        # The completion gate refuses: a rejected candidate cannot complete.
        code, finalize = self.run_bridge("finalize", sidecar)
        self.assertEqual(code, dsh_bridge.EXIT_INTEGRITY)
        self.assertEqual(finalize["kind"], "review_required")

        # Round 2: remediation, then a reviewer qualifies the corrected candidate.
        (self.worktree / "candidate.txt").write_text("corrected\n", encoding="utf-8")
        (Path(self.run_root) / "review").mkdir(parents=True, exist_ok=True)
        review_dir = Path(self.run_root) / "review"
        for name in ("review.json", "review.json.receipt.json"):
            target = review_dir / name
            if target.exists():
                target.unlink()
        code, validated = self.run_bridge("validate", sidecar)
        self.assertEqual(validated["checker_verdict"], "pass")
        code, preflight = self.run_bridge("review-preflight", sidecar)
        self.assertEqual(code, dsh_bridge.EXIT_OK, preflight)
        commit = preflight["frozen"]["commit"]
        script = self.reviewer(outcome="qualified", commit=commit,
                               oracle=preflight["oracle_digest"])
        code, run = self.run_bridge("review-run", sidecar, reviewer_cmd=script)
        self.assertEqual(code, dsh_bridge.EXIT_OK, run)
        self.assertTrue(run["qualified"])

        code, verify = self.run_bridge("review-verify", sidecar, allow_scripted=True)
        self.assertEqual(code, dsh_bridge.EXIT_OK, verify)
        self.assertTrue(verify["qualified"])

        # A scripted receipt still cannot authorize completion.
        code, finalize = self.run_bridge("finalize", sidecar)
        self.assertEqual(code, dsh_bridge.EXIT_INTEGRITY)
        self.assertEqual(finalize["kind"], "review_required")
        self.assertTrue(any("scripted" in p for p in [finalize.get("detail", "")]))

    def test_review_is_refused_without_a_passing_validation(self) -> None:
        sidecar = self.review_sidecar(review_required=True)
        self.run_bridge("prepare", sidecar)
        code, out = self.run_bridge("review-preflight", sidecar)
        self.assertEqual(code, dsh_bridge.EXIT_INTEGRITY)
        self.assertEqual(out["kind"], "not_qualified")

    def test_a_reviewer_without_a_session_identity_is_refused(self) -> None:
        sidecar = self.review_sidecar(review_required=True)
        self.run_bridge("prepare", sidecar)
        self.run_bridge("validate", sidecar)
        _, preflight = self.run_bridge("review-preflight", sidecar)
        script = self.reviewer(outcome="qualified", session=None,
                               commit=preflight["frozen"]["commit"],
                               oracle=preflight["oracle_digest"])
        code, out = self.run_bridge("review-run", sidecar, reviewer_cmd=script)
        self.assertEqual(code, dsh_bridge.EXIT_INTEGRITY)
        self.assertEqual(out["kind"], "review_identity")
        self.assertFalse(
            (Path(self.run_root) / "review" / "review.json.receipt.json").exists()
        )

    def test_review_clean_removes_the_reviewer_worktree_only(self) -> None:
        sidecar = self.review_sidecar(review_required=True)
        self.run_bridge("prepare", sidecar)
        self.run_bridge("validate", sidecar)
        _, preflight = self.run_bridge("review-preflight", sidecar)
        worktree = Path(preflight["worktree"])
        self.assertTrue(worktree.exists())
        code, out = self.run_bridge("review-clean", sidecar)
        self.assertEqual(code, dsh_bridge.EXIT_OK, out)
        self.assertTrue(out["removed"])
        self.assertFalse(worktree.exists())
        # The report and receipt live outside the worktree and survive.
        self.assertTrue((Path(self.run_root) / "review").is_dir())
        code, again = self.run_bridge("review-clean", sidecar)
        self.assertEqual(code, dsh_bridge.EXIT_OK)
        self.assertFalse(again["removed"])

    def test_unparseable_reviewer_output_is_refused(self) -> None:
        sidecar = self.review_sidecar(review_required=True)
        self.run_bridge("prepare", sidecar)
        self.run_bridge("validate", sidecar)
        self.run_bridge("review-preflight", sidecar)
        script = self.root / "silent.sh"
        script.write_text("#!/bin/sh\necho 'no review here'\n", encoding="utf-8")
        script.chmod(0o755)
        code, out = self.run_bridge("review-run", sidecar, reviewer_cmd=str(script))
        self.assertEqual(code, dsh_bridge.EXIT_INTEGRITY)
        self.assertEqual(out["kind"], "review_unparseable")


if __name__ == "__main__":
    unittest.main()
