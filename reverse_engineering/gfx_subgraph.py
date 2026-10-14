"""Graphics subgraph extraction, dual cuts, and endpoint reachability classification."""

from __future__ import annotations

import json
import re
from collections import defaultdict
from pathlib import Path
from typing import Any

from reverse_engineering.call_graph import (
    CallGraph,
    ancestors_of,
    descendants_of,
    is_external_node,
    load_call_graph,
)
from reverse_engineering.census import GFX_RE, OPENGL_RE
from reverse_engineering.corpus import MANDATORY_ANCHOR_SYMBOLS
from reverse_engineering.paths import RE_DIR

SEEDS_PATH = RE_DIR / "graphics_seeds.json"

ENDPOINT_STATUS_NOT_OBSERVED = "not_observed_before"
ENDPOINT_STATUS_DIRECT_BYPASS = "direct_supported_bypass_candidate"
ENDPOINT_STATUS_INDETERMINATE = "indeterminate_after_cut"
ENDPOINT_STATUS_INTERCEPTED = "intercepted_in_recovered_graph"


class SeedResolutionError(Exception):
    pass


def load_seeds_config(path: Path | None = None) -> dict[str, Any]:
    p = path or SEEDS_PATH
    return json.loads(p.read_text())


def resolve_symbol_to_rva(
    functions: dict[str, dict[str, Any]],
    symbol: str,
    require_internal: bool = False,
) -> str:
    matches: list[str] = []
    for rva, row in functions.items():
        if row.get("name_mangled") == symbol:
            if require_internal and row.get("kind") not in ("internal", "thunk"):
                continue
            matches.append(rva)
    if len(matches) == 1:
        return matches[0]
    if len(matches) == 0:
        raise SeedResolutionError(f"no match for symbol {symbol}")
    raise SeedResolutionError(f"ambiguous symbol {symbol}: {matches}")


def resolve_cut_symbols(
    functions: dict[str, dict[str, Any]],
    cut_config: dict[str, list[str]],
    cut_name: str,
) -> dict[str, list[dict[str, Any]]]:
    """Resolve cut allowlist to internal RVAs only."""
    out: dict[str, list[dict[str, Any]]] = {}
    for family, symbols in cut_config.items():
        members: list[dict[str, Any]] = []
        for sym in symbols:
            if sym.startswith("ext:"):
                raise SeedResolutionError(f"{cut_name}: external symbol not allowed in cut: {sym}")
            rva = resolve_symbol_to_rva(functions, sym, require_internal=True)
            row = functions[rva]
            if row.get("kind") == "external":
                raise SeedResolutionError(f"{cut_name}: cut member {sym} is external")
            members.append({"symbol": sym, "entry_rva": rva, "family": family})
        out[family] = members
    return out


def cut_rva_set(cut_resolved: dict[str, list[dict[str, Any]]]) -> frozenset[str]:
    rvas: set[str] = set()
    for members in cut_resolved.values():
        for m in members:
            rvas.add(m["entry_rva"])
    return frozenset(rvas)


def endpoint_family(symbol: str) -> str:
    if CGL_PATTERN.match(symbol):
        return "presentation"
    if "Framebuffer" in symbol or "BindFramebuffer" in symbol:
        return "render_targets"
    if "Texture" in symbol or "Buffer" in symbol:
        return "buffers_textures"
    if "Shader" in symbol or "Program" in symbol:
        return "shaders"
    if "Draw" in symbol:
        return "draw"
    return "opengl_other"


CGL_PATTERN = re.compile(r"^_?CGL[A-Z]")


def classify_endpoint_status(
    graph: CallGraph,
    root: str,
    endpoint: str,
    cut: frozenset[str],
) -> str:
    before = graph.reachable(root, endpoint, use_direct=False, cut_rvas=frozenset())
    if not before:
        return ENDPOINT_STATUS_NOT_OBSERVED
    after_resolved = graph.reachable(root, endpoint, use_direct=False, cut_rvas=cut)
    after_direct = graph.reachable(root, endpoint, use_direct=True, cut_rvas=cut)
    if after_direct:
        return ENDPOINT_STATUS_DIRECT_BYPASS
    if after_resolved:
        return ENDPOINT_STATUS_INDETERMINATE
    return ENDPOINT_STATUS_INTERCEPTED


