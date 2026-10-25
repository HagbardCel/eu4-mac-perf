#!/usr/bin/env python3
"""Portable tests for PR-D auto-Venice acquisition."""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
BENCH = ROOT / "benchmark"
sys.path.insert(0, str(BENCH))

import autonomous_runner as auto  # noqa: E402
import eu4_benchmark as base  # noqa: E402
import eu4_gfx_live_capture as live  # noqa: E402

VENICE_LOG = "\n".join([
    "Human Player set as primary local",
    "Venice",
    "Launching SINGLEPLAYER-game",
    "Start-date: 1444.11.11",
    "End RestoreDeviceObjects",
])

LAUNCHER_JSON = {
    "exePath": "./eu4.app/Contents/MacOS/eu4",
    "exeArgs": ["-enabletelemetry"],
}


def _paused_rows(now_ns: int, count: int = 10) -> list[dict]:
    rows = []
    for index in range(count):
        rows.append({
            "monotonic_ns": now_ns - (count - index) * 100_000_000,
            "swaps": 100,
            "paused_swaps": 96,
            "swaps_s": 30.0,
        })
    rows[-1]["monotonic_ns"] = now_ns - 500_000
    return rows


class VeniceReadinessEvaluatorTests(unittest.TestCase):
    def test_ready_when_all_predicates_hold(self) -> None:
        now_ns = 10_000_000_000
        rows = _paused_rows(now_ns)
        predicates = live.evaluate_venice_readiness(
            logged=True, frontmost=True, pointer_interior=True,
            rows=rows, now_ns=now_ns)
        self.assertTrue(predicates["ready_to_pass"])

    def test_not_ready_with_stale_log(self) -> None:
        predicates = live.evaluate_venice_readiness(
            logged=False, frontmost=True, pointer_interior=True,
            rows=_paused_rows(10_000_000_000), now_ns=10_000_000_000)
        self.assertFalse(predicates["ready_to_pass"])

    def test_not_ready_without_probe_rows(self) -> None:
        predicates = live.evaluate_venice_readiness(
            logged=True, frontmost=True, pointer_interior=True,
            rows=[], now_ns=10_000_000_000)
        self.assertFalse(predicates["ready_to_pass"])

    def test_not_ready_when_unpaused(self) -> None:
        now_ns = 10_000_000_000
        rows = _paused_rows(now_ns)
        rows[-1]["paused_swaps"] = 10
        predicates = live.evaluate_venice_readiness(
            logged=True, frontmost=True, pointer_interior=True,
            rows=rows, now_ns=now_ns)
        self.assertFalse(predicates["paused_rows_ok"])

    def test_game_log_markers_require_fresh_venice_session(self) -> None:
        self.assertTrue(auto.game_log_ready(VENICE_LOG))
        self.assertFalse(auto.game_log_ready("Venice only"))


class WaitForVeniceReadyDeadlineTests(unittest.TestCase):
    class _FakeMonotonic:
        """Advances past the readiness deadline before the success decision."""

        def __init__(self, timeout_s: float) -> None:
            self._start = 100.0
            self._deadline = self._start + timeout_s
            self._calls = 0

        def __call__(self) -> float:
            self._calls += 1
            if self._calls <= 3:
                return self._start
            return self._deadline

    def test_ready_predicates_after_deadline_raise_timeout(self) -> None:
        clock = self._FakeMonotonic(live.READY_TIMEOUT_S)
        diagnostics: dict = {}
        ready = {"ready_to_pass": True}

        class FakeGame:
            pid = 42

            def poll(self):
                return None

        with patch.object(live.time, "monotonic", clock), \
             patch.object(live.time, "sleep"), \
             patch.object(live, "_record_focus_snapshot"), \
             patch.object(live, "_maybe_record_focus_snapshot"), \
             patch.object(live, "_bounded_focus_state", return_value=(True, True)), \
             patch.object(auto, "fresh_game_log", return_value=""), \
             patch.object(auto, "probe_rows", return_value=[]), \
             patch.object(live, "evaluate_venice_readiness", return_value=ready):
            with self.assertRaises(live.LiveError) as ctx:
                live.wait_for_venice_ready(
                    FakeGame(), live.FOCUS, Path("/tmp/probe.csv"),
                    {}, Path("/tmp/game.log"), b"", diagnostics=diagnostics)
        self.assertIn(live.VENICE_READY_TIMEOUT_MSG, str(ctx.exception))
        self.assertTrue(diagnostics.get("timed_out"))


