import json
import os
import tempfile
import unittest
from pathlib import Path

from reverse_engineering.graphics_agent.tools import EvidenceService

GRAPH_MINI = Path(__file__).resolve().parents[1] / "tests" / "re_fixtures" / "graph_mini"


class McpSmokeTests(unittest.TestCase):
    def test_evidence_service_find_paths(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
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
            svc = EvidenceService.open("rev", "mcp-inv")
            res = svc.find_paths("0x5000", "ext:_glDrawElements")
            self.assertTrue(res["found"])


if __name__ == "__main__":
    unittest.main()
