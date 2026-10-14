import json
import tempfile
import unittest
from pathlib import Path

from reverse_engineering.gfx_subgraph import (
    VERDICT_BYPASSED,
    VERDICT_INSUFFICIENT,
    VERDICT_SUPPORTED,
    build_extract,
    resolve_cut_symbols,
)
from reverse_engineering.call_graph import load_call_graph
from reverse_engineering.paths import ReError

GRAPH_MINI = Path(__file__).resolve().parent / "re_fixtures" / "graph_mini"
BYPASS = Path(__file__).resolve().parent / "re_fixtures" / "graph_bypass_only"
CROSS = Path(__file__).resolve().parent / "re_fixtures" / "graph_cross_family_intercept"
BYPASS_SEEDS = Path(__file__).resolve().parent / "re_fixtures" / "bypass_test_seeds.json"
CROSS_SEEDS = Path(__file__).resolve().parent / "re_fixtures" / "cross_test_seeds.json"
SEEDS = Path(__file__).resolve().parents[1] / "reverse_engineering" / "graphics_seeds.json"


class GfxSubgraphTests(unittest.TestCase):
    def test_backend_not_in_gfx_cut_symbols(self) -> None:
        seeds = json.loads(SEEDS.read_text())
        gfx_syms = {s for fam in seeds["gfx_abstraction_cut"].values() for s in fam}
        backend_syms = {s for fam in seeds["backend_implementation_cut"].values() for s in fam}
        self.assertNotIn("__ZN13SShaderOpenGL6SetAllEv", gfx_syms)
        self.assertIn("__ZN13SShaderOpenGL6SetAllEv", backend_syms)

    def test_extract_requires_provenance(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            inv = Path(tmp) / "inventory"
            inv.mkdir()
            for name in ("functions.jsonl", "call_edges.jsonl", "coverage.json"):
                (inv / name).write_text((GRAPH_MINI / "inventory" / name).read_text())
            with self.assertRaises(ReError):
                build_extract(inv)

    def test_graph_mini_extract_allow_digest(self) -> None:
        bundle = build_extract(GRAPH_MINI / "inventory", allow_digest_mismatch=True)
        self.assertEqual(bundle["overall_boundary_verdict"], VERDICT_INSUFFICIENT)
        self.assertFalse(bundle["authoritative"])

    def test_bypass_only_overall_bypassed(self) -> None:
        bundle = build_extract(
            BYPASS / "inventory",
            seeds_path=BYPASS_SEEDS,
            allow_digest_mismatch=True,
        )
        self.assertEqual(bundle["structural_boundary_verdict"], VERDICT_BYPASSED)
        self.assertEqual(bundle["overall_boundary_verdict"], VERDICT_INSUFFICIENT)

    def test_cross_family_draw_not_supported(self) -> None:
        bundle = build_extract(
            CROSS / "inventory",
            seeds_path=CROSS_SEEDS,
            allow_digest_mismatch=True,
        )
        draw_verdict = bundle["family_verdicts"]["draw"]["verdict"]
        self.assertNotEqual(draw_verdict, VERDICT_SUPPORTED)
        self.assertEqual(bundle["overall_boundary_verdict"], VERDICT_INSUFFICIENT)

    def test_cut_rejects_external(self) -> None:
        graph = load_call_graph(GRAPH_MINI / "inventory")
        with self.assertRaises(Exception):
            resolve_cut_symbols(
                graph.functions,
                {"draw": ["ext:_glDrawElements"]},
                "gfx",
            )


if __name__ == "__main__":
    unittest.main()