class _ReadinessFakeClock:
    """Advances past the readiness deadline when the loop sleeps."""

    def __init__(self) -> None:
        self.now = 100.0

    def monotonic(self) -> float:
        return self.now

    def sleep(self, _seconds: float) -> None:
        self.now += _seconds + live.READY_TIMEOUT_S


class WaitForVeniceReadyGateTests(unittest.TestCase):
    def _wait(
        self,
        *,
        require_focus: bool,
        frontmost: bool,
        interior: bool,
        logged: bool = True,
        rows: list[dict] | None = None,
        poll_result=None,
        diagnostics: dict | None = None,
        advance_on_sleep: bool = False,
        record_snapshots: bool = False,
    ):
        now_ns = 10_000_000_000
        if rows is None:
            rows = _paused_rows(now_ns)
        if diagnostics is None:
            diagnostics = {}

        class FakeGame:
            pid = 42

            def poll(self):
                return poll_result

        clock = _ReadinessFakeClock()
        patches = [
            patch.object(live.time, "monotonic_ns", return_value=now_ns),
            patch.object(live, "_bounded_focus_state",
                          return_value=(frontmost, interior)),
            patch.object(live, "_observe_focus_state",
                          return_value=(frontmost, interior)),
            patch.object(auto, "fresh_game_log",
                         return_value=VENICE_LOG if logged else ""),
            patch.object(auto, "probe_rows", return_value=rows),
        ]
        if advance_on_sleep:
            patches.extend([
                patch.object(live.time, "monotonic", clock.monotonic),
                patch.object(live.time, "sleep", clock.sleep),
            ])
        else:
            patches.append(patch.object(live.time, "sleep"))
        if not record_snapshots:
            patches.extend([
                patch.object(live, "_record_focus_snapshot"),
                patch.object(live, "_maybe_record_focus_snapshot"),
            ])
        for item in patches:
            item.start()
        try:
            return live.wait_for_venice_ready(
                FakeGame(), live.FOCUS, Path("/tmp/probe.csv"),
                {}, Path("/tmp/game.log"), b"", diagnostics=diagnostics,
                require_focus=require_focus)
        finally:
            for item in reversed(patches):
                item.stop()

    def test_strict_ready_requires_focus_and_pointer(self) -> None:
        result = self._wait(
            require_focus=True, frontmost=True, interior=True)
        self.assertTrue(result["selected_gate_ready"])
        self.assertTrue(result["strict_ready"])

    def test_strict_not_ready_when_focus_false(self) -> None:
        diagnostics: dict = {}
        with patch.object(
            live, "evaluate_venice_readiness",
            wraps=live.evaluate_venice_readiness) as evaluate:
            with self.assertRaises(live.LiveError):
                self._wait(require_focus=True, frontmost=False, interior=False,
                           diagnostics=diagnostics, advance_on_sleep=True)
        evaluate.assert_called()
        self.assertFalse(diagnostics["selected_gate_ready"])
        self.assertFalse(diagnostics["strict_ready"])

    def test_unfocused_ready_when_log_probe_ok_without_focus(self) -> None:
        result = self._wait(
            require_focus=False, frontmost=False, interior=False)
        self.assertTrue(result["selected_gate_ready"])
        self.assertFalse(result["strict_ready"])
        self.assertTrue(result["venice_log_probe_ready"])

    def test_unfocused_not_ready_without_log_markers(self) -> None:
        diagnostics: dict = {}
        with patch.object(
            live, "evaluate_venice_readiness",
            wraps=live.evaluate_venice_readiness) as evaluate:
            with self.assertRaises(live.LiveError):
                self._wait(require_focus=False, frontmost=False, interior=False,
                           logged=False, diagnostics=diagnostics,
                           advance_on_sleep=True)
        evaluate.assert_called()
        self.assertFalse(diagnostics["selected_gate_ready"])
        self.assertFalse(diagnostics["venice_log_probe_ready"])

    def test_unfocused_not_ready_with_bad_probe(self) -> None:
        rows = _paused_rows(10_000_000_000)
        rows[-1]["paused_swaps"] = 0
        diagnostics: dict = {}
        with patch.object(
            live, "evaluate_venice_readiness",
            wraps=live.evaluate_venice_readiness) as evaluate:
            with self.assertRaises(live.LiveError):
                self._wait(require_focus=False, frontmost=False, interior=False,
                           rows=rows, diagnostics=diagnostics,
                           advance_on_sleep=True)
        evaluate.assert_called()
        self.assertFalse(diagnostics["selected_gate_ready"])
        self.assertFalse(diagnostics["venice_log_probe_ready"])

    def test_timeout_preserves_initial_focus_snapshot(self) -> None:
        initial_snapshot = {
            "frontmost_bundle_id": "com.apple.Terminal",
            "check_rc": 4,
        }
        diagnostics: dict = {}
        inspect_calls = 0

        def fake_inspect(_focus_bin, _pid, _timeout):
            nonlocal inspect_calls
            inspect_calls += 1
            if inspect_calls == 1:
                return initial_snapshot, None
            return None, "should not run after deadline"

        with patch.object(live, "_focus_inspect", side_effect=fake_inspect):
            with self.assertRaises(live.LiveError):
                self._wait(require_focus=False, frontmost=False, interior=False,
                           logged=False, diagnostics=diagnostics,
                           advance_on_sleep=True, record_snapshots=True)
        self.assertTrue(diagnostics.get("timed_out"))
        self.assertEqual(
            diagnostics["last_focus_snapshot"]["snapshot"],
            initial_snapshot)
        self.assertEqual(inspect_calls, 1)

    def test_unfocused_uses_observe_not_bounded(self) -> None:
        focus_actions: list[str] = []

        def track_focus_command(_focus_bin, action, _pid, _timeout):
            focus_actions.append(action)
            return False

        with patch.object(live, "_bounded_focus_state") as bounded, \
             patch.object(live.time, "sleep"), \
             patch.object(live.time, "monotonic_ns",
                          return_value=10_000_000_000), \
             patch.object(live, "_record_focus_snapshot"), \
             patch.object(live, "_maybe_record_focus_snapshot"), \
             patch.object(live, "_focus_command", side_effect=track_focus_command), \
             patch.object(auto, "fresh_game_log", return_value=VENICE_LOG), \
             patch.object(auto, "probe_rows",
                          return_value=_paused_rows(10_000_000_000)):

            class FakeGame:
                pid = 42

                def poll(self):
                    return None

            live.wait_for_venice_ready(
                FakeGame(), live.FOCUS, Path("/tmp/p"), {}, Path("/tmp/g"), b"",
                require_focus=False)
        bounded.assert_not_called()
        self.assertIn("check", focus_actions)
        self.assertFalse(any(a in ("activate", "park") for a in focus_actions))


