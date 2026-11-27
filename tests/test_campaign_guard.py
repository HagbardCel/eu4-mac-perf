"""Tests for campaign modes, host activation, stops and deadlines."""

from __future__ import annotations

import contextlib
import json
import os
import sys
import tempfile
import threading
import time
import types
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from automation import (campaign_guard, gfx_independent_replay,  # noqa: E402
                        launch_lease, gfx_frame)

AUTONOMOUS = campaign_guard.MODE_AUTONOMOUS
CONTROLLED = campaign_guard.MODE_CONTROLLED
RATIONALE = {
    "question": "Does the sampler explain event-15?",
    "evidence": ["."],
    "gap": "sampler state was not isolated",
    "hypothesis": "Metal sampler state differs",
    "action": "targeted replay",
    "expected_information": "confirm or refute",
    "cheaper_alternatives_checked": True,
}


class GuardTestCase(unittest.TestCase):
    """Isolate the state file and the host-wide activation marker."""

    def setUp(self):
        base = ROOT / ".local" / "test-runs"
        base.mkdir(parents=True, exist_ok=True)
        self.temporary = tempfile.TemporaryDirectory(dir=base)
        self.root = Path(self.temporary.name)
        self.path = self.root / "campaign-state.json"
        self.markers = self.root / "markers"
        self.marker = mock.patch.object(
            campaign_guard, "_marker_path",
            return_value=self.markers / campaign_guard.MARKER_NAME)
        self.marker.start()
        self.addCleanup(self.marker.stop)
        os.environ.pop("EU4_CAMPAIGN_STATE", None)
        self.guard = campaign_guard.Guard(self.path)

    def tearDown(self):
        self.temporary.cleanup()

    def init(self, mode=CONTROLLED, **kwargs):
        return self.guard.init(campaign_id="pilot", mode=mode, **kwargs)


