import hashlib
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from reverse_engineering.graphics_agent import ghidra_queries
from reverse_engineering.graphics_agent.research_store import (
    connect,
    ensure_investigation,
    ghidra_op_get,
    ghidra_op_reserve,
    ghidra_op_transition,
)
from reverse_engineering.manifest import inventory_bundle_digest

GRAPH_MINI = Path(__file__).resolve().parent / "re_fixtures" / "graph_mini"


class GhidraProtocolTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        tmp = self.tmp.name
        inv_src = GRAPH_MINI / "inventory"
        exp = Path(tmp) / "exports" / "rev"
        inv = exp / "inventory"
        inv.mkdir(parents=True)
        for name in ("functions.jsonl", "call_edges.jsonl", "coverage.json"):
            (inv / name).write_text((inv_src / name).read_text())
        digest = inventory_bundle_digest(inv_src)
        (exp / "provenance.json").write_text(
            json.dumps({"inventory_bundle_digest": digest, "baseline_revision": "rev"}) + "\n"
        )
        (exp / "agent").mkdir(parents=True)
        os.environ["EU4_RE_EXPORT"] = tmp
        self.exp = exp
        self.binary_root = Path(tmp)
        self.conn = connect(exp / "agent" / "research.sqlite")
        ensure_investigation(self.conn, "inv", "bin", digest, "rev")
        self.cache = exp / "agent" / "cache" / "ghidra"
        self.cache.mkdir(parents=True, exist_ok=True)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_publish_without_commit_recovery(self) -> None:
        rvas = ["0x0000000000005000"]
        ikey = ghidra_queries.idempotency_key(
            inventory_bundle_digest(self.exp / "inventory"),
            "disassembly",
            rvas,
            {"max_insns": 8},
            "QueryFunctionEvidence.java",
        )
        out_path = ghidra_queries.cache_path(self.cache, ikey)
        staging = out_path.with_suffix(".staging.jsonl")
        line = json.dumps(
            {
                "entry_rva": "0x0000000000005000",
                "operation": "disassembly",
                "status": "complete_for_requested_scope",
                "facts": [],
            }
        )
        staging.write_text(line + "\n")
        ghidra_op_reserve(self.conn, ikey, "inv", "disassembly", {"rvas": rvas})
        ghidra_op_transition(self.conn, ikey, "running")
        content = staging.read_bytes()
        out_hash = hashlib.sha256(content).hexdigest()
        ghidra_queries.publish_atomic(staging, out_path)
        from reverse_engineering.graphics_agent.research_store import reconcile_uncommitted_publish

        self.assertTrue(reconcile_uncommitted_publish(self.conn, ikey, out_path, out_hash))
        row = ghidra_op_get(self.conn, ikey)
        self.assertEqual(row["state"], "published")

    def test_published_row_missing_file_fails(self) -> None:
        ikey = "deadbeef"
        ghidra_op_reserve(self.conn, ikey, "inv", "disassembly", {"rvas": ["0x1"]})
        ghidra_op_transition(
            self.conn,
            ikey,
            "published",
            output_hash="abc",
            output_path=str(self.cache / "missing.jsonl"),
        )
        from reverse_engineering.graphics_agent.research_store import verify_published_artifact

        with self.assertRaises(Exception):
            verify_published_artifact(self.conn, ikey)

    @mock.patch("reverse_engineering.graphics_agent.ghidra_queries.analyze_headless")
    def test_dedup_skips_headless(self, mock_headless: mock.Mock) -> None:
        rvas = ["0x0000000000005000"]
        inv_digest = inventory_bundle_digest(self.exp / "inventory")
        ikey = ghidra_queries.idempotency_key(
            inv_digest, "disassembly", rvas, {"max_insns": 8}, "QueryFunctionEvidence.java"
        )
        out_path = ghidra_queries.cache_path(self.cache, ikey)
        payload = json.dumps(
            {
                "entry_rva": "0x0000000000005000",
                "operation": "disassembly",
                "status": "complete_for_requested_scope",
                "facts": [],
            }
        )
        out_path.write_text(payload + "\n")
        out_hash = hashlib.sha256(out_path.read_bytes()).hexdigest()
        ghidra_op_reserve(self.conn, ikey, "inv", "disassembly", {"rvas": rvas})
        ghidra_op_transition(
            self.conn,
            ikey,
            "published",
            output_hash=out_hash,
            output_path=str(out_path),
        )
        rows = ghidra_queries.run_query_evidence(
            self.conn,
            "inv",
            inv_digest,
            self.exp,
            self.binary_root,
            self.cache,
            "disassembly",
            rvas,
            max_insns=8,
        )
        self.assertEqual(len(rows), 1)
        mock_headless.assert_not_called()


if __name__ == "__main__":
    unittest.main()
