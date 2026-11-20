"""Offline continuation, restart, replacement, and budget tests."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from automation.agents import ContractError
from automation.runner import Runner, candidate_digest, skill_trust
from automation.state import file_sha256, load_campaign, load_state, save_state
from automation_fake import FakeAgent

REPO = Path(__file__).resolve().parents[1]
SMOKE_ACCEPT = REPO / "campaigns" / "automation-smoke" / "accept.py"


class Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def monotonic(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += seconds


def write_campaign(root: Path, **overrides) -> Path:
    seed = root / "seed"
    seed.mkdir()
    (seed / "marker.txt").write_text("todo\n", encoding="utf-8")
    (seed / "progress.md").write_text("# Progress\n\n## Next\nwait\n", encoding="utf-8")
    (root / "objective.md").write_text(
        "Make marker.txt contain exactly ready.\n", encoding="utf-8")
    checker = Path(overrides.get("checker", SMOKE_ACCEPT))
    data = {
        "id": "test-campaign",
        "objective": "objective.md",
        "runtime": "opencode",
        "model": "fake/model",
        "opencode_version": "2.2.5",
        "workspace": "seed",
        "accept": ["python3", str(checker)],
        "max_rounds": overrides.get("max_rounds", 4),
        "max_seconds": overrides.get("max_seconds", 100),
        "accept_timeout_seconds": 5,
    }
    path = root / "campaign.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def _ready(workspace: Path) -> None:
    (workspace / "marker.txt").write_text("ready\n", encoding="utf-8")


class RunnerTests(unittest.TestCase):
    def _runner(self, root: Path, agent: FakeAgent, **overrides):
        campaign = load_campaign(write_campaign(root, **overrides))
        clock = Clock()
        runner = Runner(
            campaign, agent, root / "run", clock=clock.monotonic, sleep=clock.sleep)
        return runner, clock

    def test_continues_until_the_second_check_passes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            agent = FakeAgent()
            agent.on_submit = _ready
            runner, _clock = self._runner(root, agent, max_rounds=4)
            state = runner.execute(resume_only=False)
            self.assertEqual(state["status"], "complete")
            self.assertEqual(state["rounds"], 2)
            self.assertEqual(state["last_verdict"], "pass")
            self.assertEqual(len(agent.submits), 2)
            log = (root / "run" / "events.jsonl").read_text(encoding="utf-8")
            self.assertIn('"type": "validation"', log)
            self.assertIn('"verdict": "fail"', log)
            self.assertIn('"verdict": "pass"', log)

    def test_restart_keeps_the_submission_id_and_workspace(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            agent = FakeAgent()
            agent.interrupt = True
            runner, _clock = self._runner(root, agent, max_rounds=1)
            with self.assertRaises(KeyboardInterrupt):
                runner.execute(resume_only=False)
            saved = load_state(root / "run" / "state.json")
            marker = root / "run" / "workspace" / "marker.txt"
            marker.write_text("kept\n", encoding="utf-8")
            state = runner.execute(resume_only=True)
            self.assertEqual(marker.read_text(encoding="utf-8"), "kept\n")
            self.assertEqual(state["session_id"], "ses_fake_1")
            self.assertEqual(agent.submits, [saved["pending_submission_id"]])
            self.assertEqual(state["pause_reason"], "round_limit")

    def test_same_id_is_reconciled_instead_of_replaced(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            agent = FakeAgent()
            agent.hide_once = True
            agent.on_submit = _ready
            runner, _clock = self._runner(root, agent, max_rounds=2)
            state = runner.execute(resume_only=False)
            self.assertEqual(agent.conflicts, 1)
            self.assertEqual(len(agent.submits), 2)
            self.assertEqual(state["status"], "complete")
            self.assertEqual(len(set(agent.submits)), 2)

    def test_missing_session_is_replaced_without_discarding_files(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            agent = FakeAgent()
            runner, _clock = self._runner(root, agent, max_rounds=1)
            campaign = runner.campaign
            live = root / "run" / "workspace"
            live.mkdir(parents=True)
            (live / "keep.txt").write_text("keep\n", encoding="utf-8")
            (live / "progress.md").write_text("# Progress\n\n## Next\nwait\n", encoding="utf-8")
            save_state(runner.state_path, {
                "campaign_id": campaign.id,
                "checker_sha256": file_sha256(campaign.checker),
                "elapsed_seconds": 0.0,
                "last_diagnostics": None,
                "last_observation": None,
                "last_verdict": None,
                "model": None,
                "pause_reason": None,
                "pending_submission_id": None,
                "pending_text": None,
                "rounds": 0,
                "runtime": "opencode",
                "session_id": "ses_old",
                "started_at": "2026-11-03T00:00:00+00:00",
                "status": "paused",
                "validated_submission_id": None,
                "workspace": str(live),
            })
            state = runner.execute(resume_only=True)
            self.assertEqual((live / "keep.txt").read_text(encoding="utf-8"), "keep\n")
            self.assertEqual(state["session_id"], "ses_fake_1")
            self.assertIn("progress.md", agent.prompts[0])
            self.assertIn("ready", agent.prompts[0])

    def test_replacement_after_a_failed_check_submits_the_objective(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            agent = FakeAgent()

            def ready_then_drop(workspace: Path) -> None:
                _ready(workspace)
                agent.drop_on_inspect = True

            agent.on_submit = ready_then_drop
            runner, _clock = self._runner(root, agent, max_rounds=2)
            state = runner.execute(resume_only=False)
            self.assertEqual(state["status"], "complete")
            self.assertEqual(len(agent.messages), 2)
            session_id, submission_id, message = agent.messages[1]
            self.assertNotEqual(session_id, agent.messages[0][0])
            self.assertNotEqual(submission_id, agent.dropped_submission_id)
            self.assertIn("Make marker.txt", message)
            self.assertIn("external check", message)
            self.assertIn("progress.md", message)
            self.assertIn("Confirmation round required", message)

    def test_session_loss_before_the_first_check_gets_a_new_id(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            agent = FakeAgent()
            agent.phase_after_submit = "executing"

            def drop_after_the_first_submit(_workspace: Path) -> None:
                agent.drop_on_inspect = True

            agent.on_submit = drop_after_the_first_submit
            runner, _clock = self._runner(root, agent, max_rounds=1)
            state = runner.execute(resume_only=False)
            self.assertEqual(len(agent.messages), 2)
            first_session, first_id, _first = agent.messages[0]
            session_id, submission_id, message = agent.messages[1]
            self.assertNotEqual(session_id, first_session)
            self.assertEqual(agent.dropped_submission_id, first_id)
            self.assertNotEqual(submission_id, first_id)
            self.assertIn("Make marker.txt", message)
            self.assertIn("external check", message)
            self.assertIn("progress.md", message)
            self.assertEqual(state["rounds"], 1)
            self.assertEqual(state["pause_reason"], "round_limit")

    def test_deleted_checker_pauses(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            checker = root / "accept.py"
            checker.write_text(
                "import json\nprint(json.dumps({'verdict':'pass','diagnostics':'ok'}))\n",
                encoding="utf-8",
            )
            agent = FakeAgent()

            def remove_checker(_workspace: Path) -> None:
                checker.unlink()

            agent.on_submit = remove_checker
            runner, _clock = self._runner(root, agent, checker=checker)
            state = runner.execute(resume_only=False)
            self.assertEqual(state["pause_reason"], "checker_modified")
            self.assertNotEqual(state["status"], "complete")

    def test_failed_history_does_not_validate(self) -> None:
        cases = (
            (RuntimeError("down"), "trace_unavailable"),
            (ContractError("history_incomplete"), "history_incomplete"),
        )
        for error, reason in cases:
            with self.subTest(reason):
                with tempfile.TemporaryDirectory() as temporary:
                    root = Path(temporary)
                    agent = FakeAgent()
                    agent.history_error = error
                    agent.on_submit = _ready
                    runner, _clock = self._runner(root, agent)
                    state = runner.execute(resume_only=False)
                    self.assertEqual(state["pause_reason"], reason)
                    self.assertNotEqual(state["status"], "complete")
                    self.assertEqual(state["rounds"], 0)

    def test_incomplete_history_from_inspect_pauses(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            agent = FakeAgent()
            agent.inspect_error = ContractError("history_incomplete")
            runner, _clock = self._runner(root, agent)
            state = runner.execute(resume_only=False)
            self.assertEqual(state["pause_reason"], "history_incomplete")
            self.assertEqual(agent.submits, [])
            self.assertNotEqual(state["status"], "complete")

    def test_raw_history_redacts_structured_secrets(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            agent = FakeAgent()
            agent.on_submit = _ready
            agent.plant_raw = {
                "token": "secret123",
                "tokens": {"input": 4},
                "note": "PASSWORD=sekret",
            }
            agent.plant_event = {
                "event_id": "plant/message",
                "type": "message",
                "token": "secret123",
                "tokens": {"input": 4},
                "text": "PASSWORD=sekret",
            }
            runner, _clock = self._runner(root, agent, max_rounds=2)
            state = runner.execute(resume_only=False)
            self.assertEqual(state["status"], "complete")
            raw_text = "\n".join(path.read_text(encoding="utf-8")
                                 for path in (root / "run" / "raw").glob("*.json"))
            log_text = (root / "run" / "events.jsonl").read_text(encoding="utf-8")
            for body in (raw_text, log_text):
                self.assertNotIn("secret123", body)
                self.assertNotIn("sekret", body)
                self.assertIn('"input": 4', body)

    def test_round_limit_and_unknown_runtime_pause_without_passing(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            agent = FakeAgent()
            runner, _clock = self._runner(root, agent, max_rounds=1)
            state = runner.execute(resume_only=False)
            self.assertEqual(state["status"], "paused")
            self.assertEqual(state["pause_reason"], "round_limit")
            self.assertNotEqual(state["last_verdict"], "pass")

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            agent = FakeAgent()
            agent.inspect_phase = "unknown"
            runner, _clock = self._runner(root, agent)
            state = runner.execute(resume_only=False)
            self.assertEqual(state["pause_reason"], "runtime_unavailable")
            self.assertNotEqual(state["status"], "complete")

    def test_active_time_limit_pauses(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            agent = FakeAgent()
            agent.phase_after_submit = "executing"
            runner, clock = self._runner(root, agent, max_seconds=5)

            def jump(_seconds: float) -> None:
                clock.now += 10

            runner.sleep = jump
            state = runner.execute(resume_only=False)
            self.assertEqual(state["pause_reason"], "time_limit")
            self.assertEqual(agent.history_calls, 0)
            self.assertGreaterEqual(state["elapsed_seconds"], 5)
            self.assertNotEqual(state["status"], "complete")

    def test_bad_checks_never_pass(self) -> None:
        cases = {
            "inconclusive": (
                "import json\nprint(json.dumps({'verdict':'inconclusive','diagnostics':'not sure'}))\n",
                "no_progress",
                2,
            ),
            "crash": ("import sys\nsys.exit(2)\n", "validation_unavailable", 4),
            "pass_nonzero": (
                "import json,sys\nprint(json.dumps({'verdict':'pass','diagnostics':'no'}))\nsys.exit(1)\n",
                "validation_unavailable",
                4,
            ),
        }
        for name, (source, reason, rounds) in cases.items():
            with self.subTest(name):
                with tempfile.TemporaryDirectory() as temporary:
                    root = Path(temporary)
                    checker = root / "accept.py"
                    checker.write_text(source, encoding="utf-8")
                    agent = FakeAgent()
                    runner, _clock = self._runner(
                        root, agent, max_rounds=rounds, checker=checker)
                    state = runner.execute(resume_only=False)
                    self.assertNotEqual(state["status"], "complete")
                    self.assertEqual(state["pause_reason"], reason)

    def test_identical_deterministic_failure_pauses_after_one_repeat(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            checker = root / "accept.py"
            checker.write_text(
                "import json\nprint(json.dumps({'verdict':'fail',"
                "'diagnostics':'research note missing'}))\n",
                encoding="utf-8")
            agent = FakeAgent()  # never changes the candidate
            runner, _clock = self._runner(
                root, agent, max_rounds=6, checker=checker)
            state = runner.execute(resume_only=False)
        self.assertEqual(state["pause_reason"], "no_progress")
        self.assertNotEqual(state["status"], "complete")
        self.assertEqual(state["rounds"], 2)
        self.assertEqual(state["no_progress"]["repeats"], 2)

    def test_changed_candidate_is_meaningful_continuation(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            checker = root / "accept.py"
            checker.write_text(
                "import json,pathlib,sys\n"
                "marker=(pathlib.Path(sys.argv[1])/'marker.txt').read_text().strip()\n"
                "print(json.dumps({'verdict':'pass' if marker=='ready' "
                "else 'fail','diagnostics':'research note missing'}))\n",
                encoding="utf-8")
            agent = FakeAgent()
            submits = {"count": 0}

            def change_candidate(workspace):
                submits["count"] += 1
                (workspace / "marker.txt").write_text(
                    "ready\n" if submits["count"] >= 2 else "attempt-1\n")

            agent.on_submit = change_candidate
            runner, _clock = self._runner(
                root, agent, max_rounds=4, checker=checker)
            state = runner.execute(resume_only=False)
        self.assertEqual(state["status"], "complete")
        self.assertEqual(state["rounds"], 2)
        self.assertNotIn("no_progress", state)

    def test_candidate_fingerprint_hashes_untracked_non_git_content(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            workspace = Path(temporary) / "workspace"
            workspace.mkdir()
            (workspace / "source.txt").write_text("a\n", encoding="utf-8")
            first = candidate_digest(workspace)
            (workspace / "source.txt").write_text("b\n", encoding="utf-8")
            second = candidate_digest(workspace)
            self.assertNotEqual(first, second)
            (workspace / "notes.txt").write_text("one\n", encoding="utf-8")
            third = candidate_digest(workspace)
            self.assertNotEqual(second, third)
            (workspace / "notes.txt").write_text("two\n", encoding="utf-8")
            fourth = candidate_digest(workspace)
            self.assertNotEqual(third, fourth)
            # Two distinct content changes cannot share a digest.
            (workspace / "source.txt").write_text("c\n", encoding="utf-8")
            fifth = candidate_digest(workspace)
            (workspace / "notes.txt").write_text("three\n", encoding="utf-8")
            self.assertNotEqual(fifth, candidate_digest(workspace))

    def test_candidate_fingerprint_excludes_evidence_and_output_trees(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            workspace = Path(temporary) / "workspace"
            workspace.mkdir()
            (workspace / "source.txt").write_text("a\n", encoding="utf-8")
            before = candidate_digest(workspace)
            for relative in ("evidence/report.json", "output/frame.png",
                             ".local/state.json", "results/run.json",
                             "artifacts/blob.bin"):
                path = workspace / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("noise\n", encoding="utf-8")
            self.assertEqual(before, candidate_digest(workspace))
            (workspace / "evidence" / "report.json").write_text(
                "changed noise\n", encoding="utf-8")
            self.assertEqual(before, candidate_digest(workspace))

    def test_candidate_fingerprint_hashes_a_git_untracked_file(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            repo = Path(temporary) / "repo"
            repo.mkdir()
            _git(repo, "init")
            (repo / "source.txt").write_text("tracked\n", encoding="utf-8")
            _git(repo, "add", "-A")
            _git(repo, "commit", "-m", "base")
            before = candidate_digest(repo)
            (repo / "untracked.txt").write_text("one\n", encoding="utf-8")
            first = candidate_digest(repo)
            self.assertNotEqual(before, first)
            (repo / "untracked.txt").write_text("two\n", encoding="utf-8")
            second = candidate_digest(repo)
            self.assertNotEqual(first, second)
            (repo / "evidence").mkdir()
            (repo / "evidence" / "report.json").write_text(
                "noise\n", encoding="utf-8")
            self.assertEqual(second, candidate_digest(repo))

    def test_no_progress_reconnect_does_not_repeat_an_unchanged_failure(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            checker = root / "accept.py"
            checker.write_text(
                "import json\nprint(json.dumps({'verdict':'fail',"
                "'diagnostics':'research note missing'}))\n",
                encoding="utf-8")
            agent = FakeAgent()
            runner, _clock = self._runner(
                root, agent, max_rounds=6, checker=checker)
            first = runner.execute(resume_only=False)
            self.assertEqual(first["pause_reason"], "no_progress")
            submits = len(agent.submits)
            resumed = Runner(
                runner.campaign, agent, root / "run",
                clock=runner.clock, sleep=runner.sleep)
            state = resumed.execute(resume_only=True)
            self.assertEqual(state["pause_reason"], "no_progress")
            self.assertEqual(state["rounds"], first["rounds"])
            self.assertEqual(len(agent.submits), submits)

    def test_evidence_or_output_churn_does_not_permit_a_new_attempt(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            checker = root / "accept.py"
            checker.write_text(
                "import json\nprint(json.dumps({'verdict':'fail',"
                "'diagnostics':'research note missing'}))\n",
                encoding="utf-8")
            agent = FakeAgent()
            runner, _clock = self._runner(
                root, agent, max_rounds=6, checker=checker)
            first = runner.execute(resume_only=False)
            self.assertEqual(first["pause_reason"], "no_progress")
            submits = len(agent.submits)
            workspace = root / "run" / "workspace"
            for relative in ("evidence/result.json", "output/frame.txt"):
                path = workspace / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text("generated\n", encoding="utf-8")
            state = runner.execute(resume_only=True)
            self.assertEqual(state["pause_reason"], "no_progress")
            self.assertEqual(len(agent.submits), submits)

    def test_substantive_untracked_change_permits_a_new_attempt(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            checker = root / "accept.py"
            checker.write_text(
                "import json,pathlib,sys\n"
                "workspace=pathlib.Path(sys.argv[1])\n"
                "note=workspace/'notes.txt'\n"
                "text=note.read_text().strip() if note.exists() else ''\n"
                "print(json.dumps({'verdict':'pass' if text=='ready' "
                "else 'fail','diagnostics':'research note missing'}))\n",
                encoding="utf-8")
            agent = FakeAgent()
            runner, _clock = self._runner(
                root, agent, max_rounds=6, checker=checker)
            first = runner.execute(resume_only=False)
            self.assertEqual(first["pause_reason"], "no_progress")
            submits = len(agent.submits)
            (root / "run" / "workspace" / "notes.txt").write_text(
                "ready\n", encoding="utf-8")
            state = runner.execute(resume_only=True)
            self.assertEqual(state["status"], "complete")
            self.assertGreater(len(agent.submits), submits)
            self.assertNotIn("no_progress", state)

    def test_interruption_reconnects_to_the_same_no_progress_pause(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            checker = root / "accept.py"
            checker.write_text(
                "import json\nprint(json.dumps({'verdict':'fail',"
                "'diagnostics':'research note missing'}))\n",
                encoding="utf-8")
            agent = FakeAgent()

            def interrupt_second_submit(_workspace: Path) -> None:
                agent.interrupt = True

            agent.on_submit = interrupt_second_submit
            runner, _clock = self._runner(
                root, agent, max_rounds=6, checker=checker)
            with self.assertRaises(KeyboardInterrupt):
                runner.execute(resume_only=False)
            interrupted = load_state(runner.state_path)
            self.assertEqual(interrupted["pause_reason"], "interrupted")
            self.assertIn("no_progress", interrupted)
            agent.on_submit = None
            attempts = len(agent.submits)
            state = runner.execute(resume_only=True)
            self.assertEqual(state["pause_reason"], "no_progress")
            self.assertEqual(len(agent.submits), attempts)

    def test_two_concurrent_sessions_cannot_both_run(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            checker = root / "accept.py"
            checker.write_text(
                "import json\nprint(json.dumps({'verdict':'fail',"
                "'diagnostics':'no'}))\n",
                encoding="utf-8")
            agent = FakeAgent()
            runner, _clock = self._runner(
                root, agent, max_rounds=1, checker=checker)
            release = threading.Event()
            original = runner._loop

            def hold(state):
                release.wait(5)
                original(state)

            runner._loop = hold
            outcomes: list[str] = []

            def first():
                try:
                    runner.execute(resume_only=False)
                    outcomes.append("ran")
                except RuntimeError as exc:
                    outcomes.append(str(exc))

            thread = threading.Thread(target=first)
            thread.start()
            deadline = time.monotonic() + 5
            while not runner.state_path.exists() and time.monotonic() < deadline:
                time.sleep(0.01)
            time.sleep(0.1)
            second = Runner(
                runner.campaign, FakeAgent(), root / "run")
            with self.assertRaises(RuntimeError):
                second.execute(resume_only=True)
            release.set()
            thread.join(timeout=10)
            self.assertEqual(outcomes, ["ran"])

    def test_restart_compares_against_the_original_checker_digest(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            checker = root / "accept.py"
            checker.write_text(
                "import json\nprint(json.dumps({'verdict':'fail','diagnostics':'no'}))\n",
                encoding="utf-8",
            )
            agent = FakeAgent()
            runner, _clock = self._runner(root, agent, max_rounds=0, checker=checker)
            first = runner.execute(resume_only=False)
            original = first["checker_sha256"]
            checker.write_text(
                "import json\nprint(json.dumps({'verdict':'pass','diagnostics':'tampered'}))\n",
                encoding="utf-8",
            )
            state = runner.execute(resume_only=True)
            self.assertEqual(state["pause_reason"], "checker_modified")
            self.assertEqual(state["checker_sha256"], original)
            self.assertNotEqual(state["status"], "complete")


_SKILL = ".opencode/skills/opengl-metal-reconstruction/SKILL.md"


def _git(path: Path, *args: str) -> str:
    completed = subprocess.run(
        ["git", "-C", str(path), "-c", "user.email=t@example.com", "-c", "user.name=Test", *args],
        check=True, capture_output=True, text=True,
    )
    return completed.stdout.strip()


def _pin(repo: Path, worktree: Path, commit: str) -> Path:
    (repo / "objective.md").write_text("Make marker.txt contain exactly ready.\n", encoding="utf-8")
    path = repo / "campaign.json"
    path.write_text(json.dumps({
        "id": "skill-trust",
        "objective": "objective.md",
        "runtime": "opencode",
        "model": "fake/model",
        "opencode_version": "2.2.5",
        "workspace": {"mode": "worktree", "path": "../wt", "commit": commit},
        "accept": ["python3", str(SMOKE_ACCEPT)],
        "max_rounds": 2,
        "max_seconds": 100,
        "accept_timeout_seconds": 5,
    }), encoding="utf-8")
    return path


def _worktree(root: Path, *, skill: bool) -> tuple[Path, Path, str]:
    repo = root / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    (repo / "README").write_text("seed\n", encoding="utf-8")
    if skill:
        path = repo / _SKILL
        path.parent.mkdir(parents=True)
        path.write_text("trusted\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "init")
    commit = _git(repo, "rev-parse", "HEAD")
    worktree = root / "wt"
    _git(repo, "worktree", "add", "--detach", str(worktree), commit)
    return repo, worktree, commit


def _skill_ready(workspace: Path) -> None:
    (workspace / "marker.txt").write_text("ready\n", encoding="utf-8")
    (workspace / "progress.md").write_text("# Progress\n\n## Next\ndone\n", encoding="utf-8")


class SkillTrustTests(unittest.TestCase):
    def _run(self, repo: Path, commit: str) -> tuple[FakeAgent, dict]:
        agent = FakeAgent()
        agent.allow_skill = False
        agent.on_submit = _skill_ready
        clock = Clock()
        runner = Runner(
            load_campaign(_pin(repo, repo.parent / "wt", commit)),
            agent, repo / "run", clock=clock.monotonic, sleep=clock.sleep,
        )
        return agent, runner.execute(resume_only=False)

    def test_pinned_matching_skill_is_loaded_and_allowed(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            repo, worktree, commit = _worktree(Path(raw), skill=True)
            self.assertEqual(skill_trust(worktree, commit), "trusted")
            agent, state = self._run(repo, commit)
            self.assertTrue(agent.allow_skill)
            self.assertIn("load the `opengl-metal-reconstruction` skill", agent.prompts[0])
            self.assertEqual(state["status"], "complete")

    def test_older_pin_does_not_load_or_permit_the_skill(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            repo, worktree, commit = _worktree(Path(raw), skill=False)
            self.assertEqual(skill_trust(worktree, commit), "absent")
            agent, state = self._run(repo, commit)
            self.assertFalse(agent.allow_skill)
            self.assertIn("no trusted `opengl-metal-reconstruction` skill", agent.prompts[0])
            self.assertNotIn("load the `opengl-metal-reconstruction` skill", agent.prompts[0])
            self.assertEqual(state["status"], "complete")

    def test_untracked_skill_in_an_older_worktree_is_not_trusted(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            repo, worktree, commit = _worktree(Path(raw), skill=False)
            planted = worktree / _SKILL
            planted.parent.mkdir(parents=True)
            planted.write_text("untracked\n", encoding="utf-8")
            self.assertEqual(skill_trust(worktree, commit), "absent")
            agent, _state = self._run(repo, commit)
            self.assertFalse(agent.allow_skill)
            self.assertIn("no trusted `opengl-metal-reconstruction` skill", agent.prompts[0])

    def test_seed_campaign_has_no_trusted_skill(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            campaign = load_campaign(write_campaign(root, max_rounds=2))
            self.assertIsNone(campaign.worktree_commit)
            self.assertEqual(skill_trust(root / "seed", campaign.worktree_commit), "absent")
            agent = FakeAgent()
            agent.allow_skill = True
            agent.on_submit = _ready
            clock = Clock()
            runner = Runner(
                campaign, agent, root / "run", clock=clock.monotonic, sleep=clock.sleep)
            state = runner.execute(resume_only=False)
            self.assertFalse(agent.allow_skill)
            self.assertIn("no trusted `opengl-metal-reconstruction` skill", agent.prompts[0])
            self.assertEqual(state["status"], "complete")

    def test_missing_live_skill_pauses_before_create(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            repo, worktree, commit = _worktree(Path(raw), skill=True)
            (worktree / _SKILL).unlink()
            self.assertEqual(skill_trust(worktree, commit), "mismatch")
            agent, state = self._run(repo, commit)
            self.assertEqual(state["pause_reason"], "skill_mismatch")
            self.assertEqual(state["status"], "paused")
            self.assertEqual(agent.prompts, [])

    def test_modified_pinned_skill_pauses_before_create(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            repo, worktree, commit = _worktree(Path(raw), skill=True)
            (worktree / _SKILL).write_text("changed\n", encoding="utf-8")
            self.assertEqual(skill_trust(worktree, commit), "mismatch")
            agent, state = self._run(repo, commit)
            self.assertEqual(state["pause_reason"], "skill_mismatch")
            self.assertEqual(state["status"], "paused")
            self.assertEqual(agent.prompts, [])

    def _interrupted(self, repo: Path, commit: str):
        agent = FakeAgent()
        agent.allow_skill = False
        agent.interrupt = True
        campaign = load_campaign(_pin(repo, repo.parent / "wt", commit))
        clock = Clock()
        runner = Runner(
            campaign, agent, repo / "run", clock=clock.monotonic, sleep=clock.sleep)
        with self.assertRaises(KeyboardInterrupt):
            runner.execute(resume_only=False)
        saved = load_state(repo / "run" / "state.json")
        self.assertNotEqual(saved["status"], "complete")
        self.assertTrue(saved["pending_submission_id"])
        self.assertTrue(saved["pending_text"])
        return agent, campaign, clock, saved

    def test_resume_keeps_the_pending_submission_and_restores_trust(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            repo, _tree, commit = _worktree(Path(raw), skill=True)
            agent, campaign, clock, saved = self._interrupted(repo, commit)
            agent.interrupt = False
            resumed = Runner(
                campaign, agent, repo / "run", clock=clock.monotonic, sleep=clock.sleep)
            state = resumed.execute(resume_only=True)
            self.assertNotEqual(state["status"], "complete")
            self.assertTrue(agent.allow_skill)
            self.assertEqual(agent.submits[0], saved["pending_submission_id"])
            self.assertEqual(agent.messages[0][2], saved["pending_text"])

    def test_resume_pauses_when_the_pending_skill_no_longer_matches(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            repo, worktree, commit = _worktree(Path(raw), skill=True)
            agent, campaign, clock, saved = self._interrupted(repo, commit)
            (worktree / _SKILL).write_text("changed\n", encoding="utf-8")
            agent.interrupt = False
            resumed = Runner(
                campaign, agent, repo / "run", clock=clock.monotonic, sleep=clock.sleep)
            state = resumed.execute(resume_only=True)
            kept = load_state(repo / "run" / "state.json")
            self.assertEqual(state["pause_reason"], "skill_mismatch")
            self.assertNotEqual(state["status"], "complete")
            self.assertEqual(agent.submits, [])
            self.assertEqual(kept["pending_submission_id"], saved["pending_submission_id"])
            self.assertEqual(kept["pending_text"], saved["pending_text"])

    def test_between_submissions_uses_restored_skill_trust(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            repo, worktree, commit = _worktree(Path(raw), skill=True)
            agent = FakeAgent()
            agent.allow_skill = False
            session = agent.create(worktree, "existing")
            campaign = load_campaign(_pin(repo, repo.parent / "wt", commit))
            save_state(repo / "run" / "state.json", {
                "campaign_id": campaign.id,
                "checker_sha256": file_sha256(campaign.checker),
                "elapsed_seconds": 0.0,
                "last_diagnostics": None,
                "last_observation": None,
                "last_verdict": None,
                "model": None,
                "pause_reason": "interrupted",
                "pending_submission_id": None,
                "pending_text": None,
                "rounds": 0,
                "runtime": "opencode",
                "session_id": session,
                "started_at": "2026-11-05T00:00:00+00:00",
                "status": "paused",
                "validated_submission_id": None,
                "workspace": str(worktree),
            })
            clock = Clock()
            resumed = Runner(
                campaign, agent, repo / "run", clock=clock.monotonic, sleep=clock.sleep)
            state = resumed.execute(resume_only=True)
            self.assertNotEqual(state["status"], "complete")
            self.assertTrue(agent.allow_skill)
            self.assertIn("load the `opengl-metal-reconstruction` skill", agent.messages[0][2])
            self.assertNotIn("no trusted `opengl-metal-reconstruction` skill", agent.messages[0][2])

    def test_git_lookup_failure_is_unavailable(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            self.assertEqual(skill_trust(Path(raw), "not-a-commit"), "unavailable")


if __name__ == "__main__":
    unittest.main()