class GuardTests(GuardTestCase):
    def test_init_records_mode_and_no_budget(self):
        state = self.init()
        self.assertEqual(state["schema"], campaign_guard.SCHEMA)
        self.assertEqual(state["mode"], CONTROLLED)
        self.assertTrue(state["live_capture"])
        self.assertFalse(state["auto_merge"])
        self.assertIsNone(state["deadline_epoch"])
        for retired in ("budgets", "counts", "failures", "blocked_actions",
                        "reservations", "exhausted"):
            self.assertNotIn(retired, state)

    def test_init_refuses_an_unknown_mode(self):
        with self.assertRaises(campaign_guard.CampaignRefusal) as raised:
            self.guard.init(mode="whatever")
        self.assertEqual(raised.exception.kind, "invalid_mode")

    def test_init_records_an_operator_deadline(self):
        state = self.init(deadline_seconds=60)
        self.assertGreater(state["deadline_epoch"], time.time())
        self.assertGreater(self.guard.status()["seconds_remaining"], 0)

    def test_a_second_init_does_not_replace_the_state(self):
        self.init()
        with self.assertRaises(campaign_guard.CampaignRefusal) as raised:
            self.init()
        self.assertEqual(raised.exception.kind, "state_exists")

    def test_ensure_creates_then_matches_the_same_mode(self):
        created = self.guard.ensure(campaign_id="pilot", mode=AUTONOMOUS,)
        self.assertTrue(created["created"])
        again = self.guard.ensure(campaign_id="pilot", mode=AUTONOMOUS,)
        self.assertFalse(again["created"])

    def test_ensure_refuses_to_change_an_active_campaign_mode(self):
        self.init(mode=AUTONOMOUS)
        with self.assertRaises(campaign_guard.CampaignRefusal) as raised:
            self.guard.ensure(campaign_id="pilot", mode=CONTROLLED,)
        self.assertEqual(raised.exception.kind, "mode_conflict")
        self.assertEqual(self.guard.state()["mode"], AUTONOMOUS)

    def test_ensure_preserves_a_stopped_state(self):
        self.init(mode=AUTONOMOUS)
        self.guard.stop("environment_unavailable")
        resumed = self.guard.ensure(campaign_id="pilot", mode=AUTONOMOUS,)
        self.assertFalse(resumed["created"])
        self.assertEqual(resumed["stop"]["reason"], "environment_unavailable")

    def test_ensure_holds_the_state_lock_across_the_unused_state_check(self):
        self.init()
        entered = threading.Event()
        release = threading.Event()

        def hold_lock():
            with self.guard._locked():
                entered.set()
                release.wait(5)

        holder = threading.Thread(target=hold_lock)
        holder.start()
        try:
            self.assertTrue(entered.wait(5))
            finished = threading.Event()
            failure = []

            def run_ensure():
                try:
                    self.guard.ensure(campaign_id="pilot", mode=CONTROLLED)
                except Exception as exc:  # surfaced after the join
                    failure.append(exc)
                finished.set()

            worker = threading.Thread(target=run_ensure)
            worker.start()
            # An unlocked read/check/write would finish while the lock is held
            # and could overwrite concurrent state updates.
            self.assertFalse(finished.wait(0.2))
        finally:
            release.set()
            holder.join(5)
        worker.join(5)
        self.assertTrue(finished.is_set())
        self.assertEqual(failure, [])

    def test_the_deadline_starts_at_launch_and_is_not_extended_by_resume(self):
        self.init(mode=AUTONOMOUS)
        first = self.guard.ensure(campaign_id="pilot", mode=AUTONOMOUS,
                                  deadline_seconds=600)
        epoch = first["deadline_epoch"]
        time.sleep(0.01)
        second = self.guard.ensure(campaign_id="pilot", mode=AUTONOMOUS,
                                   deadline_seconds=600)
        self.assertEqual(second["deadline_epoch"], epoch)

    def test_status_names_the_policy_and_deadline(self):
        self.init(mode=AUTONOMOUS)
        status = self.guard.status()
        self.assertEqual(status["schema"], campaign_guard.SCHEMA)
        self.assertEqual(status["mode"], AUTONOMOUS)
        self.assertFalse(status["live_capture"])
        self.assertIsNone(status["stop"])

    def test_a_corrupt_state_is_refused(self):
        self.path.write_text("{not json", encoding="utf-8")
        with self.assertRaises(campaign_guard.CampaignRefusal) as raised:
            self.guard.status()
        self.assertEqual(raised.exception.kind, "state_missing")

    def test_stop_reasons_are_structured_and_audited(self):
        self.init()
        self.guard.stop("invalid_evidence", "the seal is not authentic")
        status = self.guard.status()
        self.assertEqual(status["stop"]["reason"], "invalid_evidence")
        self.assertEqual(self.guard.state()["history"][-1]["action"], "stop")
        with self.assertRaises(campaign_guard.CampaignRefusal) as raised:
            self.guard.stop("budget_exhausted", "obsolete reason")
        self.assertEqual(raised.exception.kind, "unknown_stop_reason")

    def test_an_operator_resume_clears_only_the_stop(self):
        self.init()
        self.guard.stop("environment_unavailable", "no Metal device")
        state = self.guard.resume(detail="device restored")
        self.assertIsNone(state["stop"])
        self.assertEqual(state["history"][-1]["action"], "resume")

    def test_an_expired_deadline_refuses_new_work(self):
        self.init(deadline_seconds=0.05)
        time.sleep(0.1)
        with self.assertRaises(campaign_guard.CampaignRefusal) as raised:
            campaign_guard.Guard(self.path).require()
        self.assertEqual(raised.exception.kind, "deadline")


def _marker_file(markers: Path) -> Path:
    return markers / campaign_guard.MARKER_NAME


