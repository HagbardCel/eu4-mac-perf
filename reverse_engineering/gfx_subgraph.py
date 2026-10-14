"""Graphics subgraph extraction, per-family Gfx cuts, and boundary verdicts."""

from __future__ import annotations

import json
import re
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from reverse_engineering.call_graph import (
    ENDPOINT_STATUS_DIRECT_BYPASS,
    ENDPOINT_STATUS_INDETERMINATE,
    ENDPOINT_STATUS_INTERCEPTED,
    ENDPOINT_STATUS_NOT_OBSERVED,
    CallGraph,
    ancestors_of,
    descendants_of,
    endpoint_status_from_sets,
    is_external_node,
    load_call_graph,
    verify_inventory_digest,
)
from reverse_engineering.census import GFX_RE
from reverse_engineering.corpus import MANDATORY_ANCHOR_SYMBOLS
from reverse_engineering.paths import RE_DIR, ReError
from reverse_engineering.symbol_resolve import (
    SeedResolutionError,
    build_symbol_index,
    resolve_symbol_to_rva,
)

SEEDS_PATH = RE_DIR / "graphics_seeds.json"
BORDER_HOOK = RE_DIR.parent / "analysis" / "border-hook-site.json"
WITNESS_LIMIT = 5
UNASSESSED_DRAW_DIAGNOSTIC_LIMIT = 20

VERDICT_NOT_CHARACTERIZED = "not_characterized"
VERDICT_INSUFFICIENT = "insufficient_evidence"
VERDICT_BYPASSED = "boundary_bypassed"
VERDICT_SUPPORTED = "candidate_boundary_supported"

CGL_PATTERN = re.compile(r"^_?CGL[A-Z]")
REQUIRED_GFX_FAMILIES_FOR_FULL_SEAM = ("draw", "state", "buffers_textures", "render_targets", "presentation")

PINNED_MESH_RVA = "0x00000000014c7da4"
PINNED_BORDER_RVA = "0x00000000010cbb00"


def load_seeds_config(path: Path | None = None) -> dict[str, Any]:
    return json.loads((path or SEEDS_PATH).read_text())


def resolve_cut_symbols(
    functions: dict[str, dict[str, Any]],
    cut_config: dict[str, list[str]],
    cut_name: str,
    symbol_index: dict[str, list[str]] | None = None,
) -> dict[str, list[dict[str, Any]]]:
    out: dict[str, list[dict[str, Any]]] = {}
    for family, symbols in cut_config.items():
        members: list[dict[str, Any]] = []
        if not symbols:
            out[family] = members
            continue
        for sym in symbols:
            if sym.startswith("ext:"):
                raise SeedResolutionError(f"{cut_name}: external symbol not allowed in cut: {sym}")
            rva = resolve_symbol_to_rva(
                functions, sym, require_internal=True, index=symbol_index
            )
            if functions[rva].get("kind") == "external":
                raise SeedResolutionError(f"{cut_name}: cut member {sym} is external")
            members.append({"symbol": sym, "entry_rva": rva, "family": family})
        out[family] = members
    return out


def cut_sets_by_family(
    cut_resolved: dict[str, list[dict[str, Any]]],
) -> dict[str, frozenset[str]]:
    return {
        family: frozenset(m["entry_rva"] for m in members)
        for family, members in cut_resolved.items()
    }


def cut_union(cut_by_family: dict[str, frozenset[str]]) -> frozenset[str]:
    out: set[str] = set()
    for s in cut_by_family.values():
        out |= set(s)
    return frozenset(out)


def classify_endpoint_symbol(symbol: str, explicit: dict[str, str]) -> str:
    if symbol in explicit:
        return explicit[symbol]
    return "unclassified"


def explicit_family_classified(family: str, explicit: dict[str, str]) -> bool:
    return any(auth == family for auth in explicit.values())


def intercepted_endpoint_metrics(
    all_rows: list[dict[str, Any]],
    total_counts: dict[str, int],
) -> dict[str, Any]:
    symbols = sorted(
        {
            row["endpoint"]
            for row in all_rows
            if row.get("status") == ENDPOINT_STATUS_INTERCEPTED
        }
    )
    return {
        "intercepted_root_endpoint_pair_count": total_counts.get(
            ENDPOINT_STATUS_INTERCEPTED, 0
        ),
        "distinct_intercepted_endpoint_count": len(symbols),
        "distinct_intercepted_endpoint_symbols": symbols,
    }


