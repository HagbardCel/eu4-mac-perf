import json
import os
import tempfile
import unittest
from pathlib import Path

from reverse_engineering.graphics_agent.tools import EvidenceService
from reverse_engineering.graphics_agent.validation import REVIEW_EVIDENCE_VALIDATED, REVIEW_PROPOSED
from reverse_engineering.graphics_agent.verification import REVIEW_REJECTED, run_verification
from reverse_engineering.manifest import inventory_bundle_digest

GRAPH_MINI = Path(__file__).resolve().parent / "re_fixtures" / "graph_mini"
class VerificationTests(unittest.TestCase):
    def setUp(self) -> None:
        os.environ["ALLOW_MODEL_REQUESTS"] = "false"
        os.environ["EU4_AGENT_FIXTURE_MODE"] = "1"

    def _service(self, tmp: str) -> EvidenceService:
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
        from reverse_engineering import index_db

        index_db.rebuild_index(exp / "index.sqlite", inv, exp / "decompiled", manifest={"batches": {}})
        (exp / "agent").mkdir(parents=True)
        os.environ["EU4_RE_EXPORT"] = tmp
        return EvidenceService.open("rev", "test-inv")

    def test_claim_stored_then_verified_separately(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            svc = self._service(tmp)
            path_res = svc.find_paths("0x5000", "ext:_glDrawElements")
            eid = path_res["evidence_ids"][0]
            stored = svc.submit_findings(
                {
                    "claim_id": "draw-path-1",
                    "statement": "Draw path reaches gl draw",
                    "evidence_ids": [eid],
                    "scientific_status": "supported_inference",
                }
            )
            self.assertTrue(stored["ok"])
            self.assertEqual(stored["review_status"], REVIEW_EVIDENCE_VALIDATED)
            row = svc.research_conn.execute(
                "SELECT review_status FROM claims WHERE claim_id = ?", ("draw-path-1",)
            ).fetchone()
            self.assertEqual(row["review_status"], REVIEW_EVIDENCE_VALIDATED)
            verify = run_verification(svc.research_conn, "test-inv", "draw-path-1", service=svc)
            self.assertTrue(verify["ok"])
            self.assertNotEqual(verify["outcome"], REVIEW_PROPOSED)

    def test_contradictory_claim_rejected_by_verifier(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            svc = self._service(tmp)
            path_res = svc.find_paths("0x5000", "ext:_glDrawElements")
            eid = path_res["evidence_ids"][0]
            svc.submit_findings(
                {
                    "claim_id": "draw-reject",
                    "statement": "The draw path only calls malloc and never reaches OpenGL",
                    "evidence_ids": [eid],
                    "scientific_status": "hypothesis",
                }
            )
            out = run_verification(svc.research_conn, "test-inv", "draw-reject", service=svc)
            self.assertEqual(out["outcome"], REVIEW_REJECTED)


if __name__ == "__main__":
    unittest.main()
