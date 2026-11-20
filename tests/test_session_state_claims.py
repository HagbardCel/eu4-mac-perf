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

    def test_empty_ledger_is_valid(self) -> None:
        self._write(b"")
        self.assertEqual(self.claims.rows(), [])

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
