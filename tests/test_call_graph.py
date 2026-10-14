import json
import tempfile
import unittest
from pathlib import Path

from reverse_engineering.call_graph import (
    CONFIDENCE_THUNK,
    ENDPOINT_STATUS_DIRECT_BYPASS,
    ENDPOINT_STATUS_INDETERMINATE,
    build_audit_report,
    endpoint_status_from_sets,
    load_call_graph,
    verify_inventory_digest,
)
from reverse_engineering.gfx_subgraph import reachability_views

FIXTURE_MINI = Path(__file__).resolve().parent / "re_fixtures" / "export_mini"
GRAPH_MINI = Path(__file__).resolve().parent / "re_fixtures" / "graph_mini"
GRAPH_THUNK = Path(__file__).resolve().parent / "re_fixtures" / "graph_thunk_only"
DRAW = Path(__file__).resolve().parents[1] / "analysis" / "draw-callers.json"


class CallGraphTests(unittest.TestCase):
    def test_load_direct_excludes_thunk(self) -> None:
        graph = load_call_graph(FIXTURE_MINI / "inventory")
        self.assertNotIn("0x0000000000002000", graph.direct_adj)
        self.assertIn("0x0000000000002000", graph.resolved_adj)

    def test_unresolved_frontier(self) -> None:
        graph = load_call_graph(FIXTURE_MINI / "inventory")
        frontiers = graph.reachable_frontiers("0x0000000000002000", frozenset())
        self.assertEqual(len(frontiers), 1)

    def test_digest_missing_not_match(self) -> None:
        info = verify_inventory_digest(FIXTURE_MINI / "inventory", None)
        self.assertFalse(info["digest_match"])
        self.assertTrue(info["digest_recorded_missing"])

    def test_audit_report_schema(self) -> None:
        report = build_audit_report(FIXTURE_MINI / "inventory", DRAW, None)
        self.assertIn("gfx_draw_reconcile", report)

    def test_mixed_path_direct_bypass_status(self) -> None:
        graph = load_call_graph(GRAPH_MINI / "inventory")
        root = "0x0000000000005000"
        endpoint = "ext:_glDrawElements"
        cut = frozenset({"0x0000000000006010"})
        views = reachability_views(graph, root, cut)
        status = endpoint_status_from_sets(
            views.before, views.after_resolved, views.after_direct, endpoint
        )
        self.assertEqual(status, ENDPOINT_STATUS_DIRECT_BYPASS)

    def test_thunk_only_indeterminate(self) -> None:
        graph = load_call_graph(GRAPH_THUNK / "inventory")
        root = "0x0000000000005000"
        endpoint = "ext:_glDrawElements"
        cut = frozenset({"0x0000000000006010"})
        views = reachability_views(graph, root, cut)
        status = endpoint_status_from_sets(
            views.before, views.after_resolved, views.after_direct, endpoint
        )
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


if __name__ == "__main__":
    unittest.main()
