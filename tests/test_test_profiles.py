"""Explicit offline/live profile contract (M7-P2).

These tests are portable and offline.  They prove that the default profile is
offline, that a profiled offline run cannot reach a live launch entry point,
and that the mandatory offline suite manifest cannot silently shrink.
"""

from __future__ import annotations

import os
import unittest
from pathlib import Path
from unittest import mock

from automation import test_profiles, test_runner


class ProfileSelectionTests(unittest.TestCase):
    def test_absent_profile_defaults_to_offline(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertEqual(test_profiles.active_profile(),
                             test_profiles.OFFLINE)
            self.assertFalse(test_profiles.live_allowed())
        # An unset variable is production, where the historical launch paths
        # remain available; an explicit offline profile is what fails closed.
        with mock.patch.dict(
                os.environ, {test_profiles.PROFILE_ENV:
                             test_profiles.OFFLINE}, clear=True):
            with self.assertRaises(test_profiles.LiveProfileError):
                test_profiles.ensure_live_launch("unit test launch")

    def test_explicit_live_profile_allows_launch(self):
        with mock.patch.dict(
                os.environ, {test_profiles.PROFILE_ENV: test_profiles.LIVE},
                clear=True):
            self.assertEqual(test_profiles.active_profile(),
                             test_profiles.LIVE)
            self.assertTrue(test_profiles.live_allowed())
            test_profiles.ensure_live_launch("unit test launch")

    def test_offline_marker_wins_even_if_other_variable_is_live(self):
        with mock.patch.dict(os.environ, {
                test_profiles.PROFILE_ENV: test_profiles.LIVE,
                test_profiles.OFFLINE_ONLY_ENV: "1"}, clear=True):
            self.assertFalse(test_profiles.live_allowed())
            with self.assertRaises(test_profiles.LiveProfileError):
                test_profiles.ensure_live_launch("unit test launch")

    def test_unknown_profile_is_refused_not_guessed(self):
        with mock.patch.dict(
                os.environ, {test_profiles.PROFILE_ENV: "interactive"},
                clear=True):
            with self.assertRaises(ValueError):
                test_profiles.active_profile()
            self.assertFalse(test_profiles.live_allowed())

    def test_offline_environment_carries_the_marker(self):
        environment = test_profiles.profile_environment(test_profiles.OFFLINE)
        self.assertEqual(environment[test_profiles.PROFILE_ENV],
                         test_profiles.OFFLINE)
        self.assertEqual(environment[test_profiles.OFFLINE_ONLY_ENV], "1")
        live = test_profiles.profile_environment(test_profiles.LIVE)
        self.assertEqual(live[test_profiles.PROFILE_ENV], test_profiles.LIVE)
        self.assertNotIn(test_profiles.OFFLINE_ONLY_ENV, live)

    def test_requires_live_is_a_classified_skip_offline(self):
        @test_profiles.requires_live("needs a real game process")
        def live_test():
            return "ran"

        with mock.patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(unittest.SkipTest) as caught:
                live_test()
        self.assertIn("EU4_TEST_PROFILE=live", str(caught.exception))
        self.assertIn("needs a real game process", str(caught.exception))


class LaunchEntryPointGuardTests(unittest.TestCase):
    def test_startup_run_ops_refuses_offline_before_spawn(self):
        import tempfile

        import eu4_gfx_startup_trace as startup

        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "run"
            with mock.patch.dict(
                    os.environ, {test_profiles.PROFILE_ENV:
                                 test_profiles.OFFLINE}, clear=True):
                with self.assertRaises(test_profiles.LiveProfileError):
                    startup.run_ops(
                        targets=[], max_events=1, duration_s=1.0,
                        timeout_s=1.0, output=output, authorize_live=True,
                        evidence={"executable_sha256": "0" * 64})
            self.assertFalse(output.exists())

    def test_gfx_launch_spawn_refuses_offline_before_preparing(self):
        from automation import gfx_launch

        with mock.patch.dict(
                os.environ, {test_profiles.PROFILE_ENV:
                             test_profiles.OFFLINE}, clear=True):
            with self.assertRaises(gfx_launch.LaunchError) as caught:
                gfx_launch.spawn("reference", None, Path("."), lease=None,
                                 session=None)
        self.assertEqual(caught.exception.kind, "test_profile")


class OfflineGateTests(unittest.TestCase):
    def test_mandatory_manifest_modules_exist(self):
        value = test_profiles.verify_offline_gate()
        self.assertTrue(value["ok"], value["problems"])
        self.assertEqual(value["profile"], test_profiles.OFFLINE)
        self.assertGreaterEqual(len(value["mandatory_modules"]), 6)
        for item in value["mandatory_modules"]:
            self.assertTrue(item["present"], item)

    def test_duplicate_manifest_is_reported(self):
        with mock.patch.object(
                test_profiles, "MANDATORY_OFFLINE_MODULES",
                ("tests.test_agent_tools", "tests.test_agent_tools")):
            value = test_profiles.verify_offline_gate()
        self.assertFalse(value["ok"])
        self.assertTrue(any(problem["code"] == "duplicate_mandatory_module"
                            for problem in value["problems"]))

    def test_missing_manifest_module_is_reported(self):
        with mock.patch.object(
                test_profiles, "MANDATORY_OFFLINE_MODULES",
                ("tests.test_module_that_does_not_exist",)):
            value = test_profiles.verify_offline_gate()
        self.assertFalse(value["ok"])
        self.assertTrue(any(problem["code"] == "unimportable_test_module"
                            for problem in value["problems"]))


class TestRunnerProfileTests(unittest.TestCase):
    def test_live_profile_requires_an_explicit_selection(self):
        with self.assertRaises(ValueError) as caught:
            test_runner.run_suite(profile=test_profiles.LIVE)
        self.assertIn("explicit module", str(caught.exception))

    def test_unknown_profile_is_refused(self):
        with self.assertRaises(ValueError):
            test_runner.run_suite(profile="interactive")

    def test_nonpositive_test_floor_is_refused(self):
        with self.assertRaises(ValueError):
            test_runner.run_suite(min_tests=0)


if __name__ == "__main__":
    unittest.main()
