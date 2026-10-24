"""Independent F1 acceptance tests. Implementation agents must not edit this file."""
from __future__ import annotations

import importlib
import tempfile
import time
import unittest
from hashlib import sha256
from pathlib import Path

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

ROOT = Path(__file__).resolve().parents[1]
FLOOD_SOURCE = ROOT / "tests" / "oracle_shadow_acceptance" / "flood_harness.py"
OUTPUT_CAP = 65536
DEADLINE = 5.0

try:
    run_owned = importlib.import_module("oracle.shadow").run_owned
except (ImportError, AttributeError):
    run_owned = None


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


@unittest.skipIf(run_owned is None, "oracle.shadow.run_owned not implemented")
class RunOwnedImplementationTests(unittest.TestCase):
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
            result = run_owned(
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
            result = run_owned(
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
