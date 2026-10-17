"""P3 slice end-to-end: controller attempts, abort/budget paths and terminal receipts."""
from __future__ import annotations

import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from oracle.slice import compare, controller, corpus, receipts
from oracle.slice.adapters import AttemptResult, CommandAdapter, StubAdapter
from oracle.slice.controller import (
    STATUS_ABORTED,
    STATUS_CERTIFIED,
    STATUS_HELD_OUT_FAILED,
    STATUS_VISIBLE_FAILED,
    RunConfig,
)

SLICE_DIR = Path(__file__).resolve().parents[1] / "oracle" / "slice"


class _AlwaysProgress:
    name = "always-progress"

    def step(self, ctx):
        ctx.tools.call("inspect", {"function_id": "synth:apply"})
        return AttemptResult("progress", "no change")


class _Deleting:
    name = "deleting"

    def step(self, ctx):
        ctx.candidate_path.unlink()
        return AttemptResult("gave_up", "candidate removed")


def _run(root: Path, adapter, **overrides) -> controller.RunResult:
    params = dict(
        run_id="run-e2e",
        candidate_src=SLICE_DIR / "target.c",
        base_dir=root / "runs",
        budget_calls=60,
        max_attempts=4,
        wall_seconds=60.0,
    )
    params.update(overrides)
    return controller.run(RunConfig(**params), adapter)


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:  # pragma: no cover - defensive
        return True
    return True


_SPAWNER = (
    "import os, subprocess, sys, time\n"
    "open(sys.argv[1], 'w').write(str(os.getpid()))\n"
    "subprocess.Popen([sys.executable, '-c',\n"
    "    'import os,sys,time; open(sys.argv[1],\"w\").write(str(os.getpid())); time.sleep(300)',\n"
    "    sys.argv[2]])\n"
    "time.sleep(300)\n"
)