def is_graphics_function(row: dict[str, Any]) -> bool:
    text = " ".join(
        filter(None, [row.get("name_mangled"), row.get("name_demangled"), row.get("namespace")])
    )
    return bool(GFX_RE.search(text))


def build_extract(
    inventory_dir: Path,
    seeds_path: Path | None = None,
    allow_digest_mismatch: bool = False,
    provenance_path: Path | None = None,
) -> dict[str, Any]:
    from reverse_engineering.call_graph import verify_inventory_digest

    graph = load_call_graph(inventory_dir)
    seeds = load_seeds_config(seeds_path)
    digest_info = verify_inventory_digest(inventory_dir, provenance_path)

    seed_resolution: dict[str, Any] = {"mandatory": {}, "optional": {}, "errors": []}
    for sym in seeds.get("mandatory_identity", MANDATORY_ANCHOR_SYMBOLS):
        try:
            seed_resolution["mandatory"][sym] = {
                "entry_rva": resolve_symbol_to_rva(graph.functions, sym),
                "status": "resolved",
            }
        except SeedResolutionError as exc:
            seed_resolution["errors"].append(str(exc))
            raise SeedResolutionError(f"mandatory seed failed: {exc}") from exc

    renderer_roots: list[str] = []
    for sym in seeds.get("renderer_roots", []):
        try:
            renderer_roots.append(resolve_symbol_to_rva(graph.functions, sym))
        except SeedResolutionError as exc:
            seed_resolution["optional"][sym] = {"status": "missing", "error": str(exc)}

    for sym in seeds.get("renderer_sub_entries", []):
        try:
            rva = resolve_symbol_to_rva(graph.functions, sym)
            if rva not in renderer_roots:
                renderer_roots.append(rva)
        except SeedResolutionError as exc:
            seed_resolution["optional"][sym] = {"status": "missing", "error": str(exc)}

    gfx_cut_resolved = resolve_cut_symbols(
        graph.functions, seeds.get("gfx_abstraction_cut", {}), "gfx_abstraction_cut"
    )
    backend_cut_resolved = resolve_cut_symbols(
        graph.functions, seeds.get("backend_implementation_cut", {}), "backend_implementation_cut"
    )
    gfx_cut = cut_rva_set(gfx_cut_resolved)
    backend_cut = cut_rva_set(backend_cut_resolved)

    gl_endpoints = graph.opengl_endpoints()
    desc_r = descendants_of(graph, renderer_roots) if renderer_roots else set()
    anc_gl = ancestors_of(graph, gl_endpoints)
    s_core = desc_r & anc_gl

    missing_connections: list[dict[str, Any]] = []
    for sym, info in seed_resolution["mandatory"].items():
        rva = info["entry_rva"]
        if sym in seeds.get("renderer_roots", []) or sym in seeds.get("renderer_sub_entries", []):
            if rva not in desc_r and renderer_roots:
                missing_connections.append(
                    {"symbol": sym, "entry_rva": rva, "reason": "not_in_Desc(R)"}
                )

    cuts_analysis: dict[str, Any] = {}
    for cut_id, cut_set, cut_resolved in (
        ("gfx_abstraction", gfx_cut, gfx_cut_resolved),
        ("backend_implementation", backend_cut, backend_cut_resolved),
    ):
        endpoint_rows: list[dict[str, Any]] = []
        status_counts: dict[str, int] = defaultdict(int)
        for root in renderer_roots:
            for endpoint in gl_endpoints:
                sym = endpoint[4:]
                status = classify_endpoint_status(graph, root, endpoint, cut_set)
                status_counts[status] += 1
                witness = None
                if status != ENDPOINT_STATUS_NOT_OBSERVED:
                    use_direct = status == ENDPOINT_STATUS_DIRECT_BYPASS
                    witness = graph.shortest_path(
                        root, endpoint, use_direct=use_direct, cut_rvas=cut_set
                    )
                endpoint_rows.append(
                    {
                        "root_rva": root,
                        "endpoint": sym,
                        "endpoint_family": endpoint_family(sym),
                        "status": status,
                        "witness_path": witness,
                    }
                )

        frontiers_before: list[dict[str, Any]] = []
        frontiers_after: list[dict[str, Any]] = []
        for root in renderer_roots:
            frontiers_before.extend(graph.reachable_frontiers(root, frozenset()))
            frontiers_after.extend(graph.reachable_frontiers(root, cut_set))

        gfx_frontiers = [
            f
            for f in frontiers_before
            if is_graphics_function(graph.functions.get(f["caller_rva"], {}))
        ]

        cuts_analysis[cut_id] = {
            "cut_members": cut_resolved,
            "endpoint_coverage": endpoint_rows,
            "status_summary": dict(status_counts),
            "reachable_unresolved_calls_before": len(frontiers_before),
            "reachable_unresolved_calls_after": len(frontiers_after),
            "unresolved_graphics_candidates": len(gfx_frontiers),
            "unknown_target_risk": len(frontiers_after) > 0,
        }

    authoritative_boundary = (
        digest_info["digest_match"]
        and not seed_resolution["errors"]
        and not any(
            cuts_analysis[c]["unknown_target_risk"]
            for c in cuts_analysis
            if cuts_analysis[c]["status_summary"].get(ENDPOINT_STATUS_INDETERMINATE, 0) > 0
        )
    )
    boundary_conclusion = (
        "supported_by_recovered_graph"
        if authoritative_boundary and not missing_connections
        else "insufficient_evidence"
    )

    if not digest_info["digest_match"] and not allow_digest_mismatch:
        boundary_conclusion = "insufficient_evidence"

    priorities = _build_priorities(
        graph, gfx_cut_resolved, backend_cut_resolved, s_core, cuts_analysis, missing_connections
    )

    return {
        "schema": 1,
        "inventory_provenance": digest_info,
        "seed_resolution": seed_resolution,
        "structural": {
            "renderer_roots": renderer_roots,
            "desc_r_size": len(desc_r),
            "anc_gl_size": len(anc_gl),
            "s_core_size": len(s_core),
            "s_core_sample": sorted(s_core)[:50],
        },
        "cuts": cuts_analysis,
        "missing_connections": missing_connections,
        "boundary_conclusion": boundary_conclusion,
        "decompile_priorities": priorities,
        "completeness_note": (
            "Endpoint status uses dual reachability (resolved vs direct_known after cut). "
            "Unresolved indirect sites are frontiers only."
        ),
    }


