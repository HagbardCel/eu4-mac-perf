"""Contracts for the native build/prerequisite split.

These tests are portable: they inject a fake build runner, so no Metal,
OpenGL or real compiler is needed.  They encode the distinction the native
suites rely on: a missing, declared prerequisite is a skip; an eligible
compiler failure, timeout or execution error is a failure; a successful build
lets the suite's assertions execute.
"""

from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from automation import native_build  # noqa: E402


def _completed(returncode: int = 0, stdout: str = "",
               stderr: str = "") -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(
        ["injected-compiler"], returncode, stdout, stderr)


def _fake_runner(result=None, error: BaseException | None = None):
    calls: list[tuple[list[str], dict]] = []

    def runner(command, **kwargs):
        calls.append((list(command), kwargs))
        if error is not None:
            raise error
        return result

    return runner, calls


def _log_path(message: str) -> Path:
    return Path(message.rsplit("log=", 1)[1].strip())


class PrerequisiteTests(unittest.TestCase):
    def test_missing_compiler_is_a_declared_skip_with_a_reason(self):
        with mock.patch.object(native_build.shutil, "which", return_value=None):
            with self.assertRaises(unittest.SkipTest) as caught:
                native_build.require_prerequisites(
                    tools=("eu4-definitely-missing-tool",),
                    require_macos=False, require_arm64=False)
        reason = str(caught.exception)
        self.assertIn("eu4-definitely-missing-tool", reason)
        self.assertIn("unavailable", reason)

    def test_missing_sdk_framework_is_a_declared_skip_with_a_reason(self):
        with mock.patch.object(native_build, "_framework_present",
                               return_value=False):
            with self.assertRaises(unittest.SkipTest) as caught:
                native_build.require_prerequisites(
                    frameworks=("DefinitelyMissingSDKFramework",),
                    require_macos=False, require_arm64=False)
        reason = str(caught.exception)
        self.assertIn("DefinitelyMissingSDKFramework", reason)
        self.assertIn("unavailable", reason)

    def test_missing_source_is_a_declared_skip(self):
        with tempfile.TemporaryDirectory() as temporary:
            missing = Path(temporary) / "missing.mm"
            with self.assertRaises(unittest.SkipTest) as caught:
                native_build.require_prerequisites(
                    sources=[missing], require_macos=False,
                    require_arm64=False)
        self.assertIn(str(missing), str(caught.exception))

    def test_arm64_requirement_is_opt_in(self):
        with mock.patch.object(native_build.platform, "machine",
                               return_value="x86_64"):
            # An architecture-independent OpenGL-only suite still runs.
            native_build.require_prerequisites(
                tools=(), require_macos=False, require_arm64=False)
            with self.assertRaises(unittest.SkipTest) as caught:
                native_build.require_prerequisites(
                    tools=(), require_macos=False, require_arm64=True)
        self.assertIn("arm64", str(caught.exception))

    def test_macos_requirement_is_opt_in(self):
        with mock.patch.object(native_build.sys, "platform", "linux"):
            native_build.require_prerequisites(
                tools=(), require_macos=False, require_arm64=False)
            with self.assertRaises(unittest.SkipTest) as caught:
                native_build.require_prerequisites(
                    tools=(), require_macos=True, require_arm64=False)
        self.assertIn("macOS", str(caught.exception))


class EligibleBuildTests(unittest.TestCase):
    def test_eligible_compile_failure_fails_with_evidence_and_log(self):
        runner, calls = _fake_runner(_completed(
            2, stderr="error: expected ';' after expression\n"))
        with tempfile.TemporaryDirectory() as temporary, \
                mock.patch.object(native_build, "LOG_ROOT",
                                  Path(temporary)):
            with self.assertRaises(AssertionError) as caught:
                native_build.build(
                    ["clang", "-c", "broken.mm"],
                    label="native-build-contract", runner=runner)
            message = str(caught.exception)
            self.assertIn("eligible native build failed", message)
            self.assertIn("native-build-contract", message)
            self.assertIn("exit 2", message)
            self.assertIn("error: expected ';'", message)
            self.assertIn("log=", message)
            log = _log_path(message)
            self.assertTrue(log.is_file(), message)
            self.assertEqual(log.parent, Path(temporary))
            evidence = log.read_text()
            self.assertIn("argv:", evidence)
            self.assertIn("exit: 2", evidence)
            self.assertIn("error: expected ';'", evidence)
        self.assertNotIsInstance(caught.exception, unittest.SkipTest)
        self.assertEqual(len(calls), 1)

    def test_compiler_output_is_compact_and_the_log_keeps_the_rest(self):
        marker = "FULL-COMPILER-TAIL-MARKER"
        long_stderr = "error: first diagnostic\n" + ("x" * 10_000) + marker
        runner, _ = _fake_runner(_completed(1, stderr=long_stderr))
        with tempfile.TemporaryDirectory() as temporary, \
                mock.patch.object(native_build, "LOG_ROOT",
                                  Path(temporary)):
            with self.assertRaises(AssertionError) as caught:
                native_build.build(["clang"], label="compact-contract",
                                   runner=runner)
            message = str(caught.exception)
            self.assertIn("error: first diagnostic", message)
            self.assertIn("[truncated]", message)
            self.assertNotIn(marker, message)
            log = _log_path(message)
            self.assertTrue(log.is_file(), message)
            self.assertIn(marker, log.read_text())

    def test_timeout_is_a_failure_not_a_skip(self):
        runner, calls = _fake_runner(error=subprocess.TimeoutExpired(
            ["clang"], 0.25, output="partial stdout",
            stderr="partial compiler output"))
        with tempfile.TemporaryDirectory() as temporary, \
                mock.patch.object(native_build, "LOG_ROOT",
                                  Path(temporary)):
            with self.assertRaises(AssertionError) as caught:
                native_build.build(["clang"], label="timeout-contract",
                                   timeout=0.25, runner=runner)
            message = str(caught.exception)
            self.assertNotIsInstance(caught.exception, unittest.SkipTest)
            self.assertIn("timed out after 0.25s", message)
            self.assertIn("timeout-contract", message)
            self.assertIn("partial compiler output", message)
            self.assertIn("log=", message)
            log = _log_path(message)
            self.assertTrue(log.is_file(), message)
            evidence = log.read_text()
            self.assertIn("timed out after 0.25s", evidence)
            self.assertIn("partial compiler output", evidence)
        self.assertEqual(len(calls), 1)

    def test_execution_error_is_a_failure_not_a_skip(self):
        runner, calls = _fake_runner(error=FileNotFoundError(
            2, "No such file or directory", "eu4-missing-clang"))
        with tempfile.TemporaryDirectory() as temporary, \
                mock.patch.object(native_build, "LOG_ROOT",
                                  Path(temporary)):
            with self.assertRaises(AssertionError) as caught:
                native_build.build(["eu4-missing-clang"], label="exec-contract",
                                   runner=runner)
            message = str(caught.exception)
            self.assertNotIsInstance(caught.exception, unittest.SkipTest)
            self.assertIn("could not run", message)
            self.assertIn("exec-contract", message)
            self.assertIn("eu4-missing-clang", message)
            self.assertIn("log=", message)
            log = _log_path(message)
            self.assertTrue(log.is_file(), message)
            self.assertIn("execution error", log.read_text())
        self.assertEqual(len(calls), 1)

    def test_passing_build_returns_the_completed_process(self):
        runner, calls = _fake_runner(_completed(0, stdout="built"))
        completed = native_build.build(
            ["clang", "-o", "host"], label="native-build-pass",
            timeout=42, runner=runner)
        self.assertEqual(completed.returncode, 0)
        self.assertEqual(completed.stdout, "built")
        self.assertEqual(len(calls), 1)
        command, kwargs = calls[0]
        self.assertEqual(command, ["clang", "-o", "host"])
        self.assertTrue(kwargs["capture_output"])
        self.assertTrue(kwargs["text"])
        self.assertFalse(kwargs["check"])
        self.assertEqual(kwargs["timeout"], 42)