class StubRepairTests(unittest.TestCase):
    def test_stub_repair_is_certified(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = _run(Path(tmp), StubAdapter("repair"))
            self.assertEqual(result.status, STATUS_CERTIFIED)
            self.assertIsNone(result.abort_reason)
            self.assertEqual(result.visible["status"], compare.STATUS_PASS)
            self.assertEqual(result.held_out["status"], compare.STATUS_PASS)
            self.assertEqual(len(result.attempts), 2)
            receipts.verify(result.receipt, expected_sha256=result.receipt["sha256"])
            self.assertEqual(receipts.load(result.receipt_path), result.receipt)

    def test_repair_actually_changed_the_candidate(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = _run(Path(tmp), StubAdapter("repair"))
            source = result.receipt["payload"]["source"]
            self.assertNotEqual(source["defective_candidate_sha256"], source["final_candidate_sha256"])

    def test_overfit_candidate_passes_visible_but_fails_held_out(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = _run(Path(tmp), StubAdapter("overfit"))
            self.assertEqual(result.status, STATUS_HELD_OUT_FAILED)
            self.assertEqual(result.visible["status"], compare.STATUS_PASS)
            self.assertEqual(result.held_out["status"], compare.STATUS_MISMATCH)
            self.assertTrue(result.held_out["failures"])


class AbortPathTests(unittest.TestCase):
    def test_give_up_aborts_cleanly(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = _run(Path(tmp), StubAdapter("give_up"), max_attempts=3)
            self.assertEqual(result.abort_reason, "gave_up")
            self.assertEqual(result.status, STATUS_VISIBLE_FAILED)
            self.assertTrue(result.receipt_path.is_file())
            receipts.verify(result.receipt)

    def test_max_attempts_abort(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = _run(Path(tmp), _AlwaysProgress(), max_attempts=3, no_progress_limit=10)
            self.assertEqual(result.abort_reason, "max_attempts")
            self.assertEqual(len(result.attempts), 3)
            receipts.verify(result.receipt)

    def test_no_progress_abort(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = _run(Path(tmp), _AlwaysProgress(), max_attempts=6, no_progress_limit=2)
            self.assertEqual(result.abort_reason, "no_progress")
            self.assertLess(len(result.attempts), 6)
            receipts.verify(result.receipt)

    def test_budget_is_enforced_before_execution(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = _run(Path(tmp), StubAdapter("repair"), budget_calls=3)
            self.assertEqual(result.abort_reason, "budget")
            granted = result.receipt["payload"]["agent"]["budget"]["granted"]
            self.assertLessEqual(granted, 3)
            self.assertEqual(granted, 3)

    def test_missing_candidate_is_reported_without_crashing(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = _run(Path(tmp), _Deleting())
            self.assertEqual(result.status, STATUS_ABORTED)
            self.assertIsNotNone(result.receipt["payload"]["source"]["freeze_error"])
            receipts.verify(result.receipt)


class CommandAdapterTests(unittest.TestCase):
    def test_timeout_terminates_process_group_and_writes_receipt(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            parent_pid_file = root / "parent.pid"
            child_pid_file = root / "child.pid"
            adapter = CommandAdapter(
                [sys.executable, "-c", _SPAWNER, str(parent_pid_file), str(child_pid_file)]
            )
            result = _run(root, adapter, wall_seconds=3.0, max_attempts=1)
            self.assertEqual(result.abort_reason, "timeout")
            self.assertTrue(result.receipt_path.is_file())
            receipts.verify(result.receipt)
            self.assertTrue(parent_pid_file.is_file(), "agent never started")
            self.assertTrue(child_pid_file.is_file(), "agent descendant never started")
            self.assertFalse(_pid_alive(int(parent_pid_file.read_text())))
            self.assertFalse(_pid_alive(int(child_pid_file.read_text())))

    def test_crash_produces_a_terminal_receipt(self):
        with tempfile.TemporaryDirectory() as tmp:
            adapter = CommandAdapter([sys.executable, "-c", "import os; os.abort()"])
            result = _run(Path(tmp), adapter, max_attempts=1)
            self.assertEqual(result.abort_reason, "crashed")
            self.assertTrue(result.receipt_path.is_file())
            receipts.verify(result.receipt)


class CertificationBoundaryTests(unittest.TestCase):
    def test_certify_is_invoked_exactly_once(self):
        with tempfile.TemporaryDirectory() as tmp:
            counter = {"n": 0}
            real = controller.certify_candidate

            def counting(*args, **kwargs):
                counter["n"] += 1
                return real(*args, **kwargs)

            with mock.patch.object(controller, "certify_candidate", counting):
                result = _run(Path(tmp), StubAdapter("repair"))
            self.assertEqual(counter["n"], 1)
            self.assertEqual(result.held_out["status"], compare.STATUS_PASS)

    def test_held_out_verdict_carries_no_expectations(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = _run(Path(tmp), StubAdapter("repair"))
            blob = json.dumps(result.held_out)
            for leaked in ("expected", "return_hex", "post_memory", "effects"):
                self.assertNotIn(leaked, blob)

    def test_receipt_visible_corpus_is_visible_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = _run(Path(tmp), StubAdapter("repair"))
            visible_ids = {e["fixture_id"] for e in result.receipt["payload"]["corpus"]["visible"]["fixtures"]}
            held_ids = {e["fixture_id"] for e in result.receipt["payload"]["corpus"]["held_out"]["fixtures"]}
            self.assertTrue(visible_ids)
            self.assertTrue(held_ids)
            self.assertFalse(visible_ids & held_ids)
            self.assertFalse(any(fid.startswith("hid-") for fid in visible_ids))


class ReceiptIdentityTests(unittest.TestCase):
    def test_receipt_binds_the_required_identities(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = _run(Path(tmp), StubAdapter("repair"), baseline_commit="11d4608",
                          model={"provider": "opencode", "id": "placeholder", "variant": "high"})
            payload = result.receipt["payload"]
            for key in ("phase", "run_id", "source", "corpus", "toolchain", "agent", "execution", "verdict"):
                self.assertIn(key, payload)
            for key in ("baseline_commit", "defective_candidate_sha256", "final_candidate_sha256"):
                self.assertIn(key, payload["source"])
            for key in ("visible", "held_out"):
                manifest = payload["corpus"][key]
                self.assertEqual(len(manifest["sha256"]), 64)
                self.assertGreater(manifest["count"], 0)
                self.assertTrue(manifest["schema_version"])
            for key in ("cc", "flags", "compiler_version", "harness_sha256", "comparator_sha256"):
                self.assertIn(key, payload["toolchain"])
            self.assertEqual(payload["agent"]["model"], {"provider": "opencode", "id": "placeholder", "variant": "high"})
            self.assertEqual(payload["verdict"]["status"], STATUS_CERTIFIED)
            self.assertEqual(len(result.receipt["sha256"]), 64)


if __name__ == "__main__":
    unittest.main()
