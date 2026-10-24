"""G3a: the function-agent seam over the synthetic ``synth:apply`` package.

Every test here is offline and uses a **synthetic** agent: the deterministic P3
stub or a local process, never a model call. The synthetic nature is recorded in
the returned claim and in the controller-sealed receipt (``agent.model``).
"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

from oracle.function_agent import seam
from oracle.slice import compare, controller, receipts
from oracle.slice.adapters import CommandAdapter, StubAdapter

SLICE_DIR = Path(__file__).resolve().parents[1] / "oracle" / "slice"

_SPAWNER = (
    "import os, subprocess, sys, time\n"
    "open(sys.argv[1], 'w').write(str(os.getpid()))\n"
    "subprocess.Popen([sys.executable, '-c',\n"
    "    'import os,sys,time; open(sys.argv[1],\"w\").write(str(os.getpid())); time.sleep(300)',\n"
    "    sys.argv[2]])\n"
    "time.sleep(300)\n"
)


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:  # pragma: no cover - defensive
        return True
    return True


def _run(root: Path, inner, *, downstream: str = "synthetic-stub",
         synthetic: bool = True, **overrides) -> seam.SeamResult:
    params = dict(
        run_id="run-g3a",
        base_dir=root / "runs",
        inner=inner,
        downstream=downstream,
        synthetic=synthetic,
        budget_calls=60,
        max_attempts=4,
        wall_seconds=60.0,
    )
    params.update(overrides)
    return seam.run_function_package("synth:apply", **params)


class SyntheticConformanceTests(unittest.TestCase):
    """The existing ``synth:apply`` case, run through the seam end to end."""

    def test_synthetic_stub_conformance_is_certified_through_the_seam(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = _run(Path(tmp), StubAdapter("repair"))

            self.assertEqual(result.status, controller.STATUS_CERTIFIED)
            self.assertTrue(result.certified)
            self.assertIsNone(result.abort_reason)
            self.assertIsNotNone(result.run)
            self.assertEqual(result.run.visible["status"], compare.STATUS_PASS)
            self.assertEqual(result.run.held_out["status"], compare.STATUS_PASS)
            self.assertEqual(len(result.run.attempts), 2)

            # Receipt sealing is the controller's, not reimplemented here.
            receipts.verify(result.receipt, expected_sha256=result.receipt["sha256"])
            self.assertEqual(receipts.load(result.receipt_path), result.receipt)

            payload = result.receipt["payload"]
            source = payload["source"]
            self.assertNotEqual(source["defective_candidate_sha256"],
                                source["final_candidate_sha256"])
            # The synthetic downstream producer is labelled in the sealed receipt.
            self.assertEqual(payload["agent"]["adapter"], "function-agent:stub")
            self.assertTrue(payload["agent"]["model"]["synthetic"])
            self.assertEqual(payload["agent"]["model"]["id"], "synthetic-stub")
            self.assertFalse(payload["agent"]["model"]["arbitrary_cpp_supported"])

    def test_synthetic_overfit_fails_held_out_through_the_seam(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = _run(Path(tmp), StubAdapter("overfit"))

            self.assertEqual(result.status, controller.STATUS_HELD_OUT_FAILED)
            self.assertEqual(result.run.visible["status"], compare.STATUS_PASS)
            self.assertEqual(result.run.held_out["status"], compare.STATUS_MISMATCH)
            receipts.verify(result.receipt)

    def test_claim_does_not_assert_arbitrary_cpp_support(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = _run(Path(tmp), StubAdapter("repair"))

            self.assertFalse(result.claim["arbitrary_cpp_supported"])
            self.assertFalse(result.claim["generic_workspace"])
            self.assertTrue(result.claim["synthetic"])
            self.assertEqual(result.claim["function_id"], "synth:apply")
            self.assertIn("synth:apply", result.claim["scope"])
            self.assertIn("does not support arbitrary reconstructed C++ functions",
                          result.claim["scope"])
            self.assertFalse(result.package.arbitrary_cpp_supported)
            self.assertEqual(result.package.workspace_kind, "synthetic-candidate-only")


class ReusedControllerBoundaryTests(unittest.TestCase):
    """Budget, abort and process-group behaviour remain the controller's."""

    def test_budget_is_enforced_through_the_seam(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = _run(Path(tmp), StubAdapter("repair"), budget_calls=3)

            self.assertEqual(result.abort_reason, "budget")
            self.assertEqual(result.status, controller.STATUS_ABORTED)
            granted = result.receipt["payload"]["agent"]["budget"]["granted"]
            self.assertEqual(granted, 3)
            receipts.verify(result.receipt, expected_sha256=result.receipt["sha256"])
            self.assertEqual(receipts.load(result.receipt_path), result.receipt)

    def test_give_up_aborts_with_a_sealed_receipt(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = _run(Path(tmp), StubAdapter("give_up"), max_attempts=3)

            self.assertEqual(result.abort_reason, "gave_up")
            self.assertEqual(result.status, controller.STATUS_ABORTED)
            self.assertFalse(result.certified)
            self.assertTrue(result.run.certification.startswith("skipped"))
            self.assertIsNone(result.run.held_out)
            receipts.verify(result.receipt, expected_sha256=result.receipt["sha256"])

    def test_synthetic_process_group_is_terminated_and_receipted(self):
        """A timed-out synthetic agent cannot leave a descendant running."""
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            parent_pid = root / "parent.pid"
            child_pid = root / "child.pid"
            adapter = CommandAdapter(
                [sys.executable, "-c", _SPAWNER, str(parent_pid), str(child_pid)])
            result = _run(root, adapter, downstream="synthetic-process",
                          wall_seconds=3.0, max_attempts=1)

            self.assertEqual(result.abort_reason, "timeout")
            self.assertEqual(result.status, controller.STATUS_ABORTED)
            self.assertTrue(parent_pid.is_file(), "the synthetic agent never started")
            self.assertTrue(child_pid.is_file(), "the synthetic descendant never started")
            self.assertFalse(_pid_alive(int(parent_pid.read_text())))
            self.assertFalse(_pid_alive(int(child_pid.read_text())))
            self.assertTrue(result.receipt_path.is_file())
            receipts.verify(result.receipt, expected_sha256=result.receipt["sha256"])
            self.assertTrue(result.receipt["payload"]["agent"]["model"]["synthetic"])
            self.assertEqual(result.receipt["payload"]["agent"]["model"]["id"],
                             "synthetic-process")


class RefusalTests(unittest.TestCase):
    """A missing package is refused, never fabricated into a success."""

    def test_missing_package_is_refused_without_starting_a_run(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            result = seam.run_function_package(
                "synth:not-installed", run_id="run-g3a", base_dir=root / "runs",
                inner=StubAdapter("repair"), downstream="synthetic-stub", synthetic=True)

            self.assertEqual(result.status, "refused: no-package")
            self.assertFalse(result.certified)
            self.assertIsNone(result.run)
            self.assertIsNone(result.receipt)
            self.assertIsNone(result.receipt_path)
            self.assertEqual(result.refusal["reason"], "no-package")
            self.assertFalse(result.claim["arbitrary_cpp_supported"])
            # Nothing was materialized and no verdict was invented.
            self.assertFalse((root / "runs").exists())

    def test_an_unregistered_reconstructed_cpp_function_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = seam.run_function_package(
                "re0:border_loop", run_id="run-g3a", base_dir=Path(tmp) / "runs",
                inner=StubAdapter("repair"), downstream="synthetic-stub", synthetic=True)

            self.assertEqual(result.status, "refused: no-package")
            self.assertFalse(result.certified)
            self.assertIsNone(result.run)
            self.assertIn("not a supported function package", result.refusal["detail"])

    def test_incomplete_package_is_refused_rather_than_run(self):
        from oracle.function_agent import packages

        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(packages.PackageRefused) as caught:
                packages.describe_package("synth:apply",
                                          fixtures_root=Path(tmp) / "no-fixtures")
            self.assertEqual(caught.exception.status, "refused: incomplete-package")


if __name__ == "__main__":
    unittest.main()
