"""Independent F1 acceptance tests. Implementation agents must not edit this file."""
from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
import tempfile
import time
import unittest
from hashlib import sha256
from pathlib import Path
from unittest import mock

from oracle.function import synthetic
from oracle.function.fixture import load_descriptor

from tests.oracle_shadow_acceptance.contract import (
    assert_accounting_partition,
    assert_executable_identity_enforced,
    assert_flood_stage_markers,
    assert_flood_supervised,
    assert_result_shape,
    prepare_owned_executable,
    read_observed_output_bytes,
    weak_supervisor_stand_in,
)
from tests.oracle_shadow_acceptance.import_gate import (
    load_run_owned,
    run_owned_import_status,
)

ROOT = Path(__file__).resolve().parents[1]
FLOOD_SOURCE = ROOT / "tests" / "oracle_shadow_acceptance" / "flood_harness.py"
OUTPUT_CAP = 65536
DEADLINE = 5.0


def _snapshot() -> str:
    return synthetic.capture_text(load_descriptor(), "s03")


class ContractShapeTests(unittest.TestCase):
    def test_weak_stand_in_fails_flood_supervision(self):
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp)
            (work / "flood.started").write_text("1\n", encoding="utf-8")
            result = weak_supervisor_stand_in([_snapshot()])
            assert_result_shape(result, 1)
            assert_accounting_partition(result, 1)
            with self.assertRaises(AssertionError):
                assert_flood_supervised(
                    work, result, deadline_seconds=DEADLINE,
                    output_limit_bytes=OUTPUT_CAP, started=time.monotonic(),
                    observed_output_bytes=0)

    def test_accounting_rejects_outcome_mismatch(self):
        result = weak_supervisor_stand_in([_snapshot()])
        result["accounting"]["refused"] = 1
        result["accounting"]["completed"] = 0
        with self.assertRaises(AssertionError):
            assert_accounting_partition(result, 1)


class RunOwnedImportGateTests(unittest.TestCase):
    def test_broken_oracle_shadow_package_fails_when_present(self):
        if importlib.util.find_spec("oracle.shadow") is None:
            self.skipTest("oracle.shadow absent on this tree")
        status, detail = run_owned_import_status()
        if status == "broken":
            self.fail(f"oracle.shadow is broken: {detail}")
        self.assertEqual(status, "ready")


@unittest.skipIf(run_owned_import_status()[0] == "absent",
                 "oracle.shadow absent — F1 preparation only")
class RunOwnedImplementationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        status, detail = run_owned_import_status()
        if status == "broken":
            raise AssertionError(f"oracle.shadow broken: {detail}")
        cls.run_owned = load_run_owned()
        assert cls.run_owned is not None

    def test_flood_fixture_within_process_group(self):
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp)
            owned = prepare_owned_executable(FLOOD_SOURCE, work)
            candidate = {
                "function_id": "gfx:set-constant-buffer",
                "source_sha256": "0" * 64,
                "executable_sha256": sha256(owned.read_bytes()).hexdigest(),
                "harness_path": str(owned),
            }
            started = time.monotonic()
            result = self.run_owned(
                [_snapshot()],
                candidate,
                work,
                deadline_seconds=DEADLINE,
                output_limit_bytes=OUTPUT_CAP,
                queue_limit=4,
            )
            observed = read_observed_output_bytes(work)
            assert_result_shape(result, 1)
            assert_accounting_partition(result, 1)
            assert_flood_stage_markers(work)
            assert_flood_supervised(
                work, result, deadline_seconds=DEADLINE,
                output_limit_bytes=OUTPUT_CAP, started=started,
                observed_output_bytes=observed)

    def test_replaced_executable_without_digest_update_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp)
            owned = prepare_owned_executable(FLOOD_SOURCE, work)
            wrong = work / "other.py"
            wrong.write_text("print('ok')\n", encoding="utf-8")
            wrong.chmod(0o755)
            candidate = {
                "function_id": "gfx:set-constant-buffer",
                "source_sha256": "0" * 64,
                "executable_sha256": sha256(owned.read_bytes()).hexdigest(),
                "harness_path": str(wrong),
            }
            result = self.run_owned(
                [_snapshot()],
                candidate,
                work,
                deadline_seconds=DEADLINE,
                output_limit_bytes=OUTPUT_CAP,
                queue_limit=4,
            )
            assert_executable_identity_enforced(result)


if __name__ == "__main__":
    unittest.main()


class SupervisedPidCleanupTests(unittest.TestCase):
    """The cleanup observation must not fail closed on one slow ``ps`` query."""

    def test_a_transient_ps_failure_is_retried_within_the_deadline(self):
        from tests.oracle_shadow_acceptance import contract

        with tempfile.TemporaryDirectory() as tmp:
            pid_file = Path(tmp) / "flood.pid"
            pid_file.write_text(f"{os.getpid()}\n")
            calls = {"n": 0}
            real = contract.subprocess.run

            def flaky(command, **kwargs):
                calls["n"] += 1
                # Three consecutive transient failures: the old single-shot
                # form re-queried exactly three times and then failed closed.
                if calls["n"] <= 3:
                    raise contract.subprocess.TimeoutExpired(command, 1.0)
                return subprocess.CompletedProcess(command, 1, "", "")

            with mock.patch.object(
                    contract.subprocess, "run", side_effect=flaky):
                contract.assert_no_running_pids(pid_file, timeout=3.0)
            self.assertGreaterEqual(calls["n"], 2)

    def test_a_live_supervised_pid_is_still_refused(self):
        from tests.oracle_shadow_acceptance import contract

        with tempfile.TemporaryDirectory() as tmp:
            pid_file = Path(tmp) / "flood.pid"
            sleeper = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
            self.addCleanup(sleeper.wait)
            self.addCleanup(sleeper.terminate)
            pid_file.write_text(f"{sleeper.pid}\n")
            with self.assertRaisesRegex(AssertionError, "still running"):
                contract.assert_no_running_pids(pid_file, timeout=0.5)
