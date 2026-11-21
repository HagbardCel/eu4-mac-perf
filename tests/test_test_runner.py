"""Phase 4: focused, inspectable, duplicate-aware test execution."""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import tempfile
import textwrap
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from automation import test_runner  # noqa: E402

PASS = """
import unittest
class Passing(unittest.TestCase):
    def test_one(self):
        self.assertEqual(1, 1)
    def test_two(self):
        self.assertTrue(True)
"""

FAIL = """
import unittest
class Failing(unittest.TestCase):
    def test_good(self):
        self.assertTrue(True)
    def test_bad(self):
        self.assertEqual(1, 2)
"""

SLOW = """
import time
import unittest
class Slow(unittest.TestCase):
    def test_sleeps(self):
        time.sleep(30)
"""


class RunnerTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.state = self.root / "state"
        (self.root / "runner_pass.py").write_text(PASS)
        (self.root / "runner_fail.py").write_text(FAIL)
        (self.root / "runner_slow.py").write_text(SLOW)

    def run_module(self, module: str, **kwargs):
        return test_runner.run_suite(
            modules=[module], cwd=self.root, state_dir=self.state, **kwargs)

    def test_focused_pass_writes_a_log_and_a_compact_summary(self):
        value = self.run_module("runner_pass")
        self.assertEqual(value["status"], "passed")
        self.assertTrue(value["ok"])
        self.assertEqual(value["exit_code"], 0)
        self.assertEqual(value["kind"], "focused")
        self.assertEqual(value["counts"]["ran"], 2)
        self.assertEqual(value["counts"]["failures"], 0)
        self.assertTrue(Path(value["log"]).is_file())
        self.assertIn("Ran 2 tests", Path(value["log"]).read_text())
        stored = json.loads(Path(value["record_path"]).read_text())
        self.assertEqual(stored["status"], "passed")
        self.assertEqual(stored["exit_code"], 0)

    def test_subprocess_exit_code_is_preserved_even_though_output_is_piped(self):
        value = self.run_module("runner_fail")
        self.assertEqual(value["status"], "failed")
        self.assertFalse(value["ok"])
        self.assertEqual(value["exit_code"], 1)
        self.assertEqual(value["counts"]["ran"], 2)
        identifiers = [row["id"] for row in value["failures"]]
        self.assertTrue(any(identifier.endswith("test_bad") for identifier in identifiers),
                        identifiers)
        # The log is complete, not just the tail the CLI prints.
        self.assertIn("FAILED (failures=1)", Path(value["log"]).read_text())

    def test_zero_test_success_is_not_a_passing_run(self):
        (self.root / "runner_empty.py").write_text("VALUE = 1\n")
        value = self.run_module("runner_empty")
        self.assertEqual(value["status"], "incomplete")
        self.assertFalse(value["ok"])
        self.assertEqual(value["counts"]["ran"], 0)
        self.assertEqual(value["exit_code"], 5)
        self.assertIn("zero tests", value["reason"])
        import contextlib
        import io
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            code = test_runner.main([
                "run", "--module", "runner_empty", "--cwd", str(self.root),
                "--state-dir", str(self.state)])
        self.assertNotEqual(code, 0, buffer.getvalue())

    def test_timeout_is_reported_and_the_child_is_stopped(self):
        value = self.run_module("runner_slow", timeout=1.0)
        self.assertEqual(value["status"], "timeout")
        self.assertTrue(value["timed_out"])
        self.assertIn("timeout", Path(value["log"]).read_text())

    def test_an_identical_running_suite_returns_its_status(self):
        suite = test_runner._suite(["runner_pass"], None, self.root.resolve())
        running = {
            "schema": test_runner.SCHEMA,
            "id": "already-running",
            "key": suite["key"],
            "status": "running",
            "pid": os.getpid(),
            "host": os.uname().nodename,
            "exit_code": None,
            "log": None,
        }
        path = self.state / "runs" / "already-running.json"
        path.parent.mkdir(parents=True)
        path.write_text(json.dumps(running))
        value = self.run_module("runner_pass")
        self.assertEqual(value["status"], "pending")
        self.assertTrue(value["pending"])
        self.assertFalse(value["completed"])
        self.assertFalse(value["ok"])
        self.assertEqual(value["duplicate_of"], "already-running")
        # CLI success must not imply a completed verification.
        import contextlib
        import io
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            code = test_runner.main([
                "run", "--module", "runner_pass", "--cwd", str(self.root),
                "--state-dir", str(self.state)])
        self.assertEqual(code, 2, buffer.getvalue())
        # No second invocation was launched.
        self.assertEqual(len(list((self.state / "runs").glob("*.log"))), 0)

    def test_status_and_list_expose_running_and_completed_invocations(self):
        value = self.run_module("runner_pass")
        listing = test_runner.list_runs(state_dir=self.state)
        self.assertEqual(listing["counts"]["total"], 1)
        self.assertEqual(listing["runs"][0]["id"], value["id"])
        shown = test_runner.status(value["id"], state_dir=self.state)
        self.assertEqual(shown["status"], "passed")
        self.assertFalse(shown["running"])
        self.assertTrue(shown["log_tail"])

    def test_status_marks_a_dead_running_record_lost(self):
        record = {
            "schema": test_runner.SCHEMA,
            "id": "lost-run",
            "status": "running",
            "pid": 99999999,
            "log": None,
        }
        path = self.state / "runs" / "lost-run.json"
        path.parent.mkdir(parents=True)
        path.write_text(json.dumps(record))
        shown = test_runner.status("lost-run", state_dir=self.state)
        self.assertEqual(shown["status"], "lost")

    def test_base_versus_candidate_failure_comparison(self):
        base = self.run_module("runner_fail")
        candidate = self.run_module("runner_fail")
        comparison = test_runner.compare_records(
            base["id"], candidate["id"], state_dir=self.state)
        self.assertEqual(comparison["verdict"], "no_new_failures")
        self.assertEqual(comparison["new_failures"], [])
        self.assertEqual(len(comparison["preexisting_failures"]), 1)
        passed = self.run_module("runner_pass")
        comparison = test_runner.compare_records(
            base["id"], passed["id"], state_dir=self.state)
        # Different test selections are not a regression comparison.
        self.assertEqual(comparison["verdict"], "incomparable")
        self.assertTrue(comparison["reasons"])

    def test_a_new_failure_is_reported_against_a_clean_base(self):
        base = self.run_module("runner_pass")
        # Same selection, changed implementation: the candidate genuinely adds
        # a failure without changing which tests were requested.
        (self.root / "runner_pass.py").write_text(FAIL)
        candidate = self.run_module("runner_pass")
        comparison = test_runner.compare_records(
            base["id"], candidate["id"], state_dir=self.state)
        self.assertTrue(comparison["comparable"], comparison)
        self.assertEqual(comparison["verdict"], "new_failures")
        self.assertTrue(comparison["new_failures"])

    def test_unrelated_suites_are_incomparable(self):
        base = self.run_module("runner_pass")
        candidate = self.run_module("runner_fail")
        comparison = test_runner.compare_records(
            base["id"], candidate["id"], state_dir=self.state)
        self.assertFalse(comparison["comparable"])
        self.assertEqual(comparison["verdict"], "incomparable")
        self.assertEqual(comparison["new_failures"], [])
        import contextlib
        import io
        with contextlib.redirect_stdout(io.StringIO()):
            code = test_runner.main([
                "compare", "--base", base["id"], "--candidate", candidate["id"],
                "--state-dir", str(self.state)])
        self.assertEqual(code, 2)

    def test_matching_suites_in_separate_worktrees_are_comparable(self):
        other = self.root / "other-worktree"
        other.mkdir()
        (other / "runner_pass.py").write_text(PASS)
        base = self.run_module("runner_pass")
        candidate = test_runner.run_suite(
            modules=["runner_pass"], cwd=other, state_dir=self.state)
        comparison = test_runner.compare_records(
            base["id"], candidate["id"], state_dir=self.state)
        self.assertTrue(comparison["comparable"], comparison)
        self.assertEqual(comparison["verdict"], "no_new_failures")
        self.assertNotEqual(comparison["base"]["cwd"],
                            comparison["candidate"]["cwd"])

    def test_interrupted_and_zero_test_runs_are_incomparable(self):
        base = self.run_module("runner_pass")
        timeout = self.run_module("runner_slow", timeout=1.0)
        timeout_record = json.loads(Path(timeout["record_path"]).read_text())
        timeout_record["selection"] = {
            "kind": "focused", "modules": ["runner_pass"], "pattern": None}
        Path(timeout["record_path"]).write_text(json.dumps(timeout_record))
        comparison = test_runner.compare_records(
            base["id"], timeout["id"], state_dir=self.state)
        self.assertEqual(comparison["verdict"], "incomparable")
        self.assertIn("timed out", " ".join(comparison["reasons"]))

        empty = {
            "schema": test_runner.SCHEMA,
            "id": "zero-tests",
            "status": "passed",
            "completed": True,
            "exit_code": 0,
            "selection": {"kind": "focused", "modules": ["runner_pass"],
                          "pattern": None},
            "counts": {"ran": 0, "failures": 0, "errors": 0, "skipped": 0},
            "failures": [],
        }
        path = self.state / "runs" / "zero-tests.json"
        path.write_text(json.dumps(empty))
        comparison = test_runner.compare_records(
            base["id"], "zero-tests", state_dir=self.state)
        self.assertEqual(comparison["verdict"], "incomparable")
        self.assertIn("zero tests", " ".join(comparison["reasons"]))

        running = {
            "schema": test_runner.SCHEMA,
            "id": "interrupted",
            "status": "running",
            "selection": {"kind": "focused", "modules": ["runner_pass"],
                          "pattern": None},
            "pid": os.getpid(),
            "exit_code": None,
            "counts": {"ran": 0},
            "failures": [],
        }
        path = self.state / "runs" / "interrupted.json"
        path.write_text(json.dumps(running))
        comparison = test_runner.compare_records(
            base["id"], "interrupted", state_dir=self.state)
        self.assertEqual(comparison["verdict"], "incomparable")
        self.assertIn("not a completed test run",
                      " ".join(comparison["reasons"]))

    def test_full_suite_is_the_only_implicit_invocation(self):
        suite = test_runner._suite([], None, self.root.resolve())
        self.assertEqual(suite["kind"], "full")
        self.assertEqual(suite["argv"],
                         ["-m", "unittest", "discover", "-s", "tests"])
        focused = test_runner._suite(["tests.test_agent_tools"], None, ROOT)
        self.assertEqual(focused["kind"], "focused")

    def test_failure_ids_are_stable_across_invocation_modes(self):
        module_style = textwrap.dedent("""
            FAIL: test_bad (tests.test_mod.Class.test_bad)
            FAILED (failures=1)
        """)
        discover_style = textwrap.dedent("""
            FAIL: test_bad (test_mod.Class.test_bad)
            FAILED (failures=1)
        """)
        first = test_runner._parse_log(module_style)["failures"][0]
        second = test_runner._parse_log(discover_style)["failures"][0]
        self.assertEqual(first["id"], second["id"])
        self.assertEqual(first["id"], "test_mod.Class.test_bad")

    def test_unknown_run_is_a_clear_error(self):
        with self.assertRaises(ValueError):
            test_runner.status("missing", state_dir=self.state)
        with self.assertRaises(ValueError):
            test_runner.compare_records("missing", "also-missing",
                                        state_dir=self.state)


