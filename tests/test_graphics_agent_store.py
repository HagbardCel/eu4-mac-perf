import tempfile
import unittest
from pathlib import Path

from reverse_engineering.graphics_agent.research_scheduler import InvalidTransition, transition
from reverse_engineering.graphics_agent.research_store import (
    connect,
    ensure_investigation,
    ghidra_op_reserve,
    upsert_claim,
)
from reverse_engineering.graphics_agent.validation import REVIEW_PROPOSED


class ResearchStoreTests(unittest.TestCase):
    def test_investigation_and_claim(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            conn = connect(Path(tmp) / "research.sqlite")
            ensure_investigation(conn, "inv1", "bin", "invdigest", "rev1", "PENDING")
            transition(conn, "inv1", "INVESTIGATING")
            upsert_claim(
                conn,
                {
                    "claim_id": "c1",
                    "investigation_id": "inv1",
                    "statement": "test",
                    "scientific_status": "hypothesis",
                    "review_status": REVIEW_PROPOSED,
                    "evidence_ids": [],
                },
            )
            row = conn.execute("SELECT claim_id FROM claims").fetchone()
            self.assertEqual(row["claim_id"], "c1")

    def test_invalid_transition(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            conn = connect(Path(tmp) / "research.sqlite")
            ensure_investigation(conn, "inv1", "bin", "invdigest", "rev1", "PENDING")
            with self.assertRaises(InvalidTransition):
                transition(conn, "inv1", "COMPLETED")

    def test_ghidra_idempotency(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            conn = connect(Path(tmp) / "research.sqlite")
            ensure_investigation(conn, "inv1", "bin", "invdigest", "rev1")
            k = "key1"
            s1 = ghidra_op_reserve(conn, k, "inv1", "disassembly", {"rvas": ["0x1"]})
            s2 = ghidra_op_reserve(conn, k, "inv1", "disassembly", {"rvas": ["0x1"]})
            self.assertEqual(s1, "reserved")
            self.assertEqual(s2, "reserved")


if __name__ == "__main__":
    unittest.main()
