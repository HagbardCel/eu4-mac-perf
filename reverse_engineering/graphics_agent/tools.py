"""EvidenceService — shared tool implementation for runner and MCP."""

from __future__ import annotations

import json
import sqlite3
import threading
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from reverse_engineering import index_db
from reverse_engineering.call_graph import (
    CallGraph,
    ancestors_of,
    descendants_of,
    external_node,
    is_external_node,
    load_call_graph,
    _resolved_target,
)
from reverse_engineering.census import OPENGL_RE
from reverse_engineering.graphics_agent import budgets
from reverse_engineering.graphics_agent.architecture_kb import (
    build_architecture_map_snapshot,
    build_backlog_snapshot,
)
from reverse_engineering.paths import ROOT
from reverse_engineering.graphics_agent.evidence_ids import call_edge_id, function_id
from reverse_engineering.graphics_agent.ghidra_queries import run_query_evidence
from reverse_engineering.graphics_agent.provenance_guard import assert_corpus_consistency
from reverse_engineering.graphics_agent.research_store import (
    bump_revision,
    enqueue_verification,
    known_evidence_ids,
    log_event,
    register_evidence,
    research_revision,
    upsert_claim,
)
from reverse_engineering.graphics_agent.validation import (
    REVIEW_EVIDENCE_VALIDATED,
    REVIEW_PROPOSED,
    validate_claim_evidence_refs,
)
from reverse_engineering.intervals import format_rva, parse_rva
from reverse_engineering.manifest import inventory_bundle_digest
from reverse_engineering.paths import ReError, binary_sha256, local_binary_root, resolve_export_dir

MAX_TOOL_RESPONSE_BYTES = 96_000