if __name__ == "__main__":
    unittest.main()


SLOW_MODULE = """
import os
import time
import unittest
class Slow(unittest.TestCase):
    def test_sleeps(self):
        time.sleep(float(os.environ.get("TEST_RUNNER_SLEEP", "30")))
"""

BARRIER_MODULE = """
import os
import time
import unittest
from pathlib import Path
class Barrier(unittest.TestCase):
    def test_the_other_worktree_starts_while_this_one_runs(self):
        directory = Path(os.environ["BARRIER_DIR"])
        (directory / (os.environ["BARRIER_NAME"] + ".started")).write_text("1")
        wait_for = directory / os.environ["BARRIER_WAIT"]
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            if wait_for.exists():
                return
            time.sleep(0.02)
        self.fail("the other worktree did not start while this one was running")
"""


class WorktreeExecutionLockTests(unittest.TestCase):
    """Different selections may not execute concurrently in one worktree."""

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.state = self.root / "state"
        self.worktree = self.root / "worktree"
        self.worktree.mkdir()

    def _module(self, worktree: Path, name: str, body: str) -> None:
        (worktree / f"{name}.py").write_text(textwrap.dedent(body))

    def _command(self, module: str, worktree: Path) -> list[str]:
        return [sys.executable, "-m", "automation.test_runner", "run",
                "--profile", "offline", "--module", module,
                "--cwd", str(worktree), "--state-dir", str(self.state)]

    def _environment(self, **extra) -> dict:
        environment = dict(os.environ)
        environment["PYTHONPATH"] = str(ROOT)
        environment.update({key: str(value) for key, value in extra.items()})
        return environment

    def _spawn(self, module: str, worktree: Path, **env):
        return subprocess.Popen(
            self._command(module, worktree), cwd=str(ROOT),
            env=self._environment(**env), stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, text=True)

    def _wait_for_running_record(self, timeout: float = 15.0) -> dict:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            directory = self.state / "runs"
            if directory.is_dir():
                for path in sorted(directory.glob("*.json")):
                    try:
                        value = json.loads(path.read_text())
                    except (OSError, json.JSONDecodeError):
                        continue
                    if value.get("status") == "running":
                        return value
            time.sleep(0.02)
        self.fail("no running record appeared")

    def _collect(self, process, timeout: float = 60.0):
        stdout, stderr = process.communicate(timeout=timeout)
        try:
            value = json.loads(stdout)
        except json.JSONDecodeError:
            value = {}
        return process.returncode, value, stderr

    def test_two_different_selections_in_one_worktree_do_not_overlap(self):
        self._module(self.worktree, "runner_slowish", SLOW_MODULE)
        self._module(self.worktree, "runner_pass", PASS)
        slow = self._spawn("runner_slowish", self.worktree,
                           TEST_RUNNER_SLEEP="3")
        try:
            running = self._wait_for_running_record()
            second = self._spawn("runner_pass", self.worktree)
            code, value, stderr = self._collect(second, timeout=30)
        finally:
            code_slow, slow_value, slow_stderr = self._collect(slow, timeout=60)
        self.assertEqual(code_slow, 0, slow_stderr)
        self.assertEqual(slow_value.get("status"), "passed", slow_value)
        self.assertEqual(code, 2, (code, value, stderr))
        self.assertFalse(value.get("ok"))
        self.assertIn(value.get("status"), ("pending", "busy"))
        self.assertTrue(value.get("pending"))
        self.assertFalse(value.get("completed"))
        self.assertEqual(value.get("blocked_by"), running.get("id"))
        self.assertEqual(value.get("log"), running.get("log"))
        # The refused invocation created no second run record or log.
        self.assertEqual(len(list((self.state / "runs").glob("*.json"))), 1)
        self.assertEqual(len(list((self.state / "runs").glob("*.log"))), 1)

    def test_different_worktrees_proceed_concurrently(self):
        other = self.root / "other-worktree"
        other.mkdir()
        barrier = self.root / "barrier"
        barrier.mkdir()
        for worktree, name, wait_for in (
                (self.worktree, "a", "b.started"),
                (other, "b", "a.started")):
            self._module(worktree, "runner_barrier", BARRIER_MODULE)
        first = self._spawn("runner_barrier", self.worktree,
                            BARRIER_DIR=barrier, BARRIER_NAME="a",
                            BARRIER_WAIT="b.started")
        second = self._spawn("runner_barrier", other,
                             BARRIER_DIR=barrier, BARRIER_NAME="b",
                             BARRIER_WAIT="a.started")
        code_a, value_a, err_a = self._collect(first)
        code_b, value_b, err_b = self._collect(second)
        self.assertEqual(code_a, 0, err_a)
        self.assertEqual(code_b, 0, err_b)
        self.assertEqual(value_a.get("status"), "passed", value_a)
        self.assertEqual(value_b.get("status"), "passed", value_b)
        self.assertNotEqual(value_a.get("cwd"), value_b.get("cwd"))

    def test_abrupt_runner_termination_releases_the_worktree_lock(self):
        self._module(self.worktree, "runner_very_slow", SLOW_MODULE)
        self._module(self.worktree, "runner_pass", PASS)
        slow = self._spawn("runner_very_slow", self.worktree,
                           TEST_RUNNER_SLEEP="30")
        running = None
        try:
            running = self._wait_for_running_record()
            slow.kill()
            slow.wait(timeout=10)
        finally:
            if running and running.get("child_pid"):
                try:
                    os.kill(int(running["child_pid"]), signal.SIGKILL)
                except OSError:
                    pass
        # The kernel released the flock with the killed process; a different
        # selection in the same worktree can now run.
        second = self._spawn("runner_pass", self.worktree)
        code, value, stderr = self._collect(second, timeout=60)
        self.assertEqual(code, 0, (code, value, stderr))
        self.assertEqual(value.get("status"), "passed", value)
        self.assertEqual(value.get("counts", {}).get("ran"), 2)

    def test_a_held_lock_without_a_record_is_busy_not_passed(self):
        self._module(self.worktree, "runner_pass", PASS)
        handle = test_runner._try_lock(
            test_runner._worktree_lock_path(self.worktree))
        self.assertIsNotNone(handle)
        try:
            value = test_runner.run_suite(
                modules=["runner_pass"], cwd=self.worktree,
                state_dir=self.state)
        finally:
            test_runner._release_lock(handle)
        self.assertEqual(value["status"], "busy")
        self.assertFalse(value["ok"])
        self.assertFalse(value["pending"])
        self.assertFalse(value["completed"])
        self.assertIsNone(value["blocked_by"])
        self.assertFalse(list((self.state / "runs").glob("*.json")))

    def test_a_normal_run_releases_the_worktree_lock(self):
        self._module(self.worktree, "runner_pass", PASS)
        process = self._spawn("runner_pass", self.worktree)
        code, value, stderr = self._collect(process)
        self.assertEqual(code, 0, (code, value, stderr))
        handle = test_runner._try_lock(
            test_runner._worktree_lock_path(self.worktree))
        self.assertIsNotNone(handle)
        test_runner._release_lock(handle)