def suggested_endpoint_family(symbol: str) -> str:
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
    return "unclassified"


def endpoint_class(node: str) -> str:
    sym = node[4:] if is_external_node(node) else node
    return "cgl" if CGL_PATTERN.match(sym) else "opengl"


def is_graphics_function(row: dict[str, Any]) -> bool:
    text = " ".join(
        filter(None, [row.get("name_mangled"), row.get("name_demangled"), row.get("namespace")])
    )
    return bool(GFX_RE.search(text))


@dataclass
class ReachabilityViews:
    before: set[str]
    after_resolved: set[str]
    after_direct: set[str]
    prev_before: dict
    prev_after_resolved: dict
    prev_after_direct: dict


def reachability_views(graph: CallGraph, root: str, cut: frozenset[str]) -> ReachabilityViews:
    b0 = graph.bfs(root, use_direct=False, cut_rvas=frozenset())
    br = graph.bfs(root, use_direct=False, cut_rvas=cut)
    bd = graph.bfs(root, use_direct=True, cut_rvas=cut)
    return ReachabilityViews(
        before=b0.visited,
        after_resolved=br.visited,
        after_direct=bd.visited,
        prev_before=b0.prev,
        prev_after_resolved=br.prev,
        prev_after_direct=bd.prev,
    )


