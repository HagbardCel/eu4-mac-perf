import json
import os
import tempfile
import unittest
from pathlib import Path

from reverse_engineering.graphics_agent.tools import EvidenceService
from reverse_engineering.graphics_agent.research_store import resolve_evidence_artifact
from reverse_engineering.manifest import inventory_bundle_digest

GRAPH_MINI = Path(__file__).resolve().parent / "re_fixtures" / "graph_mini"


class GraphicsAgentPathTests(unittest.TestCase):
    def _service(self, tmp: str) -> EvidenceService:
        inv_src = GRAPH_MINI / "inventory"
        exp = Path(tmp) / "exports" / "rev"
        inv = exp / "inventory"
        inv.mkdir(parents=True)
        for name in ("functions.jsonl", "call_edges.jsonl", "coverage.json"):
            (inv / name).write_text((inv_src / name).read_text())
        digest = inventory_bundle_digest(inv_src)
        (exp / "provenance.json").write_text(
            json.dumps(
                {
                    "inventory_bundle_digest": digest,
                    "baseline_revision": "rev",
                }
            )
            + "\n"
        )
        from reverse_engineering import index_db

        index_db.rebuild_index(exp / "index.sqlite", inv, exp / "decompiled", manifest={"batches": {}})
        ctx = exp / "agent"
        ctx.mkdir(parents=True)
        (ctx / "graphics-agent-context.json").write_text(json.dumps({"known_architecture": {}}) + "\n")
        os.environ["EU4_RE_EXPORT"] = tmp
        return EvidenceService.open("rev", "test-inv")

    def test_draw_path_to_gl_draw_elements(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            svc = self._service(tmp)
            res = svc.find_paths("0x5000", "ext:_glDrawElements")
            self.assertTrue(res["found"])
            self.assertTrue(res["evidence_ids"])
            symbols = []
            for eid in res["evidence_ids"]:
                artifact = resolve_evidence_artifact(svc.research_conn, eid)
                self.assertIsNotNone(artifact)
                symbols.append(artifact["artifact"].get("ultimate_external_symbol"))
            self.assertIn("_glDrawElements", symbols)

    def test_supported_inference_requires_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            svc = self._service(tmp)
            out = svc.submit_findings(
                {
                    "claim_id": "c-no-ev",
                    "statement": "test",
                    "evidence_ids": [],
                    "scientific_status": "supported_inference",
                }
            )
            self.assertFalse(out["ok"])


if __name__ == "__main__":
    unittest.main()
