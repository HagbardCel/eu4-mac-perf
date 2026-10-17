"""P3g: the repair-acceptance gates, exercised offline.

The gates are the execution-side half of the acceptance contract: they run
*before* the held-out corpus is spent, and they are scoped to an acceptance run
(``RunConfig.require_repair_acceptance``), so a deterministic P3-A adapter is
never asked for an event stream it cannot produce.

Every negative here is a regression for a specific defect an earlier review
found: a candidate that never changed, a diagnostic the agent never completed, a
source that changed between the pre-build hash and the trusted copy, and a call
census that cannot be closed.
"""
from __future__ import annotations

import dataclasses
import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from oracle.slice import controller, harness, receipts, workspace
from oracle.slice.bridge import Bridge
from oracle.slice.budget import RunBudgetLedger
from oracle.slice.controller import (
    STATUS_ABORTED,
    STATUS_ACCEPTANCE_GATE_FAILED,
    STATUS_CERTIFIED,
    STATUS_NOT_REPAIRED,
    RunConfig,
    _reconcile_calls,
)
from tests.test_oracle_slice_opencode import (
    SLICE_DIR,
    _adapter,
    _completed_stream,
    _run,
    _write_stub,
)


def _run_acceptance(root: Path, adapter, **overrides):
    overrides.setdefault("wall_seconds", 600.0)
    return _run(root, adapter, require_repair_acceptance=True, **overrides)


def _records(run_dir: Path) -> list[dict]:
    return [json.loads(path.read_text(encoding="utf-8"))
            for path in sorted((run_dir / "tests").glob("*.json"))]


