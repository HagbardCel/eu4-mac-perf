"""Agent-facing entry points must enforce rationale, stop and launch policy.

These are the boundaries an agent can actually invoke: the tool bridge, the
CLI replay entry points, the direct frame replay and the launcher scripts.  A
requirement enforced only in a shared library that a wrapper bypasses is not
enforced.
"""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
RATIONALE = {
    "question": "does the renderer match?",
    "evidence": ["."],
    "gap": "the corrected renderer was not executed",
    "hypothesis": "the correction fixes the divergence",
    "action": "targeted replay",
    "expected_information": "exact match or first divergence",
    "cheaper_alternatives_checked": True,
}


class ReplayCliTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)

    def test_independent_replay_cli_refuses_without_a_rationale(self):
        from automation import gfx_independent_replay

        with mock.patch.object(
                gfx_independent_replay, "replay",
                side_effect=AssertionError("the replay must not run")), \
             mock.patch("sys.stdout"):
            code = gfx_independent_replay.main([
                "--frame", str(self.root / "frame"),
                "--out", str(self.root / "out"),
            ])
        self.assertEqual(code, 2)

    def test_independent_replay_cli_proceeds_with_a_rationale(self):
        from automation import gfx_independent_replay

        rationale = self.root / "rationale.json"
        rationale.write_text(json.dumps(RATIONALE))
        report = {"schema": "gfx-independent-replay/v1",
                  "status": "confirmed_rendering_difference",
                  "outcome": {}, "graph": {}, "evaluation": {}, "final": {},
                  "binding_audit": {}, "checkpoints": []}
        with mock.patch.object(gfx_independent_replay, "replay",
                               return_value=report) as replay, \
             mock.patch("sys.stdout"):
            code = gfx_independent_replay.main([
                "--frame", str(self.root / "frame"),
                "--out", str(self.root / "out"),
                "--rationale", str(rationale),
            ])
        self.assertIn(code, (0, 1))
        self.assertEqual(replay.call_count, 1)
        self.assertIsInstance(replay.call_args.kwargs.get("rationale"), dict)

    def test_case_replay_cli_refuses_without_a_rationale(self):
        from automation import gfx_replay_case

        case = self.root / "case"
        case.mkdir()
        (case / "case.json").write_text("{}")
        with mock.patch.object(
                gfx_replay_case, "replay_case",
                side_effect=AssertionError("the replay must not run")), \
             mock.patch("sys.stdout"):
            code = gfx_replay_case.main([
                "replay", "--case", str(case),
                "--out", str(self.root / "out"),
            ])
        self.assertEqual(code, 2)


class FrameReplayToolTests(unittest.TestCase):
    def test_the_frame_tool_refuses_without_a_rationale(self):
        from types import SimpleNamespace

        from automation import agent_tools

        args = SimpleNamespace(
            replay_op="frame", frame="frame",
            replay_out="out", shaders=None, only_event=None,
            rationale=None,
        )
        with mock.patch.object(
                agent_tools, "_run_module",
                side_effect=AssertionError("the frame replay must not run")):
            value = agent_tools.eu4_replay(args)
        self.assertFalse(value["ok"])
        self.assertEqual(value["status"], "refused")
        self.assertIn("rationale", value["reason"])


class LauncherPolicyTests(unittest.TestCase):
    """The launchers must validate an existing state, not only create one."""

    def test_launchers_use_ensure_and_pass_the_mode(self):
        for name in ("run.sh", "run-campaign.sh"):
            text = (ROOT / "dsh" / "scripts" / name).read_text(encoding="utf-8")
            self.assertIn("campaign_guard ensure", text, name)
            self.assertNotIn("campaign_guard init", text, name)
            self.assertIn("--mode", text, name)
            self.assertIn("--deadline-seconds", text, name)

    def _environment(self):
        environment = dict(os.environ)
        for name in ("EU4_CAMPAIGN_STATE", "EU4_CAMPAIGN_ADMIN_TOKEN",
                     "EU4_CAMPAIGN_AUTHORIZATION"):
            environment.pop(name, None)
        environment["EU4_DSH_HOME"] = str(Path(tempfile.mkdtemp()))
        return environment

    def test_run_sh_refuses_a_campaign_without_a_state(self):
        sidecar = ROOT / "dsh" / "campaigns" / "m7-autonomy-offline.json"
        ran = subprocess.run(
            ["sh", str(ROOT / "dsh" / "scripts" / "run.sh"),
             "--campaign", str(sidecar)],
            capture_output=True, text=True, env=self._environment(),
            timeout=60)
        self.assertEqual(ran.returncode, 2, ran.stderr)
        self.assertIn("requires --campaign-state", ran.stderr)

    def test_run_campaign_refuses_a_campaign_without_a_state(self):
        sidecar = ROOT / "dsh" / "campaigns" / "m7-autonomy-offline.json"
        ran = subprocess.run(
            ["sh", str(ROOT / "dsh" / "scripts" / "run-campaign.sh"),
             str(sidecar)],
            capture_output=True, text=True, env=self._environment(),
            timeout=60)
        self.assertEqual(ran.returncode, 2, ran.stderr)
        self.assertIn("EU4_CAMPAIGN_STATE", ran.stderr)


class DeadlineEntrypointTests(unittest.TestCase):
    def test_an_expired_campaign_refuses_a_replay(self):
        from automation import campaign_guard, gfx_independent_replay

        with tempfile.TemporaryDirectory(dir=ROOT / ".local" / "test-runs") as tmp:
            root = Path(tmp)
            state = root / "state.json"
            guard = campaign_guard.Guard(state)
            marker = mock.patch.object(
                campaign_guard, "_marker_path",
                return_value=root / "markers" / campaign_guard.MARKER_NAME)
            marker.start()
            try:
                guard.init(mode=campaign_guard.MODE_AUTONOMOUS,
                           admin_token="token", deadline_seconds=0.01)
                with mock.patch.dict("os.environ",
                                     {"EU4_CAMPAIGN_STATE": str(state)}):
                    import time
                    time.sleep(0.05)
                    with mock.patch.object(
                            gfx_independent_replay, "_replay_locked",
                            side_effect=AssertionError("must not run")):
                        with self.assertRaises(
                                gfx_independent_replay.ReplayError) as raised:
                            gfx_independent_replay.replay(
                                root / "frame", root / "out")
                self.assertEqual(raised.exception.kind, "campaign_refused")
                self.assertIn("deadline", str(raised.exception))
            finally:
                marker.stop()


if __name__ == "__main__":
    unittest.main()