def _build_priorities(
    graph: CallGraph,
    gfx_cut: dict[str, list[dict[str, Any]]],
    backend_cut: dict[str, list[dict[str, Any]]],
    s_core: set[str],
    cuts_analysis: dict[str, Any],
    missing_connections: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    scored: dict[str, dict[str, Any]] = {}

    def add(rva: str, reason: str, **extra: Any) -> None:
        if rva not in graph.functions:
            return
        if rva not in scored:
            scored[rva] = {
                "entry_rva": rva,
                "name_mangled": graph.functions[rva].get("name_mangled"),
                "reasons": [],
                "unresolved_questions": [],
            }
        if reason not in scored[rva]["reasons"]:
            scored[rva]["reasons"].append(reason)
        scored[rva].update(extra)

    for _family, members in gfx_cut.items():
        for m in members:
            add(m["entry_rva"], "gfx_abstraction_cut_member", priority_boost=10)

    for _family, members in backend_cut.items():
        for m in members:
            add(m["entry_rva"], "backend_implementation_cut_member", priority_boost=8)

    for rva in s_core:
        add(rva, "s_core_intersection", priority_boost=5)

    for cut_id, data in cuts_analysis.items():
        for row in data.get("endpoint_coverage", []):
            if row["status"] == ENDPOINT_STATUS_DIRECT_BYPASS:
                add(
                    row["root_rva"],
                    f"direct_supported_bypass_{cut_id}",
                    witness_path=row.get("witness_path"),
                )

    if missing_connections:
        for mc in missing_connections:
            add(
                mc["entry_rva"],
                "missing_connection",
                unresolved_questions=[mc.get("reason", "")],
            )

    out = list(scored.values())
    out.sort(
        key=lambda x: (
            -max(x.get("priority_boost", 0), len(x.get("reasons", []))),
            x["entry_rva"],
        )
    )
    return out[:200]