class ActivationTests(GuardTestCase):
    def marker_file(self):
        return self.markers / campaign_guard.MARKER_NAME

    def test_init_publishes_a_host_wide_marker(self):
        self.init()
        self.assertTrue(self.marker_file().is_file())
        value = json.loads(self.marker_file().read_text(encoding="utf-8"))
        self.assertEqual(value["schema"], campaign_guard.MARKER_SCHEMA)
        self.assertEqual(value["state"], str(self.path))

    def test_clearing_the_environment_does_not_disable_the_guard(self):
        self.init(mode=AUTONOMOUS)
        os.environ.pop("EU4_CAMPAIGN_STATE", None)
        self.assertIsNotNone(campaign_guard.from_env())
        with self.assertRaises(campaign_guard.CampaignRefusal):
            campaign_guard.require_live_campaign()

    def test_an_explicit_environment_path_cannot_override_the_active_policy(self):
        other = self.root / "other-state.json"
        other_guard = campaign_guard.Guard(other)
        other_guard.init(mode=CONTROLLED)
        other_guard.deactivate()
        self.init()
        with mock.patch.dict(os.environ, {"EU4_CAMPAIGN_STATE": str(other)}):
            guard = campaign_guard.from_env()
        self.assertIsNotNone(guard)
        self.assertEqual(guard.path, self.path.resolve())
        with self.assertRaises(campaign_guard.CampaignRefusal) as raised:
            guard.require()
        self.assertEqual(raised.exception.kind, "campaign_conflict")

    def test_activating_another_campaign_does_not_replace_the_policy(self):
        self.init(mode=AUTONOMOUS)
        other = campaign_guard.Guard(self.root / "other-state.json")
        with self.assertRaises(campaign_guard.CampaignRefusal) as raised:
            other.init(mode=CONTROLLED)
        self.assertEqual(raised.exception.kind, "campaign_active")
        self.assertEqual(
            campaign_guard.Guard(self.path).state()["mode"], AUTONOMOUS)

    def test_a_marker_naming_a_missing_state_fails_closed(self):
        self.init()
        self.path.unlink()
        os.environ.pop("EU4_CAMPAIGN_STATE", None)
        guard = campaign_guard.from_env()
        self.assertIsNotNone(guard)
        with self.assertRaises(campaign_guard.CampaignRefusal):
            campaign_guard.require_live_campaign()

    def test_a_malformed_marker_fails_closed(self):
        self.markers.mkdir(parents=True, exist_ok=True)
        self.marker_file().write_text("{broken", encoding="utf-8")
        self.assertIsNotNone(campaign_guard.from_env())
        with self.assertRaises(campaign_guard.CampaignRefusal):
            campaign_guard.require_live_campaign()

    def test_the_marker_path_ignores_the_launch_lease_override(self):
        from automation import launch_lease

        with mock.patch.dict(
                os.environ,
                {"EU4_LAUNCH_LEASE_DIR": str(self.root / "elsewhere")}):
            self.assertEqual(launch_lease.lease_dir(),
                             launch_lease.DEFAULT_LEASE_DIR)
            self.marker.stop()
            try:
                self.assertEqual(
                    campaign_guard._marker_path(),
                    launch_lease.DEFAULT_LEASE_DIR / campaign_guard.MARKER_NAME)
            finally:
                self.marker.start()

    def test_a_lease_directory_override_cannot_hide_an_active_stop(self):
        self.init(mode=AUTONOMOUS)
        self.guard.stop("operator_stop", "operator cancelled")
        with mock.patch.dict(
                os.environ,
                {"EU4_LAUNCH_LEASE_DIR": str(self.root / "elsewhere")}):
            os.environ.pop("EU4_CAMPAIGN_STATE", None)
            self.assertIsNotNone(campaign_guard.from_env())
            with self.assertRaises(campaign_guard.CampaignRefusal) as raised:
                campaign_guard.require_live_campaign()
        self.assertEqual(raised.exception.kind, "stopped")

    def test_concurrent_activation_admits_exactly_one_campaign(self):
        paths = [self.root / "campaign-a.json", self.root / "campaign-b.json"]
        for path in paths:
            guard = campaign_guard.Guard(path)
            guard.init(mode=AUTONOMOUS)
            guard.deactivate()
        marker = _marker_file(self.markers)
        self.assertFalse(marker.exists())
        outcomes = []

        def activate(path):
            try:
                campaign_guard.Guard(path).activate()
                outcomes.append("active")
            except campaign_guard.CampaignRefusal as exc:
                outcomes.append(exc.kind)

        threads = [threading.Thread(target=activate, args=(path,))
                   for path in paths]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(outcomes.count("active"), 1, outcomes)
        self.assertEqual(outcomes.count("campaign_active"), 1, outcomes)
        value = json.loads(marker.read_text())
        self.assertIn(value["state"],
                      [str(path.resolve()) for path in paths])

    def test_deactivate_removes_only_its_own_marker(self):
        self.init()
        self.assertTrue(self.guard.deactivate())
        self.assertIsNone(campaign_guard.from_env())
        other = campaign_guard.Guard(self.root / "other-state.json")
        other.init(mode=CONTROLLED)
        self.assertFalse(self.guard.deactivate())
        self.assertIsNotNone(campaign_guard.from_env())

    def test_an_environment_marker_override_does_not_move_the_marker(self):
        default = self.markers / campaign_guard.MARKER_NAME
        with mock.patch.dict(
                os.environ,
                {"EU4_CAMPAIGN_MARKER_DIR": str(self.root / "elsewhere")}):
            self.assertEqual(campaign_guard._marker_path(), default)

    def test_no_state_and_no_marker_means_no_enforcement(self):
        self.assertIsNone(campaign_guard.from_env())
        with mock.patch.dict(os.environ, {}):
            os.environ.pop("EU4_CAMPAIGN_STATE", None)
            self.assertIsNone(campaign_guard.require_active_from_env())

    def test_the_marker_falls_back_to_the_state_file(self):
        self.init()
        guard = campaign_guard.from_env()
        self.assertIsNotNone(guard)
        self.assertEqual(guard.status()["campaign_id"], "pilot")