class AcceptanceGateTests(unittest.TestCase):
    def test_a_genuine_repair_passes_every_gate_and_certifies(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            stub = _write_stub(root, _completed_stream())
            adapter = _adapter(root, stub)
            result = _run_acceptance(root, adapter)

            self.assertEqual(result.status, STATUS_CERTIFIED)
            acceptance = result.receipt["payload"]["acceptance"]
            self.assertTrue(acceptance["required"])
            self.assertEqual(acceptance["outcome"], "passed")
            self.assertEqual(acceptance["failed"], [])
            self.assertTrue(all(gate["ok"] for gate in acceptance["gates"].values()))

            # The gate evidence is the *trusted* record the bridge wrote, not a
            # claim the agent made: it names the frozen bytes and attests them.
            records = _records(result.run_dir)
            self.assertTrue(records)
            attested = [r for r in records
                        if r["candidate_sha256"] == result.receipt["payload"]["source"][
                            "final_candidate_sha256"]]
            self.assertTrue(attested)
            self.assertEqual(attested[0]["source_identity"], "attested")
            # The journal is exported next to the receipt and its digest is sealed.
            journal = result.receipt["payload"]["agent"]["journal"]
            self.assertEqual(result.receipt["payload"]["agent"]["budget"]["granted"], 2)
            self.assertEqual(journal["admitted"], 2)
            self.assertEqual(journal["rejected"], 0)
            exported = result.run_dir / "tool-calls.json"
            self.assertTrue(exported.is_file())
            self.assertEqual(receipts.file_sha256(exported), journal["sha256"])

    def test_an_unchanged_candidate_is_not_repaired_and_spends_no_holdout(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            stub = _write_stub(root, _completed_stream(), write_candidate=False)
            result = _run_acceptance(root, _adapter(root, stub))

            payload = result.receipt["payload"]
            self.assertEqual(result.status, STATUS_NOT_REPAIRED)
            self.assertIsNone(payload["verdict"]["held_out"])
            self.assertFalse(payload["verdict"]["candidate_verified"])
            self.assertIn("candidate_changed", payload["acceptance"]["failed"])
            self.assertEqual(
                payload["execution"]["finalization"]["certification"],
                "skipped: acceptance gates failed (candidate_changed)")

    def test_an_unchanged_reference_candidate_is_not_repaired(self):
        """Isolate the no-op case from a visible failure.

        The input already passes the visible suite, so without the
        ``candidate_changed`` gate an unchanged result would certify. The gate must
        still reject it - byte-identical to the defective input - before the
        held-out corpus is spent.
        """
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            stub = _write_stub(root, _completed_stream(), write_candidate=False)
            result = _run_acceptance(root, _adapter(root, stub),
                                     candidate_src=SLICE_DIR / "reference.c")

            payload = result.receipt["payload"]
            self.assertEqual(result.status, STATUS_NOT_REPAIRED)
            self.assertIsNone(payload["verdict"]["held_out"])
            self.assertFalse(payload["verdict"]["candidate_verified"])
            self.assertEqual(payload["source"]["defective_candidate_sha256"],
                             payload["source"]["final_candidate_sha256"])
            self.assertIn("candidate_changed", payload["acceptance"]["failed"])

    def test_a_failed_journal_export_aborts_the_run_and_prevents_certification(self):
        """A run whose mandatory evidence cannot be published is not certified.

        Both suites pass, so ``candidate_verified`` stays true - the semantic
        result is preserved - but the terminal status is ``aborted`` and the
        failure is recorded on the finalization channel. The receipt is still
        sealed and reloadable.
        """
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            stub = _write_stub(root, _completed_stream())
            adapter = _adapter(root, stub)
            real_write_text = Path.write_text

            def failing_write_text(self, *args, **kwargs):
                if self.name == "tool-calls.json":
                    raise OSError("simulated write failure")
                return real_write_text(self, *args, **kwargs)

            with mock.patch.object(Path, "write_text", failing_write_text):
                result = _run_acceptance(root, adapter)

            payload = result.receipt["payload"]
            self.assertEqual(result.status, STATUS_ABORTED)
            self.assertTrue(payload["verdict"]["candidate_verified"])
            self.assertIsNone(payload["agent"]["journal"])
            self.assertIn("the tool-call journal could not be exported",
                          payload["execution"]["finalization"]["error"])
            # The receipt is sealed and verifiable even though the journal was not
            # published.
            reloaded = json.loads(result.receipt_path.read_text(encoding="utf-8"))
            receipts.verify(reloaded, expected_sha256=reloaded["sha256"])

    def test_a_missing_required_diagnostic_fails_the_gate_before_certification(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            # The agent tested the repair but never consulted the declared evidence.
            stub = _write_stub(root, _completed_stream(), mcp_calls=[
                {"tool": "test", "arguments": {"function_id": "synth:apply",
                                               "candidate_path": "candidate.c"}}])
            result = _run_acceptance(root, _adapter(root, stub))

            payload = result.receipt["payload"]
            self.assertEqual(result.status, STATUS_ACCEPTANCE_GATE_FAILED)
            self.assertEqual(payload["acceptance"]["failed"], ["required_tools"])
            self.assertIn("oracle_inspect", payload["acceptance"]["gates"]["required_tools"]["detail"])
            # The candidate really would have been accepted; the holdout was not spent.
            self.assertIsNone(payload["verdict"]["held_out"])

    def test_a_bridge_rejection_is_distinguished_from_a_permission_denial(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            # A tool the bridge does not serve: rejected *before* admission, so it
            # consumes no reservation - yet it is still an agent-visible invocation
            # and must be reconciled, not mistaken for a permission denial.
            stub = _write_stub(root, _completed_stream(), mcp_calls=[
                {"tool": "inspect", "arguments": {"function_id": "synth:apply"}},
                {"tool": "nonexistent", "arguments": {}},
                {"tool": "test", "arguments": {"function_id": "synth:apply",
                                               "candidate_path": "candidate.c"}},
            ])
            result = _run_acceptance(root, _adapter(root, stub))

            payload = result.receipt["payload"]
            self.assertEqual(result.status, STATUS_CERTIFIED)
            reconciliation = payload["acceptance"]["gates"]["reconciliation"]
            self.assertTrue(reconciliation["ok"], reconciliation["problems"])
            self.assertEqual(reconciliation["admitted"], 2)
            self.assertEqual(reconciliation["rejected_before_admission"], 1)
            self.assertEqual(reconciliation["denied"], 0)
            journal = payload["agent"]["journal"]
            self.assertEqual(journal["rejected_by_reason"], {"unknown_tool": 1})
            self.assertEqual(payload["agent"]["budget"]["granted"], 2)

    def test_a_source_that_changed_before_the_build_is_never_reported_as_pass(self):
        """A post-admission identity mismatch is an admitted failure, not a PASS.

        The comparison happens in the bridge process, so this drives the trusted
        tool in-process with a build whose materialized digest does not describe
        the candidate the agent named - exactly what a racing writer would cause.
        """
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            run_dir = root / "run"
            run_dir.mkdir()
            ledger = RunBudgetLedger.create(run_dir, "run-race", 10)
            try:
                ws = workspace.materialize_workspace(
                    root / "ws", candidate_src=SLICE_DIR / "target.c",
                    header_src=SLICE_DIR / "synth.h")
                ws.candidate_path.write_text((SLICE_DIR / "reference.c").read_text(),
                                             encoding="utf-8")
                bridge = Bridge(run_dir=run_dir, workspace=ws.root, ledger=ledger)
                real_build = harness.build

                def racing_build(candidate, build_dir, **kwargs):
                    return dataclasses.replace(real_build(candidate, build_dir, **kwargs),
                                               source_sha256="0" * 64)

                with mock.patch.object(harness, "build", racing_build):
                    response = bridge.call("test", {"function_id": "synth:apply",
                                                    "candidate_path": "candidate.c"})
                journal = ledger.tool_calls()
            finally:
                ledger.close()

            self.assertNotEqual(response["status"], "PASS")
            records = _records(run_dir)
            self.assertEqual(len(records), 1)
            record = records[0]
            self.assertEqual(record["source_identity"], "mismatch")
            self.assertTrue(record["summary"]["source_identity_mismatch"])
            self.assertEqual(record["materialized_sha256"], "0" * 64)
            # The reservation stands: this was discovered *after* admission, so the
            # call is admitted and never re-counted as a pre-admission rejection.
            self.assertEqual([row["decision"] for row in journal], ["admitted"])
            self.assertEqual([row["outcome"] for row in journal], ["completed"])


class ReconciliationTests(unittest.TestCase):
    """The accounting identity must close, or the gate fails."""

    @staticmethod
    def _binding(invocations, by_tool, denials, completed):
        return {"events": {"oracle_invocations": invocations, "oracle_by_tool": by_tool,
                           "oracle_denials": denials, "completed_tools": completed}}

    @staticmethod
    def _row(seq, tool, decision, reason, outcome):
        return {"seq": seq, "invocation": str(seq), "tool": tool, "decision": decision,
                "reason": reason, "outcome": outcome, "created": "x"}

    def test_a_denial_and_a_bridge_rejection_are_both_accounted(self):
        journal = [
            self._row(1, "inspect", "admitted", "admitted", "completed"),
            self._row(2, "test", "rejected", "bad_arguments", "n/a"),
            self._row(3, "inspect", "admitted", "admitted", "failed"),
        ]
        binding = self._binding(4, {"oracle_inspect": 2, "oracle_test": 1, "oracle_diff": 1},
                                {"oracle_diff": 1}, ["oracle_inspect"])
        result = _reconcile_calls(binding, journal, granted=2, invocation_count=4)
        self.assertTrue(result["ok"], result["problems"])
        self.assertEqual(result["admitted"], 2)
        self.assertEqual(result["rejected_before_admission"], 1)
        self.assertEqual(result["denied"], 1)

    def test_an_admitted_failure_is_not_recounted_as_a_rejection(self):
        # Two admissions, one of them failed after admission. The reservation
        # stands, so the ledger must show two - never one.
        journal = [
            self._row(1, "inspect", "admitted", "admitted", "completed"),
            self._row(2, "test", "admitted", "admitted", "failed"),
        ]
        binding = self._binding(2, {"oracle_inspect": 1, "oracle_test": 1}, {}, ["oracle_inspect"])
        result = _reconcile_calls(binding, journal, granted=2, invocation_count=2)
        self.assertTrue(result["ok"], result["problems"])

    def test_an_invocation_that_fits_no_class_fails_closed(self):
        journal = [self._row(1, "inspect", "admitted", "admitted", "completed")]
        # The stream shows a second oracle invocation that never reached the bridge
        # and is not a permission denial: it cannot be classified.
        binding = self._binding(2, {"oracle_inspect": 1, "oracle_test": 1}, {}, ["oracle_inspect"])
        result = _reconcile_calls(binding, journal, granted=1, invocation_count=2)
        self.assertFalse(result["ok"])
        self.assertTrue(any("per-tool" in problem for problem in result["problems"]),
                        result["problems"])

    def test_a_journal_that_disagrees_with_the_ledger_fails_closed(self):
        journal = [self._row(1, "inspect", "admitted", "admitted", "completed")]
        binding = self._binding(1, {"oracle_inspect": 1}, {}, ["oracle_inspect"])
        result = _reconcile_calls(binding, journal, granted=2, invocation_count=1)
        self.assertFalse(result["ok"])
        self.assertTrue(any("granted" in problem for problem in result["problems"]))

    def test_a_missing_event_census_fails_closed(self):
        journal = [self._row(1, "inspect", "admitted", "admitted", "completed")]
        result = _reconcile_calls(None, journal, granted=1, invocation_count=None)
        self.assertFalse(result["ok"])


class BuildAttestationTests(unittest.TestCase):
    def test_the_build_attests_the_materialized_source(self):
        with tempfile.TemporaryDirectory() as tmp:
            candidate = Path(tmp) / "candidate.c"
            candidate.write_text((SLICE_DIR / "reference.c").read_text(), encoding="utf-8")
            build = harness.build(candidate, Path(tmp) / "build", cc="cc")
            self.assertEqual(build.source_sha256, build.compiled_sha256)
            self.assertEqual(build.source_sha256,
                             hashlib.sha256(candidate.read_bytes()).hexdigest())

    def test_the_build_refuses_a_source_that_changes_during_compilation(self):
        with tempfile.TemporaryDirectory() as tmp:
            candidate = Path(tmp) / "candidate.c"
            candidate.write_text((SLICE_DIR / "reference.c").read_text(), encoding="utf-8")
            build_dir = Path(tmp) / "build"
            real = harness.run_bounded

            def tampering(argv, **kwargs):
                capture = real(argv, **kwargs)
                # The compiler has already read the file; mutate it now so the
                # post-compilation re-hash disagrees with the attested one.
                Path(kwargs["capture_dir"]).joinpath("candidate.c").write_text(
                    "/* replaced */\n", encoding="utf-8")
                return capture

            with unittest.mock.patch.object(harness, "run_bounded", tampering):
                with self.assertRaises(harness.HarnessError):
                    harness.build(candidate, build_dir, cc="cc")


if __name__ == "__main__":
    unittest.main()
