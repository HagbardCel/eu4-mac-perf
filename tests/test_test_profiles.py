"""Explicit offline/live profile contract (M7-P2).

These tests are portable and offline.  They prove that the default profile is
offline, that a profiled offline run cannot reach a live launch entry point,
and that the mandatory offline suite manifest cannot silently shrink.
"""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from automation import test_profiles, test_runner
from tests.test_case_baseline import _write_case


class _SkippedMandatoryTest(unittest.TestCase):
    @unittest.skip("deliberate negative fixture")
    def test_skipped(self):
        pass


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


class ExecutedFloorTests(unittest.TestCase):
    def _tiny_module(self, root: Path, body: str) -> Path:
        path = root / "runner_tiny.py"
        path.write_text(body)
        return root

    def test_skipped_tests_do_not_satisfy_the_floor(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self._tiny_module(root, """import unittest
class Tiny(unittest.TestCase):
    @unittest.skip("classified")
    def test_skipped(self):
        pass
""")
            value = test_runner.run_suite(
                modules=["runner_tiny"], cwd=root,
                state_dir=root / "state", min_tests=1, timeout=60)
        self.assertEqual(value["status"], "incomplete")
        self.assertEqual(value["counts"]["executed"], 0)
        self.assertEqual(value["counts"]["skipped"], 1)
        self.assertEqual(value["failure_kind"], "test_not_executed")

    def test_executed_test_satisfies_the_floor(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self._tiny_module(root, """import unittest
class Tiny(unittest.TestCase):
    def test_runs(self):
        self.assertTrue(True)
""")
            value = test_runner.run_suite(
                modules=["runner_tiny"], cwd=root,
                state_dir=root / "state", min_tests=1, timeout=60)
        self.assertEqual(value["status"], "passed")
        self.assertEqual(value["counts"]["executed"], 1)

    def test_duplicate_identity_includes_the_floor(self):
        base = test_runner._suite(["tests.test_agent_tools"], None,
                                  Path.cwd(), "offline", 1)
        stricter = test_runner._suite(["tests.test_agent_tools"], None,
                                      Path.cwd(), "offline", 500)
        self.assertNotEqual(base["key"], stricter["key"])
        self.assertEqual(stricter["selection"]["min_tests"], 500)

    def test_duplicate_identity_includes_the_profile(self):
        offline = test_runner._suite(["tests.test_agent_tools"], None,
                                     Path.cwd(), "offline", 1)
        live = test_runner._suite(["tests.test_agent_tools"], None,
                                  Path.cwd(), "live", 1)
        self.assertNotEqual(offline["key"], live["key"])

    def test_suite_floor_mismatch_is_incomparable(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            record = {
                "id": "r", "status": "passed", "exit_code": 0,
                "completed": True,
                "counts": {"ran": 2, "executed": 2, "skipped": 0},
                "failures": [],
                "selection": {"kind": "focused", "modules": ["x"],
                              "pattern": None, "profile": "offline",
                              "min_tests": 1},
            }
            other = dict(record)
            other["selection"] = dict(record["selection"], min_tests=500)
            self.assertNotEqual(test_runner._selection(record),
                                test_runner._selection(other))


class MandatoryTestCoverageTests(unittest.TestCase):
    def test_mandatory_modules_have_offline_executable_tests(self):
        value = test_profiles.verify_offline_gate()
        self.assertTrue(value["ok"], value["problems"])
        for module in value["mandatory_modules"]:
            self.assertGreater(module["offline_executable"], 0, module)

    def test_skipped_mandatory_test_is_reported(self):
        dotted = ("tests.test_test_profiles._SkippedMandatoryTest."
                  "test_skipped")
        with mock.patch.object(test_profiles, "MANDATORY_OFFLINE_TEST_IDS",
                               (dotted,)):
            value = test_profiles.verify_offline_gate()
        self.assertFalse(value["ok"])
        self.assertTrue(any(problem["code"] == "mandatory_test_skipped"
                            for problem in value["problems"]))

    def test_missing_mandatory_test_is_reported(self):
        with mock.patch.object(
                test_profiles, "MANDATORY_OFFLINE_TEST_IDS",
                ("tests.test_test_profiles.NoSuchTest.test_nope",)):
            value = test_profiles.verify_offline_gate()
        self.assertFalse(value["ok"])
        self.assertTrue(any(problem["code"] == "mandatory_test_missing"
                            for problem in value["problems"]))


class LiveCaseVerificationTests(unittest.TestCase):
    def test_valid_live_case_passes(self):
        with tempfile.TemporaryDirectory() as temporary:
            case = _write_case(Path(temporary))
            result = test_profiles.verify_live_case(case)
        self.assertTrue(result["ok"], result["problems"])
        self.assertTrue(result["verification"]["independently_reproducible"])

    def test_missing_case_is_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            result = test_profiles.verify_live_case(Path(temporary) / "absent")
        self.assertFalse(result["ok"])
        self.assertEqual(result["problems"][0]["code"], "missing_case_json")

    def test_corrupt_live_case_is_rejected_even_with_an_artifact_path(self):
        with tempfile.TemporaryDirectory() as temporary:
            case = _write_case(Path(temporary))
            (case / "replay" / "metal-frame.bgra").write_bytes(b"\1" * 16)
            result = test_profiles.verify_live_case(case)
        self.assertFalse(result["ok"])
        self.assertTrue(any("digest drift" in problem["detail"]
                            for problem in result["problems"]))


if __name__ == "__main__":
    unittest.main()
