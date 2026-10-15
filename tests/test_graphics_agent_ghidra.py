import hashlib
import json
import multiprocessing
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from reverse_engineering.graphics_agent import ghidra_queries
from reverse_engineering.graphics_agent.ghidra_acquire import (
    ghidra_complete_publish,
    ghidra_try_acquire,
    project_key_from_identity,
    reconcile_published_evidence,
)
from reverse_engineering.graphics_agent.evidence_ids import args_canonical_hash, ghidra_artifact_id
from reverse_engineering.corpus import script_hash
from reverse_engineering.paths import ReError
from tests.ghidra_mp_workers import mp_try_acquire
from reverse_engineering.graphics_agent.research_store import (
    connect,
    ensure_investigation,
    ghidra_op_get,
    ghidra_op_reserve,
    ghidra_op_transition,
    reconcile_uncommitted_publish,
    verify_published_artifact,
)
from reverse_engineering.manifest import inventory_bundle_digest

GRAPH_MINI = Path(__file__).resolve().parent / "re_fixtures" / "graph_mini"


def fixture_identity() -> dict[str, str]:
    return {
        "ghidra_version": "fixture",
        "project_name": "fixture",
        "project_meta_hash": hashlib.sha256(b"fixture").hexdigest(),
        "program_name": "fixture",
    }


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
        os.environ["EU4_AGENT_FIXTURE_MODE"] = "1"
        self.exp = exp
        self.binary_root = Path(tmp)
        self.conn = connect(exp / "agent" / "research.sqlite")
        ensure_investigation(self.conn, "inv", "bin", digest, "rev")
        self.cache = exp / "agent" / "cache" / "ghidra"
        self.cache.mkdir(parents=True, exist_ok=True)
        self.identity = fixture_identity()
        self.inv_digest = digest

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _ikey(self, operation: str, rvas: list[str], extra: dict, script: str = "QueryFunctionEvidence.java") -> str:
        return ghidra_queries.idempotency_key(
            self.identity, self.inv_digest, operation, rvas, extra, script
        )

    def test_publish_without_commit_recovery(self) -> None:
        rvas = ["0x0000000000005000"]
        ikey = self._ikey("disassembly", rvas, {"max_insns": 8})
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
        with self.assertRaises(Exception):
            verify_published_artifact(self.conn, ikey)

    @mock.patch("reverse_engineering.graphics_agent.ghidra_queries.analyze_headless")
    def test_dedup_skips_headless(self, mock_headless: mock.Mock) -> None:
        rvas = ["0x0000000000005000"]
        ikey = self._ikey("disassembly", rvas, {"max_insns": 8})
        out_path = ghidra_queries.cache_path(self.cache, ikey)
        meta_file = ghidra_queries.meta_path(self.cache, ikey)
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
        ghidra_queries.write_meta(meta_file, self.identity, out_hash)
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
            self.inv_digest,
            self.exp,
            self.binary_root,
            self.cache,
            "disassembly",
            rvas,
            max_insns=8,
        )
        self.assertEqual(len(rows), 1)
        mock_headless.assert_not_called()

    def test_stale_fencing_blocks_publish(self) -> None:
        ikey = self._ikey("disassembly", ["0x0000000000005000"], {"max_insns": 8})
        project_key = project_key_from_identity(self.identity)
        ghidra_op_reserve(self.conn, ikey, "inv", "disassembly", {"rvas": ["0x5000"]})
        self.conn.execute(
            "UPDATE ghidra_operations SET lease_generation = 2, state = 'running' WHERE idempotency_key = ?",
            (ikey,),
        )
        self.conn.commit()
        with self.assertRaises(Exception):
            ghidra_complete_publish(
                self.conn,
                idempotency_key=ikey,
                project_key=project_key,
                lease_generation=1,
                output_hash="abc",
                output_path=str(self.cache / "x.jsonl"),
                evidence_id="ghidra:test",
                evidence_kind="ghidra",
                evidence_artifact={},
            )

    def test_multiprocess_acquire_one_winner(self) -> None:
        ikey = self._ikey("disassembly", ["0x0000000000005000"], {"max_insns": 8})
        project_key = project_key_from_identity(self.identity)
        db_path = str(self.exp / "agent" / "research.sqlite")
        ctx = multiprocessing.get_context("spawn")
        barrier = ctx.Barrier(2)
        queue: multiprocessing.Queue = ctx.Queue()
        args = (
            db_path,
            self.inv_digest,
            "inv",
            project_key,
            ikey,
        )
        p1 = ctx.Process(
            target=mp_try_acquire,
            args=(*args, "owner-a", 10, barrier, queue),
        )
        p2 = ctx.Process(
            target=mp_try_acquire,
            args=(*args, "owner-b", 10, barrier, queue),
        )
        p1.start()
        p2.start()
        p1.join(timeout=30)
        p2.join(timeout=30)
        results = [queue.get(timeout=5), queue.get(timeout=5)]
        ok = sum(1 for r in results if r.get("ok"))
        err = sum(1 for r in results if r.get("error"))
        self.assertEqual(ok, 1)
        self.assertEqual(err, 1)

    def test_multiprocess_quota_single_slot(self) -> None:
        ikey_a = self._ikey("disassembly", ["0x0000000000005000"], {"max_insns": 4})
        ikey_b = self._ikey("references", ["0x0000000000005000"], {"max_insns": 4})
        project_key = project_key_from_identity(self.identity)
        db_path = str(self.exp / "agent" / "research.sqlite")
        ctx = multiprocessing.get_context("spawn")
        barrier = ctx.Barrier(2)
        queue: multiprocessing.Queue = ctx.Queue()
        base = (db_path, self.inv_digest, "inv", project_key)
        p1 = ctx.Process(
            target=mp_try_acquire,
            args=(*base, ikey_a, "quota-a", 1, barrier, queue),
        )
        p2 = ctx.Process(
            target=mp_try_acquire,
            args=(*base, ikey_b, "quota-b", 1, barrier, queue),
        )
        p1.start()
        p2.start()
        p1.join(timeout=30)
        p2.join(timeout=30)
        results = [queue.get(timeout=5), queue.get(timeout=5)]
        self.assertEqual(sum(1 for r in results if r.get("ok")), 1)

    def test_cross_investigation_blocks_second_project_lease(self) -> None:
        ensure_investigation(self.conn, "inv-b", "bin", self.inv_digest, "rev")
        project_key = project_key_from_identity(self.identity)
        ikey1 = self._ikey("disassembly", ["0x0000000000005000"], {"max_insns": 8})
        ikey2 = self._ikey("references", ["0x0000000000005000"], {"max_insns": 8})
        ghidra_try_acquire(
            self.conn,
            project_key=project_key,
            idempotency_key=ikey1,
            investigation_id="inv",
            operation="disassembly",
            args={"rvas": ["0x5000"]},
            owner="owner-a",
            worker_pid=os.getpid(),
            quota_key="ghidra_queries",
            quota_limit=10,
            quota_delta=1,
            max_concurrent=1,
        )
        with self.assertRaises(ReError):
            ghidra_try_acquire(
                self.conn,
                project_key=project_key,
                idempotency_key=ikey2,
                investigation_id="inv-b",
                operation="references",
                args={"rvas": ["0x5000"]},
                owner="owner-b",
                worker_pid=os.getpid(),
                quota_key="ghidra_queries",
                quota_limit=10,
                quota_delta=1,
                max_concurrent=1,
            )

    def test_published_missing_evidence_reregisters_after_validation(self) -> None:
        rvas = ["0x0000000000005000"]
        ikey = self._ikey("disassembly", rvas, {"max_insns": 8})
        out_path = ghidra_queries.cache_path(self.cache, ikey)
        meta_file = ghidra_queries.meta_path(self.cache, ikey)
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
        ghidra_queries.write_meta(meta_file, self.identity, out_hash)
        ghidra_op_reserve(self.conn, ikey, "inv", "disassembly", {"rvas": rvas})
        ghidra_op_transition(
            self.conn,
            ikey,
            "published",
            output_hash=out_hash,
            output_path=str(out_path),
        )
        project_key = project_key_from_identity(self.identity)
        args_hash = args_canonical_hash({"operation": "disassembly", "rvas": rvas, "max_insns": 8})
        eid = ghidra_artifact_id(
            self.inv_digest,
            "fixture",
            "fixture",
            script_hash("QueryFunctionEvidence.java"),
            "disassembly",
            args_hash,
            out_hash,
        )
        artifact = {
            "operation": "disassembly",
            "rvas": rvas,
            "output_path": str(out_path),
            "output_hash": out_hash,
        }
        self.assertTrue(
            reconcile_published_evidence(
                self.conn,
                ikey,
                out_path,
                out_hash,
                eid,
                "ghidra",
                artifact,
                project_key,
                meta_file=meta_file,
                identity=self.identity,
            )
        )
        row = ghidra_op_get(self.conn, ikey)
        self.assertEqual(row["evidence_id"], eid)

    def test_reconcile_rejects_hash_mismatch(self) -> None:
        rvas = ["0x0000000000005000"]
        ikey = self._ikey("disassembly", rvas, {"max_insns": 8})
        out_path = ghidra_queries.cache_path(self.cache, ikey)
        meta_file = ghidra_queries.meta_path(self.cache, ikey)
        out_path.write_text('{"entry_rva":"0x0000000000005000","operation":"disassembly","status":"complete_for_requested_scope","facts":[]}\n')
        out_hash = hashlib.sha256(out_path.read_bytes()).hexdigest()
        ghidra_queries.write_meta(meta_file, self.identity, out_hash)
        ghidra_op_reserve(self.conn, ikey, "inv", "disassembly", {"rvas": rvas})
        ghidra_op_transition(self.conn, ikey, "running")
        project_key = project_key_from_identity(self.identity)
        with self.assertRaises(ReError):
            reconcile_published_evidence(
                self.conn,
                ikey,
                out_path,
                "deadbeef",
                "ghidra:x",
                "ghidra",
                {"operation": "disassembly", "rvas": rvas, "output_hash": "deadbeef"},
                project_key,
                meta_file=meta_file,
                identity=self.identity,
            )


if __name__ == "__main__":
    unittest.main()
