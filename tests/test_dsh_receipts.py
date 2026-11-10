"""The qualification record, and the candidate identities beside it."""

import json
import os
import tempfile
import unittest
from pathlib import Path

from automation import dsh_receipts as receipts


class TreeDigestTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def test_digest_is_stable_and_content_addressed(self) -> None:
        (self.root / "a.txt").write_text("one\n", encoding="utf-8")
        first, _ = receipts.tree_digest(self.root)
        second, _ = receipts.tree_digest(self.root)
        self.assertEqual(first, second)
        (self.root / "a.txt").write_text("two\n", encoding="utf-8")
        third, _ = receipts.tree_digest(self.root)
        self.assertNotEqual(first, third)

    def test_symlinks_are_recorded_by_target(self) -> None:
        (self.root / "target.txt").write_text("data\n", encoding="utf-8")
        os.symlink(self.root / "target.txt", self.root / "link.txt")
        digest, entries = receipts.tree_digest(self.root)
        kinds = {entry["path"]: entry["kind"] for entry in entries}
        self.assertEqual(kinds["link.txt"], "symlink")
        self.assertEqual(len(digest), 64)


class QualificationRecordTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.run_root = Path(self.tmp.name) / "run"
        receipts.paths(self.run_root).ensure()

    def record(self, verdict="fail") -> dict:
        return {
            "schema": receipts.SCHEMA,
            "identities": {
                "campaign_id": "c",
                "checker_sha256": "a" * 64,
                "candidate": {"mode": "seed", "digest": "b" * 64},
            },
            "outcome": {"checker_verdict": verdict, "bridge_classification": "verdict"},
            "created_at": "2026-01-01T00:00:00+00:00",
        }

    def test_identity_digest_ignores_key_order(self) -> None:
        self.assertEqual(
            receipts.identity_digest({"b": 2, "a": 1}),
            receipts.identity_digest({"a": 1, "b": 2}),
        )

    def test_one_record_round_trips_and_is_replaced_in_place(self) -> None:
        path, sealed = receipts.write_receipt(self.run_root, self.record("fail"))
        self.assertTrue(path.is_file())
        self.assertEqual(
            receipts.load_current_receipt(self.run_root)["receipt_digest"],
            sealed["receipt_digest"],
        )
        # A second validation replaces the record: there is no attempt history.
        receipts.write_receipt(self.run_root, self.record("pass"))
        self.assertEqual(
            receipts.load_current_receipt(self.run_root)["outcome"]["checker_verdict"],
            "pass",
        )
        self.assertEqual(len(list(self.run_root.glob("*.json"))), 1)

    def test_tampering_is_detected(self) -> None:
        path, _ = receipts.write_receipt(self.run_root, self.record("fail"))
        body = json.loads(path.read_text(encoding="utf-8"))
        body["outcome"]["checker_verdict"] = "pass"
        path.write_text(json.dumps(body), encoding="utf-8")
        problems = receipts.verify_receipt_digest(body)
        self.assertTrue(problems)
        self.assertIn("receipt_digest mismatch", problems[0])

    def test_absent_record_reads_as_none(self) -> None:
        self.assertIsNone(receipts.load_current_receipt(self.run_root))


if __name__ == "__main__":
    unittest.main()