def cap_tool_payload(payload: dict[str, Any], max_bytes: int = MAX_TOOL_RESPONSE_BYTES) -> dict[str, Any]:
    raw = json.dumps(payload, default=str)
    if len(raw.encode("utf-8")) <= max_bytes:
        return payload
    return {
        "status": "truncated",
        "status_detail": f"tool response exceeded {max_bytes} bytes",
        "preview": raw[: max_bytes // 2],
    }


@dataclass
class EvidenceService:
    investigation_id: str
    baseline_revision: str
    export_dir: Path
    research_conn: sqlite3.Connection
    limits: budgets.BudgetLimits = field(default_factory=budgets.BudgetLimits)
    _graph: CallGraph | None = None
    _index_conn: sqlite3.Connection | None = None
    _db_lock: threading.Lock = field(default_factory=threading.Lock, repr=False, compare=False)

    @classmethod
    def open(
        cls,
        baseline_revision: str,
        investigation_id: str,
        config: dict[str, Any] | None = None,
    ) -> EvidenceService:
        digest = binary_sha256()
        binary_root = local_binary_root(digest)
        exp = resolve_export_dir(binary_root, baseline_revision)
        agent_dir = exp / "agent"
        from reverse_engineering.graphics_agent.research_store import connect, ensure_investigation

        inv_digest = inventory_bundle_digest(exp / "inventory")
        assert_corpus_consistency(exp, digest)
        conn = connect(agent_dir / "research.sqlite")
        ensure_investigation(conn, investigation_id, digest, inv_digest, baseline_revision)
        limits = budgets.limits_from_config(config or {})
        return cls(investigation_id, baseline_revision, exp, conn, limits)

    def _register_evidence(self, evidence_id: str, kind: str, artifact: dict[str, Any]) -> None:
        with self._db_lock:
            register_evidence(self.research_conn, evidence_id, kind, artifact)

    def _tool_budget(self) -> None:
        with self._db_lock:
            budgets.check_and_increment(
                self.research_conn,
                self.investigation_id,
                "tool_calls",
                self.limits.max_tool_calls,
            )

    def inventory_dir(self) -> Path:
        return self.export_dir / "inventory"

    def agent_dir(self) -> Path:
        return self.export_dir / "agent"

    def cache_dir(self) -> Path:
        p = self.agent_dir() / "cache" / "ghidra"
        p.mkdir(parents=True, exist_ok=True)
        return p

    def graph(self) -> CallGraph:
        if self._graph is None:
            self._graph = load_call_graph(self.inventory_dir())
        return self._graph

    def index(self) -> sqlite3.Connection:
        if self._index_conn is None:
            db = self.export_dir / "index.sqlite"
            if not db.is_file():
                raise ReError(f"missing index at {db}")
            self._index_conn = index_db.connect(db)
        return self._index_conn

    def inventory_digest(self) -> str:
        return inventory_bundle_digest(self.inventory_dir())

    def search_functions(
        self,
        query: str = "",
        namespace: str | None = None,
        limit: int = 50,
    ) -> dict[str, Any]:
        self._tool_budget()
        conn = self.index()
        rows = index_db.search_symbol(conn, query or "Gfx", limit=limit)
        out = []
        for row in rows:
            d = dict(row)
            if namespace and (d.get("namespace") or "").find(namespace) < 0:
                continue
            out.append(d)
        return {"functions": out, "truncated": len(out) >= limit}

    def get_function_context(self, rva: str, include_pseudocode: bool = False) -> dict[str, Any]:
        self._tool_budget()
        rva = format_rva(parse_rva(rva))
        conn = self.index()
        row = index_db.function_row(conn, rva)
        if row is None:
            return {"error": "not_found", "rva": rva}
        callers = [dict(r) for r in list(index_db.callers(conn, rva))[:15]]
        callees = [dict(r) for r in list(index_db.callees(conn, rva))[:15]]
        digest = self.inventory_digest()
        dossier = {
            "function": dict(row),
            "callers": {"sample": callers, "sample_truncated": len(callers) >= 15},
            "callees": {"sample": callees, "sample_truncated": len(callees) >= 15},
            "evidence_ids": [function_id(digest, rva)],
            "decompilation": {"status": "not_loaded"},
        }
        if include_pseudocode:
            pseudo = conn.execute("SELECT body FROM pseudocode WHERE entry_rva = ?", (rva,)).fetchone()
            if pseudo:
                dossier["decompilation"] = {"status": "loaded", "body": pseudo["body"][:8000]}
            else:
                dossier["decompilation"] = {"status": "not_in_index"}
        fid = function_id(digest, rva)
        self._register_evidence(fid, "function", {"entry_rva": rva, "function": dict(row)})
        return dossier

    def get_subgraph(self, seeds: list[str], direction: str = "forward", node_budget: int = 250) -> dict[str, Any]:
        self._tool_budget()
        node_budget = min(node_budget, self.limits.max_graph_nodes)
        g = self.graph()
        start_nodes = [self._resolve_path_endpoint(g, s) for s in seeds]
        visited: set[str] = set()
        edges_out: list[dict[str, Any]] = []
        queue: deque[str] = deque(start_nodes)
        reverse_adj: dict[str, list[tuple[str, dict[str, Any]]]] = {}
        if direction == "reverse":
            for caller, edges in g.resolved_adj.items():
                for callee, meta in edges:
                    reverse_adj.setdefault(callee, []).append((caller, meta))
        while queue and len(visited) < node_budget:
            node = queue.popleft()
            if node in visited:
                continue
            visited.add(node)
            edge_list = reverse_adj.get(node, []) if direction == "reverse" else g.resolved_adj.get(node, [])
            for nxt, meta in edge_list:
                edges_out.append(
                    {"from": node, "to": nxt, "meta": meta} if direction != "reverse" else {"from": nxt, "to": node, "meta": meta}
                )
                if nxt not in visited and not is_external_node(nxt):
                    queue.append(nxt)
        frontiers = []
        for s in start_nodes:
            frontiers.extend(g.reachable_frontiers(s, frozenset())[:20])
        return {
            "nodes": sorted(visited),
            "edges": edges_out[: node_budget * 2],
            "unresolved_frontiers": frontiers[:50],
            "truncated": len(visited) >= node_budget,
        }

    def _resolve_path_endpoint(self, g: CallGraph, endpoint: str) -> str:
        raw = endpoint.strip()
        if raw.startswith("ext:"):
            return raw
        if not raw.startswith("0x"):
            needle = raw.lstrip("_")
            for rva, row in g.functions.items():
                name = (row.get("name_mangled") or row.get("name") or "") or ""
                if name == raw or name.endswith(needle) or needle in name:
                    return rva
            sym = raw if raw.startswith("_") else f"_{raw}"
            node = external_node(sym)
            if any(callee == node for edges in g.resolved_adj.values() for callee, _ in edges):
                return node
            for ext in g.collect_external_endpoints(OPENGL_RE):
                if ext == node or ext.endswith(raw) or ext.endswith(sym):
                    return ext
            return node
        return format_rva(parse_rva(raw))

    def _edge_record_for_hop(
        self, g: CallGraph, parent: str, child: str, edge_meta: dict[str, Any]
    ) -> tuple[dict[str, Any], int] | None:
        callsite = edge_meta.get("callsite_rva") or ""
        for idx, row in enumerate(g.edges_raw):
            if row.get("callsite_rva") != callsite:
                continue
            if row.get("caller_rva") != parent:
                continue
            if _resolved_target(row) == child:
                return row, idx
        return None

    def find_paths(
        self,
        source_rva: str,
        target_rva: str,
        max_depth: int = 64,
    ) -> dict[str, Any]:
        self._tool_budget()
        g = self.graph()
        src = self._resolve_path_endpoint(g, source_rva)
        tgt = self._resolve_path_endpoint(g, target_rva)
        path = g.shortest_path(src, tgt, False, frozenset(), max_depth=max_depth)
        witness = path or []
        digest = self.inventory_digest()
        evidence = []
        prev_node = None
        for step in witness:
            node = step.get("node")
            edge = step.get("edge")
            if edge and prev_node is not None and node is not None:
                matched = self._edge_record_for_hop(g, prev_node, node, edge)
                if matched:
                    row, row_idx = matched
                    callee_key = (
                        row.get("callee_internal_rva")
                        or row.get("ultimate_external_symbol")
                        or row.get("callee_external_symbol")
                        or ""
                    )
                    cid = call_edge_id(
                        digest,
                        row.get("callsite_rva", ""),
                        row.get("caller_rva"),
                        callee_key,
                        edge_row_id=row_idx,
                    )
                    self._register_evidence(
                        cid,
                        "call-edge",
                        {
                            "callsite_rva": row.get("callsite_rva"),
                            "caller_rva": row.get("caller_rva"),
                            "callee_internal_rva": row.get("callee_internal_rva"),
                            "callee_external_symbol": row.get("callee_external_symbol"),
                            "ultimate_external_symbol": row.get("ultimate_external_symbol"),
                            "resolution": row.get("resolution"),
                            "inventory_bundle_digest": digest,
                        },
                    )
                    evidence.append(cid)
            prev_node = node
        return {"path": witness, "found": path is not None, "evidence_ids": evidence}

    def get_api_endpoints(
        self,
        api_family: str = "opengl",
        symbol_filter: str | None = None,
        limit: int = 200,
    ) -> dict[str, Any]:
        self._tool_budget()
        g = self.graph()
        if api_family == "opengl":
            endpoints = g.opengl_endpoints()
        elif api_family == "cgl":
            endpoints = g.cgl_endpoints()
        elif api_family == "all_external":
            endpoints = sorted(
                {
                    callee
                    for edges in g.resolved_adj.values()
                    for callee, _ in edges
                    if is_external_node(callee)
                }
            )
        else:
            return {"error": "unknown_api_family", "api_family": api_family}
        if symbol_filter:
            sf = symbol_filter.lower()
            endpoints = [e for e in endpoints if sf in e.lower()]
        endpoints = endpoints[:limit]
        samples = []
        for ep in endpoints[:30]:
            callers = [c for c, adj in g.resolved_adj.items() if any(x[0] == ep for x in adj)]
            samples.append({"endpoint": ep, "caller_sample": callers[:5]})
        return {"endpoints": endpoints, "caller_samples": samples, "truncated": len(endpoints) >= limit}

    def find_related_functions(self, rva: str, limit: int = 25) -> dict[str, Any]:
        self._tool_budget()
        rva = format_rva(parse_rva(rva))
        g = self.graph()
        related: set[str] = set()
        for caller, meta in g.resolved_adj.items():
            for callee, _ in g.resolved_adj.get(caller, []):
                if callee == rva:
                    related.add(caller)
        for callee, _ in g.resolved_adj.get(rva, []):
            if not is_external_node(callee):
                related.add(callee)
        row = g.functions.get(rva, {})
        ns = row.get("namespace") or ""
        if ns:
            for other, orow in g.functions.items():
                if (orow.get("namespace") or "") == ns and other != rva:
                    related.add(other)
        return {"related": sorted(related)[:limit]}

    def get_architecture_overview(self) -> dict[str, Any]:
        self._tool_budget()
        ctx_path = self.agent_dir() / "graphics-agent-context.json"
        briefing = json.loads(ctx_path.read_text()) if ctx_path.is_file() else {}
        rev = research_revision(self.research_conn)
        digests = {"inventory_bundle_digest": self.inventory_digest()}
        snap = build_architecture_map_snapshot(
            self.research_conn, self.investigation_id, rev, digests
        )
        return {"briefing_summary": briefing.get("known_architecture"), "research_snapshot": snap}

    def decompile_functions(self, rvas: list[str], timeout_seconds: int = 120) -> dict[str, Any]:
        self._tool_budget()
        if len(rvas) > 8:
            rvas = rvas[:8]
        from reverse_engineering.graphics_agent.ghidra_queries import run_decompile_batch

        digest = binary_sha256()
        binary_root = local_binary_root(digest)
        results = run_decompile_batch(
            self.research_conn,
            self.investigation_id,
            self.inventory_digest(),
            self.export_dir,
            binary_root,
            self.agent_dir() / "cache" / "decompiled",
            rvas,
            timeout_seconds,
            limits=self.limits,
        )
        return {"results": results}

    def read_constant_data(self, rva: str, size: int = 64) -> dict[str, Any]:
        self._tool_budget()
        if size > 4096:
            size = 4096
        return {
            "function_rva": format_rva(parse_rva(rva)),
            "query": "constant_data",
            "status": "unsupported",
            "status_detail": "constant_data_extraction_not_implemented_in_pilot",
            "facts": [],
            "interpretations": [],
        }

    def get_pseudocode(self, rva: str) -> dict[str, Any]:
        self._tool_budget()
        rva = format_rva(parse_rva(rva))
        conn = self.index()
        row = conn.execute("SELECT body FROM pseudocode WHERE entry_rva = ?", (rva,)).fetchone()
        if row:
            return {"status": "from_corpus_index", "body": row["body"][: self.limits.max_output_bytes]}
        cache = self.agent_dir() / "cache" / "decompiled" / f"{rva}.json"
        if cache.is_file():
            return json.loads(cache.read_text())
        return {"status": "not_available"}

    def get_semantic_query(self, rva: str, operation: str, max_insns: int = 128) -> dict[str, Any]:
        self._tool_budget()
        digest = binary_sha256()
        binary_root = local_binary_root(digest)
        with self._db_lock:
            rows = run_query_evidence(
                self.research_conn,
                self.investigation_id,
                self.inventory_digest(),
                self.export_dir,
                binary_root,
                self.cache_dir(),
                operation,
                [rva],
                max_insns=max_insns,
                limits=self.limits,
            )
        if not rows:
            return {"status": "unresolved", "status_detail": "empty_result"}
        row = rows[0]
        if isinstance(row, dict) and row.get("evidence_id"):
            return cap_tool_payload(row)
        return cap_tool_payload(row if isinstance(row, dict) else {"rows": row})

    def get_disassembly(self, rva: str, max_insns: int = 128) -> dict[str, Any]:
        return self.get_semantic_query(rva, "disassembly", max_insns)

    def get_references(self, rva: str) -> dict[str, Any]:
        return self.get_semantic_query(rva, "references")

    def get_function_signature(self, rva: str) -> dict[str, Any]:
        return self.get_semantic_query(rva, "signature")

    def get_type_layout(self, rva: str) -> dict[str, Any]:
        return self.get_semantic_query(rva, "type_layout")

    def get_data_accesses(self, rva: str, max_insns: int = 256) -> dict[str, Any]:
        return self.get_semantic_query(rva, "data_accesses", max_insns)

    def get_indirect_dispatch(self, rva: str, max_insns: int = 256) -> dict[str, Any]:
        return self.get_semantic_query(rva, "indirect_dispatch", max_insns)

    def get_runtime_evidence(self, subsystem: str | None = None) -> dict[str, Any]:
        self._tool_budget()
        from reverse_engineering.graphics_agent.context_builder import _runtime_evidence_index

        items = _runtime_evidence_index()
        if subsystem:
            items = [i for i in items if subsystem in json.dumps(i)]
        return {"items": items, "disclaimer": "fixture-specific measurements"}

    def get_measurement_provenance(self, manifest_path: str) -> dict[str, Any]:
        self._tool_budget()
        if ".." in manifest_path or manifest_path.startswith("/"):
            return {"error": "path_not_allowed"}
        allowed_roots = (
            ROOT / "analysis",
            ROOT / "results",
        )
        path = (ROOT / manifest_path).resolve()
        if not any(path.is_relative_to(root.resolve()) for root in allowed_roots if root.exists()):
            return {"error": "path_not_allowed"}
        if not path.is_file():
            return {"error": "not_found"}
        return {"manifest": json.loads(path.read_text()), "note": "not_general_renderer_law"}

    def compare_static_runtime_evidence(self, rva: str, manifest_path: str | None = None) -> dict[str, Any]:
        self._tool_budget()
        return {
            "static": self.get_function_context(rva),
            "runtime": self.get_runtime_evidence(),
            "correlation_is_not_proof": True,
        }

    def get_research_state(self, area: str | None = None) -> dict[str, Any]:
        self._tool_budget()
        claims = self.research_conn.execute(
            "SELECT * FROM claims WHERE investigation_id = ?",
            (self.investigation_id,),
        ).fetchall()
        questions = self.research_conn.execute(
            "SELECT * FROM open_questions WHERE investigation_id = ?",
            (self.investigation_id,),
        ).fetchall()
        return {
            "claims": [dict(c) for c in claims],
            "open_questions": [dict(q) for q in questions if not area or q["domain"] == area],
            "revision": research_revision(self.research_conn),
        }

    def submit_findings(self, finding: dict[str, Any]) -> dict[str, Any]:
        self._tool_budget()
        known = known_evidence_ids(self.research_conn)
        errors = validate_claim_evidence_refs(finding, self.inventory_digest(), known)
        if errors:
            return {"ok": False, "errors": errors}
        refs = finding.get("evidence_ids") or []
        review_status = REVIEW_EVIDENCE_VALIDATED if refs else REVIEW_PROPOSED
        claim = {
            "claim_id": finding["claim_id"],
            "investigation_id": self.investigation_id,
            "statement": finding["statement"],
            "scientific_status": finding.get("scientific_status", "hypothesis"),
            "review_status": review_status,
            "evidence_ids": refs,
            "limitations": finding.get("limitations"),
        }
        with self._db_lock:
            upsert_claim(self.research_conn, claim)
        return {"ok": True, "claim_id": claim["claim_id"], "review_status": review_status}

    def propose_investigation(self, domain: str, question: str) -> dict[str, Any]:
        self._tool_budget()
        self.research_conn.execute(
            "INSERT INTO open_questions (investigation_id, domain, question, status) VALUES (?, ?, ?, 'open')",
            (self.investigation_id, domain, question),
        )
        self.research_conn.commit()
        bump_revision(self.research_conn)
        return {"ok": True}

    def get_unresolved_questions(self) -> dict[str, Any]:
        rows = self.research_conn.execute(
            "SELECT * FROM open_questions WHERE investigation_id = ? AND status = 'open'",
            (self.investigation_id,),
        ).fetchall()
        return {"questions": [dict(r) for r in rows]}

    def get_coverage_report(self) -> dict[str, Any]:
        rev = research_revision(self.research_conn)
        digests = {"inventory_bundle_digest": self.inventory_digest()}
        return build_backlog_snapshot(self.research_conn, self.investigation_id, rev, digests)

    def request_verification(self, claim_id: str) -> dict[str, Any]:
        enqueue_verification(self.research_conn, self.investigation_id, claim_id)
        log_event(self.research_conn, self.investigation_id, "verification_requested", {"claim_id": claim_id})
        return {"queued": True, "claim_id": claim_id}

    def export_snapshots(self) -> None:
        rev = research_revision(self.research_conn)
        digests = {"inventory_bundle_digest": self.inventory_digest()}
        backlog = build_backlog_snapshot(self.research_conn, self.investigation_id, rev, digests)
        arch = build_architecture_map_snapshot(self.research_conn, self.investigation_id, rev, digests)
        ad = self.agent_dir()
        (ad / "research-backlog.snapshot.json").write_text(json.dumps(backlog, indent=2) + "\n")
        (ad / "architecture-map.snapshot.json").write_text(json.dumps(arch, indent=2) + "\n")
