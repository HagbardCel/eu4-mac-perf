import json
import os
import tempfile
import unittest
from pathlib import Path

from reverse_engineering.call_graph import load_call_graph
from reverse_engineering.graphics_agent.tools import EvidenceService
from reverse_engineering.manifest import inventory_bundle_digest

GRAPH_MINI = Path(__file__).resolve().parent / "re_fixtures" / "graph_mini"


class GraphicsAgentToolsTests(unittest.TestCase):
    def _service(self, tmp: str) -> EvidenceService:
        inv_src = GRAPH_MINI / "inventory"
        exp = Path(tmp) / "exports" / "rev"
        inv = exp / "inventory"
        inv.mkdir(parents=True)
        for name in ("functions.jsonl", "call_edges.jsonl", "coverage.json"):
            (inv / name).write_text((inv_src / name).read_text())
        (exp / "provenance.json").write_text(
            json.dumps({"inventory_bundle_digest": inventory_bundle_digest(inv_src)}) + "\n"
        )
        from reverse_engineering import index_db

        index_db.rebuild_index(exp / "index.sqlite", inv, exp / "decompiled", manifest={"batches": {}})
        ctx = exp / "agent"
        ctx.mkdir(parents=True)
        (ctx / "graphics-agent-context.json").write_text(
            json.dumps({"known_architecture": {}}) + "\n"
        )
        os.environ["EU4_RE_EXPORT"] = tmp
        return EvidenceService.open("rev", "test-inv")

    def test_search_and_context(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            svc = self._service(tmp)
            hits = svc.search_functions("fn", limit=5)
            self.assertIn("functions", hits)
            rvas = [f["entry_rva"] for f in hits["functions"]]
            if rvas:
                ctx = svc.get_function_context(rvas[0])
                self.assertIn("callers", ctx)

    def test_api_endpoints_all_external(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            svc = self._service(tmp)
            eps = svc.get_api_endpoints(api_family="all_external", limit=20)
            self.assertIn("endpoints", eps)

    def test_find_paths(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            svc = self._service(tmp)
            g = load_call_graph(GRAPH_MINI / "inventory")
            internal = [r for r in g.functions if not r.startswith("ext:")]
            if len(internal) >= 2:
                res = svc.find_paths(internal[0], internal[1])
                self.assertIn("found", res)


if __name__ == "__main__":
    unittest.main()
