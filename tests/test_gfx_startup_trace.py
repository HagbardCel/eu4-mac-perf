#!/usr/bin/env python3
import json
import os
import platform
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "benchmark"))

import eu4_gfx_startup_trace as startup  # noqa: E402
from automation import live_gfx, m6_frame  # noqa: E402


@unittest.skipUnless(sys.platform == "darwin", "startup trace host is macOS x86_64")
class GfxStartupTraceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.host = startup.build_host()

    def _run(self, scenario: str, directory: Path) -> subprocess.CompletedProcess:
        command = [str(self.host), scenario, str(directory)]
        if platform.machine() != "x86_64":
            command = ["arch", "-x86_64", *command]
        return subprocess.run(command, capture_output=True, text=True, check=False)

    def test_patch_bytes_match_pinned_windows(self):
        evidence = startup.preflight()
        self.assertEqual(evidence["settings_patch"], startup.PATCH_BYTES.hex())
        self.assertEqual(evidence["init_patch"], startup.PATCH_BYTES.hex())
        self.assertEqual(sum(startup.PATCH_LENGTHS), len(startup.PATCH_BYTES))

    def test_basic_complete_trace(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            result = self._run("basic", directory)
            self.assertEqual(result.returncode, 0, result.stderr or result.stdout)
            events = (directory / "events.jsonl").read_text().strip().splitlines()
            self.assertEqual(len(events), 4)
            parsed = [json.loads(line) for line in events]
            self.assertEqual([e["seq"] for e in parsed], [1, 2, 3, 4])
            self.assertEqual(parsed[0]["site"], "settings")
            self.assertEqual(parsed[0]["phase"], "entry")
            self.assertEqual(parsed[1]["phase"], "exit")
            self.assertEqual(parsed[0]["call_id"], parsed[1]["call_id"])
            self.assertEqual(parsed[0]["sample_count"], 1)
            self.assertEqual(parsed[0]["sample_captured"], 1)
            self.assertEqual(parsed[0]["sample_values"], [2])
            self.assertEqual(parsed[1]["sample_flags"], [1])
            self.assertEqual(parsed[2]["site"], "init")
            self.assertEqual(parsed[2]["call_id"], parsed[3]["call_id"])
            self.assertEqual(parsed[3]["success"], 1)
            self.assertTrue(parsed[3]["master_non_null"])
            self.assertTrue(parsed[3]["gl_context_present"])
            summary = json.loads((directory / "summary.json").read_text())
            self.assertTrue(summary["complete"])
            self.assertEqual(summary["settings"], "observed")
            self.assertEqual(summary["qualifying_init"], 1)
            self.assertTrue(summary["events_copied"])

    def test_failed_init_does_not_qualify(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            result = self._run("fail-init", directory)
            self.assertEqual(result.returncode, 0, result.stderr or result.stdout)
            summary = json.loads((directory / "summary.json").read_text())
            self.assertFalse(summary["complete"])
            self.assertEqual(summary["qualifying_init"], 0)

    def test_drain_refuse_keeps_complete(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            result = self._run("drain-refuse", directory)
            self.assertEqual(result.returncode, 0, result.stderr or result.stdout)
            summary = json.loads((directory / "summary.json").read_text())
            self.assertTrue(summary["complete"])
            self.assertGreaterEqual(summary["refused"], 1)
            self.assertEqual(summary["unmatched"], 0)

    def test_pause_drain_stays_inflight(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            result = self._run("pause-drain", directory)
            self.assertEqual(result.returncode, 0, result.stderr or result.stdout)
            summary = json.loads((directory / "summary.json").read_text())
            self.assertFalse(summary["complete"])
            self.assertEqual(summary["unmatched"], 0)
            self.assertEqual(summary["settings_calls"], 1)

    def test_tls_full_releases_inflight(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            result = self._run("tls-full", directory)
            self.assertEqual(result.returncode, 0, result.stderr or result.stdout)
            summary = json.loads((directory / "summary.json").read_text())
            self.assertEqual(summary["unmatched"], 0)
            self.assertEqual(summary["in_flight"], 0)
            self.assertGreaterEqual(summary["refused"], 1)

    def test_null_settings_incomplete(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            result = self._run("null-settings", directory)
            self.assertEqual(result.returncode, 0, result.stderr or result.stdout)
            summary = json.loads((directory / "summary.json").read_text())
            self.assertFalse(summary["complete"])
            self.assertGreaterEqual(summary["unavailable"], 1)

    def test_oversize_samples_incomplete(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            result = self._run("oversize", directory)
            self.assertEqual(result.returncode, 0, result.stderr or result.stdout)
            summary = json.loads((directory / "summary.json").read_text())
            self.assertFalse(summary["complete"])
            events = [
                json.loads(line)
                for line in (directory / "events.jsonl").read_text().splitlines()
                if line.strip()
            ]
            entry = next(
                e for e in events if e["site"] == "settings" and e["phase"] == "entry"
            )
            self.assertEqual(entry["sample_count"], 65)
            self.assertEqual(entry["sample_captured"], 0)
            self.assertEqual(entry["sample_values"], [])
            self.assertEqual(entry["sample_flags"], [])

    def test_dual_finalize_same_result(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            result = self._run("dual-finalize", directory)
            self.assertEqual(result.returncode, 0, result.stderr or result.stdout)
            summary = json.loads((directory / "summary.json").read_text())
            self.assertTrue(summary["complete"])

    def test_unsealed_finalize_diagnostic(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            result = self._run("unsealed-finalize", directory)
            self.assertEqual(result.returncode, 0, result.stderr or result.stdout)
            summary = json.loads((directory / "summary.json").read_text())
            self.assertFalse(summary["complete"])
            self.assertFalse(summary["events_copied"])
            self.assertEqual(summary["reason"], "drain_timeout")
            self.assertEqual(summary["event_count"], 0)
            events = (directory / "events.jsonl").read_text().strip()
            self.assertEqual(events, "")

    def test_write_fail_no_complete_summary(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            result = self._run("write-fail", directory)
            self.assertEqual(result.returncode, 0, result.stderr or result.stdout)
            self.assertFalse((directory / "summary.json").exists())

    def test_parallel_seq(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            result = self._run("parallel-seq", directory)
            self.assertEqual(result.returncode, 0, result.stderr or result.stdout)
            events = [
                json.loads(line)
                for line in (directory / "events.jsonl").read_text().splitlines()
                if line.strip()
            ]
            seqs = [e["seq"] for e in events]
            self.assertEqual(seqs, sorted(seqs))
            self.assertEqual(len(seqs), len(set(seqs)))

    def test_bad_bytes_refuse_install(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = self._run("bad-bytes", Path(tmp))
            self.assertEqual(result.returncode, 0, result.stderr or result.stdout)

    def test_unsafe_read_marks_incomplete(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            result = self._run("unsafe-read", directory)
            self.assertEqual(result.returncode, 0, result.stderr or result.stdout)
            summary = json.loads((directory / "summary.json").read_text())
            self.assertFalse(summary["complete"])
            self.assertGreaterEqual(summary["unavailable"], 1)

    def test_authorize_live_required(self):
        with self.assertRaises(startup.StartupError):
            startup.run(authorize_live=False)

    def test_authorized_launch_preserves_configured_args_and_environment(self):
        """Regression: run() must reach Popen without NameError.

        The integration change that appended ``game_args`` to the launch
        command also referenced ``env_extra`` without declaring either in the
        signature.  The launch path is exercised here with preflight, the
        lease, process creation and sleep mocked; no game is started.
        """
        from unittest import mock

        observed: dict = {}

        class FakeProcess:
            pid = 4242

            def __init__(self):
                self.alive = True

            def poll(self):
                return None if self.alive else 0

            def terminate(self):
                observed["terminated"] = True
                self.alive = False

            def wait(self, timeout=None):
                return 0

            def kill(self):
                observed["killed"] = True
                self.alive = False

        class FakeLease:
            def child_fds(self):
                return []

            def release(self):
                observed["released"] = True

        def fake_popen(command, **kwargs):
            observed["command"] = command
            observed["env"] = dict(kwargs["env"])
            observed["cwd"] = kwargs["cwd"]
            run_dir = Path(kwargs["env"]["EU4_GFX_STARTUP_DIR"])
            (run_dir / "startup.status").write_text(
                "state=installed\npid=4242\nerror=\n"
            )
            (run_dir / "summary.json").write_text(
                json.dumps({"complete": True, "events": 1})
            )
            return FakeProcess()

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            launcher = root / "launcher.json"
            launcher.write_text(
                json.dumps(
                    {
                        "exePath": "./eu4.app/Contents/MacOS/eu4",
                        "exeArgs": ["-fixed", "value"],
                    }
                )
            )
            executable = root / "eu4.app" / "Contents" / "MacOS" / "eu4"
            executable.parent.mkdir(parents=True)
            executable.write_text("")
            evidence = {
                "executable_sha256": "a" * 64,
                "settings_patch": startup.PATCH_BYTES.hex(),
                "init_patch": startup.PATCH_BYTES.hex(),
                "library_sha256": "b" * 64,
            }
            with (
                mock.patch.object(startup, "ROOT", root),
                mock.patch.object(startup, "preflight", return_value=evidence),
                mock.patch.object(startup.base, "GOG_LAUNCHER", launcher),
                mock.patch.object(startup.base, "GOG_EXE", executable),
                mock.patch.object(
                    startup.launch_lease, "acquire", return_value=FakeLease()
                ),
                mock.patch.object(startup.subprocess, "Popen", side_effect=fake_popen),
                mock.patch.object(startup.time, "sleep", return_value=None),
            ):
                report = startup.run(
                    authorize_live=True,
                    env_extra={"EU4_GFX_FRAME_TRACE_BOUNDARY": "scene"},
                    game_args=["--continuelastsave", "-debug"],
                )

        self.assertEqual(
            observed["command"],
            ["./eu4", "-fixed", "value", "--continuelastsave", "-debug"],
        )
        self.assertEqual(
            observed["env"]["EU4_GFX_FRAME_TRACE_BOUNDARY"], "scene"
        )
        self.assertEqual(
            observed["env"]["DYLD_INSERT_LIBRARIES"], str(startup.LIBRARY)
        )
        self.assertNotIn("EU4_METAL_BOOT_MODE", observed["env"])
        self.assertEqual(observed["cwd"], executable.parent)
        self.assertTrue(observed["released"])
        self.assertTrue(report["accepted"])
        self.assertEqual(report["pid"], 4242)


class TerminalWaitTests(unittest.TestCase):
    class Game:
        def poll(self):
            return None

    def test_terminal_predicate_ends_the_wait_before_the_deadline(self):
        import time
        with tempfile.TemporaryDirectory() as tmp:
            started = time.monotonic()
            reason = startup._wait_pair(
                Path(tmp), self.Game(), time.monotonic() + 30,
                terminal=lambda: True)
            elapsed = time.monotonic() - started
        self.assertEqual(reason, "terminal")
        self.assertLess(elapsed, 2.0)

    def test_no_terminal_record_keeps_the_original_wait(self):
        import time
        with tempfile.TemporaryDirectory() as tmp:
            started = time.monotonic()
            reason = startup._wait_pair(
                Path(tmp), self.Game(), time.monotonic() + 0.4,
                terminal=lambda: False)
            elapsed = time.monotonic() - started
        self.assertEqual(reason, "deadline")
        self.assertLess(elapsed, 2.0)


class ShadowTerminalRecordTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.run = Path(self.temporary.name) / "run"
        self.shadow = self.run / "shadow"
        self.shadow.mkdir(parents=True)
        self.capture_id = "capture-a"
        self.other_id = "capture-b"

    def tearDown(self):
        self.temporary.cleanup()

    def terminal(self, document, *, capture_id=None, run=None,
                 continuous=False, max_frames=1):
        path = self.shadow / "coordinator.json"
        if document is None:
            if path.exists():
                path.unlink()
        else:
            path.write_text(
                document if isinstance(document, str) else json.dumps(document))
        return m6_frame._shadow_terminal(
            self.shadow,
            capture_id=(capture_id or self.capture_id),
            run=(run or self.run),
            continuous=continuous,
            max_frames=max_frames)

    def single(self, **overrides):
        record = {
            "schema": "m7-shadow-coordinator/v1",
            "run": str(self.run.resolve()),
            "capture_id": self.capture_id,
            "status": "ok",
            "seal": {"capture_frame_id": 7},
            "pixel_verdict": "divergent",
        }
        record.update(overrides)
        return record

    def continuous(self, frames, *, status="ok", max_frames=2, **overrides):
        record = {
            "schema": "m7-shadow-coordinator/v1",
            "mode": "continuous",
            "run": str(self.run.resolve()),
            "capture_id": self.capture_id,
            "max_frames": max_frames,
            "status": status,
            "frames": [{"sequence": value} for value in frames],
            "stop_reason": "frame_limit",
        }
        record.update(overrides)
        return record

    def test_partial_or_running_records_are_not_terminal(self):
        self.assertFalse(self.terminal(None))
        self.assertFalse(self.terminal("{partial"))
        self.assertFalse(self.terminal('{"schema": '))
        self.assertFalse(self.terminal({
            "schema": "m7-shadow-coordinator/v1", "status": "running",
            "run": str(self.run.resolve()),
            "capture_id": self.capture_id}))
        self.assertFalse(self.terminal({
            "schema": "other", "status": "ok",
            "run": str(self.run.resolve()),
            "capture_id": self.capture_id}))

    def test_stale_record_from_another_capture_is_rejected(self):
        self.assertFalse(self.terminal(self.single(capture_id=self.other_id)))
        self.assertFalse(self.terminal(self.single(capture_id="")))
        self.assertFalse(self.terminal(
            self.single(run=str((self.run / "other").resolve()))))
        # A stale failure record is rejected for exactly the same binding.
        self.assertFalse(self.terminal({
            "schema": "m7-shadow-coordinator/v1",
            "run": str(self.run.resolve()),
            "capture_id": self.other_id,
            "status": "failed",
            "error": "gpu"}))

    def test_bound_completed_record_is_terminal(self):
        self.assertTrue(self.terminal(self.single()))
        self.assertTrue(self.terminal(self.single(pixel_verdict=None,
                                                  classification="match")))
        self.assertTrue(self.terminal({
            "schema": "m7-shadow-coordinator/v1",
            "run": str(self.run.resolve()),
            "capture_id": self.capture_id,
            "status": "failed",
            "error": "gpu"}))
        self.assertFalse(self.terminal(
            self.single(seal={}, pixel_verdict=None, classification=None)))

    def test_bound_continuous_record_requires_a_contiguous_prefix(self):
        self.assertTrue(self.terminal(
            self.continuous([1, 2]), continuous=True, max_frames=2))
        self.assertTrue(self.terminal(
            self.continuous([1], max_frames=3), continuous=True,
            max_frames=3))
        self.assertFalse(self.terminal(
            self.continuous([2]), continuous=True, max_frames=2))
        self.assertFalse(self.terminal(
            self.continuous([2, 3]), continuous=True, max_frames=3))

    def test_a_later_frame_does_not_settle_an_earlier_capture(self):
        # The capture requested one frame; a record produced for frame 2, or a
        # sequence that runs past the requested boundary, is not its result.
        self.assertFalse(self.terminal(
            self.continuous([2]), continuous=True, max_frames=1))
        self.assertFalse(self.terminal(
            self.continuous([1, 2]), continuous=True, max_frames=1))
        self.assertFalse(self.terminal(
            self.continuous([1], max_frames=2), continuous=True,
            max_frames=1))
        self.assertTrue(self.terminal(
            self.continuous([1], max_frames=1), continuous=True,
            max_frames=1))

    def test_coordinator_failure_record_carries_the_capture_identity(self):
        args = m6_frame._parser().parse_args([
            "shadow-coordinator", "--run", str(self.run),
            "--capture-id", self.capture_id, "--timeout", "0"])
        status = m6_frame.shadow_coordinator(args)
        record = json.loads(
            (self.shadow / "coordinator.json").read_text())
        self.assertEqual(status, 1)
        self.assertEqual(record["status"], "failed")
        self.assertEqual(record["capture_id"], self.capture_id)
        self.assertEqual(record["run"], str(self.run.resolve()))
        self.assertTrue(m6_frame._shadow_terminal(
            self.shadow, capture_id=self.capture_id, run=self.run,
            continuous=False, max_frames=None))
        self.assertFalse(m6_frame._shadow_terminal(
            self.shadow, capture_id=self.other_id, run=self.run,
            continuous=False, max_frames=None))

    def test_mode_mismatch_is_rejected(self):
        self.assertFalse(self.terminal(
            self.continuous([1, 2]), continuous=False, max_frames=2))
        self.assertFalse(self.terminal(
            self.single(mode="continuous"), continuous=False))


class _FakeGame:
    def __init__(self, pid=424242, *, exits_on_terminate=True,
                 exits_on_kill=True, terminate_error=None):
        self.pid = pid
        self.alive = True
        self.returncode = None
        self.terminate_calls = 0
        self.wait_calls = 0
        self.kill_calls = 0
        self.exits_on_terminate = exits_on_terminate
        self.exits_on_kill = exits_on_kill
        self.terminate_error = terminate_error

    def poll(self):
        return None if self.alive else self.returncode

    def terminate(self):
        self.terminate_calls += 1
        if self.terminate_error is not None:
            raise self.terminate_error
        if self.exits_on_terminate:
            self.alive = False
            self.returncode = 0

    def wait(self, timeout=None):
        self.wait_calls += 1
        if self.alive:
            raise subprocess.TimeoutExpired("eu4", timeout)
        return self.returncode

    def kill(self):
        self.kill_calls += 1
        if self.exits_on_kill:
            self.alive = False
            self.returncode = -9


class _FakeLease:
    def __init__(self, error=None, release_error=None):
        self.error = error
        self.release_error = release_error
        self.release_calls = 0
        self.released = False

    def child_fds(self):
        return ()

    def release(self):
        self.release_calls += 1
        if self.error is not None:
            raise self.error
        self.released = True


class _FakeDisplay:
    def __init__(self, restore=None, error=None):
        self.restore_result = restore if restore is not None else {
            "restored": True}
        self.error = error
        self.restore_calls = 0

    env = {}
    placement_library = "fake-placement.dylib"
    isolation_library = None

    def as_dict(self):
        return {"session": "fake-display"}

    def restore(self):
        self.restore_calls += 1
        if self.error is not None:
            raise self.error
        return self.restore_result


class RunOpsCleanupTests(unittest.TestCase):
    """Cleanup is independent, reported, and part of the operation outcome."""

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.run_index = 0
        self.output = self.root / "run"
        self.launcher = self.root / "launcher.json"
        self.launcher.write_text(json.dumps({"exeArgs": []}))
        self.evidence = {
            "executable_sha256": "a" * 64,
            "library_sha256": "b" * 64,
            "ops": [],
        }

    def tearDown(self):
        self.temporary.cleanup()

    def run_ops(self, *, game=None, lease=None, display=None, complete=False,
                terminal=None):
        self.run_index += 1
        self.output = self.root / f"run-{self.run_index}"
        game = game or _FakeGame()
        lease = lease or _FakeLease()

        def popen(*_args, **_kwargs):
            self.output.mkdir(parents=True, exist_ok=True)
            (self.output / "startup.status").write_text(
                f"state=installed\npid={game.pid}\n")
            if complete:
                (self.output / "summary.json").write_text(
                    json.dumps({"complete": True}))
                (self.output / "operations-summary.json").write_text(
                    json.dumps({"integrity_ok": True,
                                "records_copied": True, "events": 1}))
            return game

        patches = [
            mock.patch.object(startup, "preflight_ops",
                              return_value=self.evidence),
            mock.patch("automation.test_profiles.ensure_live_launch",
                       return_value=None),
            mock.patch.object(startup.base, "GOG_LAUNCHER", self.launcher),
            mock.patch.object(startup.launch_lease, "acquire",
                              return_value=lease),
            mock.patch.object(startup.subprocess, "Popen", side_effect=popen),
        ]
        if display is not None:
            patches.append(mock.patch(
                "automation.gfx_display.activate_background",
                return_value=display))
        for patcher in patches:
            patcher.start()
        try:
            return startup.run_ops(
                targets=[], max_events=1, duration_s=0.1, timeout_s=1,
                output=self.output, authorize_live=True,
                evidence=self.evidence,
                background_plan=(object() if display is not None else None),
                terminal_result=(terminal if terminal is not None
                                 else (lambda: True)))
        finally:
            for patcher in reversed(patches):
                patcher.stop()

    def test_wait_exception_still_reports_cleanup_failures(self):
        game = _FakeGame(exits_on_terminate=False, exits_on_kill=False)
        lease = _FakeLease(error=RuntimeError("lease close failed"))
        with mock.patch.object(
                startup, "_wait_pair",
                side_effect=startup.StartupError("wait failed")):
            with self.assertRaises(startup.StartupError) as raised:
                self.run_ops(game=game, lease=lease,
                             terminal=lambda: False)
        written = json.loads(
            (self.output / "launcher-report.json").read_text())
        self.assertEqual(written["outcome"], "failed")
        self.assertFalse(written["cleanup_ok"])
        self.assertTrue(written["cleanup_errors"])
        self.assertEqual(written["accepted"], False)
        self.assertTrue(getattr(raised.exception, "cleanup_errors", []))

    def test_normal_completion_terminates_and_releases(self):
        game = _FakeGame()
        lease = _FakeLease()
        display = _FakeDisplay({"restored": True})
        report = self.run_ops(game=game, lease=lease, display=display,
                              complete=True, terminal=lambda: False)
        self.assertTrue(report["cleanup_ok"])
        self.assertEqual(report["cleanup_errors"], [])
        self.assertFalse(report["process_alive"])
        self.assertTrue(report["accepted"])
        self.assertEqual(report["outcome"], "ok")
        self.assertTrue(lease.released)
        self.assertEqual(display.restore_calls, 1)
        self.assertFalse(game.alive)
        self.assertEqual(game.wait_calls, 1)

    def test_terminal_shadow_path_cleans_up(self):
        game = _FakeGame()
        lease = _FakeLease()
        report = self.run_ops(game=game, lease=lease, terminal=lambda: True)
        self.assertEqual(report["wait_reason"], "terminal")
        self.assertTrue(report["terminal_shadow"])
        self.assertTrue(report["cleanup_ok"])
        self.assertFalse(report["process_alive"])
        self.assertTrue(lease.released)
        self.assertFalse(game.alive)

    def test_game_exit_path_cleans_up(self):
        game = _FakeGame()
        game.alive = False
        game.returncode = 0
        lease = _FakeLease()
        report = self.run_ops(game=game, lease=lease, terminal=lambda: False)
        self.assertEqual(report["wait_reason"], "game_exit")
        self.assertTrue(report["cleanup_ok"])
        self.assertFalse(report["process_alive"])
        self.assertTrue(lease.released)

    def test_failed_termination_is_not_success(self):
        game = _FakeGame(exits_on_terminate=False, exits_on_kill=False)
        lease = _FakeLease()
        report = self.run_ops(game=game, lease=lease, complete=True,
                              terminal=lambda: False)
        self.assertFalse(report["cleanup_ok"])
        self.assertFalse(report["accepted"])
        self.assertEqual(report["outcome"], "failed")
        self.assertTrue(report["process_alive"])
        self.assertTrue(any("still alive" in item
                            for item in report["cleanup_errors"]))
        # The lease was still attempted and released despite the live process.
        self.assertTrue(lease.released)
        self.assertTrue(game.alive)
        self.assertGreaterEqual(game.terminate_calls, 1)

    def test_failed_display_restoration_is_not_success(self):
        for display in (
            _FakeDisplay({"restored": False, "error": "backup corrupt"}),
            _FakeDisplay(error=RuntimeError("restore exploded")),
        ):
            with self.subTest(display=display):
                lease = _FakeLease()
                report = self.run_ops(
                    game=_FakeGame(), lease=lease, display=display,
                    complete=True, terminal=lambda: False)
                self.assertFalse(report["cleanup_ok"])
                self.assertFalse(report["accepted"])
                self.assertEqual(report["outcome"], "failed")
                self.assertTrue(any("display restore" in item
                                    for item in report["cleanup_errors"]))
                self.assertEqual(display.restore_calls, 1)
                self.assertTrue(lease.released)
                self.assertFalse(report["process_alive"])

    def test_failed_lease_release_is_not_success(self):
        lease = _FakeLease(error=RuntimeError("close failed"))
        display = _FakeDisplay({"restored": True})
        report = self.run_ops(game=_FakeGame(), lease=lease, display=display,
                              complete=True, terminal=lambda: False)
        self.assertFalse(report["cleanup_ok"])
        self.assertFalse(report["accepted"])
        self.assertEqual(report["outcome"], "failed")
        self.assertTrue(any("lease release" in item
                            for item in report["cleanup_errors"]))
        self.assertFalse(lease.released)
        self.assertEqual(lease.release_calls, 1)
        self.assertEqual(display.restore_calls, 1)
        self.assertFalse(report["process_alive"])

    def test_all_cleanup_failures_are_attempted_independently(self):
        game = _FakeGame(terminate_error=RuntimeError("terminate failed"))
        display = _FakeDisplay(error=RuntimeError("restore failed"))
        lease = _FakeLease(error=RuntimeError("release failed"))
        report = self.run_ops(game=game, lease=lease, display=display,
                              terminal=lambda: True)
        joined = " | ".join(report["cleanup_errors"])
        self.assertIn("terminate", joined)
        self.assertIn("display restore", joined)
        self.assertIn("lease release", joined)
        self.assertEqual(game.terminate_calls, 1)
        self.assertEqual(display.restore_calls, 1)
        self.assertEqual(lease.release_calls, 1)
        self.assertFalse(report["cleanup_ok"])
        self.assertFalse(report["accepted"])
        self.assertEqual(report["outcome"], "failed")


class CaptureCleanupOutcomeTests(unittest.TestCase):
    """The capture report and exit status inherit cleanup/terminal failures."""

    def capture(self, output, report, *, shadow_record=None):
        def fake_run_ops(**kwargs):
            Path(kwargs["output"]).mkdir(parents=True, exist_ok=True)
            if shadow_record is not None:
                shadow = Path(kwargs["output"]) / "shadow"
                shadow.mkdir(parents=True, exist_ok=True)
                (shadow / "coordinator.json").write_text(
                    json.dumps(shadow_record))
            return dict(report)

        class Coordinator:
            def communicate(self, timeout=None):
                return "", ""

            def poll(self):
                return 0

        args = m6_frame._parser().parse_args(
            ["capture", "--output", str(output)]
            + (["--shadow"] if shadow_record is not None else []))
        patches = [
            mock.patch.object(startup, "preflight_ops", return_value={}),
            mock.patch.object(startup, "run_ops", side_effect=fake_run_ops),
            mock.patch.object(m6_frame, "load_trace", return_value={}),
            mock.patch.object(m6_frame, "coverage_report",
                              return_value={"ok": True}),
            mock.patch.object(m6_frame, "load_seal", return_value={
                "capture_frame_id": 1, "session_id": "s", "pid": 4242}),
            mock.patch.object(m6_frame.subprocess, "Popen",
                              return_value=Coordinator()),
        ]
        for patcher in patches:
            patcher.start()
        try:
            return m6_frame.capture(args)
        finally:
            for patcher in reversed(patches):
                patcher.stop()

    def report(self, **overrides):
        value = {
            "session": "session",
            "pid": 4242,
            "display": None,
            "cleanup_ok": True,
            "cleanup_errors": [],
            "process_alive": False,
            "termination": {"pid": 4242, "terminated": True, "alive": False},
            "lease": {"released": True},
        }
        value.update(overrides)
        return value

    def test_live_capture_status_rejects_cleanup_failure(self):
        status = live_gfx._capture_status({
            "cleanup_ok": False,
            "cleanup_errors": ["lease release: close failed"],
            "status": {"state": "installed"},
        })
        self.assertEqual(status[0], "unavailable")
        self.assertIn("cleanup_failed", status[1])
        self.assertIn("close failed", status[1])

    def test_cleanup_failure_makes_the_capture_fail(self):
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "run"
            status = self.capture(output, self.report(
                cleanup_ok=False,
                cleanup_errors=["terminate: owned process 4242 is still alive"],
                process_alive=True))
            written = json.loads(
                (output / "capture-report.json").read_text())
        self.assertEqual(status, 8)
        self.assertEqual(written["outcome"], "failed")
        self.assertFalse(written["cleanup"]["ok"])
        self.assertIn("still alive", written["cleanup_failure"])

    def test_cleanup_failure_does_not_mask_a_primary_failure(self):
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "run"

            def fake_run_ops(**kwargs):
                Path(kwargs["output"]).mkdir(parents=True, exist_ok=True)
                return self.report(
                    cleanup_ok=False,
                    cleanup_errors=["lease release: close failed"])

            patches = [
                mock.patch.object(startup, "preflight_ops", return_value={}),
                mock.patch.object(startup, "run_ops",
                                  side_effect=fake_run_ops),
                mock.patch.object(m6_frame, "load_trace", return_value={}),
                mock.patch.object(m6_frame, "coverage_report",
                                  return_value={"ok": False}),
            ]
            for patcher in patches:
                patcher.start()
            try:
                args = m6_frame._parser().parse_args(
                    ["capture", "--output", str(output)])
                status = m6_frame.capture(args)
            finally:
                for patcher in reversed(patches):
                    patcher.stop()
            written = json.loads(
                (output / "capture-report.json").read_text())
        self.assertEqual(status, 5)
        self.assertEqual(written["outcome"], "failed")
        self.assertIn("cleanup failed", written["cleanup_failure"])

    def test_stale_coordinator_record_is_not_consumed(self):
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "run"
            stale = {
                "schema": "m7-shadow-coordinator/v1",
                "run": str(output.resolve()),
                "capture_id": "another-capture",
                "status": "ok",
                "seal": {"capture_frame_id": 1},
                "pixel_verdict": "divergent",
            }
            status = self.capture(output, self.report(),
                                  shadow_record=stale)
            written = json.loads(
                (output / "capture-report.json").read_text())
        self.assertEqual(status, 7)
        self.assertEqual(written["outcome"], "failed")
        self.assertIn("no bound terminal report", written["shadow_error"])
        self.assertNotIn("shadow", written)

    def test_failed_coordinator_record_is_terminal_but_not_success(self):
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "run"
            seen = {}

            class Coordinator:
                def communicate(self, timeout=None):
                    return "", ""

                def poll(self):
                    return 0

            def fake_run_ops(**kwargs):
                Path(kwargs["output"]).mkdir(parents=True, exist_ok=True)
                return self.report()

            def fake_popen(command, *_args, **_kwargs):
                capture_id = command[command.index("--capture-id") + 1]
                seen["capture_id"] = capture_id
                shadow = Path(command[command.index("--shadow-dir") + 1])
                shadow.mkdir(parents=True, exist_ok=True)
                (shadow / "coordinator.json").write_text(json.dumps({
                    "schema": "m7-shadow-coordinator/v1",
                    "run": str(output.resolve()),
                    "capture_id": capture_id,
                    "status": "failed",
                    "error": "gpu_execution_failure",
                }))
                return Coordinator()

            patches = [
                mock.patch.object(startup, "preflight_ops", return_value={}),
                mock.patch.object(startup, "run_ops",
                                  side_effect=fake_run_ops),
                mock.patch.object(m6_frame, "load_trace", return_value={}),
                mock.patch.object(m6_frame, "coverage_report",
                                  return_value={"ok": True}),
                mock.patch.object(m6_frame, "load_seal", return_value={
                    "capture_frame_id": 1, "session_id": "s", "pid": 4242}),
                mock.patch.object(m6_frame.subprocess, "Popen",
                                  side_effect=fake_popen),
            ]
            for patcher in patches:
                patcher.start()
            try:
                args = m6_frame._parser().parse_args(
                    ["capture", "--output", str(output), "--shadow"])
                status = m6_frame.capture(args)
            finally:
                for patcher in reversed(patches):
                    patcher.stop()
            written = json.loads(
                (output / "capture-report.json").read_text())
        self.assertEqual(status, 7)
        self.assertEqual(written["outcome"], "failed")
        self.assertTrue(seen["capture_id"])
        self.assertIn("gpu_execution_failure", written["shadow_error"])
        self.assertEqual(written["shadow"]["status"], "failed")