def analyze_endpoints_for_cut(
    graph: CallGraph,
    root: str,
    cut: frozenset[str],
    endpoints: list[str],
    scope_family: str | None,
    explicit_class: dict[str, str],
    witness_limit: int = WITNESS_LIMIT,
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    views = reachability_views(graph, root, cut)
    rows: list[dict[str, Any]] = []
    counts: dict[str, int] = defaultdict(int)
    witness_budget: dict[str, int] = defaultdict(int)

    for endpoint in endpoints:
        sym = endpoint[4:]
        auth_family = classify_endpoint_symbol(sym, explicit_class)
        suggested = suggested_endpoint_family(sym)
        assessed = auth_family != "unclassified"
        in_scope = scope_family is None or auth_family == scope_family or (
            scope_family == "draw"
            and auth_family == "unclassified"
            and suggested == "draw"
        )
        if not in_scope:
            continue
        status = endpoint_status_from_sets(
            views.before, views.after_resolved, views.after_direct, endpoint
        )
        counts[status] += 1
        witness_before = None
        witness_after = None
        if status == ENDPOINT_STATUS_INTERCEPTED and witness_budget[status] < witness_limit:
            witness_before = graph.path_from_pred(root, endpoint, views.prev_before)
            witness_budget[status] += 1
        elif status == ENDPOINT_STATUS_DIRECT_BYPASS and witness_budget[status] < witness_limit:
            witness_after = graph.path_from_pred(root, endpoint, views.prev_after_direct)
            witness_budget[status] += 1
        elif status == ENDPOINT_STATUS_INDETERMINATE and witness_budget[status] < witness_limit:
            witness_after = graph.path_from_pred(root, endpoint, views.prev_after_resolved)
            witness_budget[status] += 1

        rows.append(
            {
                "root_rva": root,
                "endpoint": sym,
                "endpoint_class": endpoint_class(endpoint),
                "auth_family": auth_family,
                "suggested_family": suggested,
                "assessed": assessed,
                "status": status,
                "witness_before_cut": witness_before,
                "witness_after_cut": witness_after,
            }
        )
    return rows, dict(counts)


def family_verdict_from_counts(
    counts: dict[str, int],
    characterized: bool,
    unassessed_draw_candidates: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    if not characterized:
        return {"verdict": VERDICT_NOT_CHARACTERIZED}
    bypass = counts.get(ENDPOINT_STATUS_DIRECT_BYPASS, 0)
    intercepted = counts.get(ENDPOINT_STATUS_INTERCEPTED, 0)
    indeterminate = counts.get(ENDPOINT_STATUS_INDETERMINATE, 0)
    observed = sum(
        counts.get(s, 0)
        for s in (
            ENDPOINT_STATUS_DIRECT_BYPASS,
            ENDPOINT_STATUS_INDETERMINATE,
            ENDPOINT_STATUS_INTERCEPTED,
        )
    )
    base: dict[str, Any] = {
        "status_counts": counts,
        "intercepted_root_endpoint_pair_count": intercepted,
        "has_indeterminate_survivors": indeterminate > 0,
    }
    if bypass > 0:
        return {**base, "verdict": VERDICT_BYPASSED}
    if unassessed_draw_candidates:
        return {
            **base,
            "verdict": VERDICT_INSUFFICIENT,
            "note": (
                f"{len(unassessed_draw_candidates)} unassessed draw endpoint(s) "
                "observed in recovered graph"
            ),
        }
    if intercepted > 0 and indeterminate == 0:
        return {**base, "verdict": VERDICT_SUPPORTED}
    if intercepted > 0 and indeterminate > 0:
        return {
            **base,
            "verdict": VERDICT_SUPPORTED,
            "note": (
                f"applies only to observed intercepted paths; "
                f"{indeterminate} indeterminate in-scope survivors"
            ),
        }
    if observed == 0:
        return {**base, "verdict": VERDICT_INSUFFICIENT}
    return {**base, "verdict": VERDICT_INSUFFICIENT}


def overall_gfx_verdict(
    family_verdicts: dict[str, dict[str, Any]],
    overall_counts: dict[str, int],
    gfx_cut_resolved: dict[str, list[dict[str, Any]]],
) -> str:
    if overall_counts.get(ENDPOINT_STATUS_DIRECT_BYPASS, 0) > 0:
        return VERDICT_BYPASSED
    for fam in REQUIRED_GFX_FAMILIES_FOR_FULL_SEAM:
        members = gfx_cut_resolved.get(fam, [])
        if not members:
            return VERDICT_INSUFFICIENT
        fv = family_verdicts.get(fam, {}).get("verdict")
        if fv != VERDICT_SUPPORTED:
            return VERDICT_INSUFFICIENT
    return VERDICT_SUPPORTED


def _hot_path_rvas(
    graph: CallGraph,
    seeds: dict[str, Any],
    symbol_index: dict[str, list[str]] | None = None,
) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    mesh_sym = "_ZN14CPdxMeshObject13RenderBucketsEP9CGraphicsP21GfxDeferredContextGFXPK7CCameraibb"
    for sym in seeds.get("measured_hot_path_symbols", [mesh_sym]):
        try:
            rva = resolve_symbol_to_rva(
                graph.functions, sym, index=symbol_index
            )
            note = None
            if sym == mesh_sym and rva.lower() != PINNED_MESH_RVA.lower():
                note = f"inventory RVA {rva} differs from pinned {PINNED_MESH_RVA}"
            out.append({"symbol": sym, "entry_rva": rva, "note": note})
        except SeedResolutionError:
            continue
    border_sym = None
    if BORDER_HOOK.is_file():
        border_sym = json.loads(BORDER_HOOK.read_text()).get("owner_symbol")
    if border_sym:
        try:
            rva = resolve_symbol_to_rva(
                graph.functions, border_sym, index=symbol_index
            )
            note = None
            if rva.lower() != PINNED_BORDER_RVA.lower():
                note = f"inventory RVA {rva} differs from pinned {PINNED_BORDER_RVA}"
            out.append({"symbol": border_sym, "entry_rva": rva, "note": note})
        except SeedResolutionError:
            pass
    return out


def _build_priorities(
    graph: CallGraph,
    gfx_cut: dict[str, list[dict[str, Any]]],
    backend_cut: dict[str, list[dict[str, Any]]],
    s_core: set[str],
    cuts_bundle: dict[str, Any],
    hot_paths: list[dict[str, Any]],
    missing_connections: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    scored: dict[str, dict[str, Any]] = {}

    def bump(rva: str, boost: int, reason: str, **extra: Any) -> None:
        if rva not in graph.functions:
            return
        if rva not in scored:
            scored[rva] = {
                "entry_rva": rva,
                "name_mangled": graph.functions[rva].get("name_mangled"),
                "priority_score": 0,
                "reasons": [],
                "unresolved_questions": [],
            }
        scored[rva]["priority_score"] = max(scored[rva]["priority_score"], boost)
        if reason not in scored[rva]["reasons"]:
            scored[rva]["reasons"].append(reason)
        for q in extra.pop("unresolved_questions", []) or []:
            if q and q not in scored[rva]["unresolved_questions"]:
                scored[rva]["unresolved_questions"].append(q)
        scored[rva].update(extra)

    for _fam, members in gfx_cut.items():
        for m in members:
            bump(m["entry_rva"], 10, "gfx_abstraction_cut_member")

    for _fam, members in backend_cut.items():
        for m in members:
            bump(m["entry_rva"], 8, "backend_implementation_cut_member")

    for hp in hot_paths:
        bump(hp["entry_rva"], 15, "measured_hot_path", unresolved_questions=[hp.get("note")])

    for rva in s_core:
        bump(rva, 5, "s_core_intersection")

    for row in cuts_bundle.get("overall_gfx", {}).get("endpoint_coverage", []):
        if row["status"] != ENDPOINT_STATUS_DIRECT_BYPASS:
            continue
        path = row.get("witness_after_cut") or []
        for step in path:
            node = step.get("node")
            if node and not is_external_node(node) and node in graph.functions:
                bump(node, 12, "on_bypass_witness_path")

    for mc in missing_connections:
        bump(mc["entry_rva"], 6, "missing_connection", unresolved_questions=[mc.get("reason")])

    out = list(scored.values())
    out.sort(key=lambda x: (-x["priority_score"], x["entry_rva"]))
    return out[:200]


def build_extract(
    inventory_dir: Path,
    seeds_path: Path | None = None,
    allow_digest_mismatch: bool = False,
    provenance_path: Path | None = None,
) -> dict[str, Any]:
    digest_info = verify_inventory_digest(inventory_dir, provenance_path)
    if not digest_info["digest_match"] and not allow_digest_mismatch:
        raise ReError("inventory provenance missing or mismatched")

    graph = load_call_graph(inventory_dir)
    seeds = load_seeds_config(seeds_path)
    explicit_class: dict[str, str] = seeds.get("endpoint_classification", {})
    symbol_index = build_symbol_index(graph.functions)

    seed_resolution: dict[str, Any] = {"mandatory": {}, "optional": {}, "errors": []}
    for sym in seeds.get("mandatory_identity", MANDATORY_ANCHOR_SYMBOLS):
        seed_resolution["mandatory"][sym] = {
            "entry_rva": resolve_symbol_to_rva(
                graph.functions, sym, index=symbol_index
            ),
            "status": "resolved",
        }

    renderer_roots: list[str] = []
    for sym in seeds.get("renderer_roots", []):
        try:
            renderer_roots.append(
                resolve_symbol_to_rva(graph.functions, sym, index=symbol_index)
            )
        except SeedResolutionError as exc:
            seed_resolution["optional"][sym] = {"status": "missing", "error": str(exc)}

    for sym in seeds.get("renderer_sub_entries", []):
        try:
            rva = resolve_symbol_to_rva(graph.functions, sym, index=symbol_index)
            if rva not in renderer_roots:
                renderer_roots.append(rva)
        except SeedResolutionError as exc:
            seed_resolution["optional"][sym] = {"status": "missing", "error": str(exc)}

    gfx_cut_resolved = resolve_cut_symbols(
        graph.functions,
        seeds.get("gfx_abstraction_cut", {}),
        "gfx_abstraction_cut",
        symbol_index=symbol_index,
    )
    backend_cut_resolved = resolve_cut_symbols(
        graph.functions,
        seeds.get("backend_implementation_cut", {}),
        "backend_implementation_cut",
        symbol_index=symbol_index,
    )
    gfx_cut_by_family = cut_sets_by_family(gfx_cut_resolved)
    gfx_cut_all = cut_union(gfx_cut_by_family)
    backend_cut_by_family = cut_sets_by_family(backend_cut_resolved)
    backend_cut_all = cut_union(backend_cut_by_family)

    gl_endpoints = graph.opengl_endpoints()
    cgl_endpoints = graph.cgl_endpoints()
    all_endpoints = sorted(set(gl_endpoints) | set(cgl_endpoints))

    desc_r = descendants_of(graph, renderer_roots) if renderer_roots else set()
    anc_gl = ancestors_of(graph, gl_endpoints)
    s_core = desc_r & anc_gl

    missing_connections: list[dict[str, Any]] = []
    for sym in seeds.get("renderer_roots", []) + seeds.get("renderer_sub_entries", []):
        info = seed_resolution["mandatory"].get(sym) or seed_resolution["optional"].get(sym)
        if not info or "entry_rva" not in info:
            continue
        rva = info["entry_rva"]
        reaches_gl = any(graph.reachable(rva, ep, False, frozenset()) for ep in gl_endpoints)
        reaches_cgl = any(graph.reachable(rva, ep, False, frozenset()) for ep in cgl_endpoints)
        if not reaches_gl and not reaches_cgl and rva not in s_core:
            missing_connections.append(
                {"symbol": sym, "entry_rva": rva, "reason": "no_path_to_gl_cgl_or_s_core"}
            )

    family_verdicts: dict[str, Any] = {}
    family_coverage: dict[str, Any] = {}

    for family, cut_set in gfx_cut_by_family.items():
        characterized = bool(gfx_cut_resolved.get(family))
        all_rows: list[dict[str, Any]] = []
        total_counts: dict[str, int] = defaultdict(int)
        for root in renderer_roots:
            rows, counts = analyze_endpoints_for_cut(
                graph,
                root,
                cut_set,
                all_endpoints,
                scope_family=family if characterized else None,
                explicit_class=explicit_class,
            )
            all_rows.extend(rows)
            for k, v in counts.items():
                total_counts[k] += v
        family_coverage[family] = {
            "cut_rvas": sorted(cut_set),
            "endpoint_coverage": all_rows,
            "status_summary": dict(total_counts),
        }
        unassessed_full = [
            r
            for r in all_rows
            if not r.get("assessed", True)
            and r.get("status") != ENDPOINT_STATUS_NOT_OBSERVED
        ]
        verdict = family_verdict_from_counts(
            dict(total_counts),
            characterized,
            unassessed_draw_candidates=unassessed_full if family == "draw" else None,
        )
        if family == "draw" and unassessed_full:
            verdict["unassessed_draw_candidate_count"] = len(unassessed_full)
            verdict["unassessed_draw_candidates"] = [
                {"endpoint": r["endpoint"], "status": r["status"], "assessed": False}
                for r in unassessed_full[:UNASSESSED_DRAW_DIAGNOSTIC_LIMIT]
            ]
        verdict.update(intercepted_endpoint_metrics(all_rows, dict(total_counts)))
        family_verdicts[family] = verdict

    for family in gfx_cut_resolved:
        if family not in family_verdicts:
            family_verdicts[family] = {"verdict": VERDICT_NOT_CHARACTERIZED}
    for fam in REQUIRED_GFX_FAMILIES_FOR_FULL_SEAM:
        if fam not in family_verdicts:
            family_verdicts[fam] = {"verdict": VERDICT_NOT_CHARACTERIZED}

    overall_rows: list[dict[str, Any]] = []
    overall_counts: dict[str, int] = defaultdict(int)
    for root in renderer_roots:
        rows, counts = analyze_endpoints_for_cut(
            graph,
            root,
            gfx_cut_all,
            all_endpoints,
            scope_family=None,
            explicit_class=explicit_class,
        )
        overall_rows.extend(rows)
        for k, v in counts.items():
            overall_counts[k] += v

    cuts_analysis: dict[str, Any] = {
        "gfx_abstraction": {
            "family_verdicts": family_verdicts,
            "family_coverage": family_coverage,
            "overall_cut_coverage": {
                "cut_rvas": sorted(gfx_cut_all),
                "endpoint_coverage": overall_rows,
                "status_summary": dict(overall_counts),
            },
        },
    }

    backend_family_verdicts: dict[str, Any] = {}
    backend_family_coverage: dict[str, Any] = {}
    for family, cut_set in backend_cut_by_family.items():
        characterized = bool(backend_cut_resolved.get(family))
        if not characterized:
            backend_family_verdicts[family] = {"verdict": VERDICT_NOT_CHARACTERIZED}
            continue
        if not explicit_family_classified(family, explicit_class):
            backend_family_verdicts[family] = {
                "verdict": VERDICT_INSUFFICIENT,
                "note": (
                    f"no explicit endpoint classification for backend family {family}"
                ),
            }
            continue
        backend_rows: list[dict[str, Any]] = []
        total_counts: dict[str, int] = defaultdict(int)
        for root in renderer_roots:
            rows, counts = analyze_endpoints_for_cut(
                graph,
                root,
                cut_set,
                all_endpoints,
                scope_family=family,
                explicit_class=explicit_class,
            )
            backend_rows.extend(rows)
            for k, v in counts.items():
                total_counts[k] += v
        backend_family_coverage[family] = {
            "cut_rvas": sorted(cut_set),
            "endpoint_coverage": backend_rows,
            "status_summary": dict(total_counts),
        }
        verdict = family_verdict_from_counts(dict(total_counts), characterized)
        verdict.update(intercepted_endpoint_metrics(backend_rows, dict(total_counts)))
        backend_family_verdicts[family] = verdict

    cuts_analysis["backend_implementation"] = {
        "family_verdicts": backend_family_verdicts,
        "family_coverage": backend_family_coverage,
    }

    frontiers_before: list[dict[str, Any]] = []
    frontiers_after: list[dict[str, Any]] = []
    for root in renderer_roots:
        frontiers_before.extend(graph.reachable_frontiers(root, frozenset()))
        frontiers_after.extend(graph.reachable_frontiers(root, gfx_cut_all))
    unknown_target_risk = len(frontiers_after) > 0

    overall_boundary_verdict = overall_gfx_verdict(
        family_verdicts, dict(overall_counts), gfx_cut_resolved
    )
    if unknown_target_risk and overall_boundary_verdict == VERDICT_SUPPORTED:
        overall_boundary_verdict = VERDICT_INSUFFICIENT

    authoritative = digest_info["digest_match"] and not allow_digest_mismatch
    structural_boundary_verdict = overall_boundary_verdict
    if not authoritative:
        overall_boundary_verdict = VERDICT_INSUFFICIENT

    hot_paths = _hot_path_rvas(graph, seeds, symbol_index=symbol_index)
    cuts_bundle = cuts_analysis["gfx_abstraction"]
    priorities = _build_priorities(
        graph,
        gfx_cut_resolved,
        backend_cut_resolved,
        s_core,
        {"overall_gfx": cuts_bundle["overall_cut_coverage"]},
        hot_paths,
        missing_connections,
    )

    cut_characterization: dict[str, Any] = {
        "gfx_abstraction": {
            fam: {
                "members": [m["symbol"] for m in gfx_cut_resolved.get(fam, [])],
                "not_characterized": not gfx_cut_resolved.get(fam),
            }
            for fam in set(gfx_cut_resolved) | set(REQUIRED_GFX_FAMILIES_FOR_FULL_SEAM)
        },
        "endpoint_classes_analyzed": ["opengl", "cgl"],
        "endpoints_unclassified": [
            ep[4:]
            for ep in all_endpoints
            if classify_endpoint_symbol(ep[4:], explicit_class) == "unclassified"
        ][:100],
    }

    return {
        "schema": 2,
        "authoritative": authoritative,
        "exploratory": allow_digest_mismatch,
        "inventory_provenance": digest_info,
        "seed_resolution": seed_resolution,
        "structural": {
            "renderer_roots": renderer_roots,
            "desc_r_size": len(desc_r),
            "anc_gl_size": len(anc_gl),
            "s_core_size": len(s_core),
        },
        "cut_characterization": cut_characterization,
        "cuts": cuts_analysis,
        "family_verdicts": family_verdicts,
        "overall_boundary_verdict": overall_boundary_verdict,
        "structural_boundary_verdict": structural_boundary_verdict,
        "boundary_conclusion": overall_boundary_verdict,
        "unknown_target_risk": unknown_target_risk,
        "missing_connections": missing_connections,
        "hot_path_resolution": hot_paths,
        "decompile_priorities": priorities,
    }
