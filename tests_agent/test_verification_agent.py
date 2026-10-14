import json
import os
import tempfile
import unittest
from pathlib import Path

from reverse_engineering.graphics_agent.research_store import register_evidence, upsert_claim
from reverse_engineering.graphics_agent.tools import EvidenceService
from reverse_engineering.graphics_agent.validation import REVIEW_EVIDENCE_VALIDATED, REVIEW_MODEL_VERIFIED
from reverse_engineering.graphics_agent.verification import REVIEW_REJECTED, run_verification
from reverse_engineering.graphics_agent.verification_agent import run_model_verification

GRAPH_MINI = Path(__file__).resolve().parents[1] / "tests" / "re_fixtures" / "graph_mini"
FIXTURE = Path(__file__).resolve().parents[1] / "tests" / "re_fixtures" / "agent_graphics" / "unsupported_claim.json"


class VerificationAgentTests(unittest.TestCase):
    def setUp(self) -> None:
        os.environ["ALLOW_MODEL_REQUESTS"] = "false"

    def _service(self, tmp: str) -> EvidenceService:
        inv_src = GRAPH_MINI / "inventory"
        exp = Path(tmp) / "exports" / "rev"
        inv = exp / "inventory"
        inv.mkdir(parents=True)
        for name in ("functions.jsonl", "call_edges.jsonl", "coverage.json"):
            (inv / name).write_text((inv_src / name).read_text())
        from reverse_engineering.manifest import inventory_bundle_digest

        (exp / "provenance.json").write_text(
            json.dumps(
                {
                    "inventory_bundle_digest": inventory_bundle_digest(inv_src),
                    "baseline_revision": "rev",
                }
            )
            + "\n"
        )
        from reverse_engineering import index_db

        index_db.rebuild_index(exp / "index.sqlite", inv, exp / "decompiled", manifest={"batches": {}})
        (exp / "agent").mkdir(parents=True)
        os.environ["EU4_RE_EXPORT"] = tmp
        return EvidenceService.open("rev", "verify-agent-inv")

    def test_supported_claim_verified_via_agent_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            svc = self._service(tmp)
            path_res = svc.find_paths("0x5000", "ext:_glDrawElements")
            eid = path_res["evidence_ids"][0]
            svc.submit_findings(
                {
                    "claim_id": "agent-supported",
                    "statement": "Indexed draw path reaches OpenGL draw call",
                    "evidence_ids": [eid],
                    "scientific_status": "supported_inference",
                }
            )
            out = run_model_verification(svc, "agent-supported")
            self.assertTrue(out["ok"])
            self.assertEqual(out["outcome"], REVIEW_MODEL_VERIFIED)

    def test_unsupported_claim_rejected_via_agent_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            svc = self._service(tmp)
            fixture = json.loads(FIXTURE.read_text())
            register_evidence(
                svc.research_conn,
                fixture["evidence_ids"][0],
                "call-edge",
                {"ultimate_external_symbol": "_glDrawElements", "caller_rva": "0x5000"},
            )
            upsert_claim(
                svc.research_conn,
                {
                    "claim_id": fixture["claim_id"],
                    "investigation_id": "verify-agent-inv",
                    "statement": fixture["statement"],
                    "scientific_status": fixture["scientific_status"],
                    "review_status": REVIEW_EVIDENCE_VALIDATED,
                    "evidence_ids": fixture["evidence_ids"],
                },
            )
            out = run_verification(svc.research_conn, "verify-agent-inv", fixture["claim_id"], service=svc)
            self.assertEqual(out["outcome"], REVIEW_REJECTED)


if __name__ == "__main__":
    unittest.main()
