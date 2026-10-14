import unittest
from pathlib import Path

from reverse_engineering.call_graph import (
    CONFIDENCE_THUNK,
    build_audit_report,
    load_call_graph,
)
from reverse_engineering.gfx_subgraph import (
    ENDPOINT_STATUS_DIRECT_BYPASS,
    ENDPOINT_STATUS_INDETERMINATE,
    classify_endpoint_status,
)

FIXTURE_MINI = Path(__file__).resolve().parent / "re_fixtures" / "export_mini"
GRAPH_MINI = Path(__file__).resolve().parent / "re_fixtures" / "graph_mini"
DRAW = Path(__file__).resolve().parents[1] / "analysis" / "draw-callers.json"


class CallGraphTests(unittest.TestCase):
    def test_load_direct_excludes_thunk(self) -> None:
        graph = load_call_graph(FIXTURE_MINI / "inventory")
        self.assertNotIn("0x0000000000002000", graph.direct_adj)
        self.assertIn("0x0000000000002000", graph.resolved_adj)
        callees = [c for c, _ in graph.resolved_adj["0x0000000000002000"]]
        self.assertIn("0x0000000000003000", callees)

    def test_unresolved_frontier(self) -> None:
        graph = load_call_graph(FIXTURE_MINI / "inventory")
        frontiers = graph.reachable_frontiers("0x0000000000002000", frozenset())
        self.assertEqual(len(frontiers), 1)
        self.assertEqual(frontiers[0]["kind"], "unresolved_indirect")

    def test_thunk_edge_confidence(self) -> None:
        graph = load_call_graph(FIXTURE_MINI / "inventory")
        for _callee, meta in graph.resolved_adj["0x0000000000002000"]:
            if meta["resolution"] == "thunk":
                self.assertEqual(meta["confidence"], CONFIDENCE_THUNK)

    def test_audit_report_schema(self) -> None:
        report = build_audit_report(FIXTURE_MINI / "inventory", DRAW, None)
        self.assertIn("gfx_draw_reconcile", report)
        self.assertIn("resolution_counts", report)

    def test_mixed_path_direct_bypass_status(self) -> None:
        graph = load_call_graph(GRAPH_MINI / "inventory")
        root = "0x0000000000005000"
        endpoint = "ext:_glDrawElements"
        cut = frozenset({"0x0000000000006010"})
        status = classify_endpoint_status(graph, root, endpoint, cut)
        self.assertEqual(status, ENDPOINT_STATUS_DIRECT_BYPASS)

    def test_thunk_only_indeterminate(self) -> None:
        graph = load_call_graph(GRAPH_MINI / "inventory")
        # Only thunk path if we remove direct edge from renderer to direct_helper
        graph.direct_adj["0x0000000000005000"] = [
            e for e in graph.direct_adj.get("0x0000000000005000", []) if e[0] != "0x0000000000007000"
        ]
        root = "0x0000000000005000"
        endpoint = "ext:_glDrawElements"
        cut = frozenset({"0x0000000000006010"})
        status = classify_endpoint_status(graph, root, endpoint, cut)
        self.assertEqual(status, ENDPOINT_STATUS_INDETERMINATE)

    def test_unreachable_gl_via_unresolved(self) -> None:
        graph = load_call_graph(GRAPH_MINI / "inventory")
        ok = graph.reachable(
            "0x0000000000009000",
            "ext:_glDrawElements",
            use_direct=False,
            cut_rvas=frozenset(),
        )
        self.assertFalse(ok)
        frontiers = graph.reachable_frontiers("0x0000000000009000", frozenset())
        self.assertEqual(len(frontiers), 1)


if __name__ == "__main__":
    unittest.main()
