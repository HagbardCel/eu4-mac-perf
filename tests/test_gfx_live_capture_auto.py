#!/usr/bin/env python3
"""Portable tests for PR-D auto-Venice acquisition."""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
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

    def tearDown(self) -> None:
        import shutil
        shutil.rmtree(self.root, ignore_errors=True)

    def test_dual_injection_and_continuelastsave(self) -> None:
        env_holder: dict = {}
        argv_holder: list = []

        class FakeGame:
            pid = 99

            def poll(self):
                return None

        def capture_popen(argv, **kwargs):
            argv_holder.extend(argv)
            env_holder.update(kwargs.get("env", {}))
            return FakeGame()

        evidence = {
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

        working = self.root / "working.eu4"
        working.write_bytes(b"fixture")
        with patch.object(live, "_require_focus_helper", return_value=live.FOCUS), \
             patch.object(live, "preflight", return_value=evidence), \
             patch.object(live, "FixtureManager") as fixture_cls, \
             patch.object(live.subprocess, "Popen", side_effect=capture_popen), \
             patch.object(live, "_wait_for", return_value=True), \
             patch.object(live, "_verify_startup_attestation", return_value={}), \
             patch.object(live, "wait_for_venice_ready",
                          return_value={"predicates": {}, "counters": {}}), \
             patch.object(base, "running_game_pid", return_value=99), \
             patch.object(base, "sha256", return_value="abc"), \
             patch.object(live, "_arm_and_seal_capture"), \
             patch.object(live, "_terminate_owned_game", return_value=True), \
             patch.object(live, "report",
                          return_value={"status": "accepted"}):
            fixture = fixture_cls.return_value.__enter__.return_value
            fixture.recover.return_value = False
            fixture.install.return_value = working
            fixture.finish.return_value = False
            fixture.journal = self.root / "journal"
            fixture.journal.unlink(missing_ok=True)
            result = live.run(self.root / "out", authorized=True, auto_venice=True)
        self.assertEqual(result["status"], "accepted")

        self.assertIn("--continuelastsave", argv_holder)
        injected = env_holder["DYLD_INSERT_LIBRARIES"]
        self.assertIn(str(live.PROBE), injected)
        self.assertIn(str(live.LIBRARY), injected)


if __name__ == "__main__":
    unittest.main()
