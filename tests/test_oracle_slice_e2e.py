"""P3 slice end-to-end: controller attempts, abort/budget paths and terminal receipts."""
from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from oracle.slice import compare, controller, corpus, harness, receipts
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


class _InvalidC:
    """A completed attempt that leaves syntactically invalid C behind."""

    name = "invalid-c"

    def step(self, ctx):
        ctx.candidate_path.write_text("int this is not valid C\n", encoding="utf-8")
        return AttemptResult("done", "left invalid C in place")


class _RepairThenCrash:
    """Repairs the candidate, then reports an abnormal agent termination."""

    name = "repair-then-crash"

    def __init__(self):
        self._stub = StubAdapter("repair")
        self._patched = False

    def step(self, ctx):
        if not self._patched:
            self._stub._patch(ctx)
            self._patched = True
            return AttemptResult("progress", "patched mode 0")
        return AttemptResult("crashed", "agent crashed after repairing")


_SLOW_SOURCE = (
    '#include "synth.h"\n#include <unistd.h>\n'
    "int synth_apply(struct synth_state *s, int mode, int arg) "
    "{ (void)s; (void)mode; (void)arg; for (;;) sleep(1); return 0; }\n"
)


class _SlowTest:
    """Runs a visible test against a candidate that never returns."""

    name = "slow-test"

    def step(self, ctx):
        ctx.tools.call("test", {"function_id": "synth:apply",
                                "candidate_path": str(ctx.candidate_path)})
        return AttemptResult("gave_up", "timed out against the visible suite")


class _LateDone:
    """Repairs the candidate, overruns the wall clock, then claims completion."""

    name = "late-done"

    def __init__(self, seconds: float):
        self.seconds = seconds
        self._stub = StubAdapter("repair")
        self._patched = False

    def step(self, ctx):
        if not self._patched:
            self._stub._patch(ctx)
            self._patched = True
        time.sleep(self.seconds)
        return AttemptResult("done", "claimed completion after the deadline")


class _LateProgress:
    """Repairs nothing and reports progress only after the deadline."""

    name = "late-progress"

    def __init__(self, seconds: float):
        self.seconds = seconds

    def step(self, ctx):
        time.sleep(self.seconds)
        return AttemptResult("progress", "reported progress after the deadline")


def _fixtures_root(root: Path, *, held_out: str) -> Path:
    """A fixtures root with a real visible set and a deliberately broken holdout."""
    fixtures = root / "fixtures"
    shutil.copytree(corpus.FIXTURES_DIR / corpus.VISIBLE, fixtures / corpus.VISIBLE)
    if held_out == "malformed":
        directory = fixtures / corpus.HELD_OUT
        directory.mkdir(parents=True)
        (directory / "hid-000.json").write_text("{ this is not json", encoding="utf-8")
    return fixtures


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
            # An aborted run is never a completed run, even though the candidate
            # was still evaluated for diagnostic evidence.
            self.assertEqual(result.status, STATUS_ABORTED)
            self.assertIsNotNone(result.visible)
            self.assertIsNone(result.held_out)
            self.assertTrue(result.certification.startswith("skipped"))
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


