"""Load inventory call edges into direct_known and resolved graph views."""

from __future__ import annotations

import json
import re
from collections import defaultdict, deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

from reverse_engineering.census import OPENGL_RE
from reverse_engineering.manifest import inventory_bundle_digest
from reverse_engineering.reconcile_gfx import load_functions_by_rva, reconcile_draw_sites

DIRECT_RESOLUTIONS = frozenset({"direct_internal", "direct_external", "external_via_stub"})
CGL_RE = re.compile(r"^_?CGL[A-Z]")

CONFIDENCE_DIRECT = "direct_established"
CONFIDENCE_THUNK = "collapsed_thunk"
CONFIDENCE_UNRESOLVED = "unresolved_dispatch"
CONFIDENCE_RESOLVED_OTHER = "resolved_non_direct"

ENDPOINT_STATUS_NOT_OBSERVED = "not_observed_before"
ENDPOINT_STATUS_DIRECT_BYPASS = "direct_supported_bypass_candidate"
ENDPOINT_STATUS_INDETERMINATE = "indeterminate_after_cut"
ENDPOINT_STATUS_INTERCEPTED = "intercepted_in_recovered_graph"


@dataclass
class BfsResult:
    visited: set[str]
    prev: dict[str, tuple[str, dict[str, Any]]]


def external_node(symbol: str) -> str:
    return f"ext:{symbol}"


def is_external_node(node: str) -> bool:
    return node.startswith("ext:")


def edge_confidence(resolution: str, in_direct_known: bool) -> str:
    if in_direct_known:
        return CONFIDENCE_DIRECT
    if resolution == "thunk":
        return CONFIDENCE_THUNK
    if resolution in ("unresolved_indirect", "unresolved_other"):
        return CONFIDENCE_UNRESOLVED
    return CONFIDENCE_RESOLVED_OTHER


@dataclass
class CallGraph:
    functions: dict[str, dict[str, Any]]
    edges_raw: list[dict[str, Any]]
    resolved_adj: dict[str, list[tuple[str, dict[str, Any]]]]
    direct_adj: dict[str, list[tuple[str, dict[str, Any]]]]
    frontier_by_caller: dict[str, list[dict[str, Any]]]
    edge_stats: dict[str, int] = field(default_factory=dict)

    def neighbors_resolved(self, node: str, cut_rvas: frozenset[str] | None = None) -> Iterable[str]:
        cut = cut_rvas or frozenset()
        if node in cut:
            return ()
        for callee, _meta in self.resolved_adj.get(node, []):
            if callee not in cut:
                yield callee

    def neighbors_direct(self, node: str, cut_rvas: frozenset[str] | None = None) -> Iterable[str]:
        cut = cut_rvas or frozenset()
        if node in cut:
            return ()
        for callee, _meta in self.direct_adj.get(node, []):
            if callee not in cut:
                yield callee

    def reachable(self, start: str, goal: str, use_direct: bool, cut_rvas: frozenset[str]) -> bool:
        if start in cut_rvas or goal in cut_rvas:
            if start == goal:
                return False
        adj = self.neighbors_direct if use_direct else self.neighbors_resolved
        seen = {start}
        queue: deque[str] = deque([start])
        while queue:
            node = queue.popleft()
            for nxt in adj(node, cut_rvas):
                if nxt == goal:
                    return True
                if nxt not in seen and nxt not in cut_rvas:
                    seen.add(nxt)
                    queue.append(nxt)
        return False

    def shortest_path(
        self,
        start: str,
        goal: str,
        use_direct: bool,
        cut_rvas: frozenset[str],
        max_depth: int = 64,
    ) -> list[dict[str, Any]] | None:
        adj_map = self.direct_adj if use_direct else self.resolved_adj
        cut = cut_rvas
        prev: dict[str, tuple[str, dict[str, Any]]] = {}
        seen = {start}
        queue: deque[str] = deque([start])
        depth = {start: 0}
        while queue:
            node = queue.popleft()
            if depth[node] >= max_depth:
                continue
            for callee, meta in adj_map.get(node, []):
                if callee in cut:
                    continue
                if callee not in seen:
                    seen.add(callee)
                    prev[callee] = (node, meta)
                    depth[callee] = depth[node] + 1
                    if callee == goal:
                        return _reconstruct_path(start, goal, prev)
                    queue.append(callee)
        return None

    def reachable_frontiers(self, start: str, cut_rvas: frozenset[str]) -> list[dict[str, Any]]:
        """Unresolved call sites reachable from start without crossing cut internal RVAs."""
        out: list[dict[str, Any]] = []
        seen_sites: set[str] = set()
        seen_nodes = {start}
        queue: deque[str] = deque([start])
        while queue:
            node = queue.popleft()
            if node in cut_rvas:
                continue
            for frontier in self.frontier_by_caller.get(node, []):
                site_id = frontier["frontier_id"]
                if site_id not in seen_sites:
                    seen_sites.add(site_id)
                    out.append(frontier)
            for callee, _meta in self.resolved_adj.get(node, []):
                if callee not in cut_rvas and callee not in seen_nodes:
                    seen_nodes.add(callee)
                    queue.append(callee)
        return out

    def collect_external_endpoints(self, pattern: re.Pattern[str]) -> list[str]:
        nodes: set[str] = set()
        for _caller, edges in self.resolved_adj.items():
            for callee, _meta in edges:
                if is_external_node(callee):
                    sym = callee[4:]
                    if pattern.match(sym):
                        nodes.add(callee)
        return sorted(nodes)

    def opengl_endpoints(self) -> list[str]:
        return self.collect_external_endpoints(OPENGL_RE)

    def cgl_endpoints(self) -> list[str]:
        return self.collect_external_endpoints(CGL_RE)

    def bfs(
        self,
        start: str,
        use_direct: bool,
        cut_rvas: frozenset[str],
    ) -> BfsResult:
        adj_map = self.direct_adj if use_direct else self.resolved_adj
        cut = cut_rvas
        if start in cut:
            return BfsResult(set(), {})
        visited: set[str] = {start}
        prev: dict[str, tuple[str, dict[str, Any]]] = {}
        queue: deque[str] = deque([start])
        while queue:
            node = queue.popleft()
            for callee, meta in adj_map.get(node, []):
                if callee in cut:
                    continue
                if callee not in visited:
                    visited.add(callee)
                    prev[callee] = (node, meta)
                    queue.append(callee)
        return BfsResult(visited, prev)

    def path_from_pred(
        self,
        start: str,
        goal: str,
        prev: dict[str, tuple[str, dict[str, Any]]],
    ) -> list[dict[str, Any]] | None:
        if start == goal:
            return [{"node": start, "edge": None}]
        if goal not in prev:
            return None
        return _reconstruct_path(start, goal, prev)


