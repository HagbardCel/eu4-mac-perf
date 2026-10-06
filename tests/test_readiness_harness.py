#!/usr/bin/env python3
import datetime as dt
import plistlib
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "benchmark"))
import autonomous_runner as runner  # noqa: E402
import submission_experiment as submission_exp  # noqa: E402


class ReadinessHarnessTests(unittest.TestCase):
    def test_cpu_samples_between_ignores_future_samples(self) -> None:
        anchor = {"monotonic_ns": 0, "wall_ns": 0}
        start = dt.datetime(2026, 1, 1, tzinfo=dt.timezone.utc)
        raw = []
        for second in range(1, 21):
            raw.append(
                {
                    "timestamp": start + dt.timedelta(seconds=second),
                    "tasks": [{"pid": 42, "cputime_ms_per_s": 1000.0 if second <= 10 else 5000.0}],
                }
            )
        eval_ns = 10_000_000_000
        bounded = runner.cpu_samples_between(raw, 42, anchor, 0, eval_ns)
        self.assertTrue(all(when <= eval_ns for when, _ in bounded))
        values = [v for _, v in bounded]
        self.assertTrue(all(v == 1000.0 for v in values))

    def test_readiness_predicates_pass_without_stability_gates(self) -> None:
        rows = [
            {
                "monotonic_ns": 9_000_000_000 + i,
                "swaps_s": 60.0,
                "paused_swaps": 60,
                "swaps": 60,
            }
            for i in range(10)
        ]
        preds = runner.readiness_predicates(
            logged=True,
            frontmost=True,
            pointer_interior=True,
            rows=rows,
            now_ns=10_000_000_000,
            power=[(i, 1000.0 + (i % 3) * 200) for i in range(10)],
        )
        self.assertTrue(preds["ready_to_pass"])
        self.assertFalse(preds["power_stable"])

    def test_ensure_game_focus_re_parks(self) -> None:
        calls: list[str] = []

        def fake_focus(action: str, _pid: int) -> bool:
            calls.append(action)
            if action == "check":
                return True
            if action == "interior":
                return len([c for c in calls if c == "interior"]) >= 2
            return True

        with patch.object(runner, "focus", side_effect=fake_focus):
            state = runner.ensure_game_focus_interior(42)
        self.assertTrue(state.interior)
        self.assertTrue(state.parked)

    def test_wait_until_ready_records_wait_diag_on_timeout(self) -> None:
        class FakeGame:
            pid = 42

            def poll(self):
                return None

        wait_diag: dict = {}
        rows = [
            {
                "monotonic_ns": 9_000_000_000 + i,
                "swaps_s": 60.0,
                "paused_swaps": 60,
                "swaps": 60,
            }
            for i in range(10)
        ]
        tick = {"t": 0.0}

        def mono() -> float:
            tick["t"] += 0.5
            return tick["t"]

        with patch.object(runner, "READY_TIMEOUT_S", 1), \
             patch.object(runner.time, "monotonic", side_effect=mono), \
             patch.object(runner, "probe_rows", return_value=rows), \
             patch.object(runner, "cpu_samples_between", return_value=[(0, 1000.0)] * 10), \
             patch.object(runner, "fresh_game_log", return_value=""), \
             patch.object(runner, "game_log_ready", return_value=False), \
             patch.object(runner, "ensure_game_focus_interior",
                          return_value=runner.FocusState(True, False, False, False)), \
             patch.object(runner.time, "sleep"):
            with self.assertRaises(runner.base.BenchmarkError):
                runner.wait_until_ready(
                    FakeGame(), Path("x"), runner.PowerTail(Path("y")), {"monotonic_ns": 0, "wall_ns": 0},
                    Path("log"), b"", wait_diag=wait_diag,
                )
        self.assertIn("last_predicates", wait_diag)
        self.assertIn("counters", wait_diag)

    def test_phase_run_error_fields(self) -> None:
        err = submission_exp.PhaseRunError("b2", "settle", "lost focus")
        self.assertEqual(err.phase, "b2")
        self.assertEqual(err.stage, "settle")


if __name__ == "__main__":
    unittest.main()
