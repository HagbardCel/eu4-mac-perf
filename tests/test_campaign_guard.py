"""Externally enforced campaign reservations, budgets and stop state."""

from __future__ import annotations

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

from automation import campaign_guard, gfx_independent_replay, m6_frame  # noqa: E402


class GuardTests(unittest.TestCase):
    def setUp(self):
        base = ROOT / ".local" / "test-runs"
        base.mkdir(parents=True, exist_ok=True)
        self.temporary = tempfile.TemporaryDirectory(dir=base)
        self.path = Path(self.temporary.name) / "campaign-state.json"
        self.guard = campaign_guard.Guard(self.path)

    def tearDown(self):
        self.temporary.cleanup()

    def init(self, **kwargs):
        return self.guard.init(campaign_id="pilot", **kwargs)

    def test_init_records_the_pilot_budget_and_deadline(self):
        state = self.init(deadline_seconds=60)
        self.assertEqual(state["schema"], campaign_guard.SCHEMA)
        self.assertEqual(state["budgets"]["eu4_launches"], 1)
        self.assertEqual(state["budgets"]["replays"], 2)
        self.assertGreater(state["deadline_epoch"], time.time())
        self.assertFalse(state["auto_merge"])
        self.assertIsNone(state["admin_token_sha256"])

    def test_a_second_init_does_not_reset_the_budget(self):
        self.init()
        with self.assertRaises(campaign_guard.CampaignRefusal) as raised:
            self.init()
        self.assertEqual(raised.exception.kind, "state_exists")

    def test_reservation_charges_before_the_action(self):
        self.init(budgets={"replays": 2})
        reservation = self.guard.reserve({"replays": 1})
        state = self.guard.state()
        self.assertEqual(state["counts"]["replays"], 1)
        self.assertEqual(state["reservations"][reservation]["state"],
                         "reserved")
        self.assertTrue(self.guard.validate_reservation(reservation, "replays"))

    def test_budget_exhaustion_refuses_without_charging(self):
        self.init(budgets={"replays": 1})
        self.guard.reserve({"replays": 1})
        with self.assertRaises(campaign_guard.CampaignRefusal) as raised:
            self.guard.reserve({"replays": 1})
        self.assertEqual(raised.exception.kind, "budget_exhausted")
        self.assertEqual(self.guard.state()["counts"]["replays"], 1)

    def test_zero_budget_refuses_immediately(self):
        self.init(budgets={"replays": 0})
        with self.assertRaises(campaign_guard.CampaignRefusal) as raised:
            self.guard.reserve({"replays": 1})
        self.assertEqual(raised.exception.kind, "budget_exhausted")
        self.assertEqual(self.guard.state()["counts"]["replays"], 0)

    def test_reservation_is_atomic_for_competing_requests(self):
        self.init(budgets={"replays": 1})
        outcomes: list[str] = []

        def compete():
            guard = campaign_guard.Guard(self.path)
            try:
                guard.reserve({"replays": 1})
                outcomes.append("reserved")
            except campaign_guard.CampaignRefusal as exc:
                outcomes.append(exc.kind)

        threads = [threading.Thread(target=compete) for _ in range(8)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(outcomes.count("reserved"), 1)
        self.assertEqual(outcomes.count("budget_exhausted"), 7)
        self.assertEqual(self.guard.state()["counts"]["replays"], 1)

    def test_multi_resource_reservation_is_all_or_nothing(self):
        self.init(budgets={"eu4_launches": 1, "sealed_frames": 0})
        with self.assertRaises(campaign_guard.CampaignRefusal):
            self.guard.reserve({"eu4_launches": 1, "sealed_frames": 1})
        state = self.guard.state()
        self.assertEqual(state["counts"]["eu4_launches"], 0)
        self.assertEqual(state["counts"]["sealed_frames"], 0)
        self.assertEqual(state["reservations"], {})

    def test_completion_releases_only_unused_verified_allowances(self):
        self.init(budgets={"eu4_launches": 1, "sealed_frames": 4})
        reservation = self.guard.reserve(
            {"eu4_launches": 1, "sealed_frames": 4})
        self.guard.complete(reservation, outcome="ok",
                            consumed={"eu4_launches": 1,
                                      "sealed_frames": 2})
        state = self.guard.state()
        self.assertEqual(state["counts"]["sealed_frames"], 2)
        self.assertEqual(state["counts"]["eu4_launches"], 1)
        self.assertEqual(state["reservations"][reservation]["state"],
                         "completed")

    def test_failure_keeps_the_full_charge(self):
        self.init(budgets={"eu4_launches": 1, "sealed_frames": 4})
        reservation = self.guard.reserve(
            {"eu4_launches": 1, "sealed_frames": 4})
        self.guard.complete(reservation, outcome="failed",
                            detail="capture crashed",
                            consumed={"sealed_frames": 0})
        state = self.guard.state()
        self.assertEqual(state["counts"]["sealed_frames"], 4)
        self.assertEqual(state["reservations"][reservation]["state"],
                         "failed")

    def test_double_completion_is_refused(self):
        self.init()
        reservation = self.guard.reserve({"replays": 1})
        self.guard.complete(reservation, outcome="ok")
        with self.assertRaises(campaign_guard.CampaignRefusal) as raised:
            self.guard.complete(reservation, outcome="ok")
        self.assertEqual(raised.exception.kind, "reservation_closed")

    def test_abandoned_reservations_keep_their_charge(self):
        self.init(budgets={"eu4_launches": 1})
        reservation = self.guard.reserve({"eu4_launches": 1})
        self.guard.abandon_stale(max_age_seconds=0)
        state = self.guard.state()
        self.assertEqual(state["reservations"][reservation]["state"],
                         "abandoned")
        self.assertEqual(state["counts"]["eu4_launches"], 1)
        with self.assertRaises(campaign_guard.CampaignRefusal):
            self.guard.reserve({"eu4_launches": 1})

    def test_identical_failures_block_without_a_model_reset(self):
        self.init(budgets={"replays": 5})
        for _ in range(campaign_guard.MAX_IDENTICAL_RETRIES):
            reservation = self.guard.reserve({"replays": 1})
            self.guard.complete(reservation, outcome="failed",
                                detail="Metal command buffer did not complete")
        decision = self.guard.check("replays")
        self.assertFalse(decision["ok"])
        self.assertEqual(decision["kind"], "requires_admin_recovery")
        with self.assertRaises(campaign_guard.CampaignRefusal):
            self.guard.reserve({"replays": 1})
        # A different textual strategy is not a reset.
        self.assertFalse(hasattr(self.guard, "acknowledge_strategy"))

    def test_admin_recovery_requires_the_operator_token(self):
        self.init(budgets={"replays": 1}, admin_token="secret-token")
        reservation = self.guard.reserve({"replays": 1})
        self.guard.complete(reservation, outcome="failed", detail="timeout")
        self.guard.stop("blocked", "environment unsafe")
        with self.assertRaises(campaign_guard.CampaignRefusal):
            self.guard.admin_recover(token="wrong", reason="operator fixed it")
        with self.assertRaises(campaign_guard.CampaignRefusal):
            self.guard.admin_recover(token="", reason="operator fixed it")
        state = self.guard.admin_recover(
            token="secret-token", reason="display state restored")
        self.assertIsNone(state["stop"])
        self.assertEqual(state["history"][-1]["action"], "admin_recover")
        self.assertIn("cleared", state["history"][-1]["evidence"])

    def test_admin_recovery_is_disabled_without_a_token(self):
        self.init()
        self.guard.stop("blocked", "environment unsafe")
        with self.assertRaises(campaign_guard.CampaignRefusal) as raised:
            self.guard.admin_recover(token="anything", reason="try")
        self.assertEqual(raised.exception.kind, "admin_recovery_disabled")

    def test_exhausting_one_resource_does_not_block_another(self):
        self.init(budgets={"eu4_launches": 1, "sealed_frames": 1,
                           "replays": 1})
        self.guard.reserve({"eu4_launches": 1, "sealed_frames": 1})
        self.assertTrue(self.guard.check("replays")["ok"])
        self.assertFalse(self.guard.check("eu4_launches")["ok"])
        self.assertIn("eu4_launches", self.guard.status()["exhausted"])

    def test_deadline_refuses_new_reservations(self):
        self.init(deadline_seconds=0.01)
        time.sleep(0.05)
        with self.assertRaises(campaign_guard.CampaignRefusal) as raised:
            self.guard.reserve({"replays": 1})
        self.assertEqual(raised.exception.kind, "deadline")
        self.assertEqual(self.guard.status()["seconds_remaining"], 0.0)

    def test_explicit_stop_is_structured_and_audited(self):
        self.init()
        with self.assertRaises(campaign_guard.CampaignRefusal):
            self.guard.stop("because")
        self.guard.stop("blocked", "environment could not be restored")
        status = self.guard.status()
        self.assertEqual(status["stop"]["reason"], "blocked")
        with self.assertRaises(campaign_guard.CampaignRefusal):
            self.guard.reserve({"replays": 1})

    def test_no_environment_state_means_no_enforcement(self):
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("EU4_CAMPAIGN_STATE", None)
            self.assertIsNone(campaign_guard.from_env())
            self.assertIsNone(campaign_guard.require_from_env("replays"))
            campaign_guard.require_launch_reservation()
            self.assertFalse(self.path.exists())

    def test_state_is_machine_readable(self):
        self.init(budgets={"replays": 5})
        reservation = self.guard.reserve({"replays": 1},
                                         evidence={"case": "x"})
        state = json.loads(self.path.read_text())
        self.assertEqual(state["counts"]["replays"], 1)
        self.assertEqual(state["reservations"][reservation]["evidence"],
                         {"case": "x"})
        status = self.guard.status()
        self.assertEqual(status["open_reservations"][0]["reservation"],
                         reservation)


class LaunchReservationTests(unittest.TestCase):
    def setUp(self):
        base = ROOT / ".local" / "test-runs"
        base.mkdir(parents=True, exist_ok=True)
        self.temporary = tempfile.TemporaryDirectory(dir=base)
        self.path = Path(self.temporary.name) / "state.json"
        guard = campaign_guard.Guard(self.path)
        guard.init(campaign_id="launch", budgets={"eu4_launches": 1})
        self.guard = guard

    def tearDown(self):
        self.temporary.cleanup()

    def test_launch_without_a_reservation_is_refused(self):
        with mock.patch.dict(os.environ,
                             {"EU4_CAMPAIGN_STATE": str(self.path)}):
            os.environ.pop("EU4_CAMPAIGN_RESERVATION", None)
            with self.assertRaises(campaign_guard.CampaignRefusal) as raised:
                campaign_guard.require_launch_reservation()
            self.assertEqual(raised.exception.kind, "launch_reservation")

    def test_launch_with_an_open_reservation_is_allowed(self):
        reservation = self.guard.reserve({"eu4_launches": 1})
        with mock.patch.dict(os.environ,
                             {"EU4_CAMPAIGN_STATE": str(self.path),
                              "EU4_CAMPAIGN_RESERVATION": reservation}):
            campaign_guard.require_launch_reservation()
        # A closed reservation is no longer usable.
        self.guard.complete(reservation, outcome="ok")
        with mock.patch.dict(os.environ,
                             {"EU4_CAMPAIGN_STATE": str(self.path),
                              "EU4_CAMPAIGN_RESERVATION": reservation}):
            with self.assertRaises(campaign_guard.CampaignRefusal):
                campaign_guard.require_launch_reservation()

    def test_attempt_reserves_and_records(self):
        guard = campaign_guard.Guard(self.path)
        with mock.patch.dict(os.environ,
                             {"EU4_CAMPAIGN_STATE": str(self.path)}):
            with campaign_guard.attempt("eu4_launches"):
                pass
        self.assertEqual(guard.status()["counts"]["eu4_launches"], 1)


class ActionSiteGateTests(unittest.TestCase):
    """The budget is enforced where the resource is actually consumed."""

    def setUp(self):
        base = ROOT / ".local" / "test-runs"
        base.mkdir(parents=True, exist_ok=True)
        self.temporary = tempfile.TemporaryDirectory(dir=base)
        self.root = Path(self.temporary.name)
        self.state_path = self.root / "campaign-state.json"

    def tearDown(self):
        self.temporary.cleanup()

    def test_replay_refuses_before_building_the_host(self):
        guard = campaign_guard.Guard(self.state_path)
        guard.init(campaign_id="gate", budgets={"replays": 0})
        with mock.patch.dict(os.environ,
                             {"EU4_CAMPAIGN_STATE": str(self.state_path)}):
            with mock.patch.object(
                    gfx_independent_replay, "_replay_locked",
                    side_effect=AssertionError("the host must not run")):
                with self.assertRaises(gfx_independent_replay.ReplayError) as raised:
                    gfx_independent_replay.replay(
                        self.root / "frame", self.root / "out")
        self.assertEqual(raised.exception.kind, "campaign_refused")

    def test_successful_replay_is_reserved_and_completed(self):
        guard = campaign_guard.Guard(self.state_path)
        guard.init(campaign_id="gate", budgets={"replays": 2})
        report = {"status": "confirmed_rendering_difference",
                  "execution": {"status": "ok"}}
        with mock.patch.dict(os.environ,
                             {"EU4_CAMPAIGN_STATE": str(self.state_path)}):
            with mock.patch.object(gfx_independent_replay, "_replay_locked",
                                   return_value=report):
                result = gfx_independent_replay.replay(
                    self.root / "frame", self.root / "out")
        self.assertEqual(result, report)
        state = guard.state()
        self.assertEqual(state["counts"]["replays"], 1)
        self.assertEqual(
            [row["state"] for row in state["reservations"].values()],
            ["completed"])

    def test_capture_refuses_before_spawning_any_child(self):
        guard = campaign_guard.Guard(self.state_path)
        guard.init(campaign_id="gate",
                   budgets={"eu4_launches": 1, "sealed_frames": 1,
                            "shadow_executions": 0})
        args = m6_frame._parser().parse_args(
            ["capture", "--output", str(self.root / "capture"), "--shadow"])
        # Inject the native startup module so the refusal is exercised on any
        # platform without importing the Mach-O launcher.
        fake_startup = types.SimpleNamespace(
            preflight_ops=lambda *a, **k: {},
            run_ops=lambda *a, **k: (_ for _ in ()).throw(
                AssertionError("EU4 must not launch")))
        with mock.patch.dict(os.environ,
                             {"EU4_CAMPAIGN_STATE": str(self.state_path)}), \
             mock.patch.dict(sys.modules,
                             {"eu4_gfx_startup_trace": fake_startup}), \
             mock.patch.object(
                 m6_frame.subprocess, "Popen",
                 side_effect=AssertionError("coordinator must not spawn")):
            status = m6_frame.capture(args)
        self.assertEqual(status, 3)
        self.assertFalse((self.root / "capture").exists())
        state = guard.state()
        self.assertEqual(state["counts"]["eu4_launches"], 0)
        self.assertEqual(state["counts"]["sealed_frames"], 0)
        self.assertEqual(state["reservations"], {})

    def test_capture_reservation_covers_continuous_upper_bound(self):
        guard = campaign_guard.Guard(self.state_path)
        guard.init(campaign_id="gate",
                   budgets={"eu4_launches": 2, "sealed_frames": 2,
                            "shadow_executions": 2})
        frames = int(guard.state()["budgets"]["sealed_frames"])
        reservation = guard.reserve(
            {"eu4_launches": 1, "sealed_frames": frames,
             "shadow_executions": frames})
        self.assertTrue(guard.validate_reservation(reservation,
                                                   "eu4_launches"))
        guard.complete(reservation, outcome="ok",
                       consumed={"eu4_launches": 1, "sealed_frames": 1,
                                 "shadow_executions": 1})
        state = guard.state()
        self.assertEqual(state["counts"]["sealed_frames"], 1)
        self.assertEqual(state["counts"]["shadow_executions"], 1)


class _FakeStartup(types.ModuleType):
    """Controlled stand-in for the native startup module.

    It records the trusted launch call, verifies the explicit reservation under
    the state lock, then raises so capture's failure path (coordinator reap and
    conservative reservation retention) is exercised without a real launch.
    """

    StartupError = RuntimeError

    def __init__(self, state_path: Path):
        super().__init__("eu4_gfx_startup_trace")
        self.state_path = state_path
        self.calls: list[dict] = []
        self.reservation_valid_at_call: bool | None = None

    def preflight_ops(self, *args, **kwargs):
        return {}

    def run_ops(self, **kwargs):
        self.calls.append(kwargs)
        token = kwargs.get("campaign_reservation")
        guard = campaign_guard.Guard(self.state_path)
        self.reservation_valid_at_call = guard.validate_reservation(
            token or "", "eu4_launches")
        raise RuntimeError("controlled fake launch boundary")


class _FakePopen:
    def __init__(self, *args, **kwargs):
        self.pid = 4242
        self.returncode = None
        self.terminated = False
        self.killed = False

    def poll(self):
        return None

    def terminate(self):
        self.terminated = True

    def kill(self):
        self.killed = True

    def communicate(self, timeout=None):
        self.returncode = 0
        return "", ""


class CaptureLaunchBoundaryTests(unittest.TestCase):
    """The actual capture-to-launch boundary under an external campaign budget."""

    def setUp(self):
        base = ROOT / ".local" / "test-runs"
        base.mkdir(parents=True, exist_ok=True)
        self.temporary = tempfile.TemporaryDirectory(dir=base)
        self.root = Path(self.temporary.name)
        self.state_path = self.root / "state.json"
        self.guard = campaign_guard.Guard(self.state_path)

    def tearDown(self):
        self.temporary.cleanup()

    def init(self, **budgets):
        values = {"eu4_launches": 1, "sealed_frames": 1,
                  "shadow_executions": 1}
        values.update(budgets)
        self.guard.init(campaign_id="boundary", budgets=values)

    def capture(self, name: str, *extra: str, startup, popen):
        args = m6_frame._parser().parse_args(
            ["capture", "--output", str(self.root / name), *extra])
        with mock.patch.dict(os.environ,
                             {"EU4_CAMPAIGN_STATE": str(self.state_path)}), \
             mock.patch.dict(sys.modules,
                             {"eu4_gfx_startup_trace": startup}), \
             mock.patch.object(m6_frame.subprocess, "Popen", popen):
            return m6_frame.capture(args)

    def test_one_launch_under_a_budget_of_one_is_permitted(self):
        self.init(eu4_launches=1)
        startup = _FakeStartup(self.state_path)
        status = self.capture("one", "--seal", startup=startup,
                              popen=AssertionError)
        self.assertEqual(status, 4)
        self.assertEqual(len(startup.calls), 1)
        self.assertTrue(startup.reservation_valid_at_call)
        state = self.guard.state()
        self.assertEqual(state["counts"]["eu4_launches"], 1)
        self.assertEqual(
            [row["state"] for row in state["reservations"].values()],
            ["failed"])

    def test_second_launch_is_refused_before_starting_any_process(self):
        self.init(eu4_launches=1)
        first = _FakeStartup(self.state_path)
        self.assertEqual(self.capture("first", "--seal", startup=first,
                                      popen=AssertionError), 4)
        second = _FakeStartup(self.state_path)
        status = self.capture("second", "--seal", startup=second,
                              popen=AssertionError)
        self.assertEqual(status, 3)
        self.assertEqual(second.calls, [])

    def test_zero_frame_allowance_refuses_before_the_coordinator(self):
        self.init(sealed_frames=0)
        startup = _FakeStartup(self.state_path)
        status = self.capture("noframe", "--seal", startup=startup,
                              popen=AssertionError)
        self.assertEqual(status, 3)
        self.assertEqual(startup.calls, [])
        state = self.guard.state()
        self.assertEqual(state["counts"]["eu4_launches"], 0)
        self.assertEqual(state["reservations"], {})

    def test_continuous_capture_reserves_the_frame_and_shadow_upper_bound(self):
        self.init(sealed_frames=3, shadow_executions=3)
        startup = _FakeStartup(self.state_path)
        captured = {}

        def popen(command, *args, **kwargs):
            fake = _FakePopen()
            captured["command"] = command
            captured["proc"] = fake
            return fake

        status = self.capture(
            "continuous", "--shadow", "--continuous", "--max-frames", "3",
            "--max-pending", "2", startup=startup, popen=popen)
        self.assertEqual(status, 4)
        self.assertEqual(len(startup.calls), 1)
        state = self.guard.state()
        reservation = next(iter(state["reservations"].values()))
        self.assertEqual(reservation["actions"],
                         {"eu4_launches": 1, "sealed_frames": 3,
                          "shadow_executions": 3})
        self.assertIn("--max-frames", captured["command"])

    def test_refusal_leaves_no_coordinator_or_open_reservation(self):
        self.init(shadow_executions=0)
        startup = _FakeStartup(self.state_path)
        spawned = []

        def popen(command, *args, **kwargs):
            spawned.append(command)
            return _FakePopen()

        status = self.capture("noshadow", "--shadow", startup=startup,
                              popen=popen)
        self.assertEqual(status, 3)
        self.assertEqual(spawned, [])
        self.assertEqual(startup.calls, [])
        state = self.guard.state()
        self.assertEqual(state["reservations"], {})
        self.assertEqual(state["counts"]["eu4_launches"], 0)
        self.assertEqual(state["counts"]["shadow_executions"], 0)

    def test_direct_unreserved_launch_is_prohibited(self):
        self.init()
        import eu4_gfx_startup_trace as startup
        from automation import test_profiles

        with mock.patch.dict(os.environ,
                             {"EU4_CAMPAIGN_STATE": str(self.state_path)}), \
             mock.patch.object(test_profiles, "ensure_live_launch",
                               return_value=None), \
             mock.patch.object(startup, "preflight_ops", return_value={}), \
             mock.patch.object(
                 startup.subprocess, "Popen",
                 side_effect=AssertionError("the game must not spawn")):
            with self.assertRaises(startup.StartupError) as raised:
                startup.run_ops(
                    targets=["_GfxDraw"], max_events=16, duration_s=1.0,
                    authorize_live=True, timeout_s=10,
                    output=self.root / "direct")
        self.assertIn("launch reservation", str(raised.exception))


if __name__ == "__main__":
    unittest.main()