def endpoint_status_from_sets(
    before: set[str],
    after_resolved: set[str],
    after_direct: set[str],
    endpoint: str,
) -> str:
    if endpoint not in before:
        return ENDPOINT_STATUS_NOT_OBSERVED
    if endpoint in after_direct:
        return ENDPOINT_STATUS_DIRECT_BYPASS
    if endpoint in after_resolved:
        return ENDPOINT_STATUS_INDETERMINATE
    return ENDPOINT_STATUS_INTERCEPTED


def _reconstruct_path(
    start: str,
    goal: str,
    prev: dict[str, tuple[str, dict[str, Any]]],
) -> list[dict[str, Any]]:
    path_nodes: list[str] = [goal]
    cur = goal
    while cur != start:
        parent, _meta = prev[cur]
        path_nodes.append(parent)
        cur = parent
    path_nodes.reverse()
    steps: list[dict[str, Any]] = []
    for i, node in enumerate(path_nodes):
        if i == 0:
            steps.append({"node": node, "edge": None})
        else:
            _parent, meta = prev[node]
            steps.append({"node": node, "edge": meta})
    return steps


def _resolved_target(edge: dict[str, Any]) -> str | None:
    res = edge.get("resolution")
    if res == "unresolved_indirect":
        return None
    internal = edge.get("callee_internal_rva")
    if internal:
        return internal
    sym = edge.get("ultimate_external_symbol") or edge.get("callee_external_symbol")
    if sym:
        return external_node(sym)
    if res == "unresolved_other":
        return None
    return None


