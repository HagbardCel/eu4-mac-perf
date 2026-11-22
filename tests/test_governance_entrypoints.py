"""Agent-facing entry points must enforce rationale, stop and launch policy.

These are the boundaries an agent can actually invoke: the tool bridge, the
CLI replay entry points, the direct frame replay and the launcher scripts.  A
requirement enforced only in a shared library that a wrapper bypasses is not
enforced.
"""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
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


class CheckerBindingTests(unittest.TestCase):
    """The selected campaign sidecar owns the checker reference."""

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)

    def _accept_module(self):
        path = ROOT / "campaigns" / "eu4-m7-event20" / "accept.py"
        spec = importlib.util.spec_from_file_location("event20_accept", path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def test_the_selected_sidecar_reference_cannot_be_overridden(self):
        module = self._accept_module()
        selected = self.root / "selected.json"
        selected.write_text(json.dumps({
            "trusted_reference_root": str(self.root / "selected-reference")}))
        with mock.patch.dict(os.environ, {
                "EU4_M7_CANDIDATE_SIDECAR": str(selected),
                "EU4_M7_EVENT20_TRUSTED_ROOT": str(self.root / "wrong")}):
            with mock.patch.object(
                    module.gfx_prefix_check, "resolve_trusted_reference",
                    side_effect=lambda configured, workspace, root:
                    Path(configured)) as resolve:
                value = module._trusted_root(self.root)
        self.assertEqual(value, self.root / "selected-reference")
        self.assertEqual(resolve.call_count, 1)

    def test_standalone_trusted_root_is_still_honoured_without_a_sidecar(self):
        module = self._accept_module()
        with mock.patch.dict(os.environ, {
                "EU4_M7_EVENT20_TRUSTED_ROOT": str(self.root / "standalone")}):
            os.environ.pop("EU4_M7_CANDIDATE_SIDECAR", None)
            with mock.patch.object(
                    module.gfx_prefix_check, "resolve_trusted_reference",
                    side_effect=lambda configured, workspace, root:
                    Path(configured)):
                value = module._trusted_root(self.root)
        self.assertEqual(value, self.root / "standalone")

    def test_historical_and_live_sidecars_keep_distinct_references(self):
        module = self._accept_module()
        sidecars = [
            ROOT / "dsh" / "campaigns" / "m7-event20.json",
            ROOT / "dsh" / "campaigns" / "m7-autonomy-offline.json",
        ]
        for sidecar in sidecars:
            configured = json.loads(sidecar.read_text())["trusted_reference_root"]
            with mock.patch.dict(
                    os.environ, {"EU4_M7_CANDIDATE_SIDECAR": str(sidecar)}):
                with mock.patch.object(
                        module.gfx_prefix_check, "resolve_trusted_reference",
                        side_effect=lambda value, workspace, root:
                        Path(value)):
                    value = module._trusted_root(self.root)
            self.assertEqual(str(value), configured, sidecar.name)

    def test_the_dsh_runner_binds_the_selected_sidecar_and_scrubs_ambient_env(self):
        from automation import acceptance

        checker = self.root / "checker.py"
        checker.write_text(
            "import json, os, sys\n"
            "print(json.dumps({'verdict': 'inconclusive', 'diagnostics': "
            "json.dumps({'sidecar': os.environ.get("
            "'EU4_M7_CANDIDATE_SIDECAR', ''), 'trusted': os.environ.get("
            "'EU4_M7_EVENT20_TRUSTED_ROOT', '')})}))\n")
        campaign = type("Campaign", (), {
            "accept": [sys.executable, str(checker)],
            "root": self.root,
            "accept_timeout": 30.0,
            "accept_env": {"EU4_M7_CANDIDATE_SIDECAR": "/selected/sidecar.json"},
            "accept_env_scrub": ("EU4_M7_CANDIDATE_SIDECAR",
                                 "EU4_M7_EVENT20_TRUSTED_ROOT"),
        })()
        with mock.patch.dict(os.environ, {
                "EU4_M7_CANDIDATE_SIDECAR": "/ambient/sidecar.json",
                "EU4_M7_EVENT20_TRUSTED_ROOT": "/wrong/root"}):
            result = acceptance.run_accept(campaign, self.root)
        self.assertTrue(result["ok"], result)
        details = json.loads(result["diagnostics"])
        self.assertEqual(details["sidecar"], "/selected/sidecar.json")
        self.assertEqual(details["trusted"], "")


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
