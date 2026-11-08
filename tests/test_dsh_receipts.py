"""Receipt storage, digests and current-pointer behaviour."""

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


class ReceiptTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.run_root = Path(self.tmp.name) / "run"
        receipts.paths(self.run_root).ensure()

    def receipt(self, verdict="fail") -> dict:
        return {
            "schema": receipts.SCHEMA,
            "identities": {
                "campaign_id": "c",
                "checker_sha256": "a" * 64,
                "candidate": {"mode": "seed", "digest": "b" * 64},
            },
            "outcome": {"checker_verdict": verdict, "bridge_classification": "verdict"},
            "validation_round": 1,
            "created_at": "2026-01-01T00:00:00+00:00",
        }

    def test_identity_digest_ignores_key_order(self) -> None:
        one = {"b": 2, "a": 1}
        two = {"a": 1, "b": 2}
        self.assertEqual(receipts.identity_digest(one), receipts.identity_digest(two))

    def test_write_and_read_back_through_the_pointer(self) -> None:
        path, sealed = receipts.write_receipt(self.run_root, self.receipt())
        self.assertTrue(path.is_file())
        loaded = receipts.load_current_receipt(self.run_root)
        self.assertEqual(loaded["receipt_digest"], sealed["receipt_digest"])
        self.assertEqual(receipts.verify_receipt_digest(loaded), [])

    def test_tampering_is_detected(self) -> None:
        path, _ = receipts.write_receipt(self.run_root, self.receipt("fail"))
        body = json.loads(path.read_text(encoding="utf-8"))
        body["outcome"]["checker_verdict"] = "pass"
        path.write_text(json.dumps(body), encoding="utf-8")
        problems = receipts.verify_receipt_digest(body)
        self.assertTrue(problems)
        self.assertIn("receipt_digest mismatch", problems[0])

    def test_counters_defaults_survive_unknown_keys(self) -> None:
        receipts.save_counters(self.run_root, {"validations": 3, "surprise": 1})
        counters = receipts.load_counters(self.run_root)
        self.assertEqual(counters["validations"], 3)
        self.assertEqual(counters["policy_stops"], 0)
        self.assertNotIn("surprise", counters)

    def test_missing_pointer_reads_as_absent(self) -> None:
        self.assertIsNone(receipts.load_current_receipt(self.run_root))


class JournalTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.run_root = Path(self.tmp.name) / "run"
        receipts.paths(self.run_root).ensure()

    def test_receipts_are_chained_into_the_journal(self) -> None:
        receipts.write_receipt(self.run_root, {
            "schema": receipts.SCHEMA,
            "identities": {"campaign_id": "c", "candidate": {"digest": "a" * 64}},
            "outcome": {"checker_verdict": "pass", "bridge_classification": "verdict"},
            "validation_round": 1,
        })
        rows = receipts.load_journal(self.run_root)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["kind"], "receipt")
        self.assertEqual(rows[0]["seq"], 1)
        self.assertIsNone(rows[0]["prev"])
        self.assertEqual(receipts.verify_journal(self.run_root), [])

    def test_a_plain_receipt_is_not_in_the_journal(self) -> None:
        body = {
            "schema": receipts.SCHEMA,
            "identities": {"campaign_id": "c", "candidate": {"digest": "b" * 64}},
            "outcome": {"checker_verdict": "pass"},
            "validation_round": 1,
        }
        body["receipt_digest"] = receipts.digest_text(receipts.canonical_json(body))
        target = receipts.paths(self.run_root).receipts / "forged-r1.json"
        target.write_text(json.dumps(body), encoding="utf-8")
        receipts.atomic_write(
            receipts.paths(self.run_root).current,
            receipts.canonical_json({"schema": receipts.CURRENT_SCHEMA,
                                     "receipt": target.name}),
        )
        problems = receipts.journal_problem(self.run_root, body)
        self.assertTrue(any("not recorded" in problem for problem in problems))

    def test_a_broken_chain_is_detected(self) -> None:
        for round_number in (1, 2):
            receipts.write_receipt(self.run_root, {
                "schema": receipts.SCHEMA,
                "identities": {"campaign_id": "c", "candidate": {"digest": "c" * 64}},
                "outcome": {"checker_verdict": "fail"},
                "validation_round": round_number,
            })
        self.assertEqual(receipts.verify_journal(self.run_root), [])
        path = receipts.paths(self.run_root).journal
        lines = path.read_text(encoding="utf-8").splitlines()
        path.write_text("\n".join([lines[0]]) + "\n", encoding="utf-8")
        # Truncation leaves a short but internally valid chain: detect it by the
        # receipt no longer being the last recorded one.
        self.assertEqual(receipts.verify_journal(self.run_root), [])
        pointer = receipts.load_current(self.run_root)
        body = receipts.load_current_receipt(self.run_root)
        problems = receipts.journal_problem(self.run_root, body)
        self.assertTrue(problems, f"truncated journal still matched {pointer}")


if __name__ == "__main__":
    unittest.main()