def load_call_graph(inventory_dir: Path) -> CallGraph:
    functions = load_functions_by_rva(inventory_dir)
    edges_raw: list[dict[str, Any]] = []
    path = inventory_dir / "call_edges.jsonl"
    if path.is_file():
        for line in path.read_text().splitlines():
            if line.strip():
                edges_raw.append(json.loads(line))

    resolved_adj: dict[str, list[tuple[str, dict[str, Any]]]] = defaultdict(list)
    direct_adj: dict[str, list[tuple[str, dict[str, Any]]]] = defaultdict(list)
    frontier_by_caller: dict[str, list[dict[str, Any]]] = defaultdict(list)
    stats: dict[str, int] = defaultdict(int)

    for edge in edges_raw:
        res = edge.get("resolution") or ""
        stats[res] += 1
        caller = edge.get("caller_rva")
        if not caller:
            continue
        callsite = edge.get("callsite_rva") or ""
        target = _resolved_target(edge)
        in_direct = res in DIRECT_RESOLUTIONS and target is not None

        meta = {
            "callsite_rva": callsite,
            "resolution": res,
            "confidence": edge_confidence(res, in_direct),
            "direct_target_unknown": res == "thunk",
        }

        if res == "unresolved_indirect":
            fid = f"frontier:{callsite}"
            frontier_by_caller[caller].append(
                {
                    "frontier_id": fid,
                    "callsite_rva": callsite,
                    "caller_rva": caller,
                    "kind": "unresolved_indirect",
                }
            )
            continue

        if target is None:
            if res == "unresolved_other":
                sym = edge.get("callee_external_symbol")
                if sym:
                    target = external_node(sym)
                    meta["uncertain_target"] = True
                else:
                    fid = f"frontier:{callsite}"
                    frontier_by_caller[caller].append(
                        {
                            "frontier_id": fid,
                            "callsite_rva": callsite,
                            "caller_rva": caller,
                            "kind": "unresolved_other",
                        }
                    )
                    continue
            else:
                continue

        resolved_adj[caller].append((target, meta))
        if in_direct:
            direct_adj[caller].append((target, meta))

    return CallGraph(
        functions=functions,
        edges_raw=edges_raw,
        resolved_adj=dict(resolved_adj),
        direct_adj=dict(direct_adj),
        frontier_by_caller=dict(frontier_by_caller),
        edge_stats=dict(stats),
    )


def ancestors_of(
    graph: CallGraph,
    goals: Iterable[str],
    cut_rvas: frozenset[str] | None = None,
) -> set[str]:
    """Reverse BFS from goal nodes along resolved edges."""
    cut = cut_rvas or frozenset()
    reverse: dict[str, list[str]] = defaultdict(list)
    for caller, edges in graph.resolved_adj.items():
        if caller in cut:
            continue
        for callee, _meta in edges:
            if callee not in cut:
                reverse[callee].append(caller)

    seen: set[str] = set()
    queue: deque[str] = deque()
    for g in goals:
        if g not in cut:
            seen.add(g)
            queue.append(g)
    while queue:
        node = queue.popleft()
        for pred in reverse.get(node, []):
            if pred not in seen and pred not in cut:
                seen.add(pred)
                queue.append(pred)
    return {n for n in seen if not is_external_node(n)}


def descendants_of(
    graph: CallGraph,
    roots: Iterable[str],
    cut_rvas: frozenset[str] | None = None,
) -> set[str]:
    cut = cut_rvas or frozenset()
    seen: set[str] = set()
    queue: deque[str] = deque()
    for r in roots:
        if r not in cut:
            seen.add(r)
            queue.append(r)
    while queue:
        node = queue.popleft()
        for nxt in graph.neighbors_resolved(node, cut):
            if not is_external_node(nxt) and nxt not in seen:
                seen.add(nxt)
                queue.append(nxt)
    return seen


def verify_inventory_digest(inventory_dir: Path, provenance_path: Path | None) -> dict[str, Any]:
    computed = inventory_bundle_digest(inventory_dir)
    recorded = None
    if provenance_path and provenance_path.is_file():
        prov = json.loads(provenance_path.read_text())
        recorded = prov.get("inventory_bundle_digest")
    return {
        "inventory_bundle_digest_computed": computed,
        "inventory_bundle_digest_recorded": recorded,
        "digest_recorded_missing": recorded is None,
        "digest_match": recorded is not None and recorded == computed,
    }


def build_audit_report(
    inventory_dir: Path,
    draw_callers_path: Path,
    provenance_path: Path | None = None,
) -> dict[str, Any]:
    graph = load_call_graph(inventory_dir)
    digest_info = verify_inventory_digest(inventory_dir, provenance_path)
    draw = json.loads(draw_callers_path.read_text())
    reconcile = reconcile_draw_sites(draw, inventory_dir)

    thunk_count = graph.edge_stats.get("thunk", 0)
    unresolved_indirect = graph.edge_stats.get("unresolved_indirect", 0)

    return {
        "schema": 1,
        "edge_row_count": len(graph.edges_raw),
        "resolution_counts": graph.edge_stats,
        "direct_target_unknown_edges": thunk_count,
        "unresolved_indirect_call_sites": unresolved_indirect,
        "inventory_provenance": digest_info,
        "gfx_draw_reconcile": reconcile,
        "completeness_note": (
            "Static recovered call graph only; virtual calls, function pointers, "
            "and tail jumps may be absent. Thunk rows collapse intermediate targets."
        ),
    }
