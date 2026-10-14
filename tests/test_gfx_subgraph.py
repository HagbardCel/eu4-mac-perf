import json
import tempfile
import unittest
from pathlib import Path

from reverse_engineering.gfx_subgraph import (
    SeedResolutionError,
    build_extract,
    resolve_cut_symbols,
    resolve_symbol_to_rva,
)
from reverse_engineering.call_graph import load_call_graph

GRAPH_MINI = Path(__file__).resolve().parent / "re_fixtures" / "graph_mini"
SEEDS = Path(__file__).resolve().parents[1] / "reverse_engineering" / "graphics_seeds.json"


class GfxSubgraphTests(unittest.TestCase):
    def test_backend_not_in_gfx_cut_symbols(self) -> None:
        seeds = json.loads(SEEDS.read_text())
        gfx_syms = {s for fam in seeds["gfx_abstraction_cut"].values() for s in fam}
        backend_syms = {s for fam in seeds["backend_implementation_cut"].values() for s in fam}
        self.assertNotIn("__ZN13SShaderOpenGL6SetAllEv", gfx_syms)
        self.assertIn("__ZN13SShaderOpenGL6SetAllEv", backend_syms)

    def test_cut_rejects_external_symbol(self) -> None:
        graph = load_call_graph(GRAPH_MINI / "inventory")
        with self.assertRaises(SeedResolutionError):
            resolve_cut_symbols(
                graph.functions,
                {"draw": ["ext:_glDrawElements"]},
                "gfx_abstraction_cut",
            )

    def test_mandatory_seeds_resolve_graph_mini(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            inv = Path(tmp) / "inventory"
            inv.mkdir()
            for name in ("functions.jsonl", "call_edges.jsonl", "coverage.json"):
                src = GRAPH_MINI / "inventory" / name
                inv.joinpath(name).write_text(src.read_text())
            bundle = build_extract(inv, allow_digest_mismatch=True)
            self.assertEqual(len(bundle["seed_resolution"]["mandatory"]), 3)

    def test_ss_shader_not_renderer_root_in_seeds(self) -> None:
        seeds = json.loads(SEEDS.read_text())
        self.assertNotIn("__ZN13SShaderOpenGL6SetAllEv", seeds["renderer_roots"])

    def test_gfx_cut_members_internal(self) -> None:
        graph = load_call_graph(GRAPH_MINI / "inventory")
        seeds = json.loads(SEEDS.read_text())
        resolved = resolve_cut_symbols(
            graph.functions, seeds["gfx_abstraction_cut"], "gfx_abstraction"
        )
        draw_rvas = [m["entry_rva"] for m in resolved["draw"]]
        self.assertIn(resolve_symbol_to_rva(graph.functions, "_GfxDrawIndexed"), draw_rvas)


if __name__ == "__main__":
    unittest.main()