def _run_synthetic_suite(setup) -> tuple[unittest.TestResult, list[bool]]:
    """Run a one-test suite whose ``setUpClass`` uses the shared helper."""
    ran: list[bool] = []

    class ContractSuite(unittest.TestCase):
        @classmethod
        def setUpClass(cls):
            setup()

        def test_declared_assertion_executes(self):
            ran.append(True)
            self.assertTrue(True)

    result = unittest.TestResult()
    unittest.defaultTestLoader.loadTestsFromTestCase(ContractSuite).run(result)
    return result, ran


class SuiteOutcomeTests(unittest.TestCase):
    """The suite-level result, not just the helper, must skip/fail correctly."""

    def test_missing_prerequisite_skips_the_suite_without_failing_it(self):
        def must_not_run(*_args, **_kwargs):
            raise AssertionError("the build must not run without a compiler")

        def setup():
            native_build.require_build(
                ["clang"], label="suite-skip",
                tools=("eu4-definitely-missing-tool",),
                require_macos=False, require_arm64=False,
                runner=must_not_run)

        result, ran = _run_synthetic_suite(setup)
        self.assertTrue(result.wasSuccessful())
        self.assertEqual(result.errors, [])
        self.assertEqual(result.failures, [])
        self.assertEqual(len(result.skipped), 1)
        self.assertIn("eu4-definitely-missing-tool",
                      result.skipped[0][1])
        self.assertEqual(ran, [])

    def test_eligible_compile_failure_fails_the_suite_not_skips_it(self):
        runner, _ = _fake_runner(_completed(
            2, stderr="error: expected ';' after expression\n"))

        def setup():
            native_build.require_build(
                ["clang", "-c", "broken.mm"], label="suite-fail",
                tools=(), require_macos=False, require_arm64=False,
                runner=runner)

        with tempfile.TemporaryDirectory() as temporary, \
                mock.patch.object(native_build, "LOG_ROOT",
                                  Path(temporary)):
            result, ran = _run_synthetic_suite(setup)
            self.assertFalse(result.wasSuccessful())
            self.assertEqual(result.skipped, [])
            self.assertEqual(len(result.errors) + len(result.failures), 1)
            evidence = (result.errors or result.failures)[0][1]
            self.assertIn("eligible native build failed", evidence)
            self.assertIn("suite-fail", evidence)
            self.assertIn("exit 2", evidence)
            self.assertIn("error: expected ';'", evidence)
            self.assertIn("log=", evidence)
            log = _log_path(evidence)
            self.assertTrue(log.is_file(), evidence)
            self.assertIn("error: expected ';'", log.read_text())
        self.assertEqual(ran, [])

    def test_eligible_success_executes_the_suite_assertions(self):
        runner, calls = _fake_runner(_completed(0))

        def setup():
            native_build.require_build(
                ["clang", "-o", "host"], label="suite-pass",
                tools=(), require_macos=False, require_arm64=False,
                runner=runner)

        result, ran = _run_synthetic_suite(setup)
        self.assertTrue(result.wasSuccessful())
        self.assertEqual(result.testsRun, 1)
        self.assertEqual(result.errors, [])
        self.assertEqual(result.failures, [])
        self.assertEqual(result.skipped, [])
        self.assertEqual(ran, [True])
        self.assertEqual(len(calls), 1)


if __name__ == "__main__":
    unittest.main()
