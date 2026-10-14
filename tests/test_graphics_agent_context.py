import json
import tempfile
import unittest
from pathlib import Path

from reverse_engineering.gfx_subgraph import build_extract
from reverse_engineering.graphics_agent.context_builder import (
    GENERATOR_VERSION,
    collect_candidates,
    graph_analysis_code_hash,
    seeds_config_hash,
    subgraph_cache_valid,
)
from reverse_engineering.call_graph import load_call_graph
from reverse_engineering.manifest import inventory_bundle_digest

GRAPH_MINI = Path(__file__).resolve().parent / "re_fixtures" / "graph_mini"


class GraphicsAgentContextTests(unittest.TestCase):
    def test_subgraph_cache_valid_requires_metadata(self) -> None:
        inv = GRAPH_MINI / "inventory"
        digest = inventory_bundle_digest(inv)
        cached = {
            "schema": 2,
            "cache_provenance": {
                "inventory_bundle_digest": digest,
                "binary_sha256": "x",
                "graphics_seeds_sha256": seeds_config_hash(),
                "graph_analysis_code_sha256": graph_analysis_code_hash(),
                "generator_version": GENERATOR_VERSION,
            },
        }
        self.assertFalse(subgraph_cache_valid(cached, digest, "y", seeds_config_hash(), graph_analysis_code_hash()))
        self.assertTrue(
            subgraph_cache_valid(cached, digest, "x", seeds_config_hash(), graph_analysis_code_hash())
        )

    def test_collect_candidates_from_mini_graph(self) -> None:
        inv = GRAPH_MINI / "inventory"
        bundle = build_extract(inv, allow_digest_mismatch=True)
        graph = load_call_graph(inv)
        candidates = collect_candidates(graph, bundle, graph.functions, limit=50)
        self.assertTrue(candidates)
        for row in candidates:
            self.assertIn("discovery_sources", row)
            self.assertNotIn("data_ref_dispatch_gap", row.get("discovery_sources", []))

    def test_prepare_writes_package(self) -> None:
        from reverse_engineering.graphics_agent.context_builder import prepare_package

        with tempfile.TemporaryDirectory() as tmp:
            inv_src = GRAPH_MINI / "inventory"
            exp = Path(tmp) / "exports" / "test-rev"
            inv = exp / "inventory"
            inv.mkdir(parents=True)
            for name in ("functions.jsonl", "call_edges.jsonl", "coverage.json"):
                (inv / name).write_text((inv_src / name).read_text())
            (exp / "provenance.json").write_text(
                json.dumps({"inventory_bundle_digest": inventory_bundle_digest(inv_src)}) + "\n"
            )
            (exp / "index.sqlite").write_bytes(b"")
            import os

            os.environ["EU4_RE_EXPORT"] = tmp
            agent_dir = prepare_package("test-rev", allow_digest_mismatch=True)
            self.assertTrue((agent_dir / "graphics-agent-context.json").is_file())
            self.assertTrue((agent_dir / "graphics-candidates.jsonl").is_file())


if __name__ == "__main__":
    unittest.main()
