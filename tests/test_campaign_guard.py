"""Externally enforced campaign reservations, budgets and stop state."""

from __future__ import annotations

import json
import os
import subprocess
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

    def test_nested_attempt_charges_once_for_the_same_action(self):
        self.init(budgets={"replays": 2})
        with mock.patch.dict(os.environ,
                             {"EU4_CAMPAIGN_STATE": str(self.path)}):
            with campaign_guard.attempt("replays", count=2):
                with campaign_guard.attempt("replays", count=2):
                    pass
        state = self.guard.state()
        self.assertEqual(state["counts"]["replays"], 2)
        history = state["history"]
        self.assertEqual(
            len([row for row in history if row.get("action") == "reserve"]), 1)
        self.assertEqual(
            len([row for row in history if row.get("outcome") == "ok"]), 1)

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
            self.assertIsNone(campaign_guard.claim_launch_reservation())
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


class LaunchClaimTests(unittest.TestCase):
    """One open launch reservation authorizes exactly one process creation."""

    def setUp(self):
        base = ROOT / ".local" / "test-runs"
        base.mkdir(parents=True, exist_ok=True)
        self.temporary = tempfile.TemporaryDirectory(dir=base)
        self.path = Path(self.temporary.name) / "claim-state.json"
        self.guard = campaign_guard.Guard(self.path)
        self.guard.init(campaign_id="claim", budgets={"eu4_launches": 1})
        self.reservation = self.guard.reserve({"eu4_launches": 1})

    def tearDown(self):
        self.temporary.cleanup()

    def test_one_reservation_authorizes_one_claim(self):
        self.guard.claim_launch(self.reservation)
        state = self.guard.state()
        reservation = state["reservations"][self.reservation]
        self.assertTrue(reservation["launch_claim"])
        self.assertEqual(reservation["state"], "reserved")
        # The reservation is still open for completion/outcome accounting.
        self.assertTrue(self.guard.validate_reservation(
            self.reservation, "eu4_launches"))
        self.assertTrue(self.guard.status()["open_reservations"][0]
                        ["launch_claimed"])
        self.assertEqual(state["history"][-1]["action"], "claim_launch")

    def test_the_same_reservation_cannot_authorize_a_second_claim(self):
        self.guard.claim_launch(self.reservation)
        with self.assertRaises(campaign_guard.CampaignRefusal) as raised:
            self.guard.claim_launch(self.reservation)
        self.assertEqual(raised.exception.kind, "launch_already_claimed")
        self.assertEqual(self.guard.state()["history"][-1]["action"],
                         "claim_launch")

    def test_concurrent_claims_produce_exactly_one_success(self):
        outcomes: list[str] = []

        def compete():
            guard = campaign_guard.Guard(self.path)
            try:
                guard.claim_launch(self.reservation)
                outcomes.append("claimed")
            except campaign_guard.CampaignRefusal as exc:
                outcomes.append(exc.kind)

        threads = [threading.Thread(target=compete) for _ in range(8)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(outcomes.count("claimed"), 1)
        self.assertEqual(outcomes.count("launch_already_claimed"), 7)

    def test_a_stopped_campaign_cannot_claim(self):
        self.guard.stop("blocked", "environment unsafe")
        with self.assertRaises(campaign_guard.CampaignRefusal) as raised:
            self.guard.claim_launch(self.reservation)
        self.assertEqual(raised.exception.kind, "stopped")
        self.assertNotIn("launch_claim",
                         self.guard.state()["reservations"][self.reservation])

    def test_an_expired_campaign_cannot_claim(self):
        path = Path(self.temporary.name) / "expired.json"
        guard = campaign_guard.Guard(path)
        guard.init(campaign_id="expired", deadline_seconds=0.2,
                   budgets={"eu4_launches": 1})
        reservation = guard.reserve({"eu4_launches": 1})
        time.sleep(0.3)
        with self.assertRaises(campaign_guard.CampaignRefusal) as raised:
            guard.claim_launch(reservation)
        self.assertEqual(raised.exception.kind, "deadline")

    def test_invalid_and_missing_reservations_are_rejected(self):
        with self.assertRaises(campaign_guard.CampaignRefusal) as missing:
            self.guard.claim_launch("does-not-exist")
        self.assertEqual(missing.exception.kind, "reservation_missing")
        with self.assertRaises(campaign_guard.CampaignRefusal) as empty:
            self.guard.claim_launch("")
        self.assertEqual(empty.exception.kind, "launch_reservation")

    def test_a_completed_reservation_cannot_claim(self):
        self.guard.complete(self.reservation, outcome="ok")
        with self.assertRaises(campaign_guard.CampaignRefusal) as raised:
            self.guard.claim_launch(self.reservation)
        self.assertEqual(raised.exception.kind, "reservation_closed")

    def test_a_reservation_without_a_launch_allowance_cannot_claim(self):
        path = Path(self.temporary.name) / "replays-only.json"
        guard = campaign_guard.Guard(path)
        guard.init(campaign_id="replays-only", budgets={"replays": 1})
        reservation = guard.reserve({"replays": 1})
        with self.assertRaises(campaign_guard.CampaignRefusal) as raised:
            guard.claim_launch(reservation)
        self.assertEqual(raised.exception.kind, "launch_reservation")

    def test_a_claimed_reservation_still_completes_and_keeps_its_charge(self):
        self.guard.claim_launch(self.reservation)
        state = self.guard.complete(self.reservation, outcome="failed",
                                    detail="spawn raised",
                                    failure_action="eu4_launches")
        reservation = state["reservations"][self.reservation]
        self.assertEqual(reservation["state"], "failed")
        self.assertTrue(reservation["launch_claim"])
        self.assertEqual(state["counts"]["eu4_launches"], 1)
        # A claimed reservation can never be redirected to another process.
        with self.assertRaises(campaign_guard.CampaignRefusal):
            self.guard.claim_launch(self.reservation)

    def test_a_claimed_launch_does_not_break_multi_resource_completion(self):
        path = Path(self.temporary.name) / "multi.json"
        guard = campaign_guard.Guard(path)
        guard.init(campaign_id="multi",
                   budgets={"eu4_launches": 1, "sealed_frames": 2})
        reservation = guard.reserve({"eu4_launches": 1, "sealed_frames": 2})
        guard.claim_launch(reservation)
        state = guard.complete(reservation, outcome="ok",
                               consumed={"eu4_launches": 1,
                                         "sealed_frames": 1})
        self.assertEqual(state["counts"]["eu4_launches"], 1)
        self.assertEqual(state["counts"]["sealed_frames"], 1)
        self.assertEqual(state["reservations"][reservation]["state"],
                         "completed")


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


class OneUseLaunchBoundaryTests(unittest.TestCase):
    """Both physical launch paths spend one reservation for one process."""

    def setUp(self):
        base = ROOT / ".local" / "test-runs"
        base.mkdir(parents=True, exist_ok=True)
        self.temporary = tempfile.TemporaryDirectory(dir=base)
        self.root = Path(self.temporary.name)
        self.state_path = self.root / "state.json"
        self.guard = campaign_guard.Guard(self.state_path)
        self.guard.init(campaign_id="one-use",
                        budgets={"eu4_launches": 1})
        self.reservation = self.guard.reserve({"eu4_launches": 1})

    def tearDown(self):
        self.temporary.cleanup()

    def run_ops(self, name: str, popen, *, state_path=None,
                reservation=None):
        import eu4_gfx_startup_trace as startup
        from automation import test_profiles

        launcher = self.root / "launcher.json"
        launcher.write_text(json.dumps({"exeArgs": []}), encoding="utf-8")
        evidence = {
            "executable_sha256": "a" * 64,
            "library_sha256": "b" * 64,
            "ops": ["_GfxDraw"],
        }
        with mock.patch.dict(os.environ,
                             {"EU4_CAMPAIGN_STATE": str(
                                 state_path or self.state_path),
                              "EU4_CAMPAIGN_RESERVATION":
                                  reservation or self.reservation}), \
                mock.patch.object(test_profiles, "ensure_live_launch",
                                  return_value=None), \
                mock.patch.object(startup.base, "GOG_LAUNCHER", launcher), \
                mock.patch.object(startup.launch_lease, "acquire",
                                  return_value=_FakeLease()), \
                mock.patch.object(startup.subprocess, "Popen", popen):
            return startup.run_ops(
                targets=["_GfxDraw"], max_events=8, duration_s=1.0,
                timeout_s=5, output=self.root / name, authorize_live=True,
                evidence=evidence)

    def spawn(self, name: str, popen, *, reservation=None, enabled=True):
        from automation import gfx_launch, test_profiles

        def prepare(role, config, run_dir, libraries=None, backend=None):
            Path(run_dir).mkdir(parents=True, exist_ok=False)
            return {}, Path(run_dir), Path(run_dir) / "observe"

        run_dir = self.root / name
        environment = ({"EU4_CAMPAIGN_STATE": str(self.state_path)}
                       if enabled else {})
        with mock.patch.dict(os.environ, environment), \
                mock.patch.object(test_profiles, "ensure_live_launch",
                                  return_value=None), \
                mock.patch.object(gfx_launch, "prepare", prepare), \
                mock.patch.object(gfx_launch.subprocess, "Popen", popen):
            if not enabled:
                os.environ.pop("EU4_CAMPAIGN_STATE", None)
                os.environ.pop("EU4_CAMPAIGN_RESERVATION", None)
            config = types.SimpleNamespace(
                args=[], executable=Path("/tmp/eu4"))
            return gfx_launch.spawn(
                "reference", config, run_dir,
                lease=_FakeLease(), session=_FakeSession(),
                campaign_reservation=(self.reservation
                                      if reservation is None
                                      else reservation))

    def test_run_ops_claims_once_and_a_failed_spawn_keeps_the_charge(self):
        calls = []

        def popen(*args, **kwargs):
            calls.append(args)
            raise RuntimeError("controlled spawn failure")

        with self.assertRaises(RuntimeError):
            self.run_ops("first", popen)
        state = self.guard.state()
        self.assertTrue(state["reservations"][self.reservation]["launch_claim"])
        self.assertEqual(state["counts"]["eu4_launches"], 1)

        with self.assertRaises(RuntimeError) as raised:
            self.run_ops("second", popen)
        self.assertIn("already", str(raised.exception))
        self.assertEqual(len(calls), 1)
        self.assertEqual(self.guard.state()["counts"]["eu4_launches"], 1)

    def test_a_stopped_campaign_is_refused_immediately_before_popen(self):
        self.guard.stop("blocked", "environment unsafe")
        calls = []

        def popen(*args, **kwargs):
            calls.append(args)
            return types.SimpleNamespace(pid=4242)

        with self.assertRaises(RuntimeError) as raised:
            self.run_ops("stopped", popen)
        self.assertIn("stopped", str(raised.exception))
        self.assertEqual(calls, [])

    def test_an_expired_campaign_is_refused_immediately_before_popen(self):
        path = self.root / "expired-state.json"
        guard = campaign_guard.Guard(path)
        guard.init(campaign_id="expired-launch", deadline_seconds=0.2,
                   budgets={"eu4_launches": 1})
        reservation = guard.reserve({"eu4_launches": 1})
        time.sleep(0.3)
        calls = []

        def popen(*args, **kwargs):
            calls.append(args)
            return types.SimpleNamespace(pid=4242)

        with self.assertRaises(RuntimeError) as raised:
            self.run_ops("expired", popen, state_path=path,
                         reservation=reservation)
        self.assertIn("deadline", str(raised.exception))
        self.assertEqual(calls, [])

    def test_gfx_launch_spawn_claims_once(self):
        calls = []

        def popen(*args, **kwargs):
            calls.append(args)
            return types.SimpleNamespace(pid=4242)

        launched = self.spawn("spawn-first", popen)
        self.assertEqual(launched.process.pid, 4242)
        self.assertTrue(self.guard.state()["reservations"][self.reservation]
                        ["launch_claim"])
        with self.assertRaises(RuntimeError) as raised:
            self.spawn("spawn-second", popen)
        self.assertIn("already", str(raised.exception))
        self.assertEqual(len(calls), 1)

    def test_both_launch_paths_share_the_same_entitlement(self):
        calls = []

        def popen(*args, **kwargs):
            calls.append(args)
            raise RuntimeError("controlled spawn failure")

        with self.assertRaises(RuntimeError):
            self.run_ops("shared-first", popen)
        with self.assertRaises(RuntimeError) as raised:
            self.spawn("shared-second", popen)
        self.assertIn("already", str(raised.exception))
        self.assertEqual(len(calls), 1)

    def test_guard_disabled_spawn_keeps_its_existing_behavior(self):
        calls = []

        def popen(*args, **kwargs):
            calls.append(args)
            return types.SimpleNamespace(pid=4242)

        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("EU4_CAMPAIGN_STATE", None)
            os.environ.pop("EU4_CAMPAIGN_RESERVATION", None)
            self.assertIsNone(campaign_guard.claim_launch_reservation())
            launched = self.spawn("unguarded", popen, reservation="",
                                  enabled=False)
        self.assertEqual(launched.process.pid, 4242)
        self.assertEqual(len(calls), 1)


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


class AttemptExactlyOnceTests(unittest.TestCase):
    """One physical action is reserved and settled exactly once."""

    def setUp(self):
        base = ROOT / ".local" / "test-runs"
        base.mkdir(parents=True, exist_ok=True)
        self.temporary = tempfile.TemporaryDirectory(dir=base)
        self.root = Path(self.temporary.name)
        self.path = self.root / "state.json"
        self.guard = campaign_guard.Guard(self.path)
        for key in ("EU4_CAMPAIGN_STATE", "EU4_CAMPAIGN_ATTEMPT",
                    "EU4_CAMPAIGN_RESERVATION"):
            os.environ.pop(key, None)

    def tearDown(self):
        for key in ("EU4_CAMPAIGN_STATE", "EU4_CAMPAIGN_ATTEMPT"):
            os.environ.pop(key, None)
        self.temporary.cleanup()

    def _environment(self):
        return {**os.environ, "EU4_CAMPAIGN_STATE": str(self.path),
                "PYTHONPATH": str(ROOT)}

    def _child(self, source: str, **kwargs):
        return subprocess.run(
            [sys.executable, "-c", source], env=self._environment(),
            capture_output=True, text=True, timeout=60, **kwargs)

    def test_sequence_reserves_supplied_frames_not_max_frames(self):
        from automation import agent_tools, m7_continuous

        self.guard.init(campaign_id="frames", budgets={"replays": 8})
        frame_a = self.root / "frame-a"
        frame_b = self.root / "frame-b"
        report = {"status": "complete", "stop_reason": "frame_limit",
                  "qualified": True, "frames": []}
        args = agent_tools._parser().parse_args([
            "--out", str(self.root / "tool.json"),
            "eu4_replay", "sequence",
            "--frame-dir", str(frame_a), "--frame-dir", str(frame_b),
            "--shaders", str(self.root / "shaders"),
            "--replay-out", str(self.root / "sequence-out"),
            "--max-frames", "8",
        ])
        with mock.patch.dict(os.environ,
                             {"EU4_CAMPAIGN_STATE": str(self.path)}), \
             mock.patch.object(m7_continuous, "run_campaign",
                               return_value=report):
            value = agent_tools.eu4_replay(args)
        self.assertEqual(value["status"], "ok")
        state = self.guard.state()
        self.assertEqual(state["counts"]["replays"], 2)
        reservation = next(iter(state["reservations"].values()))
        self.assertEqual(reservation["actions"], {"replays": 2})
        self.assertEqual(reservation["state"], "completed")

    def test_case_replay_reserves_supplied_frames_not_target_index(self):
        from automation import gfx_replay_case

        self.guard.init(campaign_id="case-frames", budgets={"replays": 8})
        case = self.root / "sequence-case"
        case.mkdir()
        (case / "case.json").write_text(json.dumps({
            "schema": gfx_replay_case.SEQUENCE_SCHEMA,
            "case_id": "sequence-case",
        }), encoding="utf-8")
        (case / "sequence.json").write_text(json.dumps({
            "target_index": 1,
            "frames": [
                {"index": 1, "dir": "frames/000001"},
                {"index": 2, "dir": "frames/000002"},
            ],
        }), encoding="utf-8")
        with mock.patch.dict(os.environ,
                             {"EU4_CAMPAIGN_STATE": str(self.path)}), \
             mock.patch.object(gfx_replay_case, "verify",
                               return_value={"ok": True, "problems": []}), \
             mock.patch.object(gfx_replay_case, "_replay_sequence_case",
                               return_value={"status": "ok"}):
            gfx_replay_case.replay_case(case, self.root / "case-out")
        state = self.guard.state()
        self.assertEqual(state["counts"]["replays"], 2)
        reservation = next(iter(state["reservations"].values()))
        self.assertEqual(reservation["actions"], {"replays": 2})

    def test_direct_replay_nonzero_settles_failed_with_evidence(self):
        self.guard.init(campaign_id="direct", budgets={"replays": 2})
        frame = self.root / "frame"
        frame.mkdir()
        out = self.root / "out"
        args = m6_frame._parser().parse_args(
            ["replay", "--frame", str(frame), "--out", str(out)])

        def fail_run(*_args, **_kwargs):
            return types.SimpleNamespace(returncode=7,
                                         stderr="host exploded\n",
                                         stdout="")

        with mock.patch.dict(os.environ,
                             {"EU4_CAMPAIGN_STATE": str(self.path)}), \
             mock.patch.object(m6_frame, "build_replay_host",
                               return_value=Path("/nonexistent/replay-host")), \
             mock.patch.object(m6_frame.subprocess, "run",
                               side_effect=fail_run):
            code = m6_frame.replay_command(args)
        self.assertEqual(code, 1)
        state = self.guard.state()
        self.assertEqual(state["counts"]["replays"], 1)
        reservation = next(iter(state["reservations"].values()))
        self.assertEqual(reservation["state"], "failed")
        evidence = reservation["completion_evidence"]
        self.assertEqual(evidence["status"], "error")
        self.assertEqual(evidence["exit_code"], 1)
        self.assertEqual(evidence["returncode"], 7)
        self.assertIn("host exploded", evidence["stderr_tail"])
        self.assertIn("automation.m6_frame", " ".join(evidence["command"]))
        completions = [row for row in state["history"]
                       if row.get("action") == "complete"]
        self.assertEqual(len(completions), 1)

    def test_failed_nested_attempt_settles_once(self):
        self.guard.init(campaign_id="nested-failure", budgets={"replays": 2})
        with mock.patch.dict(os.environ,
                             {"EU4_CAMPAIGN_STATE": str(self.path)}):
            with campaign_guard.attempt("replays", count=2):
                with self.assertRaises(RuntimeError):
                    with campaign_guard.attempt("replays", count=2):
                        raise RuntimeError("inner replay failed")
        state = self.guard.state()
        self.assertEqual(state["counts"]["replays"], 2)
        self.assertEqual(
            len([row for row in state["history"]
                 if row.get("action") == "reserve"]), 1)
        completions = [row for row in state["history"]
                       if row.get("action") == "complete"]
        self.assertEqual(len(completions), 1)
        self.assertEqual(completions[0]["outcome"], "failed")
        reservation = next(iter(state["reservations"].values()))
        self.assertEqual(reservation["state"], "failed")

    def test_separate_process_joins_the_ancestor_reservation(self):
        self.guard.init(campaign_id="child-join", budgets={"replays": 2})
        child = """
from automation import campaign_guard
with campaign_guard.attempt("replays", count=2):
    pass
"""
        with mock.patch.dict(os.environ,
                             {"EU4_CAMPAIGN_STATE": str(self.path)}):
            with campaign_guard.attempt("replays", count=2):
                ran = self._child(child)
        self.assertEqual(ran.returncode, 0, ran.stderr)
        state = self.guard.state()
        self.assertEqual(state["counts"]["replays"], 2)
        self.assertEqual(
            len([row for row in state["history"]
                 if row.get("action") == "reserve"]), 1)
        self.assertEqual(len(state["reservations"]), 1)

    def test_failed_child_settles_exactly_once(self):
        self.guard.init(campaign_id="child-failure", budgets={"replays": 2})
        child = """
import sys
from automation import campaign_guard
with campaign_guard.attempt("replays", count=2) as attempt:
    attempt.fail("child replay failed", evidence={
        "status": "failed", "exit_code": 9,
        "stderr_tail": "child stderr", "command": ["child"],})
sys.exit(9)
"""
        with mock.patch.dict(os.environ,
                             {"EU4_CAMPAIGN_STATE": str(self.path)}):
            with campaign_guard.attempt("replays", count=2):
                ran = self._child(child)
                self.assertEqual(ran.returncode, 9)
        state = self.guard.state()
        self.assertEqual(state["counts"]["replays"], 2)
        completions = [row for row in state["history"]
                       if row.get("action") == "complete"]
        self.assertEqual(len(completions), 1)
        self.assertEqual(completions[0]["outcome"], "failed")
        reservation = next(iter(state["reservations"].values()))
        self.assertEqual(reservation["state"], "failed")
        self.assertEqual(reservation["completion_evidence"]["exit_code"], 9)
        self.assertIn("child stderr",
                      reservation["completion_evidence"]["stderr_tail"])

    def test_direct_replay_failure_without_a_campaign_still_returns_its_exit(self):
        frame = self.root / "frame"
        frame.mkdir()
        out = self.root / "out"
        args = m6_frame._parser().parse_args(
            ["replay", "--frame", str(frame), "--out", str(out)])

        def fail_run(*_args, **_kwargs):
            return types.SimpleNamespace(returncode=7,
                                         stderr="host exploded\n",
                                         stdout="")

        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("EU4_CAMPAIGN_STATE", None)
            os.environ.pop("EU4_CAMPAIGN_ATTEMPT", None)
            with mock.patch.object(m6_frame, "build_replay_host",
                                   return_value=Path("/nonexistent/host")), \
                 mock.patch.object(m6_frame.subprocess, "run",
                                   side_effect=fail_run):
                code = m6_frame.replay_command(args)
        self.assertEqual(code, 1)
        self.assertFalse(self.path.exists())

    def test_direct_subprocess_failure_without_a_campaign_keeps_its_report(self):
        from automation import gfx_independent_replay

        report = {"status": "invalid", "returncode": 4,
                  "stderr": "native host failed\n", "command": ["native"],
                  "execution": {"status": "failed"}}
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("EU4_CAMPAIGN_STATE", None)
            os.environ.pop("EU4_CAMPAIGN_ATTEMPT", None)
            with mock.patch.object(gfx_independent_replay, "_replay_locked",
                                   return_value=report):
                result = gfx_independent_replay.replay(
                    self.root / "frame", self.root / "out")
        self.assertEqual(result, report)
        self.assertFalse(self.path.exists())

    def test_outer_sequence_and_inner_case_replay_share_one_reservation(self):
        from automation import (agent_tools, gfx_independent_replay,
                                gfx_replay_case, m7_continuous)

        self.guard.init(campaign_id="nested-case", budgets={"replays": 8})
        frame_a = self.root / "frame-a"
        frame_b = self.root / "frame-b"
        case = self.root / "case"
        case.mkdir()
        (case / "case.json").write_text(json.dumps({
            "schema": "gfx-strategy-b-case/v1", "case_id": "inner",
        }), encoding="utf-8")
        inner_report = {"status": "confirmed_rendering_difference",
                        "execution": {"status": "ok"},
                        "command": ["inner-host"], "returncode": 0,
                        "stderr": ""}
        outer_report = {"status": "complete", "stop_reason": "frame_limit",
                        "qualified": True, "frames": []}

        def run_inner(*_args, **_kwargs):
            inner = gfx_replay_case.replay_case(case, self.root / "inner-out")
            self.assertEqual(inner["status"],
                             "confirmed_rendering_difference")
            with campaign_guard.attempt("replays", count=1):
                pass
            return outer_report

        args = agent_tools._parser().parse_args([
            "--out", str(self.root / "tool.json"),
            "eu4_replay", "sequence",
            "--frame-dir", str(frame_a), "--frame-dir", str(frame_b),
            "--shaders", str(self.root / "shaders"),
            "--replay-out", str(self.root / "sequence-out"),
            "--max-frames", "8",
        ])
        with mock.patch.dict(os.environ,
                             {"EU4_CAMPAIGN_STATE": str(self.path)}), \
             mock.patch.object(gfx_replay_case, "verify",
                               return_value={"ok": True, "problems": []}), \
             mock.patch.object(gfx_independent_replay, "_replay_locked",
                               return_value=inner_report), \
             mock.patch.object(m7_continuous, "run_campaign",
                               side_effect=run_inner):
            value = agent_tools.eu4_replay(args)
        self.assertEqual(value["status"], "ok")
        state = self.guard.state()
        self.assertEqual(len(state["reservations"]), 1)
        reservation = next(iter(state["reservations"].values()))
        self.assertEqual(reservation["actions"], {"replays": 2})
        self.assertEqual(state["counts"]["replays"], 2)

    def test_direct_subprocess_failure_is_recorded_with_its_identity(self):
        from automation import gfx_independent_replay

        self.guard.init(campaign_id="subprocess", budgets={"replays": 1})
        report = {
            "status": "invalid", "returncode": 4,
            "stderr": "native host failed\n", "command": ["native-host"],
            "execution": {"status": "failed",
                          "error": "native host exited"},
        }
        with mock.patch.dict(os.environ,
                             {"EU4_CAMPAIGN_STATE": str(self.path)}), \
             mock.patch.object(gfx_independent_replay, "_replay_locked",
                               return_value=report):
            result = gfx_independent_replay.replay(
                self.root / "frame", self.root / "out")
        self.assertEqual(result, report)
        reservation = next(iter(self.guard.state()["reservations"].values()))
        self.assertEqual(reservation["state"], "failed")
        evidence = reservation["completion_evidence"]
        self.assertEqual(evidence["status"], "invalid")
        self.assertEqual(evidence["exit_code"], 4)
        self.assertIn("native host failed", evidence["stderr_tail"])
        self.assertEqual(evidence["command"], ["native-host"])

    def test_two_processes_contend_for_the_same_budget(self):
        self.guard.init(campaign_id="contend", budgets={"replays": 1})
        child = """
import sys, time
from automation import campaign_guard
try:
    with campaign_guard.attempt("replays"):
        time.sleep(0.4)
except campaign_guard.CampaignRefusal as exc:
    print(exc.kind)
    sys.exit(2)
"""
        processes = [
            subprocess.Popen([sys.executable, "-c", child],
                             env=self._environment(),
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                             text=True)
            for _ in range(2)
        ]
        results = [process.communicate(timeout=60) for process in processes]
        codes = sorted(process.returncode for process in processes)
        self.assertEqual(codes, [0, 2], results)
        state = self.guard.state()
        self.assertEqual(state["counts"]["replays"], 1)
        self.assertEqual(len(state["reservations"]), 1)
        self.assertEqual(
            len([row for row in state["history"]
                 if row.get("action") == "reserve"]), 1)


if __name__ == "__main__":
    unittest.main()


class EnsureStateTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.path = Path(self.temporary.name) / "state.json"

    def _ensure(self):
        return campaign_guard.main([
            "ensure", "--state", str(self.path),
            "--budget", "replays=2", "--budget", "wall_clock_seconds=600",
        ])

    def test_ensure_creates_once_then_refuses_consumed_state(self):
        import contextlib
        import io
        first = io.StringIO()
        with contextlib.redirect_stdout(first):
            self.assertEqual(self._ensure(), 0)
        second = io.StringIO()
        with contextlib.redirect_stdout(second):
            self.assertEqual(self._ensure(), 0)
        guard = campaign_guard.Guard(self.path)
        guard.reserve({"replays": 1})
        third = io.StringIO()
        with contextlib.redirect_stdout(third):
            self.assertEqual(self._ensure(), 2)
        self.assertIn("consumed budget", third.getvalue())
