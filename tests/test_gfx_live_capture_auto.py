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


class GfxLiveAutoCliTests(unittest.TestCase):
    def test_auto_venice_rejects_save_combination(self) -> None:
        with self.assertRaises(live.LiveError):
            live.run(Path("/tmp/out"), save=Path("/tmp/x.eu4"), authorized=True,
                     auto_venice=True)

    def test_auto_venice_requires_authorization(self) -> None:
        with self.assertRaises(live.LiveError):
            live.run(Path("/tmp/out"), authorized=False, auto_venice=True)


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

        def terminate_side_effect(game, focus_bin):
            if game is not None:
                game.mark_exited()

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
            stack.enter_context(patch.object(
                live, "wait_for_venice_ready",
                return_value={"predicates": {}, "counters": {}}))
            stack.enter_context(patch.object(base, "running_game_pid", return_value=99))
            stack.enter_context(patch.object(base, "sha256", side_effect=sha_effect))
            stack.enter_context(patch.object(live, "_arm_and_seal_capture"))
            stack.enter_context(patch.object(
                live, "_terminate_owned_game", side_effect=terminate_side_effect))
            stack.enter_context(patch.object(base, "GOG_LAUNCHER", self.launcher_path))
            report = stack.enter_context(report_mock)

            fixture = fixture_cls.return_value.__enter__.return_value
            fixture.recover.return_value = False
            fixture.install.return_value = working
            fixture.journal = journal
            fixture.finish.side_effect = finish_side_effect

            output = self.root / output_name
            if expect_report:
                result = live.run(output, authorized=True, auto_venice=True)
            else:
                with self.assertRaises(live.LiveError) as ctx:
                    live.run(output, authorized=True, auto_venice=True)
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
        }

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
        journal = self.root / "journal-cleanup"
        journal.write_text("keep", encoding="utf-8")

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
        self.assertEqual(outcome["report_calls"], 0)


if __name__ == "__main__":
    unittest.main()
