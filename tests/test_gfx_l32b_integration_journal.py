"""Fault-injection tests for staging and publication identity."""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from automation.gfx_l32b_hooks import L32bHooks  # noqa: E402


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True, text=True, check=True,
    ).stdout.strip()


def _repo(tmp: Path) -> tuple[Path, str, str]:
    subprocess.run(["git", "init", "-q", str(tmp)], check=True)
    _git(tmp, "config", "user.email", "a@b")
    _git(tmp, "config", "user.name", "A")
    (tmp / "a.txt").write_text("a\n")
    _git(tmp, "add", "a.txt")
    _git(tmp, "commit", "-m", "base")
    base = _git(tmp, "rev-parse", "HEAD")
    (tmp / "b.txt").write_text("b\n")
    _git(tmp, "add", "b.txt")
    _git(tmp, "commit", "-m", "candidate")
    candidate = _git(tmp, "rev-parse", "HEAD")
    _git(tmp, "branch", "integration", base)
    return tmp, base, candidate


def _hooks(repo: Path) -> L32bHooks:
    return L32bHooks(
        repo=repo,
        workflow={"workflow_id": "wf", "integration_branch": "integration"},
        run_root=repo / "run",
    )


class StagingIdentityTests(unittest.TestCase):
    def test_staged_commit_is_stable_after_interruption(self):
        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            repo, base, candidate = _repo(tmp)
            hooks = _hooks(repo)
            kwargs = dict(
                base=base,
                candidate_commit=candidate,
                candidate_parent=base,
                contract_commit=None,
                contract_parent=None,
                integration_id="i1",
                staged_at="2026-01-01T00:00:00+00:00",
            )
            first = hooks._staged_commit(**kwargs)
            _git(repo, "update-ref", "-d", "refs/gfx-staging/i1")
            second = hooks._staged_commit(**kwargs)
            self.assertEqual(first, second)
            self.assertEqual(_git(repo, "rev-parse", "refs/gfx-staging/i1"), first)


class PublicationIdentityTests(unittest.TestCase):
    def test_publication_is_idempotent_after_ref_update(self):
        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            repo, base, staged = _repo(tmp)
            hooks = _hooks(repo)
            journal_path = hooks.run_root / "integrations" / "i1.json"
            journal_path.parent.mkdir(parents=True, exist_ok=True)
            journal_path.write_text(json.dumps({
                "schema": "eu4-l32b-integration-journal/v1",
                "integration_id": "i1",
                "base_head": base,
                "new_commit": staged,
                "status": "verified_staged",
                "verification": {
                    "commit": staged,
                    "candidate_digest": "9" * 64,
                },
            }))
            review = tmp / "review.json"
            review.write_text(json.dumps({
                "candidate_commit": staged,
                "outcome": "qualified",
            }))
            qualification = tmp / "qualification.json"
            qualification.write_text(json.dumps({
                "trusted_contract_commit": staged,
                "candidate_digest": "9" * 64,
            }))
            first = hooks.publish(
                "i1", staged, base,
                review_receipt=str(review),
                qualification_receipt=str(qualification),
            )
            self.assertTrue(first["published"])
            self.assertEqual(_git(repo, "rev-parse", "integration"), staged)
            second = hooks.publish(
                "i1", staged, base,
                review_receipt=str(review),
                qualification_receipt=str(qualification),
            )
            self.assertTrue(second["published"])
            self.assertEqual(_git(repo, "rev-parse", "integration"), staged)

    def test_publication_rejects_a_review_bound_to_another_commit(self):
        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            repo, base, staged = _repo(tmp)
            hooks = _hooks(repo)
            journal_path = hooks.run_root / "integrations" / "i1.json"
            journal_path.parent.mkdir(parents=True, exist_ok=True)
            journal_path.write_text(json.dumps({
                "schema": "eu4-l32b-integration-journal/v1",
                "integration_id": "i1",
                "base_head": base,
                "new_commit": staged,
                "status": "verified_staged",
                "verification": {
                    "commit": staged,
                    "candidate_digest": "9" * 64,
                },
            }))
            review = tmp / "review.json"
            review.write_text(json.dumps({
                "candidate_commit": "0" * 40,
                "outcome": "qualified",
            }))
            qualification = tmp / "qualification.json"
            qualification.write_text(json.dumps({
                "trusted_contract_commit": staged,
                "candidate_digest": "9" * 64,
            }))
            with self.assertRaises(RuntimeError):
                hooks.publish(
                    "i1", staged, base,
                    review_receipt=str(review),
                    qualification_receipt=str(qualification),
                )
            self.assertEqual(_git(repo, "rev-parse", "integration"), base)


if __name__ == "__main__":
    unittest.main()
