"""PR 1 acceptance: event-driven waits, profiles, and verified stalls."""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from automation import gfx_wait  # noqa: E402


class FakeClock:
    def __init__(self) -> None:
        self.now_value = 0.0

    def now(self) -> float:
        return self.now_value

    def sleep(self, seconds: float) -> None:
        self.now_value += max(0.0, seconds)


def profile(base: float, hard: float, idle: float, poll: float = 1.0) -> gfx_wait.WaitProfile:
    return gfx_wait.WaitProfile("test", base_seconds=base, hard_seconds=hard,
                                idle_seconds=idle, poll_seconds=poll)


class WaitTests(unittest.TestCase):
    def setUp(self):
        self.clock = FakeClock()

    def wait(self, conditions, *, prof=None, **kwargs):
        return gfx_wait.wait_for(
            conditions,
            wait_profile=prof or profile(10, 100, 4),
            clock=self.clock.now,
            sleeper=self.clock.sleep,
            **kwargs,
        )

    def test_immediate_condition(self):
        result = self.wait({"checkpoint": lambda: True})
        self.assertEqual(result.status, gfx_wait.MET)
        self.assertEqual(result.matched, "checkpoint")
        self.assertEqual(result.polls, 1)

    def test_abort_wins_over_success(self):
        result = self.wait(
            {"checkpoint": lambda: False},
            aborts={"gpu_error": lambda: True},
        )
        self.assertEqual(result.status, gfx_wait.ABORTED)
        self.assertEqual(result.matched, "gpu_error")

    def test_process_exit_is_not_a_stall(self):
        result = self.wait({"checkpoint": lambda: False}, is_alive=lambda: False)
        self.assertEqual(result.status, gfx_wait.EXITED)

    def test_progress_extends_the_stage_deadline(self):
        # The requested checkpoint arrives only at t=25, after the base 10 s.
        def condition():
            return self.clock.now() >= 25

        def progress():
            return gfx_wait.Progress("loading", "frames", 0.0,
                                     counter=float(int(self.clock.now() // 5)))

        result = self.wait(
            {"settled": condition},
            prof=profile(10, 100, 6),
            progress=progress,
            is_alive=lambda: True,
        )
        self.assertEqual(result.status, gfx_wait.MET)
        self.assertGreater(result.elapsed_seconds, 20)
        self.assertGreater(result.progress_events, 1)

    def test_stall_requires_observed_progress_then_silence(self):
        def progress():
            if self.clock.now() < 1:
                return gfx_wait.Progress("loading", "frames", 0.0, counter=1.0)
            return gfx_wait.Progress("loading", "frames", 0.0, counter=1.0)

        result = self.wait(
            {"never": lambda: False},
            prof=profile(10, 100, 3),
            progress=progress,
            is_alive=lambda: True,
        )
        self.assertEqual(result.status, gfx_wait.STALL)
        self.assertIn("no progress", result.reason)
        self.assertLess(result.elapsed_seconds, 10)

    def test_no_progress_ever_is_a_deadline_not_a_stall(self):
        result = self.wait(
            {"never": lambda: False},
            prof=profile(5, 10, 2),
            progress=lambda: None,
            is_alive=lambda: True,
        )
        self.assertEqual(result.status, gfx_wait.DEADLINE)
        self.assertEqual(result.progress_events, 0)

    def test_hard_cap_is_not_extended_forever(self):
        def progress():
            return gfx_wait.Progress("loading", "frames", 0.0,
                                     counter=float(int(self.clock.now())))

        result = self.wait(
            {"never": lambda: False},
            prof=profile(5, 12, 10),
            progress=progress,
            is_alive=lambda: True,
        )
        self.assertEqual(result.status, gfx_wait.DEADLINE)
        self.assertLessEqual(result.elapsed_seconds, 13.0)

    def test_a_broken_probe_cannot_be_hidden_by_a_later_success(self):
        result = self.wait({
            "broken": lambda: (_ for _ in ()).throw(RuntimeError("probe broke")),
            "checkpoint": lambda: True,
        })
        self.assertEqual(result.status, gfx_wait.ERROR)
        self.assertIn("probe broke", result.reason)

    def test_condition_error_is_reported(self):
        def broken():
            raise RuntimeError("probe broke")

        result = self.wait({"checkpoint": broken}, prof=profile(2, 4, 1))
        self.assertEqual(result.status, gfx_wait.ERROR)
        self.assertIn("probe broke", result.reason)

    def test_requires_a_condition(self):
        with self.assertRaises(ValueError):
            gfx_wait.wait_for({}, wait_profile=profile(1, 2, 1))

    def test_profile_validation(self):
        with self.assertRaises(ValueError):
            gfx_wait.profile("nope")
        with self.assertRaises(ValueError):
            gfx_wait.WaitProfile("x", base_seconds=-1, hard_seconds=2, idle_seconds=1)
        with self.assertRaises(ValueError):
            gfx_wait.WaitProfile("x", base_seconds=5, hard_seconds=2, idle_seconds=1)

    def test_profiles_exist_and_are_bounded(self):
        for name in (gfx_wait.PROFILE_EARLY, gfx_wait.PROFILE_TARGETED,
                     gfx_wait.PROFILE_QUALIFICATION):
            prof = gfx_wait.profile(name)
            self.assertLessEqual(prof.base_seconds, prof.hard_seconds)
            self.assertGreater(prof.idle_seconds, 0)
            self.assertLess(prof.poll_seconds, prof.idle_seconds)

    def test_file_progress_tracks_real_appends(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            tracker = gfx_wait.FileProgress(root, patterns=("frame-observe.jsonl",))
            self.assertIsNone(tracker.snapshot())
            (root / "frame-observe.jsonl").write_text('{"frame":1}\n', encoding="utf-8")
            first = tracker.snapshot()
            self.assertIsNotNone(first)
            self.assertGreater(first.counter, 0)
            again = tracker.snapshot()
            self.assertEqual(first.counter, again.counter)
            with (root / "frame-observe.jsonl").open("a", encoding="utf-8") as target:
                target.write('{"frame":2}\n')
            second = tracker.snapshot()
            self.assertGreater(second.counter, first.counter)

    def test_file_progress_ignores_empty_files(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "frame-observe.jsonl").touch()
            self.assertIsNone(gfx_wait.FileProgress(root).snapshot())


if __name__ == "__main__":
    unittest.main()
