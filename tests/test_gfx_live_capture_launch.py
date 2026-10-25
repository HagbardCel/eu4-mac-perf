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

    def _run_launch(self, *, prompt_answer: str = "no",
                    focus_bin: Path | None = live.FOCUS,
                    focus_build_error: str | None = None,
                    activate_side_effect=None,
                    fail_seal_after_arm: bool = False) -> dict:
        popen_calls: list[dict] = []
        run_calls: list[list[str]] = []
        events: list[tuple] = []

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
            if fail_seal_after_arm and path.name == "session.done":
                return False
            return path.is_file()

        def fake_prompt(message: str, required: bool = False) -> str:
            events.append(("prompt", message))
            return prompt_answer

        def fake_run(command, **kwargs):
            run_calls.append(list(command))
            if len(command) >= 2 and command[-2] == "activate":
                events.append(("activate", command[-1]))
                if activate_side_effect is not None:
                    return activate_side_effect(command, **kwargs)
            return subprocess.CompletedProcess(command, 0)

        def fake_write_control(path, value: int) -> None:
            events.append(("control", value))

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
            patch.object(live, "_write_control", side_effect=fake_write_control),
            patch.object(live.subprocess, "run", side_effect=fake_run),
        ]
        if fail_seal_after_arm:
            patches.append(patch.object(live.time, "monotonic", side_effect=[0, 100]))

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
            "events": events,
            "metadata": metadata,
            "run_dir": run_dir,
        }

    def _event_index(self, events: list[tuple], kind: str) -> int:
        return next(i for i, event in enumerate(events) if event[0] == kind)

    def test_child_stdin_detached_and_launch_env_preserved(self) -> None:
        result = self._run_launch()
        self.assertEqual(len(result["popen"]), 1)
        kwargs = result["popen"][0]
        self.assertIs(kwargs["stdin"], subprocess.DEVNULL)
        self.assertEqual(kwargs["cwd"], self.exe.parent)
        env = kwargs["env"]
        self.assertEqual(env["DYLD_INSERT_LIBRARIES"], str(live.LIBRARY))
        self.assertEqual(env["EU4_GFX_EXPECTED_EXE"], str(self.exe))
        self.assertEqual(kwargs["argv"][0], ["./eu4", "-enabletelemetry"])

    def test_focus_activation_before_prompt_and_capture_not_armed(self) -> None:
        result = self._run_launch()
        events = result["events"]
        self.assertLess(
            self._event_index(events, "activate"),
            self._event_index(events, "prompt"),
        )
        self.assertEqual(events.count(("control", 1)), 0)
        self.assertTrue(result["metadata"]["focus_activate_attempted"])
        self.assertTrue(result["metadata"]["focus_activate_ok"])

    def test_arm_only_after_yes_confirmation(self) -> None:
        result = self._run_launch(prompt_answer="yes", fail_seal_after_arm=True)
        events = result["events"]
        activate_i = self._event_index(events, "activate")
        prompt_i = self._event_index(events, "prompt")
        control_i = self._event_index(events, "control")
        self.assertLess(activate_i, prompt_i)
        self.assertLess(prompt_i, control_i)
        self.assertEqual(events[control_i], ("control", 1))
        self.assertEqual(sum(1 for event in events if event == ("control", 1)), 1)

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
                result = self._run_launch(activate_side_effect=make_activate_side(effect))
                self.assertTrue(any(event[0] == "prompt" for event in result["events"]))
                self.assertEqual(sum(1 for e in result["events"] if e == ("control", 1)), 0)
                self.assertTrue(result["metadata"]["focus_activate_attempted"])
                self.assertFalse(result["metadata"]["focus_activate_ok"])

        with self.subTest(label="helper build failed"):
            result = self._run_launch(
                focus_bin=None, focus_build_error="focus build timed out")
            activate = [event for event in result["events"] if event[0] == "activate"]
            self.assertEqual(activate, [])
            self.assertTrue(any(event[0] == "prompt" for event in result["events"]))
            self.assertEqual(sum(1 for e in result["events"] if e == ("control", 1)), 0)
            self.assertFalse(result["metadata"]["focus_activate_attempted"])
            self.assertFalse(result["metadata"]["focus_activate_ok"])
            self.assertEqual(result["metadata"]["focus_helper_build_error"],
                             "focus build timed out")


class GfxLiveFocusHelperTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp(prefix="gfx-live-focus-"))
        self.source = self.root / "mac_game_focus.m"
        self.source.write_text("int main() { return 0; }\n", encoding="utf-8")
        self.build_dir = self.root / "build"
        self.build_dir.mkdir()
        self.focus_bin = self.build_dir / "mac_game_focus"

    def tearDown(self) -> None:
        shutil.rmtree(self.root, ignore_errors=True)

    def _helper_context(self, *extra):
        from contextlib import ExitStack
        stack = ExitStack()
        stack.enter_context(patch.object(live, "FOCUS_SOURCE", self.source))
        stack.enter_context(patch.object(live, "BUILD", self.build_dir))
        stack.enter_context(patch.object(live, "FOCUS", self.focus_bin))
        for item in extra:
            stack.enter_context(item)
        return stack

    def test_compiler_nonzero_exit_is_nonfatal(self) -> None:
        def fake_compile(command, **kwargs):
            partial = Path(command[-2])
            partial.write_bytes(b"partial")
            return subprocess.CompletedProcess(command, 1, stderr="compile error")

        with self._helper_context(patch.object(live.subprocess, "run", fake_compile)):
            path, error = live._try_build_focus_helper()
        self.assertIsNone(path)
        self.assertIsNotNone(error)
        self.assertIn("focus build failed", error)

    def test_compiler_timeout_is_nonfatal(self) -> None:
        def fake_compile(command, **kwargs):
            raise subprocess.TimeoutExpired(cmd=command, timeout=live.FOCUS_BUILD_TIMEOUT_S)

        with self._helper_context(patch.object(live.subprocess, "run", fake_compile)):
            path, error = live._try_build_focus_helper()
        self.assertIsNone(path)
        self.assertEqual(error, "focus build timed out")

    def test_failure_cleanup_unlink_error_does_not_escape(self) -> None:
        partial_calls = 0
        real_unlink = Path.unlink

        def fake_unlink(self, missing_ok=False):
            nonlocal partial_calls
            if self.name.startswith(".mac_game_focus.partial"):
                partial_calls += 1
                if partial_calls >= 2:
                    raise OSError("cleanup failed")
            return real_unlink(self, missing_ok=missing_ok)

        def fake_compile(command, **kwargs):
            Path(command[-2]).write_bytes(b"x")
            return subprocess.CompletedProcess(command, 1, stderr="compile error")

        with self._helper_context(
                patch.object(live.subprocess, "run", fake_compile),
                patch.object(Path, "unlink", fake_unlink)):
            path, error = live._try_build_focus_helper()
        self.assertIsNone(path)
        self.assertIn("focus build failed", error or "")


if __name__ == "__main__":
    unittest.main()
