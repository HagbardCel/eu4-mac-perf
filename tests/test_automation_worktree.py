"""Worktree selection stays outside the seed copy, and seed mode still copies once."""

from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from automation.runner import Runner
from automation.state import git_common_dir, load_campaign, require_worktree
from automation_fake import FakeAgent
from test_automation_runner import Clock, write_campaign

SMOKE = Path(__file__).resolve().parents[1] / "campaigns" / "automation-smoke" / "accept.py"


def git(path: Path, *args: str) -> str:
    completed = subprocess.run(
        ["git", "-C", str(path), "-c", "user.email=t@example.com", "-c", "user.name=Test", *args],
        check=True, capture_output=True, text=True,
    )
    return completed.stdout.strip()


def init_repo(path: Path) -> str:
    path.mkdir()
    git(path, "init", "-q")
    (path / "README").write_text("seed\n", encoding="utf-8")
    git(path, "add", "README")
    git(path, "commit", "-q", "-m", "init")
    return git(path, "rev-parse", "HEAD")


class WorktreeTests(unittest.TestCase):
    def test_linked_worktree_runs_and_keeps_a_dirty_file(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            repo = root / "repo"
            commit = init_repo(repo)
            worktree = root / "wt"
            git(repo, "worktree", "add", "--detach", str(worktree), "HEAD")
            require_worktree(worktree, repo, commit)
            self.assertEqual(git_common_dir(worktree), git_common_dir(repo))
            campaign = self._campaign(repo, "../wt", commit)
            (worktree / "kept.txt").write_text("stay\n", encoding="utf-8")
            agent = FakeAgent()
            agent.on_submit = self._ready
            runner = self._runner(repo, campaign, agent)
            state = runner.execute(resume_only=False)
            self.assertEqual(state["status"], "complete")
            self.assertEqual((worktree / "kept.txt").read_text(encoding="utf-8"), "stay\n")
            self.assertFalse((repo / "run" / "workspace").exists())
            (worktree / "kept.txt").write_text("edited\n", encoding="utf-8")
            runner.execute(resume_only=False)
            self.assertEqual((worktree / "kept.txt").read_text(encoding="utf-8"), "edited\n")
            state = json.loads((repo / "run" / "state.json").read_text(encoding="utf-8"))
            state["workspace"] = str(root / "other")
            (repo / "run" / "state.json").write_text(json.dumps(state), encoding="utf-8")
            with self.assertRaises(RuntimeError):
                runner.execute(resume_only=False)

    def test_primary_checkout_and_other_repository_are_refused(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            repo = root / "repo"
            commit = init_repo(repo)
            with self.assertRaises(RuntimeError):
                require_worktree(repo, repo, commit)
            other = root / "other"
            init_repo(other)
            foreign = root / "foreign"
            git(other, "worktree", "add", "--detach", str(foreign), "HEAD")
            with self.assertRaises(RuntimeError):
                require_worktree(foreign, repo, commit)

    def test_seed_copy_does_not_replace_an_existing_workspace(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            path = write_campaign(root, max_rounds=1)
            clock = Clock()
            runner = Runner(
                load_campaign(path), FakeAgent(), root / "run",
                clock=clock.monotonic, sleep=clock.sleep,
            )
            runner.execute(resume_only=False)
            marker = root / "run" / "workspace" / "marker.txt"
            self.assertEqual(marker.read_text(encoding="utf-8"), "todo\n")
            marker.write_text("kept\n", encoding="utf-8")
            runner.execute(resume_only=False)
            self.assertEqual(marker.read_text(encoding="utf-8"), "kept\n")

    def _campaign(self, repo: Path, relative: str, commit: str) -> Path:
        (repo / "objective.md").write_text("Make marker.txt contain exactly ready.\n", encoding="utf-8")
        path = repo / "campaign.json"
        path.write_text(json.dumps({
            "id": "worktree-test",
            "objective": "objective.md",
            "runtime": "opencode",
            "model": "fake/model",
            "opencode_version": "2.2.5",
            "workspace": {"mode": "worktree", "path": relative, "commit": commit},
            "accept": ["python3", str(SMOKE)],
            "max_rounds": 2,
            "max_seconds": 100,
            "accept_timeout_seconds": 5,
        }), encoding="utf-8")
        return path

    def _runner(self, repo: Path, campaign: Path, agent: FakeAgent) -> Runner:
        clock = Clock()
        return Runner(
            load_campaign(campaign), agent, repo / "run",
            clock=clock.monotonic, sleep=clock.sleep,
        )

    @staticmethod
    def _ready(workspace: Path) -> None:
        (workspace / "marker.txt").write_text("ready\n", encoding="utf-8")
        (workspace / "progress.md").write_text("# Progress\n\n## Next\ndone\n", encoding="utf-8")


if __name__ == "__main__":
    unittest.main()
