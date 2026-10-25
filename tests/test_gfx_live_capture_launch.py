#!/usr/bin/env python3
"""Mocked launcher tests for PR-D live capture operator workflow."""

from __future__ import annotations

import datetime as dt
import json
import shutil
import subprocess
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
BENCH = ROOT / "benchmark"
sys.path.insert(0, str(BENCH))

import eu4_benchmark as base  # noqa: E402
import eu4_gfx_live_capture as live  # noqa: E402

FIXED_NOW = dt.datetime(2026, 10, 25, 12, 0, 0, tzinfo=dt.timezone.utc)
SESSION_ID = "gfx-live-20261025T120000Z"
GAME_PID = 42424
IMAGE_BASE = 0x100000000
PATCHED_DT = types.SimpleNamespace(
    datetime=types.SimpleNamespace(now=lambda tz=None: FIXED_NOW),
    timezone=dt.timezone,
)


def _startup_table(session_id: str, pid: int, executable: Path) -> str:
    site = IMAGE_BASE + live.TARGET_RVA
    return "\n".join([
        live.STARTUP_HEADER,
        "state=installed",
        f"session={session_id}",
        f"pid={pid}",
        f"executable={executable}",
        "arch=x86_64",
        f"image_base=0x{IMAGE_BASE:x}",
        f"site=0x{site:x}",
        "module=deadbeef",
        "error=",
        "END",
        "",
    ])


class GfxLiveLaunchTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp(prefix="gfx-live-launch-"))
        self._run_index = 0
        self.exe = self.root / "eu4.app" / "Contents" / "MacOS" / "eu4"
        self.exe.parent.mkdir(parents=True)
        self.exe.write_bytes(b"stub")
        self.save = self.root / "venice.eu4"
        self.save.write_bytes(b"save")
        self.launcher_path = self.root / "launcher.json"
        self.launcher_path.write_text(json.dumps({
            "exePath": "./eu4.app/Contents/MacOS/eu4",
            "exeArgs": ["-enabletelemetry"],
        }))
        self.evidence = {
            "save": str(self.save),
            "save_sha256": "save-hash",
            "gog": {"executable_sha256": "gog"},
            "descriptor_sha256": "desc",
            "descriptor_policy": "policy",
            "code_window_sha256": "window",
            "patch_site_sha256": "patch",
            "library": str(live.LIBRARY),
            "library_sha256": "lib",
            "source_sha256": {},
        }

    def tearDown(self) -> None:
        shutil.rmtree(self.root, ignore_errors=True)

    def _run_until_prompt(self, *, prompt_answer: str = "no",
                          focus_bin: Path | None = live.FOCUS,
                          focus_build_error: str | None = None,
                          activate_side_effect=None) -> dict:
        popen_calls: list[dict] = []
        run_calls: list[list[str]] = []
        control_calls: list[int] = []
        prompt_calls: list[str] = []

        class FakeGame:
            pid = GAME_PID

            def poll(self):
                return None

        def fake_popen(*args, **kwargs):
            popen_calls.append({"argv": args, **kwargs})
            return FakeGame()

        def fake_wait(path: Path, seconds: float, game) -> bool:
            if path.name == "startup.status":
                path.write_text(
                    _startup_table(SESSION_ID, GAME_PID, self.exe.resolve()),
                    encoding="utf-8",
                )
                return True
            return path.is_file()

        def fake_prompt(message: str, required: bool = False) -> str:
            prompt_calls.append(message)
            return prompt_answer

        def fake_run(command, **kwargs):
            run_calls.append(list(command))
            if (activate_side_effect is not None and len(command) >= 2
                    and command[-2] == "activate"):
                return activate_side_effect(command, **kwargs)
            return subprocess.CompletedProcess(command, 0)

        patches = [
            patch.object(live, "dt", PATCHED_DT),
            patch.object(live, "preflight", return_value=self.evidence),
            patch.object(live, "_try_build_focus_helper",
                         return_value=(focus_bin, focus_build_error)),
            patch.object(base, "GOG_EXE", self.exe),
            patch.object(base, "GOG_LAUNCHER", self.launcher_path),
            patch.object(base, "backup_save", return_value=str(self.root / "backup")),
            patch.object(live.subprocess, "Popen", side_effect=fake_popen),
            patch.object(live, "_wait_for", side_effect=fake_wait),
            patch.object(base, "prompt", side_effect=fake_prompt),
            patch.object(live, "_write_control",
                         side_effect=lambda path, value: control_calls.append(value)),
            patch.object(live.subprocess, "run", side_effect=fake_run),
        ]
        self._run_index += 1
        output = self.root / f"out-{self._run_index}"
        with patch.object(base, "running_game_pid", return_value=GAME_PID):
            for item in patches:
                item.start()
            try:
                with self.assertRaises(live.LiveError):
                    live.run(output, authorized=True)
            finally:
                for item in reversed(patches):
                    item.stop()

        run_dir = output / "20261025T120000Z-gfx-live"
        metadata = json.loads((run_dir / "metadata.json").read_text(encoding="utf-8"))
        return {
            "popen": popen_calls,
            "run": run_calls,
            "control": control_calls,
            "prompt": prompt_calls,
            "metadata": metadata,
            "run_dir": run_dir,
        }

    def test_child_stdin_detached_and_launch_env_preserved(self) -> None:
        result = self._run_until_prompt()
        self.assertEqual(len(result["popen"]), 1)
        kwargs = result["popen"][0]
        self.assertIs(kwargs["stdin"], subprocess.DEVNULL)
        self.assertEqual(kwargs["cwd"], self.exe.parent)
        env = kwargs["env"]
        self.assertEqual(env["DYLD_INSERT_LIBRARIES"], str(live.LIBRARY))
        self.assertEqual(env["EU4_GFX_EXPECTED_EXE"], str(self.exe))
        self.assertEqual(kwargs["argv"][0], ["./eu4", "-enabletelemetry"])

    def test_focus_activation_before_prompt_and_capture_not_armed(self) -> None:
        result = self._run_until_prompt()
        activate = [call for call in result["run"]
                    if len(call) >= 2 and call[-2] == "activate"]
        self.assertEqual(len(activate), 1)
        self.assertEqual(activate[0][-1], str(GAME_PID))
        self.assertTrue(result["prompt"])
        self.assertNotIn(1, result["control"])
        self.assertTrue(result["metadata"]["focus_activate_attempted"])
        self.assertTrue(result["metadata"]["focus_activate_ok"])

    def test_focus_failures_reach_prompt_without_arming(self) -> None:
        cases = [
            ("nonzero exit", subprocess.CompletedProcess(["x"], 3)),
            ("OSError", OSError("boom")),
            ("TimeoutExpired", subprocess.TimeoutExpired(cmd=["x"], timeout=3)),
        ]
        def make_activate_side(effect):
            if isinstance(effect, subprocess.CompletedProcess):
                return lambda command, **kwargs: effect
            return lambda command, **kwargs: (_raise(effect))

        def _raise(exc: BaseException) -> None:
            raise exc

        for label, effect in cases:
            with self.subTest(label=label):
                result = self._run_until_prompt(
                    activate_side_effect=make_activate_side(effect))
                self.assertTrue(result["prompt"])
                self.assertNotIn(1, result["control"])
                self.assertTrue(result["metadata"]["focus_activate_attempted"])
                self.assertFalse(result["metadata"]["focus_activate_ok"])

        with self.subTest(label="helper build failed"):
            result = self._run_until_prompt(
                focus_bin=None, focus_build_error="focus build timed out")
            activate = [call for call in result["run"]
                        if len(call) >= 2 and call[-2] == "activate"]
            self.assertEqual(activate, [])
            self.assertTrue(result["prompt"])
            self.assertNotIn(1, result["control"])
            self.assertFalse(result["metadata"]["focus_activate_attempted"])
            self.assertFalse(result["metadata"]["focus_activate_ok"])
            self.assertEqual(result["metadata"]["focus_helper_build_error"],
                             "focus build timed out")


if __name__ == "__main__":
    unittest.main()