class GfxLiveAutoCliTests(unittest.TestCase):
    def test_auto_venice_rejects_save_combination(self) -> None:
        with self.assertRaises(live.LiveError):
            live.run(Path("/tmp/out"), save=Path("/tmp/x.eu4"), authorized=True,
                     auto_venice=True)

    def test_auto_venice_requires_authorization(self) -> None:
        with self.assertRaises(live.LiveError):
            live.run(Path("/tmp/out"), authorized=False, auto_venice=True)

    def test_preflight_rejects_save_with_auto_venice(self) -> None:
        with self.assertRaises(live.LiveError) as ctx:
            live.preflight(save=Path("/tmp/x.eu4"), auto_venice=True)
        self.assertIn("--auto-venice cannot be combined with --save", str(ctx.exception))

    def test_allow_unfocused_requires_auto_venice(self) -> None:
        with self.assertRaises(live.LiveError) as ctx:
            live.run(Path("/tmp/out"), authorized=True,
                     allow_unfocused_venice=True)
        self.assertIn("--allow-unfocused-venice requires --auto-venice",
                      str(ctx.exception))


class GfxLiveAutoOrchestrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp(prefix="gfx-live-auto-"))
        self.launcher_path = self.root / "launcher.json"
        self.launcher_path.write_text(json.dumps(LAUNCHER_JSON), encoding="utf-8")

    def tearDown(self) -> None:
        import shutil
        shutil.rmtree(self.root, ignore_errors=True)

    def _evidence(self) -> dict:
        return {
            "fixture_sha256": "abc",
            "gog": {},
            "descriptor_sha256": "d",
            "descriptor_policy": "p",
            "code_window_sha256": "w",
            "patch_site_sha256": "p",
            "library": str(live.LIBRARY),
            "library_sha256": "l",
            "source_sha256": {},
            "probe_library": str(live.PROBE),
            "probe_library_sha256": "probe",
        }

    def _fake_game(self):
        class FakeGame:
            pid = 99
            _exited = False

            def poll(self):
                return 0 if self._exited else None

            def mark_exited(self):
                self._exited = True

        return FakeGame()

    def _run_auto(
        self,
        output_name: str = "out",
        *,
        expect_report: bool = True,
        popen_side_effect=None,
        sha256_side_effect=None,
        finish_side_effect=None,
        wait_side_effect=None,
        terminate_side_effect=None,
        allow_unfocused=False,
    ):
        env_holder: dict = {}
        argv_holder: list = []
        fake_game = self._fake_game()
        working = self.root / f"working-{output_name}.eu4"
        working.write_bytes(b"fixture")
        journal = self.root / f"journal-{output_name}"
        journal.unlink(missing_ok=True)
        report_mock = patch.object(live, "report", return_value={"status": "accepted"})

        def capture_popen(argv, **kwargs):
            argv_holder.extend(argv)
            env_holder.update(kwargs.get("env", {}))
            return fake_game

        def default_terminate(game, focus_bin):
            if game is not None:
                game.mark_exited()

        terminate_effect = (
            terminate_side_effect if terminate_side_effect is not None
            else default_terminate)

        def fake_install():
            journal.write_text("staged", encoding="utf-8")
            return working

        popen_effect = popen_side_effect or capture_popen
        sha_effect = sha256_side_effect or (lambda _path: "abc")
        if finish_side_effect is None:
            def finish_side_effect(_run_dir):
                journal.unlink(missing_ok=True)
                working.unlink(missing_ok=True)
                return False

        with ExitStack() as stack:
            stack.enter_context(patch.object(
                live, "_require_focus_helper", return_value=live.FOCUS))
            stack.enter_context(patch.object(
                live, "preflight", return_value=self._evidence()))
            fixture_cls = stack.enter_context(patch.object(live, "FixtureManager"))
            stack.enter_context(patch.object(
                live.subprocess, "Popen", side_effect=popen_effect))
            stack.enter_context(patch.object(live, "_wait_for", return_value=True))
            stack.enter_context(patch.object(
                live, "_verify_startup_attestation", return_value={}))
            if wait_side_effect is None:
                stack.enter_context(patch.object(
                    live, "wait_for_venice_ready",
                    return_value={"predicates": {}, "counters": {}}))
            else:
                stack.enter_context(patch.object(
                    live, "wait_for_venice_ready", side_effect=wait_side_effect))
            stack.enter_context(patch.object(base, "running_game_pid", return_value=99))
            stack.enter_context(patch.object(base, "sha256", side_effect=sha_effect))
            arm = stack.enter_context(patch.object(live, "_arm_and_seal_capture"))
            stack.enter_context(patch.object(
                live, "_terminate_owned_game", side_effect=terminate_effect))
            stack.enter_context(patch.object(base, "GOG_LAUNCHER", self.launcher_path))
            report = stack.enter_context(report_mock)

            fixture = fixture_cls.return_value.__enter__.return_value
            fixture.recover.return_value = False
            fixture.install.side_effect = fake_install
            fixture.journal = journal
            fixture.finish.side_effect = finish_side_effect

            output = self.root / output_name
            if expect_report:
                result = live.run(
                    output, authorized=True, auto_venice=True,
                    allow_unfocused_venice=allow_unfocused)
            else:
                with self.assertRaises(live.LiveError) as ctx:
                    live.run(
                        output, authorized=True, auto_venice=True,
                        allow_unfocused_venice=allow_unfocused)
                result = ctx.exception

        run_dir = next(output.glob("*-gfx-live"), None)
        metadata = {}
        if run_dir is not None:
            metadata = json.loads(
                (run_dir / "metadata.json").read_text(encoding="utf-8"))
        return {
            "result": result,
            "argv": argv_holder,
            "env": env_holder,
            "metadata": metadata,
            "report_calls": report.call_count,
            "finish_call_count": fixture.finish.call_count,
            "arm_call_count": arm.call_count,
            "journal": journal,
        }

    def test_allow_unfocused_venice_records_mode_and_arms(self) -> None:
        def wait_payload(*_args, **_kwargs):
            self.assertFalse(_kwargs.get("require_focus", True))
            return {
                "predicates": {"ready_to_pass": False},
                "counters": {},
                "require_focus": False,
                "strict_ready": False,
                "venice_log_probe_ready": True,
                "selected_gate_ready": True,
            }

        outcome = self._run_auto(
            output_name="out-unfocused",
            allow_unfocused=True,
            wait_side_effect=wait_payload,
        )
        self.assertEqual(
            outcome["metadata"]["readiness_mode"], "allow_unfocused_venice")
        self.assertEqual(outcome["arm_call_count"], 1)

    def test_dual_injection_and_continuelastsave(self) -> None:
        outcome = self._run_auto()
        self.assertEqual(outcome["result"]["status"], "accepted")
        self.assertEqual(
            outcome["env"]["DYLD_INSERT_LIBRARIES"],
            f"{live.PROBE}:{live.LIBRARY}",
        )
        self.assertEqual(
            outcome["argv"],
            ["./eu4", "-enabletelemetry", "--continuelastsave"],
        )
        self.assertEqual(outcome["metadata"]["status"], "captured")
        self.assertTrue(outcome["metadata"]["continue_game_restored"])
        self.assertTrue(outcome["metadata"]["fixture_cleanup_completed"])
        self.assertEqual(outcome["report_calls"], 1)

    def test_save_hash_failure_restores_without_capture(self) -> None:
        def sha_fail(_path):
            raise OSError("hash failed")

        outcome = self._run_auto(
            sha256_side_effect=sha_fail,
            expect_report=False,
        )
        meta = outcome["metadata"]
        self.assertEqual(meta["status"], "incomplete")
        self.assertIn("save_hash_error", meta)
        self.assertTrue(meta["continue_game_restored"])
        self.assertNotIn("save_after_sha256", meta)
        self.assertEqual(outcome["report_calls"], 0)

    def test_launch_failure_restores_continuation(self) -> None:
        def popen_fail(*_args, **_kwargs):
            raise OSError("launch failed")

        outcome = self._run_auto(
            output_name="out-launch-fail",
            popen_side_effect=popen_fail,
            expect_report=False,
        )
        meta = outcome["metadata"]
        self.assertFalse(meta.get("game_launched", True))
        self.assertFalse(meta["game_exited"])
        self.assertTrue(meta["continue_game_restored"])
        self.assertEqual(meta["status"], "incomplete")
        self.assertEqual(outcome["report_calls"], 0)

    def test_cleanup_incomplete_skips_report(self) -> None:
        def finish_leave_journal(_run_dir):
            return False

        outcome = self._run_auto(
            output_name="out-cleanup",
            expect_report=False,
            finish_side_effect=finish_leave_journal,
        )
        meta = outcome["metadata"]
        self.assertEqual(meta["status"], "incomplete")
        self.assertFalse(meta["fixture_cleanup_completed"])
        self.assertTrue(outcome["journal"].is_file())
        self.assertEqual(outcome["finish_call_count"], 1)
        self.assertEqual(outcome["report_calls"], 0)

    def test_game_never_exits_retains_journal_without_finish(self) -> None:
        outcome = self._run_auto(
            output_name="out-stuck",
            expect_report=False,
            terminate_side_effect=lambda _game, _focus: None,
        )
        meta = outcome["metadata"]
        self.assertEqual(outcome["finish_call_count"], 0)
        self.assertTrue(outcome["journal"].is_file())
        self.assertIn("cleanup_error", meta)
        self.assertFalse(meta["game_exited"])
        self.assertEqual(meta["status"], "incomplete")
        self.assertEqual(outcome["report_calls"], 0)

    def test_readiness_timeout_skips_arm_and_persists_diagnostics(self) -> None:
        def wait_timeout(
            _game, _focus, _probe, _anchor, _log, _old, diagnostics=None,
            **_kwargs,
        ):
            if diagnostics is not None:
                diagnostics.update({
                    "timed_out": True,
                    "last_predicates": {"ready_to_pass": False},
                    "counters": {"activation_attempts": 2},
                })
            raise live.LiveError(
                f"{live.VENICE_READY_TIMEOUT_MSG}: {{'ready_to_pass': False}}")

        outcome = self._run_auto(
            output_name="out-ready-timeout",
            expect_report=False,
            wait_side_effect=wait_timeout,
        )
        meta = outcome["metadata"]
        self.assertEqual(outcome["arm_call_count"], 0)
        self.assertEqual(outcome["finish_call_count"], 1)
        self.assertFalse(outcome["journal"].is_file())
        self.assertIn("readiness_diagnostics", meta)
        self.assertTrue(meta["readiness_diagnostics"].get("timed_out"))
        self.assertEqual(outcome["report_calls"], 0)

    def test_focus_inspect_parses_json_stdout(self) -> None:
        payload = {
            "target_pid": 42,
            "target_registered": False,
            "check_rc": 2,
            "frontmost_pid": 1,
            "frontmost_bundle_id": "com.apple.Terminal",
        }
        completed = subprocess.CompletedProcess(
            [], 0, stdout=json.dumps(payload).encode("utf-8"), stderr=b"")
        with patch.object(live.subprocess, "run", return_value=completed):
            result, error = live._focus_inspect(live.FOCUS, 42, 1.0)
        self.assertIsNone(error)
        self.assertEqual(result["check_rc"], 2)


if __name__ == "__main__":
    unittest.main()
