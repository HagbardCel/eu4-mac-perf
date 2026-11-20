"""Contracts for the native build/prerequisite split.

These tests are portable: they use shell commands and mocks, not Metal or
OpenGL.  They encode the distinction the native suites rely on: a missing,
declared prerequisite is a skip; an eligible compiler failure is a failure.
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from automation import native_build  # noqa: E402


class PrerequisiteTests(unittest.TestCase):
    def test_missing_tool_is_a_declared_skip(self):
        with self.assertRaises(unittest.SkipTest) as caught:
            native_build.require_prerequisites(
                tools=("eu4-definitely-missing-tool",),
                require_macos=False, require_arm64=False)
        self.assertIn("eu4-definitely-missing-tool", str(caught.exception))

    def test_missing_source_is_a_declared_skip(self):
        with tempfile.TemporaryDirectory() as temporary:
            missing = Path(temporary) / "missing.mm"
            with self.assertRaises(unittest.SkipTest):
                native_build.require_prerequisites(
                    sources=[missing], require_macos=False,
                    require_arm64=False)

    def test_missing_framework_is_a_declared_skip(self):
        with mock.patch.object(native_build, "_framework_present",
                               return_value=False):
            with self.assertRaises(unittest.SkipTest):
                native_build.require_prerequisites(
                    frameworks=("DefinitelyMissing",), require_macos=False,
                    require_arm64=False)


class EligibleBuildTests(unittest.TestCase):
    def test_eligible_compile_failure_fails_with_evidence(self):
        with self.assertRaises(AssertionError) as caught:
            native_build.build(
                ["/bin/sh", "-c", "echo 'syntax error' >&2; exit 1"],
                label="native-build-contract")
        message = str(caught.exception)
        self.assertIn("eligible native build failed", message)
        self.assertIn("native-build-contract", message)
        self.assertIn("exit 1", message)
        self.assertIn("syntax error", message)
        self.assertIn("log=", message)

    def test_passing_build_returns_the_completed_process(self):
        completed = native_build.build(
            ["/bin/sh", "-c", "exit 0"], label="native-build-pass")
        self.assertEqual(completed.returncode, 0)


if __name__ == "__main__":
    unittest.main()
