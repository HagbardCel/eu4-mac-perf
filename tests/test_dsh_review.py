"""Independent review: host-mediated channel, outcomes and the completion gate.

The reviewer is never a role switch: the bridge runs a separate process in a
detached worktree at the frozen candidate commit, parses its output, validates it
and writes the receipt itself. These tests drive that with a scripted reviewer so
the pipeline is exercised without spending a model call; a scripted receipt is
marked as such and can never authorize completion.
"""

import json
import shutil
import subprocess
import unittest
from pathlib import Path

from automation import dsh_bridge
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

    def test_the_real_event_shape_is_understood(self) -> None:
        """Shapes verified against the installed dsh-headless json projection."""
        payload = {"schema": "eu4-review/v1", "outcome": "rejected",
                   "candidate_commit": "a" * 40, "oracle_manifest_sha256": "b" * 64,
                   "findings": [{"id": "F1", "severity": "P1", "kind": "evidence_gap"}]}
        stream = "\n".join([
            json.dumps({"type": "session", "sessionId": "session-abc", "cwd": "/tmp"}),
            json.dumps({"type": "thinking", "text": "considering the evidence"}),
            json.dumps({"type": "final", "text": json.dumps(payload)}),
        ])
        self.assertEqual(extract_review_payload(stream)["outcome"], "rejected")
        self.assertEqual(session_id_from(stream), "session-abc")

    def test_the_final_event_wins_over_a_quoted_object(self) -> None:
        quoted = {"schema": "eu4-review/v1", "outcome": "qualified",
                  "candidate_commit": "a" * 40, "oracle_manifest_sha256": "b" * 64,
                  "findings": []}
        answer = dict(quoted, outcome="rejected")
        stream = "\n".join([
            json.dumps({"type": "session", "sessionId": "s"}),
            json.dumps({"type": "thinking",
                        "text": "the candidate claimed " + json.dumps(quoted)}),
            json.dumps({"type": "final", "text": json.dumps(answer)}),
        ])
        self.assertEqual(extract_review_payload(stream)["outcome"], "rejected")

    def test_absent_payload_is_none(self) -> None:
        self.assertIsNone(extract_review_payload('{"type":"message","text":"hello"}'))
        self.assertIsNone(session_id_from('{"type":"message","text":"hello"}'))


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
        (self.worktree / "candidate.txt").write_text("candidate\n", encoding="utf-8")

    def _drop_worktree(self) -> None:
        for path in (self.worktree, Path(self.run_root) / "review" / "worktree"):
            if path.exists():
                subprocess.run(
                    ["git", "-C", str(REPO), "worktree", "remove", "--force",
                     "--force", str(path)],
                    capture_output=True,
                )
                shutil.rmtree(path, ignore_errors=True)

    def review_sidecar(self, *, review_required: bool = True) -> Path:
        sidecar = self.sidecar(self.campaign("pass-campaign"))
        data = json.loads(sidecar.read_text(encoding="utf-8"))
        data["candidate"] = {
            "mode": "worktree",
            "workspace": str(self.worktree),
            "policy": {"allowed_paths": ["candidate.txt"]},
        }
        data["candidate_baseline_commit"] = self.base
        data["evidence"] = {"paths": ["README.md"]}
        data["goal"] = {"max_rounds": 4, "require_checker_pass": True,
                        "require_independent_review": review_required}
        sidecar.write_text(json.dumps(data), encoding="utf-8")
        return sidecar

    def reviewer(self, *, outcome: str, findings=None,
                 session: str | None = "rev-1") -> str:
        """A scripted reviewer that answers for whatever candidate it is given.

        It reads the commit and oracle out of its task text, so the test does not
        need to know them before the bridge has frozen the candidate.
        """
        template = self.root / "payload.json"
        template.write_text(json.dumps({
            "outcome": outcome,
            "findings": findings or [],
            "session": session,
        }), encoding="utf-8")
        script = self.root / "reviewer.py"
        script.write_text(
            "#!/usr/bin/env python3\n"
            "import json, re, sys\n"
            "task = sys.argv[1]\n"
            "spec = json.load(open(%r))\n" % str(template)
            + "commit = re.search(r'Candidate commit: ([0-9a-f]{40})', task).group(1)\n"
            + "oracle = re.search(r'Oracle manifest digest: ([0-9a-f]{64})', task).group(1)\n"
            + "payload = {'schema': 'eu4-review/v1', 'candidate_commit': commit,\n"
            + "           'oracle_manifest_sha256': oracle, 'outcome': spec['outcome'],\n"
            + "           'findings': spec['findings']}\n"
            + "lines = []\n"
            + "if spec['session']:\n"
            + "    lines.append({'type': 'session', 'sessionId': spec['session']})\n"
            + "lines.append({'type': 'final', 'text': json.dumps(payload)})\n"
            + "for line in lines:\n"
            + "    print(json.dumps(line))\n"
        )
        script.chmod(0o755)
        return str(script)