class FinalizationTests(unittest.TestCase):
    """Terminal receipts, abort classification and bounded finalization."""

    def test_invalid_candidate_still_produces_a_terminal_receipt(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = _run(Path(tmp), _InvalidC())
            self.assertEqual(result.status, STATUS_VISIBLE_FAILED)
            self.assertEqual(result.certification, "skipped: visible suite did not pass")
            payload = result.receipt["payload"]
            self.assertIsNotNone(payload["source"]["final_candidate_sha256"])
            self.assertEqual(payload["verdict"]["visible"]["status"], compare.STATUS_BUILD_ERROR)
            self.assertFalse(payload["verdict"]["candidate_verified"])
            self.assertTrue(result.receipt_path.is_file())
            receipts.verify(result.receipt)
            self.assertEqual(receipts.load(result.receipt_path), result.receipt)

    def test_repair_then_crash_is_not_certified(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = _run(Path(tmp), _RepairThenCrash())
            self.assertEqual(result.abort_reason, "crashed")
            # The candidate itself verifies -- but the run did not complete.
            self.assertEqual(result.visible["status"], compare.STATUS_PASS)
            self.assertIsNone(result.held_out)
            self.assertEqual(result.status, STATUS_ABORTED)
            self.assertFalse(result.candidate_verified)
            self.assertTrue(result.certification.startswith("skipped"))
            payload = result.receipt["payload"]
            self.assertEqual(payload["verdict"]["visible"]["status"], compare.STATUS_PASS)
            self.assertFalse(payload["verdict"]["candidate_verified"])
            self.assertTrue(payload["verdict"]["certification"].startswith("skipped"))
            receipts.verify(result.receipt)

    def test_finalization_is_bounded_and_the_run_deadline_binds_tools(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            slow = root / "slow.c"
            slow.write_text(_SLOW_SOURCE, encoding="utf-8")
            started = time.monotonic()
            result = _run(root, _SlowTest(), wall_seconds=2.0, finalization_seconds=5.0,
                          timeout_s=30.0, candidate_src=slow, max_attempts=1)
            elapsed = time.monotonic() - started
            self.assertLess(elapsed, 2.0 + 5.0 + 8.0,
                            "a hanging candidate must not outlive the declared budgets")
            # The agent's own ``gave_up`` is not the controller's verdict: its
            # tool call consumed the whole run deadline, so the controller
            # classifies the attempt as a deadline violation.
            self.assertEqual(result.abort_reason, "timeout")
            self.assertEqual(result.status, STATUS_ABORTED)
            self.assertEqual(result.visible["status"], compare.STATUS_TIMEOUT)
            payload = result.receipt["payload"]
            self.assertEqual(payload["execution"]["finalization"]["budget_seconds"], 5.0)
            receipts.verify(result.receipt)

    def test_certification_is_skipped_when_the_visible_suite_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = _run(Path(tmp), StubAdapter("give_up"), max_attempts=2)
            payload = result.receipt["payload"]
            self.assertEqual(payload["execution"]["finalization"]["certification"],
                             "skipped: agent aborted: gave_up")
            self.assertFalse(payload["verdict"]["candidate_verified"])

    def test_a_done_after_the_wall_deadline_is_aborted_as_a_timeout(self):
        """The controller, not the adapter, owns the wall clock."""
        with tempfile.TemporaryDirectory() as tmp:
            result = _run(Path(tmp), _LateDone(3.0), wall_seconds=1.0, max_attempts=1)
            self.assertEqual(result.abort_reason, "timeout")
            self.assertEqual(result.status, STATUS_ABORTED)
            self.assertFalse(result.candidate_verified)
            self.assertIsNone(result.held_out)
            self.assertTrue(result.certification.startswith("skipped"))
            self.assertEqual(result.attempts[-1]["status"], "timeout")
            receipts.verify(result.receipt)

    def test_a_final_attempt_overrun_is_a_timeout_not_an_attempt_limit(self):
        """A late ``progress`` on the last attempt is a deadline violation."""
        with tempfile.TemporaryDirectory() as tmp:
            result = _run(Path(tmp), _LateProgress(3.0), wall_seconds=1.0, max_attempts=1)
            self.assertEqual(result.abort_reason, "timeout")
            self.assertNotEqual(result.abort_reason, "max_attempts")
            self.assertEqual(len(result.attempts), 1)
            self.assertEqual(result.attempts[0]["status"], "timeout")
            self.assertEqual(result.status, STATUS_ABORTED)
            receipts.verify(result.receipt)


class InfrastructureFailureTests(unittest.TestCase):
    """A trusted host that cannot complete the evaluation is not a verdict."""

    def test_malformed_held_out_corpus_is_an_infrastructure_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            result = _run(root, StubAdapter("repair"),
                          fixtures_root=_fixtures_root(root, held_out="malformed"))
            payload = result.receipt["payload"]
            # The candidate is fine: the Oracle could not complete the test.
            self.assertEqual(payload["verdict"]["visible"]["status"], compare.STATUS_PASS)
            self.assertEqual(payload["verdict"]["held_out"]["status"], compare.STATUS_HARNESS_ERROR)
            self.assertNotEqual(result.status, STATUS_HELD_OUT_FAILED)
            self.assertEqual(result.status, STATUS_ABORTED)
            self.assertEqual(result.certification, "failed: CorpusError")
            self.assertTrue(payload["execution"]["finalization"]["error"].startswith("CorpusError"))
            self.assertFalse(payload["verdict"]["candidate_verified"])
            receipts.verify(result.receipt, expected_sha256=result.receipt["sha256"])
            self.assertEqual(receipts.load(result.receipt_path), result.receipt)

    def test_missing_held_out_corpus_is_an_infrastructure_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            result = _run(root, StubAdapter("repair"),
                          fixtures_root=_fixtures_root(root, held_out="missing"))
            payload = result.receipt["payload"]
            self.assertEqual(payload["verdict"]["held_out"]["status"], compare.STATUS_HARNESS_ERROR)
            self.assertNotEqual(result.status, STATUS_HELD_OUT_FAILED)
            self.assertEqual(result.status, STATUS_ABORTED)
            self.assertEqual(result.certification, "failed: CorpusError")
            self.assertTrue(payload["execution"]["finalization"]["error"].startswith("CorpusError"))
            receipts.verify(result.receipt)

    def test_an_unidentifiable_compiler_prevents_acceptance(self):
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.object(harness, "compiler_version", return_value="unknown"):
                result = _run(Path(tmp), StubAdapter("repair"))
        payload = result.receipt["payload"]
        self.assertEqual(payload["toolchain"]["compiler_version"], "unknown")
        # Both suites really did pass, so the verdict evidence is preserved ...
        self.assertTrue(payload["verdict"]["candidate_verified"])
        self.assertEqual(payload["verdict"]["held_out"]["status"], compare.STATUS_PASS)
        # ... but acceptance is not claimed on an unidentifiable toolchain.
        self.assertNotEqual(result.status, STATUS_CERTIFIED)
        self.assertEqual(result.status, STATUS_ABORTED)
        self.assertEqual(payload["execution"]["finalization"]["certification"],
                         "failed: unknown compiler identity")
        self.assertTrue(payload["execution"]["finalization"]["error"])
        receipts.verify(result.receipt)

    def test_unreadable_toolchain_metadata_still_writes_a_terminal_receipt(self):
        """A trusted-source read failure must not bypass the terminal receipt."""
        with tempfile.TemporaryDirectory() as tmp:
            real = receipts.file_sha256

            def flaky(path):
                if Path(path).name == "harness.py":
                    raise OSError("simulated metadata I/O failure")
                return real(Path(path))

            with mock.patch.object(receipts, "file_sha256", flaky):
                result = _run(Path(tmp), StubAdapter("repair"))

            # run() returned instead of propagating, and a sealed receipt exists.
            payload = result.receipt["payload"]
            self.assertIsNone(payload["toolchain"]["harness_sha256"])
            # Every digest that could be collected is preserved.
            for key in ("comparator_sha256", "driver_sha256", "interface_sha256"):
                self.assertTrue(payload["toolchain"][key], key)
            error = payload["execution"]["finalization"]["error"]
            self.assertTrue(error.startswith("toolchain metadata unavailable (harness.py)"), error)
            self.assertIn("OSError", error)
            self.assertNotEqual(result.status, STATUS_CERTIFIED)
            self.assertEqual(result.status, STATUS_ABORTED)
            # The candidate really did pass both suites; only acceptance is withheld.
            self.assertTrue(payload["verdict"]["candidate_verified"])
            self.assertEqual(payload["verdict"]["held_out"]["status"], compare.STATUS_PASS)
            self.assertEqual(payload["execution"]["finalization"]["certification"],
                             "failed: incomplete toolchain metadata")
            self.assertTrue(result.receipt_path.is_file())
            receipts.verify(result.receipt, expected_sha256=result.receipt["sha256"])
            self.assertEqual(receipts.load(result.receipt_path), result.receipt)

    def test_trusted_host_io_failures_still_write_a_terminal_receipt(self):
        """Every trusted-host I/O fault in finalization yields a receipt."""
        # label: (patcher, expected certification, expected visible status,
        #         expected held-out status, expected source.freeze_error)
        cases = {
            "freeze": (
                mock.patch.object(controller, "freeze_candidate",
                                  side_effect=OSError("simulated freeze I/O failure")),
                "not_attempted", None, None, "simulated freeze I/O failure"),
            "visible": (
                mock.patch.object(controller, "_suite_verdict",
                                  side_effect=OSError("simulated visible I/O failure")),
                "skipped: visible suite did not pass", compare.STATUS_HARNESS_ERROR, None, None),
            "certification": (
                mock.patch.object(controller, "certify_candidate",
                                  side_effect=OSError("simulated certify I/O failure")),
                "failed: OSError", compare.STATUS_PASS, compare.STATUS_HARNESS_ERROR, None),
        }
        with tempfile.TemporaryDirectory() as tmp:
            for label, (patcher, certification, visible_status, held_status,
                        freeze_error) in cases.items():
                with self.subTest(label), patcher:
                    result = _run(Path(tmp) / label, StubAdapter("repair"))
                    payload = result.receipt["payload"]
                    self.assertEqual(result.status, STATUS_ABORTED)
                    self.assertNotEqual(result.status, STATUS_CERTIFIED)
                    self.assertEqual(payload["execution"]["finalization"]["certification"],
                                     certification)
                    self.assertEqual(payload["source"]["freeze_error"], freeze_error)
                    for key, expected in (("visible", visible_status),
                                          ("held_out", held_status)):
                        verdict = payload["verdict"][key]
                        if expected is None:
                            self.assertIsNone(verdict, key)
                        else:
                            self.assertEqual(verdict["status"], expected, key)
                    error = payload["execution"]["finalization"]["error"]
                    self.assertIn("OSError", error or "")
                    self.assertTrue(result.receipt_path.is_file())
                    receipts.verify(result.receipt, expected_sha256=result.receipt["sha256"])
                    self.assertEqual(receipts.load(result.receipt_path), result.receipt)


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

    def test_the_stub_has_no_external_binding(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = _run(Path(tmp), StubAdapter("repair"))
            self.assertNotIn("opencode", result.receipt["payload"]["toolchain"])


class LedgerAuthoritativeAccountingTests(unittest.TestCase):
    """Tool accounting is the ledger's, not the in-process counter's.

    A real agent calls the Oracle through the MCP **server process**, which
    reserves in the shared SQLite ledger without touching the controller's
    in-process ``ToolClient``. The recorded per-attempt and total counts must
    therefore come from the ledger, or a real run would report zero calls.
    """

    class _ExternalMCPAgent:
        name = "external-mcp"

        def step(self, ctx):
            from oracle.slice.budget import RunBudgetLedger
            ledger_path = ctx.workspace.parent / "budget.sqlite3"
            ledger = RunBudgetLedger.attach(ledger_path, "run-e2e")
            try:
                for _ in range(3):
                    ledger.reserve(1)
            finally:
                ledger.close()
            return AttemptResult("gave_up", "simulated out-of-process tool calls")

    def test_out_of_process_calls_are_counted_from_the_ledger(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = _run(Path(tmp), self._ExternalMCPAgent(), max_attempts=1)
            payload = result.receipt["payload"]
            self.assertEqual(payload["agent"]["in_process_tool_calls"], 0)
            self.assertEqual(payload["agent"]["tool_calls"], 3)
            self.assertEqual(payload["agent"]["budget"]["granted"], 3)
            self.assertEqual(result.attempts[0]["tool_calls"], 3)
            self.assertEqual(result.attempts[0]["in_process_tool_calls"], 0)
            receipts.verify(result.receipt)


def _valid_binding() -> dict:
    hex64 = "a" * 64
    return {
        "adapter": "opencode",
        "binary": {"name": "opencode", "sha256": "b" * 64, "version": "v2.0.30"},
        "model": {"provider": "opencode", "id": "deepseek-v4.1-flash-free", "variant": "high"},
        "prompt_sha256": hex64,
        "config": {"template_sha256": "c" * 64, "materialized_sha256": "d" * 64},
        "events": {
            "raw_sha256": "e" * 64,
            "raw_bytes": 4096,
            "summary_sha256": "f" * 64,
            "tool_calls": 7,
            "oracle_invocations": 9,
            "oracle_by_tool": {"oracle_diff": 1, "oracle_inspect": 1, "oracle_test": 7},
            "oracle_denials": {"oracle_diff": 1},
            "completed_tools": ["oracle_inspect", "oracle_test"],
            "denials": [{"action": "shell", "resource": "*", "decision": "deny"}],
        },
    }


class _BindingAgent:
    """A repair adapter that also supplies external-toolchain metadata."""

    name = "binding-agent"

    def __init__(self, binding):
        self._binding = binding
        self._stub = StubAdapter("repair")

    def step(self, ctx):
        return self._stub.step(ctx)

    def receipt_metadata(self):
        return self._binding


class BindingMetadataTests(unittest.TestCase):
    def test_valid_binding_is_folded_into_the_sealed_receipt(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = _run(Path(tmp), _BindingAgent(_valid_binding()))
            self.assertEqual(result.status, STATUS_CERTIFIED)
            folded = result.receipt["payload"]["toolchain"]["opencode"]
            self.assertEqual(folded["binary"]["version"], "v2.0.30")
            self.assertEqual(folded["model"]["variant"], "high")
            self.assertEqual(folded["events"]["tool_calls"], 7)
            self.assertEqual(folded["events"]["denials"][0]["decision"], "deny")
            receipts.verify(result.receipt, expected_sha256=result.receipt["sha256"])

    def test_malformed_binding_prevents_acceptance(self):
        bad = _valid_binding()
        del bad["prompt_sha256"]
        with tempfile.TemporaryDirectory() as tmp:
            result = _run(Path(tmp), _BindingAgent(bad))
            payload = result.receipt["payload"]
            # The candidate really did verify; only acceptance is withheld.
            self.assertTrue(payload["verdict"]["candidate_verified"])
            self.assertNotEqual(result.status, STATUS_CERTIFIED)
            self.assertEqual(result.status, STATUS_ABORTED)
            self.assertEqual(payload["execution"]["finalization"]["certification"],
                             "failed: incomplete binding metadata")
            self.assertIn("binding metadata invalid", payload["execution"]["finalization"]["error"])
            self.assertNotIn("opencode", payload["toolchain"])
            receipts.verify(result.receipt)

    def test_unavailable_binding_prevents_acceptance(self):
        class _RaisingAgent:
            name = "raising"

            def __init__(self):
                self._stub = StubAdapter("repair")

            def step(self, ctx):
                return self._stub.step(ctx)

            def receipt_metadata(self):
                raise OSError("simulated binding read failure")

        with tempfile.TemporaryDirectory() as tmp:
            result = _run(Path(tmp), _RaisingAgent())
            payload = result.receipt["payload"]
            self.assertEqual(result.status, STATUS_ABORTED)
            self.assertNotEqual(result.status, STATUS_CERTIFIED)
            self.assertTrue(payload["verdict"]["candidate_verified"])
            error = payload["execution"]["finalization"]["error"]
            self.assertIn("binding metadata unavailable", error)
            self.assertIn("OSError", error)
            receipts.verify(result.receipt)


if __name__ == "__main__":
    unittest.main()
