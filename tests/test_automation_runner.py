"""Offline continuation, restart, replacement, and budget tests."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from automation.runner import Runner
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
            self.assertGreaterEqual(state["elapsed_seconds"], 5)
            self.assertNotEqual(state["status"], "complete")

    def test_bad_checks_never_pass(self) -> None:
        cases = {
            "inconclusive": (
                "import json\nprint(json.dumps({'verdict':'inconclusive','diagnostics':'not sure'}))\n",
                "round_limit",
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


if __name__ == "__main__":
    unittest.main()