class ReviewPipelineTests(ReviewCase):
    def test_a_seeded_defect_is_rejected_then_a_clean_candidate_qualifies(self) -> None:
        sidecar = self.review_sidecar()
        self.assertEqual(self.run_bridge("prepare", sidecar)[0], dsh_bridge.EXIT_OK)
        code, validated = self.run_bridge("validate", sidecar)
        self.assertEqual(code, dsh_bridge.EXIT_OK, validated)
        self.assertEqual(validated["checker_verdict"], "pass")

        script = self.reviewer(
            outcome="rejected",
            findings=[{"id": "F1", "severity": "P1", "kind": "evidence_gap"}],
        )
        code, run = self.run_bridge("review-run", sidecar, reviewer_cmd=script)
        self.assertEqual(code, dsh_bridge.EXIT_OK, run)
        self.assertEqual(run["outcome"], "rejected")
        self.assertFalse(run["qualified"])
        self.assertEqual(run["qualification_mode"], "scripted")

        code, verify = self.run_bridge("review-verify", sidecar, allow_scripted=True)
        self.assertEqual(code, dsh_bridge.EXIT_INTEGRITY)
        self.assertTrue(any("rejected" in problem for problem in verify["problems"]))

        code, finalize = self.run_bridge("finalize", sidecar)
        self.assertEqual(code, dsh_bridge.EXIT_INTEGRITY)
        self.assertEqual(finalize["kind"], "review_required")

        # Round 2: the implementer repairs, then a reviewer qualifies it.
        (self.worktree / "candidate.txt").write_text("corrected\n", encoding="utf-8")
        self.assertEqual(self.run_bridge("validate", sidecar)[1]["checker_verdict"], "pass")
        script = self.reviewer(outcome="qualified")
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

    def test_review_leaves_no_worktree_behind(self) -> None:
        sidecar = self.review_sidecar()
        self.run_bridge("prepare", sidecar)
        self.run_bridge("validate", sidecar)
        code, run = self.run_bridge("review-run", sidecar,
                                    reviewer_cmd=self.reviewer(outcome="qualified"))
        self.assertEqual(code, dsh_bridge.EXIT_OK, run)
        self.assertFalse((Path(self.run_root) / "review" / "worktree").exists(),
                         "the reviewer checkout must not outlive the review")
        # The evidence itself survives.
        self.assertTrue(Path(run["report"]).is_file())
        self.assertTrue(Path(run["receipt"]).is_file())

    def test_review_is_refused_without_a_passing_validation(self) -> None:
        sidecar = self.review_sidecar()
        self.run_bridge("prepare", sidecar)
        code, out = self.run_bridge("review-run", sidecar,
                                    reviewer_cmd=self.reviewer(outcome="qualified"))
        self.assertEqual(code, dsh_bridge.EXIT_INTEGRITY)
        self.assertEqual(out["kind"], "not_qualified")

    def test_a_seed_candidate_cannot_be_reviewed(self) -> None:
        sidecar = self.sidecar(self.campaign("pass-campaign"))
        data = json.loads(sidecar.read_text(encoding="utf-8"))
        data["goal"] = {"max_rounds": 4, "require_checker_pass": True,
                        "require_independent_review": True}
        data["evidence"] = {"paths": ["marker.txt"]}
        sidecar.write_text(json.dumps(data), encoding="utf-8")
        self.run_bridge("prepare", sidecar)
        self.run_bridge("validate", sidecar)
        code, out = self.run_bridge("review-run", sidecar,
                                    reviewer_cmd=self.reviewer(outcome="qualified"))
        self.assertEqual(code, dsh_bridge.EXIT_INTEGRITY, out)
        self.assertEqual(out["kind"], "review_needs_commit")

    def test_a_campaign_without_an_evidence_identity_cannot_be_reviewed(self) -> None:
        sidecar = self.review_sidecar()
        data = json.loads(sidecar.read_text(encoding="utf-8"))
        data.pop("evidence", None)
        sidecar.write_text(json.dumps(data), encoding="utf-8")
        self.run_bridge("prepare", sidecar)
        self.run_bridge("validate", sidecar)
        code, out = self.run_bridge("review-run", sidecar,
                                    reviewer_cmd=self.reviewer(outcome="qualified"))
        self.assertEqual(code, dsh_bridge.EXIT_INTEGRITY, out)
        self.assertEqual(out["kind"], "review_identity_missing")

    def test_a_live_review_without_a_provider_key_is_refused(self) -> None:
        """The one-shot profile cannot read the stored credential."""
        import os
        from unittest import mock

        sidecar = self.review_sidecar()
        self.run_bridge("prepare", sidecar)
        self.run_bridge("validate", sidecar)
        with mock.patch.dict(os.environ, {"DSH_HOME": os.environ.get("DSH_HOME", "/tmp")},
                             clear=True):
            code, out = self.run_bridge("review-run", sidecar)
        self.assertEqual(code, dsh_bridge.EXIT_INTEGRITY, out)
        self.assertEqual(out["kind"], "review_environment")
        self.assertIn("API key", out["detail"])

    def test_a_reviewer_without_a_session_identity_is_refused(self) -> None:
        sidecar = self.review_sidecar()
        self.run_bridge("prepare", sidecar)
        self.run_bridge("validate", sidecar)
        code, out = self.run_bridge(
            "review-run", sidecar,
            reviewer_cmd=self.reviewer(outcome="qualified", session=None),
        )
        self.assertEqual(code, dsh_bridge.EXIT_INTEGRITY)
        self.assertEqual(out["kind"], "review_identity")

    def test_a_reviewer_that_never_answers_is_an_infrastructure_error(self) -> None:
        sidecar = self.review_sidecar()
        self.run_bridge("prepare", sidecar)
        self.run_bridge("validate", sidecar)
        slow = self.root / "slow.py"
        slow.write_text("#!/usr/bin/env python3\nimport time\ntime.sleep(5)\n",
                        encoding="utf-8")
        slow.chmod(0o755)
        code, out = self.run_bridge("review-run", sidecar,
                                    reviewer_cmd=str(slow), timeout=1)
        self.assertEqual(code, dsh_bridge.EXIT_OK, out)
        self.assertEqual(out["classification"], "infrastructure_error")
        self.assertEqual(out["kind"], "review_failed")
        # Unavailable infrastructure asks for the canonical pause policy.
        self.assertEqual(out["goal_policy"], dsh_bridge.GOAL_POLICY["unavailable"])
        self.assertEqual(out["goal_policy"], "pause")

    def test_unparseable_reviewer_output_is_refused(self) -> None:
        sidecar = self.review_sidecar()
        self.run_bridge("prepare", sidecar)
        self.run_bridge("validate", sidecar)
        silent = self.root / "silent.py"
        silent.write_text("#!/usr/bin/env python3\nprint('no review here')\n",
                          encoding="utf-8")
        silent.chmod(0o755)
        code, out = self.run_bridge("review-run", sidecar, reviewer_cmd=str(silent))
        self.assertEqual(code, dsh_bridge.EXIT_INTEGRITY)
        self.assertEqual(out["kind"], "review_unparseable")


