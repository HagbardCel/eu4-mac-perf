"""The campaign claim ledger must fail closed on corrupt state.

The ledger is the campaign-wide validation allowance.  A reader that ignored a
bad row, or returned an empty list for an unreadable file, would make corrupt
state look like fewer attempts and let a caller spend the budget again.  These
tests pin the fail-closed contract, cross-process atomicity and the interrupted
append shape.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from automation import session_state

REPO = Path(__file__).resolve().parents[1]


class ClaimLedgerIntegrityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.claims = session_state.CampaignClaims(self.root)

    def _claim(self, attempt: str, limit: int | None = None) -> dict:
        return self.claims.claim(
            operation="validate", attempt_id=attempt, session_id="sess-a",
            limit=limit)

    def _write(self, body: bytes) -> None:
        self.claims.path.parent.mkdir(parents=True, exist_ok=True)
        self.claims.path.write_bytes(body)

    def test_missing_ledger_is_the_legitimate_zero_attempt_state(self) -> None:
        self.assertEqual(self.claims.rows(), [])
        claimed = self._claim("first", limit=1)
        self.assertTrue(claimed["ok"])
        self.assertEqual(len(self.claims.rows()), 1)

    def test_first_initialization_writes_a_committed_head(self) -> None:
        self.assertFalse(self.claims.head_path.exists())
        self._claim("first")
        head = json.loads(self.claims.head_path.read_text())
        self.assertEqual(head["schema"], session_state.CLAIM_HEAD_SCHEMA)
        self.assertEqual(head["count"], 1)
        self.assertEqual(
            head["prefix_sha256"],
            session_state._claim_prefix_digest(self.claims.rows()))

    def test_empty_ledger_is_valid(self) -> None:
        # A pre-created empty file with no committed rows is still the legacy
        # zero state; the first claim adopts it and writes the head.
        self._write(b"")
        self.assertEqual(self.claims.rows(), [])
        self.assertTrue(self._claim("first")["ok"])
        self.assertEqual(len(self.claims.rows()), 1)

    def test_missing_ledger_after_recorded_claims_is_rejected(self) -> None:
        self._claim("first")
        self.claims.path.unlink()
        with self.assertRaises(session_state.ClaimLedgerError) as caught:
            self.claims.rows()
        self.assertEqual(caught.exception.kind, "missing_after_claims")
        with self.assertRaises(session_state.ClaimLedgerError):
            self._claim("budget-reset", limit=2)

    def test_empty_replacement_after_claims_is_rejected(self) -> None:
        self._claim("first")
        self._claim("second")
        self._write(b"")
        with self.assertRaises(session_state.ClaimLedgerError) as caught:
            self.claims.rows()
        self.assertEqual(caught.exception.kind, "truncated")
        with self.assertRaises(session_state.ClaimLedgerError):
            self._claim("budget-reset", limit=3)

    def test_truncation_to_fewer_complete_rows_is_rejected(self) -> None:
        for attempt in ("first", "second", "third"):
            self._claim(attempt)
        lines = self.claims.path.read_text(encoding="utf-8").splitlines()
        self.claims.path.write_text(
            "\n".join(lines[:2]) + "\n", encoding="utf-8")
        with self.assertRaises(session_state.ClaimLedgerError) as caught:
            self.claims.rows()
        self.assertEqual(caught.exception.kind, "truncated")

    def test_a_replaced_prefix_is_rejected(self) -> None:
        self._claim("first")
        self._claim("second")
        lines = self.claims.path.read_text(encoding="utf-8").splitlines()
        replacement = json.loads(lines[0])
        replacement["attempt_id"] = "forged"
        self.claims.path.write_text(
            json.dumps(replacement, sort_keys=True) + "\n" + lines[1] + "\n",
            encoding="utf-8")
        with self.assertRaises(session_state.ClaimLedgerError) as caught:
            self.claims.rows()
        self.assertEqual(caught.exception.kind, "replaced")

    def test_a_ledger_without_its_committed_head_is_rejected(self) -> None:
        self._claim("first")
        self.claims.head_path.unlink()
        with self.assertRaises(session_state.ClaimLedgerError) as caught:
            self.claims.rows()
        self.assertEqual(caught.exception.kind, "missing_head")

    def test_a_malformed_committed_head_is_rejected(self) -> None:
        self._claim("first")
        self.claims.head_path.write_text("{not-json}\n", encoding="utf-8")
        with self.assertRaises(session_state.ClaimLedgerError) as caught:
            self.claims.rows()
        self.assertEqual(caught.exception.kind, "malformed_head")

    def test_an_interrupted_head_update_keeps_the_charge_and_catches_up(self) -> None:
        # The row is written and fsynced before the head; a crash between the
        # two must keep the charge. The next claim repairs the committed head
        # instead of refusing the extra row.
        self._claim("first")
        with self.claims.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps({
                "schema": session_state.CLAIM_SCHEMA,
                "attempt_id": "interrupted-checker",
                "operation": "validate",
                "session_id": "sess-a",
                "at": 1.0,
            }, sort_keys=True) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        rows = self.claims.rows()
        self.assertEqual([row["attempt_id"] for row in rows],
                         ["first", "interrupted-checker"])
        # The interrupted row is charged: the last allowance is already used.
        refused = self._claim("third", limit=2)
        self.assertFalse(refused["ok"])
        self.assertEqual(refused["kind"], "budget_exhausted")
        # A read-only claim path still catches the witness up with the rows it
        # observed, so a later truncation of the uncommitted tail is detected.
        self.assertEqual(json.loads(
            self.claims.head_path.read_text())["count"], 2)
        self.claims.path.write_text(
            self.claims.path.read_text().splitlines(True)[0], encoding="utf-8")
        with self.assertRaises(session_state.ClaimLedgerError) as caught:
            self.claims.rows()
        self.assertEqual(caught.exception.kind, "truncated")

    def test_an_interrupted_first_claim_is_charged_and_recoverable(self) -> None:
        # The head is installed before the first append, so a crash after the
        # row is durable and before the head update leaves a count-0 head. The
        # row must stay charged and the next claim must repair the witness,
        # rather than both failing as missing_head forever.
        real = self.claims._write_head
        calls = {"n": 0}

        def flaky(rows, *, at=None):
            calls["n"] += 1
            if calls["n"] == 2:
                raise session_state.ClaimLedgerError(
                    "unwritable_head", "simulated crash after append")
            return real(rows, at=at)

        with mock.patch.object(self.claims, "_write_head", side_effect=flaky):
            with self.assertRaises(session_state.ClaimLedgerError):
                self._claim("first")
        rows = self.claims.rows()
        self.assertEqual([row["attempt_id"] for row in rows], ["first"])
        self.assertEqual(json.loads(
            self.claims.head_path.read_text())["count"], 0)
        second = self._claim("second", limit=2)
        self.assertTrue(second["ok"])
        self.assertEqual(json.loads(
            self.claims.head_path.read_text())["count"], 2)
        self.assertEqual(len(self.claims.rows()), 2)

    def test_a_failed_lock_is_an_integrity_refusal_and_appends_nothing(self) -> None:
        self._claim("first")
        before = self.claims.path.read_bytes()
        with mock.patch.object(
                session_state.fcntl, "flock",
                side_effect=OSError(35, "resource unavailable")):
            with self.assertRaises(session_state.ClaimLedgerError) as caught:
                self._claim("second", limit=2)
        self.assertEqual(caught.exception.kind, "lock_failed")
        self.assertEqual(self.claims.path.read_bytes(), before)

    def test_a_reader_that_cannot_lock_is_an_integrity_refusal(self) -> None:
        self._claim("first")
        with mock.patch.object(
                session_state.fcntl, "flock",
                side_effect=OSError(35, "resource unavailable")):
            with self.assertRaises(session_state.ClaimLedgerError):
                self.claims.rows()

    def test_an_interrupted_checker_claim_stays_charged_across_reconnect(self) -> None:
        # A claim is written before the checker starts and never removed: the
        # checker process dying without a result row must not free the attempt.
        self.assertTrue(self._claim("killed-checker", limit=1)["ok"])
        reconnected = session_state.CampaignClaims(self.root)
        self.assertEqual(len(reconnected.rows()), 1)
        refused = reconnected.claim(
            operation="validate", attempt_id="retry", session_id="sess-b",
            limit=1)
        self.assertFalse(refused["ok"])
        self.assertEqual(refused["kind"], "budget_exhausted")

    def test_simultaneous_claims_spend_the_last_allowance_once(self) -> None:
        self.assertTrue(self._claim("preused", limit=2)["ok"])
        script = (
            "import json, sys\n"
            "from automation.session_state import CampaignClaims\n"
            "claim = CampaignClaims(sys.argv[1]).claim(\n"
            "    operation='validate', attempt_id=sys.argv[2],\n"
            "    session_id='sess-a', limit=2)\n"
            "print(json.dumps(claim))\n"
        )
        environment = dict(os.environ)
        environment["PYTHONPATH"] = str(REPO)
        processes = [
            subprocess.Popen(
                [sys.executable, "-c", script, str(self.root), f"last-{i}"],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                text=True, env=environment, cwd=REPO)
            for i in range(4)
        ]
        results = []
        for process in processes:
            stdout, stderr = process.communicate(timeout=120)
            self.assertEqual(process.returncode, 0, stderr)
            results.append(json.loads(stdout))
        accepted = [result for result in results if result.get("ok")]
        refused = [result for result in results if not result.get("ok")]
        self.assertEqual(len(accepted), 1, results)
        self.assertEqual(len(refused), 3, results)
        self.assertTrue(all(result["kind"] == "budget_exhausted"
                            for result in refused))
        self.assertEqual(len(self.claims.rows()), 2)

    def test_malformed_json_row_is_rejected_and_not_skipped(self) -> None:
        self._write(b'{"schema":"' + session_state.CLAIM_SCHEMA.encode() +
                    b'","attempt_id":"used"}\n{not-json}\n')
        with self.assertRaises(session_state.ClaimLedgerError) as caught:
            self.claims.rows()
        self.assertEqual(caught.exception.kind, "malformed_record")

    def test_truncated_final_record_is_rejected(self) -> None:
        partial = (b'{"schema":"' + session_state.CLAIM_SCHEMA.encode() +
                   b'","attempt_id":"interrupted"')
        self._write(partial)
        with self.assertRaises(session_state.ClaimLedgerError) as caught:
            self.claims.rows()
        self.assertEqual(caught.exception.kind, "truncated")

    def test_interrupted_append_after_a_good_row_is_rejected(self) -> None:
        claimed = self._claim("complete")
        self.assertTrue(claimed["ok"])
        with self.claims.path.open("ab") as handle:
            handle.write(b'{"schema":"' + session_state.CLAIM_SCHEMA.encode() +
                         b'","attempt_id":"half"')
        with self.assertRaises(session_state.ClaimLedgerError):
            self.claims.rows()

    def test_blank_line_is_rejected(self) -> None:
        self._write(b"\n")
        with self.assertRaises(session_state.ClaimLedgerError):
            self.claims.rows()

    def test_non_object_and_wrong_schema_rows_are_rejected(self) -> None:
        for body in (b'["not-an-object"]\n',
                     b'{"attempt_id":"x"}\n',
                     b'{"schema":"other","attempt_id":"x"}\n'):
            with self.subTest(body=body):
                self._write(body)
                with self.assertRaises(session_state.ClaimLedgerError):
                    self.claims.rows()

    def test_row_without_a_usable_attempt_id_is_rejected(self) -> None:
        for body in (
                b'{"schema":"' + session_state.CLAIM_SCHEMA.encode() + b'"}\n',
                b'{"schema":"' + session_state.CLAIM_SCHEMA.encode() +
                b'","attempt_id":""}\n',
                b'{"schema":"' + session_state.CLAIM_SCHEMA.encode() +
                b'","attempt_id":7}\n'):
            with self.subTest(body=body):
                self._write(body)
                with self.assertRaises(session_state.ClaimLedgerError) as caught:
                    self.claims.rows()
                self.assertEqual(caught.exception.kind, "malformed_record")

    def test_invalid_utf8_is_rejected(self) -> None:
        self._write(b"\xff\xfe\n")
        with self.assertRaises(session_state.ClaimLedgerError) as caught:
            self.claims.rows()
        self.assertEqual(caught.exception.kind, "invalid_encoding")

    def test_unreadable_ledger_is_rejected(self) -> None:
        self.claims.path.mkdir(parents=True)
        with self.assertRaises(session_state.ClaimLedgerError) as caught:
            self.claims.rows()
        self.assertEqual(caught.exception.kind, "unreadable")

    def test_corrupt_ledger_cannot_reset_the_budget(self) -> None:
        # If the bad row were skipped this would look like zero attempts and the
        # claim below would be allowed.  It must refuse instead.
        self._write(b"{corrupt}\n")
        before = self.claims.path.read_bytes()
        with self.assertRaises(session_state.ClaimLedgerError):
            self._claim("budget-reset", limit=1)
        self.assertEqual(self.claims.path.read_bytes(), before)
        with self.assertRaises(session_state.ClaimLedgerError):
            self.claims.rows()

    def test_corrupt_state_does_not_break_duplicate_idempotency(self) -> None:
        self._write(b"{corrupt}\n")
        with self.assertRaises(session_state.ClaimLedgerError):
            self._claim("anything")

    def test_duplicate_claim_is_still_idempotent(self) -> None:
        first = self._claim("same-attempt", limit=1)
        second = self._claim("same-attempt", limit=1)
        self.assertTrue(first["ok"])
        self.assertTrue(second["ok"])
        self.assertTrue(second["duplicate"])
        self.assertEqual(len(self.claims.rows()), 1)

    def test_cross_process_claims_spend_the_limit_once(self) -> None:
        script = (
            "import json, sys\n"
            "from automation.session_state import CampaignClaims\n"
            "claim = CampaignClaims(sys.argv[1]).claim(\n"
            "    operation='validate', attempt_id=sys.argv[2],\n"
            "    session_id='sess-a', limit=1)\n"
            "print(json.dumps(claim))\n"
        )
        environment = dict(os.environ)
        environment["PYTHONPATH"] = str(REPO)
        processes = [
            subprocess.Popen(
                [sys.executable, "-c", script, str(self.root), f"attempt-{i}"],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                text=True, env=environment, cwd=REPO)
            for i in range(6)
        ]
        results = []
        for process in processes:
            stdout, stderr = process.communicate(timeout=120)
            self.assertEqual(process.returncode, 0, stderr)
            results.append(json.loads(stdout))
        accepted = [result for result in results if result.get("ok")]
        refused = [result for result in results if not result.get("ok")]
        self.assertEqual(len(accepted), 1, results)
        self.assertEqual(len(refused), 5, results)
        self.assertTrue(all(result["kind"] == "budget_exhausted"
                            for result in refused))
        self.assertEqual(len(self.claims.rows()), 1)


if __name__ == "__main__":
    unittest.main()