class LegacyStateTests(GuardTestCase):
    def test_a_v1_state_is_refused_with_a_fresh_campaign_hint(self):
        self.path.write_text(json.dumps({
            "schema": "eu4-campaign-state/v1",
            "budgets": {"replays": 2},
            "counts": {"replays": 2},
        }), encoding="utf-8")
        with self.assertRaises(campaign_guard.CampaignRefusal) as raised:
            self.guard.status()
        self.assertEqual(raised.exception.kind, "state_invalid")
        self.assertIn("fresh campaign", raised.exception.detail)


class _FakeLease:
    def child_fds(self):
        return []

    def release(self):
        return None


class _FakeSession:
    display = None

    def display_env(self):
        return {}

    def register(self, role, process, token=None):
        return types.SimpleNamespace(
            as_dict=lambda: {"pid": process.pid, "role": role})


class ActionSiteTests(GuardTestCase):
    """Replays are unlimited; launches stay operator-authorized and one-use."""

    def test_more_than_ten_replays_run_without_a_quota(self):
        self.init(mode=AUTONOMOUS)
        report = {"schema": "gfx-independent-replay/v1",
                  "status": "confirmed_rendering_difference",
                  "execution": {"status": "ok"}}
        with mock.patch.object(gfx_independent_replay, "_replay_locked",
                               return_value=report) as locked:
            for index in range(12):
                result = gfx_independent_replay.replay(
                    self.root / f"frame-{index}", self.root / f"out-{index}")
                self.assertEqual(result, report)
        self.assertEqual(locked.call_count, 12)

    def _replay_lock_case(self, action):
        self.init(mode=AUTONOMOUS)
        frame = self.root / "frame"
        (frame / "shaders").mkdir(parents=True)
        (frame / "shaders" / "shaders.json").write_text("{}")
        out = self.root / "out"
        outcomes = []

        def run():
            try:
                gfx_independent_replay._replay_locked(frame, out)
            except BaseException as exc:  # noqa: BLE001 - recorded below
                outcomes.append(exc)

        with mock.patch.dict(os.environ,
                             {"EU4_CAMPAIGN_STATE": str(self.path)}), \
             mock.patch.object(
                 gfx_independent_replay, "_replay_locked_serialized",
                 side_effect=AssertionError("the replay must not start")):
            with launch_lease.gpu_execution():
                thread = threading.Thread(target=run)
                thread.start()
                time.sleep(0.1)  # the thread is blocked on the GPU lock
                action()
            thread.join(timeout=10)
        self.assertFalse(thread.is_alive())
        self.assertEqual(len(outcomes), 1)
        self.assertIsInstance(outcomes[0], gfx_independent_replay.ReplayError)
        self.assertIn("campaign_refused", str(outcomes[0]))

    def test_an_operator_stop_while_waiting_for_the_gpu_lock_prevents_the_replay(self):
        self._replay_lock_case(
            lambda: self.guard.stop("operator_stop", "operator cancelled"))

    def test_a_deadline_expiry_while_waiting_for_the_gpu_lock_prevents_the_replay(self):
        self.init(mode=AUTONOMOUS, deadline_seconds=0.1)
        frame = self.root / "frame2"
        (frame / "shaders").mkdir(parents=True)
        (frame / "shaders" / "shaders.json").write_text("{}")
        out = self.root / "out2"
        outcomes = []

        def run():
            try:
                gfx_independent_replay._replay_locked(frame, out)
            except BaseException as exc:  # noqa: BLE001
                outcomes.append(exc)

        with mock.patch.dict(os.environ,
                             {"EU4_CAMPAIGN_STATE": str(self.path)}), \
             mock.patch.object(
                 gfx_independent_replay, "_replay_locked_serialized",
                 side_effect=AssertionError("the replay must not start")):
            with launch_lease.gpu_execution():
                thread = threading.Thread(target=run)
                thread.start()
                time.sleep(0.2)  # expires while waiting
            thread.join(timeout=10)
        self.assertFalse(thread.is_alive())
        self.assertEqual(len(outcomes), 1)
        self.assertIn("campaign_refused", str(outcomes[0]))

    def test_a_stop_after_the_direct_replay_acquires_its_lock_prevents_execution(self):
        self.init(mode=AUTONOMOUS)
        frame = self.root / "frame"
        frame.mkdir()
        out = self.root / "out"
        args = gfx_frame._parser().parse_args([
            "replay", "--frame", str(frame), "--out", str(out)])
        guard = self.guard

        @contextlib.contextmanager
        def lock():
            # Simulate an operator stop while the replay waited for the lock.
            guard.stop("operator_stop", "operator cancelled")
            yield

        with mock.patch.dict(os.environ,
                             {"EU4_CAMPAIGN_STATE": str(self.path)}), \
             mock.patch.object(gfx_frame, "build_replay_host",
                               return_value=self.root / "host"), \
             mock.patch.object(launch_lease, "gpu_execution", lock), \
             mock.patch.object(gfx_frame.subprocess, "run",
                               side_effect=AssertionError("must not execute")):
            self.assertEqual(gfx_frame._replay_command_body(args), 3)

    def test_a_stopped_campaign_refuses_a_replay(self):
        self.init(mode=AUTONOMOUS)
        self.guard.stop("environment_unsafe", "no Metal device")
        with mock.patch.object(
                gfx_independent_replay, "_replay_locked",
                side_effect=AssertionError("the host must not run")):
            with self.assertRaises(gfx_independent_replay.ReplayError) as raised:
                gfx_independent_replay.replay(
                    self.root / "frame", self.root / "out")
        self.assertEqual(raised.exception.kind, "campaign_refused")

    def test_autonomous_capture_refuses_before_spawning_any_child(self):
        self.init(mode=AUTONOMOUS)
        args = gfx_frame._parser().parse_args(
            ["capture", "--output", str(self.root / "capture"), "--shadow"])
        fake_startup = types.SimpleNamespace(
            StartupError=RuntimeError,
            preflight_ops=lambda *a, **k: {},
            run_ops=lambda *a, **k: (_ for _ in ()).throw(
                AssertionError("EU4 must not launch")))
        with mock.patch.dict(os.environ,
                             {"EU4_CAMPAIGN_STATE": str(self.path)}), \
             mock.patch.dict(sys.modules,
                             {"eu4_gfx_startup_trace": fake_startup}), \
             mock.patch.object(
                 gfx_frame.subprocess, "Popen",
                 side_effect=AssertionError("coordinator must not spawn")):
            status = gfx_frame.capture(args)
        self.assertEqual(status, 3)
        self.assertFalse((self.root / "capture").exists())

    def test_a_stopped_campaign_refuses_capture_before_preflight(self):
        self.init(mode=CONTROLLED)
        self.guard.stop("operator_stop", "operator cancelled")
        args = gfx_frame._parser().parse_args(
            ["capture", "--output", str(self.root / "capture")])
        fake_startup = types.SimpleNamespace(
            StartupError=RuntimeError,
            preflight_ops=lambda *a, **k: (_ for _ in ()).throw(
                AssertionError("preflight/build must not run")),
            run_ops=lambda *a, **k: (_ for _ in ()).throw(
                AssertionError("EU4 must not launch")))
        with mock.patch.dict(os.environ,
                             {"EU4_CAMPAIGN_STATE": str(self.path)}), \
             mock.patch.dict(sys.modules,
                             {"eu4_gfx_startup_trace": fake_startup}):
            self.assertEqual(gfx_frame.capture(args), 3)


    def test_direct_replay_respects_a_campaign_stop(self):
        self.init(mode=AUTONOMOUS)
        self.guard.stop("operator_stop", "operator cancelled")
        args = gfx_frame._parser().parse_args([
            "replay", "--frame", str(self.root / "frame"),
            "--out", str(self.root / "out"),
            "--rationale", json.dumps(RATIONALE)])
        with mock.patch.dict(os.environ,
                             {"EU4_CAMPAIGN_STATE": str(self.path)}), \
             mock.patch.object(gfx_frame, "_replay_command_body",
                               side_effect=AssertionError("must not run")):
            self.assertEqual(gfx_frame.replay_command(args), 3)

    def test_direct_replay_requires_a_rationale(self):
        self.markers.mkdir(parents=True, exist_ok=True)
        args = gfx_frame._parser().parse_args([
            "replay", "--frame", str(self.root / "frame"),
            "--out", str(self.root / "out")])
        with mock.patch.object(gfx_frame, "_replay_command_body",
                               side_effect=AssertionError("must not run")):
            self.assertEqual(gfx_frame.replay_command(args), 3)

    def run_ops(self, name: str, popen, *, before_spawn=None):
        import eu4_gfx_startup_trace as startup
        from automation import test_profiles

        launcher = self.root / "launcher.json"
        launcher.write_text(json.dumps({"exeArgs": []}), encoding="utf-8")
        evidence = {
            "executable_sha256": "a" * 64,
            "library_sha256": "b" * 64,
            "ops": ["_GfxDraw"],
        }
        with mock.patch.dict(os.environ, {
                "EU4_CAMPAIGN_STATE": str(self.path)}), \
                mock.patch.object(test_profiles, "ensure_live_launch",
                                  return_value=None), \
                mock.patch.object(startup.base, "GOG_LAUNCHER", launcher), \
                mock.patch.object(startup.launch_lease, "acquire",
                                  side_effect=before_spawn, return_value=_FakeLease()), \
                mock.patch.object(startup.subprocess, "Popen", popen):
            return startup.run_ops(
                targets=["_GfxDraw"], max_events=8, duration_s=1.0,
                timeout_s=5, output=self.root / name, authorize_live=True,
                evidence=evidence)

    def spawn(self, name: str, popen, *, enabled=True):
        from automation import gfx_launch, test_profiles

        def prepare(role, config, run_dir, libraries=None, backend=None):
            Path(run_dir).mkdir(parents=True, exist_ok=False)
            return {}, Path(run_dir), Path(run_dir) / "observe"

        environment = ({"EU4_CAMPAIGN_STATE": str(self.path)}
                       if enabled else {})
        with mock.patch.dict(os.environ, environment), \
                mock.patch.object(test_profiles, "ensure_live_launch",
                                  return_value=None), \
                mock.patch.object(gfx_launch, "prepare", prepare), \
                mock.patch.object(gfx_launch.subprocess, "Popen", popen):
            if not enabled:
                os.environ.pop("EU4_CAMPAIGN_STATE", None)
            config = types.SimpleNamespace(args=[], executable=Path("/tmp/eu4"))
            return gfx_launch.spawn(
                "reference", config, self.root / name,
                lease=_FakeLease(), session=_FakeSession())

    def test_autonomous_mode_refuses_a_direct_spawn(self):
        self.init(mode=AUTONOMOUS)
        calls = []

        def popen(*args, **kwargs):
            calls.append(args)
            return types.SimpleNamespace(pid=4242)

        from automation import gfx_launch

        with self.assertRaises(gfx_launch.LaunchError) as raised:
            self.spawn("autonomous-direct", popen)
        self.assertIn("live_capture_disabled", str(raised.exception))
        self.assertEqual(calls, [])

    def test_a_stopped_campaign_is_refused_immediately_before_popen(self):
        self.init(mode=CONTROLLED)
        calls = []

        def popen(*args, **kwargs):
            calls.append(args)
            return types.SimpleNamespace(pid=4242)

        def stop_after_preflight(*args, **kwargs):
            self.guard.stop("blocked", "environment unsafe")
            return _FakeLease()

        with self.assertRaises(RuntimeError) as raised:
            self.run_ops("stopped", popen, before_spawn=stop_after_preflight)
        self.assertIn("stopped", str(raised.exception))
        self.assertEqual(calls, [])


    def test_standalone_spawn_keeps_its_existing_behavior(self):
        calls = []

        def popen(*args, **kwargs):
            calls.append(args)
            return types.SimpleNamespace(pid=4242)

        self.assertIsNone(campaign_guard.from_env())
        launched = self.spawn("unguarded", popen, enabled=False)
        self.assertEqual(launched.process.pid, 4242)
        self.assertEqual(len(calls), 1)


if __name__ == "__main__":
    unittest.main()