if __name__ == "__main__":
    unittest.main()


class ReviewerEnvironmentTests(unittest.TestCase):
    def test_the_reviewer_inherits_only_what_it_needs(self) -> None:
        from automation import dsh_review as review

        env = review.reviewer_env({
            "PATH": "/bin",
            "HOME": "/home/x",
            "DSH_HOME": "/dedicated",
            "OPENCODE_GO_API_KEY": "k",
            "AWS_SECRET_ACCESS_KEY": "no",
            "SOMETHING_ELSE": "no",
        })
        self.assertEqual(env["DSH_HOME"], "/dedicated")
        self.assertEqual(env["OPENCODE_GO_API_KEY"], "k")
        self.assertNotIn("AWS_SECRET_ACCESS_KEY", env)
        self.assertNotIn("SOMETHING_ELSE", env)
        # Read-only is enforcement, not an instruction in the task text.
        self.assertEqual(env["DSH_PERMISSION_MODE"], "read-only")


class ReviewerWorktreeTests(ReviewCase):
    def test_a_reviewer_that_modifies_its_worktree_is_refused(self) -> None:
        sidecar = self.review_sidecar()
        self.run_bridge("prepare", sidecar)
        self.run_bridge("validate", sidecar)

        script = self.root / "dirty_reviewer.py"
        script.write_text(
            "#!/usr/bin/env python3\n"
            "import json, pathlib, re, sys\n"
            "task = sys.argv[1]\n"
            "commit = re.search(r'Candidate commit: ([0-9a-f]{40})', task).group(1)\n"
            "oracle = re.search(r'Oracle manifest digest: ([0-9a-f]{64})', task).group(1)\n"
            "pathlib.Path('reviewer-was-here.txt').write_text('x')\n"
            "payload = {'schema': 'eu4-review/v1', 'candidate_commit': commit,\n"
            "           'oracle_manifest_sha256': oracle, 'outcome': 'qualified',\n"
            "           'findings': []}\n"
            "print(json.dumps({'type': 'session', 'sessionId': 'rev-dirty'}))\n"
            "print(json.dumps({'type': 'final', 'text': json.dumps(payload)}))\n",
            encoding="utf-8",
        )
        script.chmod(0o755)
        code, out = self.run_bridge("review-run", sidecar, reviewer_cmd=str(script))
        self.assertEqual(code, dsh_bridge.EXIT_INTEGRITY, out)
        self.assertEqual(out["kind"], "review_worktree_modified")
        self.assertFalse((Path(self.run_root) / "review" / "worktree").exists())
