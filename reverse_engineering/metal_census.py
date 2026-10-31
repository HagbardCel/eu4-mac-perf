#!/usr/bin/env python3
"""Offline census of the EU4 Gfx cut versus imported OpenGL.

Structural boundary and indirect-dispatch coverage are separate results.
The call graph and snapshot loaders are the existing ones.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

from reverse_engineering.call_graph import (
    ENDPOINT_STATUS_DIRECT_BYPASS,
    ENDPOINT_STATUS_INDETERMINATE,
    CallGraph,
    ancestors_of,
    descendants_of,
    endpoint_status_from_sets,
    is_external_node,
    load_call_graph,
)
from reverse_engineering.census import OPENGL_RE
from reverse_engineering.call_graph import CGL_RE
from reverse_engineering.gfx_subgraph import reachability_views
from reverse_engineering.intervals import format_rva, parse_rva
from reverse_engineering.paths import RE_DIR, ReError, binary_sha256, local_binary_root, resolve_export_dir
from reverse_engineering.renderer_extract import (
    load_batch_plan,
    load_callsites_by_caller,
    load_inventory_index,
    read_zip_package,
    validate_batch_package,
)
from reverse_engineering.renderer_scope import (
    DEFAULT_SCOPE_DIR,
    is_gfx_helper,
    validate_scope,
)

ROOT = RE_DIR.parent
DEFAULT_OUT = ROOT / "analysis" / "metal-census" / "v1"
ESTABLISHED_RESOLUTIONS = frozenset(
    {"thunk", "direct_external", "external_via_stub", "direct_internal"}
)
ENDPOINT_EDGE_RESOLUTIONS = frozenset({"thunk", "direct_external", "external_via_stub"})
SHADER_METHODS = frozenset(
    {
        "EnableVertexAttribArrays",
        "LinkProgram",
        "UnloadAll",
        "SetAll",
        "SetUniformLocations",
    }
)
ROOT_NAMES = (
    "CInGameIdler::Render",
    "CEU3GraphicalMap::Render",
    "CPdxMeshObject::RenderBuckets",
    "CApplication::CreateApplicationWindow",
)
WINDOW_NAME = "CApplication::CreateApplicationWindow"
WINDOW_TOKENS = (
    "SDL_CreateWindow",
    "SDL_WINDOW_OPENGL",
    "SDL_GL_CreateContext",
    "SDL_GL_SetAttribute",
    "SDL_GL_SetSwapInterval",
    "SDL_GL_SwapWindow",
    "NSView",
    "NSWindow",
    "CAMetalLayer",
    "Cocoa",
)
CALL_MEM = re.compile(
    r"qword ptr \[([A-Z][A-Z0-9]{0,2})(?: \+ (0x[0-9a-fA-F]+))?\]",
    re.I,
)
CALL_REG = re.compile(r"\bCALL ([A-Z][A-Z0-9]{0,2})$", re.I)
BARRIER = frozenset({"CALL", "RET", "JMP", "NOP"})


def norm_rva(value: str) -> str:
    return format_rva(parse_rva(value))


def fn_name(row: dict[str, Any] | None) -> str:
    if not row:
        return ""
    return row.get("name_demangled") or row.get("name_mangled") or ""


def is_public_sdl_gl(name: str) -> bool:
    if not name.startswith("_SDL_GL_"):
        return False
    if name.endswith("_REAL") or name.endswith("_DEFAULT"):
        return False
    return True


def is_glew_body(name: str) -> bool:
    return name.startswith("_glew") or name.startswith("__glew")


def endpoint_family(symbol: str) -> str | None:
    if OPENGL_RE.match(symbol):
        return "gl"
    if CGL_RE.match(symbol):
        return "cgl"
    if "NSOpenGL" in symbol:
        return "nsopengl"
    if is_public_sdl_gl(symbol):
        return "sdl_gl"
    return None


def selected_indirect_rows(scope_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The fixed site list: _Gfx*, five SShaderOpenGL methods, and one helper."""
    out: list[dict[str, Any]] = []
    for row in scope_rows:
        name = fn_name(row)
        method = name.split("::", 1)[1] if "::" in name else ""
        take = (
            is_gfx_helper(row)
            or method in SHADER_METHODS and name.startswith("SShaderOpenGL::")
            or name == "InternalSetTextureAndSamplerState"
        )
        if not take:
            continue
        for site in row.get("missing_indirect_sites") or []:
            out.append(
                {
                    "caller_rva": norm_rva(row["entry_rva"]),
                    "callsite_rva": norm_rva(site),
                    "name": name,
                }
            )
    out.sort(key=lambda item: (item["caller_rva"], item["callsite_rva"]))
    return out


def _caller_rows(graph: CallGraph) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    sdl_rva = {
        rva: fn_name(row)
        for rva, row in graph.functions.items()
        if is_public_sdl_gl(fn_name(row))
    }
    for edge in graph.edges_raw:
        caller = edge.get("caller_rva")
        if not caller:
            continue
        resolution = edge.get("resolution") or ""
        symbol = edge.get("ultimate_external_symbol") or edge.get("callee_external_symbol") or ""
        family = endpoint_family(symbol) if symbol else None
        endpoint = symbol
        if family and resolution in ENDPOINT_EDGE_RESOLUTIONS:
            pass
        elif resolution == "direct_internal" and edge.get("callee_internal_rva") in sdl_rva:
            endpoint = sdl_rva[edge["callee_internal_rva"]]
            family = "sdl_gl"
        else:
            continue
        grouped[caller].append(
            {
                "callsite_rva": edge.get("callsite_rva") or "",
                "endpoint": endpoint,
                "resolution": resolution,
                "family": family,
            }
        )
    rows = []
    for caller, sites in grouped.items():
        row = graph.functions.get(caller) or {}
        sites.sort(key=lambda item: (item["callsite_rva"], item["endpoint"], item["resolution"]))
        rows.append(
            {
                "caller_rva": caller,
                "name": fn_name(row) or caller,
                "instruction_count": row.get("instruction_count") or 0,
                "decoded_instruction_bytes": row.get("decoded_instruction_bytes") or 0,
                "in_gfx": is_gfx_helper(row),
                "callsites": sites,
            }
        )
    rows.sort(key=lambda item: item["caller_rva"])
    return rows


def _index_by_name(graph: CallGraph) -> dict[str, list[str]]:
    found: dict[str, list[str]] = defaultdict(list)
    for rva, row in graph.functions.items():
        name = fn_name(row)
        if name:
            found[name].append(rva)
    return found


def _resolve_roots(graph: CallGraph, cut: frozenset[str]) -> tuple[list[str], list[dict[str, Any]]]:
    by_name = _index_by_name(graph)
    roots: list[str] = []
    notes: list[dict[str, Any]] = []
    for name in ROOT_NAMES:
        hits = sorted(by_name.get(name, []))
        if len(hits) != 1:
            notes.append({"name": name, "status": "missing" if not hits else "ambiguous", "rvas": hits})
            continue
        roots.append(hits[0])
        notes.append({"name": name, "status": "resolved", "rvas": hits})
    shutdown = sorted(by_name.get("_GfxShutdown", []))
    callers = sorted(
        {
            edge.get("caller_rva")
            for edge in graph.edges_raw
            if edge.get("callee_internal_rva") in shutdown and edge.get("caller_rva")
        }
    )
    outside = [rva for rva in callers if rva not in cut]
    if len(outside) == 1:
        roots.append(outside[0])
        notes.append(
            {
                "name": "_GfxShutdown caller",
                "status": "resolved",
                "rvas": outside,
            }
        )
    else:
        notes.append(
            {
                "name": "_GfxShutdown caller",
                "status": "missing" if not outside else "ambiguous",
                "rvas": outside,
                "inside_cut_callers": [rva for rva in callers if rva in cut],
            }
        )
    # Preserve order, drop duplicates.
    seen: set[str] = set()
    ordered = []
    for rva in roots:
        if rva not in seen:
            seen.add(rva)
            ordered.append(rva)
    return ordered, notes


def _incoming_by_endpoint(graph: CallGraph) -> tuple[dict[str, list[tuple[str, dict[str, Any]]]], dict[str, list[tuple[str, dict[str, Any]]]]]:
    resolved: dict[str, list[tuple[str, dict[str, Any]]]] = defaultdict(list)
    direct: dict[str, list[tuple[str, dict[str, Any]]]] = defaultdict(list)
    for caller, edges in graph.resolved_adj.items():
        for callee, meta in edges:
            resolved[callee].append((caller, meta))
    for caller, edges in graph.direct_adj.items():
        for callee, meta in edges:
            direct[callee].append((caller, meta))
    return resolved, direct


def _window_notes(text: str | None, rva: str | None) -> dict[str, Any]:
    if text is None:
        return {
            "function": WINDOW_NAME,
            "entry_rva": rva,
            "status": "decompilation_not_loaded",
            "tokens_present": [],
            "tokens_absent": list(WINDOW_TOKENS),
        }
    present = [token for token in WINDOW_TOKENS if token in text]
    absent = [token for token in WINDOW_TOKENS if token not in text]
    return {
        "function": WINDOW_NAME,
        "entry_rva": rva,
        "status": "scanned",
        "tokens_present": present,
        "tokens_absent": absent,
        "note": "Token hits are names present in pseudocode, not a drawable-lifecycle assessment.",
    }


def _platform_gaps(
    graph: CallGraph,
    callers: list[dict[str, Any]],
    thunk_symbols: list[str],
    window_text: str | None,
) -> dict[str, Any]:
    families = {site["family"] for row in callers for site in row["callsites"]}
    names = {fn_name(row) for row in graph.functions.values()}
    not_observed = []
    if "cgl" not in families and not any("CGL" in name for name in names):
        not_observed.append(
            {
                "api": "CGL",
                "status": "not_observed",
                "reason": "no CGL symbol in the recovered functions or call edges",
            }
        )
    called_gl = {site["endpoint"] for row in callers for site in row["callsites"] if site["family"] == "gl"}
    thunks_without = sorted(symbol for symbol in thunk_symbols if symbol not in called_gl)
    ns_thunks = sorted(
        (rva, fn_name(row))
        for rva, row in graph.functions.items()
        if row.get("kind") == "thunk" and "NSOpenGL" in fn_name(row)
    )
    incoming = {edge.get("callee_internal_rva") for edge in graph.edges_raw}
    unreferenced = [
        {"entry_rva": rva, "name": name}
        for rva, name in ns_thunks
        if rva not in incoming
    ]
    window_rvas = [
        rva for rva, row in graph.functions.items() if fn_name(row) == WINDOW_NAME
    ]
    window_rva = window_rvas[0] if len(window_rvas) == 1 else None
    adapter = []
    for row in callers:
        if row["caller_rva"] != window_rva:
            continue
        for site in row["callsites"]:
            if site["family"] != "sdl_gl":
                continue
            adapter.append(
                {
                    "callsite_rva": site["callsite_rva"],
                    "caller_rva": row["caller_rva"],
                    "endpoint": site["endpoint"],
                    "resolution": site["resolution"],
                    "reason": "public SDL_GL callsite in window creation; anticipated adapter",
                }
            )
    adapter.sort(key=lambda item: item["callsite_rva"])
    return {
        "not_observed": not_observed,
        "unreferenced_platform_thunk": unreferenced,
        "platform_assessment": "platform_not_assessed",
        "adapter_path": adapter,
        "window_adaptation": _window_notes(window_text, window_rva),
        "boundary_thunks_without_caller": thunks_without,
    }


def build_census(
    graph: CallGraph,
    *,
    scope_rows: list[dict[str, Any]],
    thunk_symbols: list[str],
    window_text: str | None = None,
) -> dict[str, Any]:
    cut = frozenset(rva for rva, row in graph.functions.items() if is_gfx_helper(row))
    callers = _caller_rows(graph)
    gaps = _platform_gaps(graph, callers, thunk_symbols, window_text)
    adapter_sites = {row["callsite_rva"] for row in gaps["adapter_path"]}
    roots, root_notes = _resolve_roots(graph, cut)
    missing_roots = [note["name"] for note in root_notes if note["status"] != "resolved"]

    gl_endpoints = set(graph.opengl_endpoints())
    sdl_endpoints = {
        rva for rva, row in graph.functions.items() if is_public_sdl_gl(fn_name(row))
    }
    endpoints = sorted(gl_endpoints | sdl_endpoints | set(graph.cgl_endpoints()))
    views = {root: reachability_views(graph, root, cut) for root in roots}
    resolved_in, direct_in = _incoming_by_endpoint(graph)

    bypasses = []
    for root in roots:
        view = views[root]
        for endpoint in endpoints:
            status = endpoint_status_from_sets(
                view.before, view.after_resolved, view.after_direct, endpoint
            )
            if status not in (ENDPOINT_STATUS_DIRECT_BYPASS, ENDPOINT_STATUS_INDETERMINATE):
                continue
            table = direct_in if status == ENDPOINT_STATUS_DIRECT_BYPASS else resolved_in
            visited = view.after_direct if status == ENDPOINT_STATUS_DIRECT_BYPASS else view.after_resolved
            edges = []
            for caller, meta in table.get(endpoint, []):
                if caller not in visited:
                    continue
                if meta.get("callsite_rva") in adapter_sites:
                    continue
                edges.append(
                    {
                        "caller_rva": caller,
                        "callsite_rva": meta.get("callsite_rva"),
                        "resolution": meta.get("resolution"),
                        "caller_is_root": caller == root,
                    }
                )
            if not edges:
                continue
            # A thunk edge names its ultimate symbol. That is an established survivor.
            established = all(edge["resolution"] in ESTABLISHED_RESOLUTIONS for edge in edges)
            bypasses.append(
                {
                    "root_rva": root,
                    "endpoint": endpoint[4:] if endpoint.startswith("ext:") else endpoint,
                    "endpoint_node": endpoint,
                    "status": status,
                    "established": established,
                    "edges": edges,
                }
            )
    bypasses.sort(key=lambda item: (item["root_rva"], item["endpoint"], item["status"]))

    # descendants_of skips a root that is inside the cut, so the seeds are the first callees outside it.
    cone_seeds = [
        callee
        for member in cut
        for callee, _meta in graph.resolved_adj.get(member, [])
        if callee not in cut and not is_external_node(callee)
    ]
    cone = descendants_of(graph, cone_seeds, cut)
    gl_reachers = ancestors_of(graph, gl_endpoints)
    gl_caller_rvas = {
        row["caller_rva"]
        for row in callers
        if any(site["family"] == "gl" for site in row["callsites"])
    }
    classify_ids = (cone & gl_reachers) | (gl_caller_rvas - cut)
    unrooted = []
    helpers = []
    behind: set[str] = set()
    for rva in sorted(classify_ids):
        reached = [root for root in roots if rva in views[root].before]
        avoiding = [root for root in roots if rva in views[root].after_resolved]
        record = {
            "entry_rva": rva,
            "name": fn_name(graph.functions.get(rva)),
            "in_cone": rva in cone,
        }
        if not reached:
            record["known_root"] = "unknown"
            if rva in gl_caller_rvas:
                unrooted.append(record)
            else:
                helpers.append(record)
            continue
        if avoiding and rva in cone:
            record["known_root"] = "shared"
        elif not avoiding:
            record["known_root"] = "behind_cut"
            behind.add(rva)
        else:
            record["known_root"] = "unknown"
        helpers.append(record)
    helpers.sort(key=lambda item: item["entry_rva"])
    unrooted.sort(key=lambda item: item["entry_rva"])

    region = cut | behind
    outside_edges = []
    for caller, edges in graph.resolved_adj.items():
        if caller in region or is_external_node(caller):
            continue
        for callee, meta in edges:
            if callee in behind:
                outside_edges.append(
                    {
                        "caller_rva": caller,
                        "caller_name": fn_name(graph.functions.get(caller)),
                        "callee_rva": callee,
                        "callee_name": fn_name(graph.functions.get(callee)),
                        "callsite_rva": meta.get("callsite_rva"),
                        "resolution": meta.get("resolution"),
                    }
                )
    outside_edges.sort(key=lambda item: (item["caller_rva"], item["callee_rva"], item["callsite_rva"] or ""))

    established = [row for row in bypasses if row["established"]]
    uncertain = [row for row in bypasses if not row["established"]]
    if missing_roots or uncertain:
        boundary_verdict = "inconclusive"
    elif not established:
        boundary_verdict = "compact_gfx_boundary"
    elif any(edge["caller_is_root"] for row in established for edge in row["edges"]):
        boundary_verdict = "dispersed_backend"
    else:
        boundary_verdict = "expanded_boundary"

    sites = selected_indirect_rows(scope_rows)
    gfx_rows = [row for row in callers if any(site["family"] == "gl" for site in row["callsites"])]
    outside_gl = [row for row in gfx_rows if not row["in_gfx"]]
    direct_gl_candidates = [
        {
            "caller_rva": row["caller_rva"],
            "name": row["name"],
            "instruction_count": row["instruction_count"],
            "endpoints": sorted({site["endpoint"] for site in row["callsites"] if site["family"] == "gl"}),
        }
        for row in gfx_rows
        if not is_glew_body(row["name"])
    ]
    implementation_surface = [
        {"entry_rva": rva, "name": fn_name(graph.functions.get(rva)), "role": "cut"}
        for rva in sorted(cut)
    ]
    implementation_surface.extend(
        {
            "entry_rva": row["entry_rva"],
            "name": row["name"],
            "role": row["known_root"],
        }
        for row in helpers
        if row["known_root"] in {"behind_cut", "shared"}
    )
    glew_init = sorted(
        rva for rva, row in graph.functions.items() if fn_name(row) in {"_glewInit", "__glewInit"}
    )
    glew_callers = sorted(
        {
            edge.get("caller_rva")
            for edge in graph.edges_raw
            if edge.get("callee_internal_rva") in glew_init and edge.get("caller_rva")
        }
    )
    cut_instructions = sum((graph.functions[rva].get("instruction_count") or 0) for rva in cut)
    cut_bytes = sum((graph.functions[rva].get("decoded_instruction_bytes") or 0) for rva in cut)
    return {
        "boundary_verdict": boundary_verdict,
        "boundary_limit": (
            "Within recovered static edges from the selected roots. "
            "Higher-level indirect calls, including those in CInGameIdler::Render, were not classified. "
            "This is not proof that no out-of-cut dispatch exists."
        ),
        "roots": root_notes,
        "cut_function_count": len(cut),
        "cut_instructions": cut_instructions,
        "cut_decoded_bytes": cut_bytes,
        "direct_callers": callers,
        "direct_gl_caller_count": len(gfx_rows),
        "direct_gl_in_gfx": sum(1 for row in gfx_rows if row["in_gfx"]),
        "outside_gfx_gl_callers": [
            {
                "caller_rva": row["caller_rva"],
                "name": row["name"],
                "instruction_count": row["instruction_count"],
                "endpoints": sorted({site["endpoint"] for site in row["callsites"] if site["family"] == "gl"}),
            }
            for row in outside_gl
        ],
        "direct_gl_candidates": direct_gl_candidates,
        "implementation_surface": implementation_surface,
        "platform_gaps": gaps,
        "bypasses": bypasses,
        "helpers": helpers,
        "unrooted_in_selected_graph": unrooted,
        "outside_region_edges": outside_edges,
        "glew_init_dependency": {
            "callee_rvas": glew_init,
            "caller_rvas": glew_callers,
            "note": "GLEW bodies are excluded from reconstruction. Init still has to be retired or replaced.",
        },
        "indirect_dispatch_coverage": {
            "selected_sites": len(sites),
            "reviewed": 0,
            "confirmed_graphics_dispatch": 0,
            "confirmed_non_graphics": 0,
            "bounded_candidates": 0,
            "unresolved": len(sites),
            "note": (
                "Counts of indirect sites only. They do not measure shader behavior, "
                "resource lifetimes, Metal init, or presentation."
            ),
        },
        "indirect_sites": sites,
        "effort": {"status": "pending_indirect_dispatch"},
    }


def render_boundary_md(bundle: dict[str, Any]) -> str:
    coverage = bundle["indirect_dispatch_coverage"]
    outside = bundle["outside_gfx_gl_callers"]
    gaps = bundle["platform_gaps"]
    lines = [
        "# Gfx boundary census",
        "",
        f"**Boundary verdict:** `{bundle['boundary_verdict']}`",
        "",
        bundle["boundary_limit"],
        "",
        "## Direct API census",
        "",
        f"- Direct GL callers: {bundle['direct_gl_caller_count']} "
        f"({bundle['direct_gl_in_gfx']} inside the {bundle['cut_function_count']} `_Gfx*` functions)",
        f"- `_Gfx*` instructions / bytes: {bundle['cut_instructions']} / {bundle['cut_decoded_bytes']}",
        "- Outside-Gfx direct GL callers, from call edges:",
    ]
    if outside:
        for row in outside:
            ends = ", ".join(row["endpoints"])
            lines.append(
                f"  - `{row['caller_rva']}` {row['name']} ({row['instruction_count']} instructions): {ends}"
            )
    else:
        lines.append("  - none")
    lines.extend(
        [
            "",
            "## Boundary assessment",
            "",
            f"- Established or uncertain survivors after the cut: {len(bundle['bypasses'])}",
            f"- Helpers labeled from the selected roots: {len(bundle['helpers'])}",
            f"- `behind_cut`: {sum(1 for row in bundle['helpers'] if row['known_root'] == 'behind_cut')}",
            f"- `shared`: {sum(1 for row in bundle['helpers'] if row['known_root'] == 'shared')}",
            f"- `unknown`: {sum(1 for row in bundle['helpers'] if row['known_root'] == 'unknown')}",
            f"- Incoming edges from outside the cut and outside `behind_cut`: {len(bundle['outside_region_edges'])}",
            f"- `unrooted_in_selected_graph`: {len(bundle['unrooted_in_selected_graph'])}",
        ]
    )
    for row in bundle["unrooted_in_selected_graph"]:
        lines.append(f"  - `{row['entry_rva']}` {row['name']}")
    for row in bundle["helpers"]:
        if row["known_root"] == "behind_cut":
            continue
        lines.append(f"- `{row['known_root']}` `{row['entry_rva']}` {row['name']}")
    lines.extend(
        [
            "",
            "Adapter callsites are not counted as structural bypasses.",
            "",
            "## Implementation dependencies",
            "",
            f"- Selected indirect sites: {coverage['selected_sites']}",
            f"- Unresolved among them: {coverage['unresolved']}",
            f"- Bounded candidates: {coverage['bounded_candidates']}",
            f"- Confirmed graphics dispatches: {coverage['confirmed_graphics_dispatch']}",
            f"- Confirmed non-graphics dispatches: {coverage['confirmed_non_graphics']}",
            f"- Minimum direct-GL candidates: {len(bundle['direct_gl_candidates'])}",
            f"- Implementation surface (cut plus behind-cut and shared helpers): {len(bundle['implementation_surface'])}",
            f"- GLEW init callers: {len(bundle['glew_init_dependency']['caller_rvas'])}",
            "",
            coverage["note"],
            "",
            "## Platform adaptation",
            "",
            f"- Platform assessment: `{gaps['platform_assessment']}`",
            f"- Unreferenced NSOpenGL thunks: {len(gaps['unreferenced_platform_thunk'])}",
            f"- Adapter SDL_GL callsites: {len(gaps['adapter_path'])}",
            f"- CGL rows: {len(gaps['not_observed'])}",
            f"- Window scan: {gaps['window_adaptation'].get('status')}",
            "- Window tokens are names found in pseudocode, not a drawable-lifecycle result.",
        ]
    )
    present = gaps["window_adaptation"].get("tokens_present") or []
    if present:
        lines.append("- Window tokens present: " + ", ".join(present))
    lines.extend(
        [
            "",
            "## Remaining uncertainties",
            "",
            "- Material: any survivor listed in `boundary.json` under `bypasses`.",
            "- Platform lifecycle was not assessed. Unreferenced NSOpenGL thunks and `not_observed` CGL stay disclosed.",
            "- Not analyzed: indirect sites in `CInGameIdler::Render` and other functions outside the selected site list.",
            "",
            "## Effort estimate",
            "",
        ]
    )
    effort = bundle.get("effort") or {}
    if effort.get("status") == "pending_indirect_dispatch":
        lines.append("Pending the indirect-dispatch pass. Original `_Gfx*` size is above.")
    else:
        lines.append(effort.get("summary") or "")
    lines.extend(["", "## Recommendation", "", f"`{bundle['boundary_verdict']}`", ""])
    return "\n".join(lines)


def publish_census(out_dir: Path, bundle: dict[str, Any], *, computed_digest: str, expected_digest: str) -> None:
    if computed_digest != expected_digest:
        raise ReError("inventory bundle digest mismatch; refusing to publish")
    out_dir.mkdir(parents=True, exist_ok=True)
    callers = bundle["direct_callers"]
    (out_dir / "direct-callers.jsonl").write_text(
        "".join(json.dumps(row, sort_keys=True) + "\n" for row in callers)
    )
    (out_dir / "platform-gaps.json").write_text(
        json.dumps(bundle["platform_gaps"], indent=2, sort_keys=True) + "\n"
    )
    stored = dict(bundle)
    stored["inventory_bundle_digest"] = computed_digest
    (out_dir / "boundary.json").write_text(json.dumps(stored, indent=2, sort_keys=True) + "\n")
    (out_dir / "boundary.md").write_text(render_boundary_md(stored))


def destination_register(ins: dict[str, Any]) -> str | None:
    mnem = (ins.get("mnemonic") or "").upper()
    if mnem in BARRIER or mnem.startswith("J") or mnem in {"CMP", "TEST"}:
        return None
    text = ins.get("operands") or ""
    if text.upper().startswith(mnem):
        text = text[len(mnem) :].strip()
    dest = text.split(",")[0].strip().upper()
    if re.fullmatch(r"[A-Z][A-Z0-9]{0,2}", dest):
        return dest
    return None


def trace_call_operand(instructions: list[dict[str, Any]], index: int) -> dict[str, Any]:
    """Tie a call operand to a prior load in this straight-line window, or leave it untraced."""
    ins = instructions[index]
    operands = ins.get("operands") or ""
    mem = CALL_MEM.search(operands)
    reg_only = CALL_REG.search(operands.strip())
    if mem:
        reg = mem.group(1).upper()
        disp = mem.group(2)
        if disp:
            return {
                "mechanism_id": "disp_indirect",
                "classification": "unresolved",
                "confidence": "untraced",
                "target_evidence": [norm_rva(ins["instruction_rva"])],
                "reason": f"call through [{reg}+{disp}] without a traced slot",
                "reviewed": False,
            }
    elif reg_only:
        reg = reg_only.group(1).upper()
    else:
        return {
            "mechanism_id": "unclassified_indirect",
            "classification": "unresolved",
            "confidence": "untraced",
            "target_evidence": [norm_rva(ins["instruction_rva"])],
            "reason": "indirect call operand was not a register or memory form",
            "reviewed": False,
        }
    for prior in reversed(instructions[max(0, index - 16) : index]):
        mnem = (prior.get("mnemonic") or "").upper()
        if mnem in BARRIER or mnem.startswith("J"):
            break
        if destination_register(prior) != reg:
            continue
        slot = next(
            (
                ref.get("target_rva")
                for ref in prior.get("references_from") or []
                if ref.get("target_rva")
            ),
            None,
        )
        if mnem in {"LEA", "MOV"} and slot:
            return {
                "mechanism_id": "slot_loaded_register",
                "classification": "unresolved",
                "confidence": "untraced",
                "target_evidence": [
                    norm_rva(prior["instruction_rva"]),
                    norm_rva(ins["instruction_rva"]),
                    norm_rva(slot),
                ],
                "operand_source": {
                    "load_rva": norm_rva(prior["instruction_rva"]),
                    "slot_rva": norm_rva(slot),
                    "reason": (
                        "The call register is loaded from this slot and not overwritten in this window. "
                        "The slot is a pointer source, not a closed set of target functions."
                    ),
                },
                "reason": (
                    f"{reg} is loaded from slot {norm_rva(slot)} before the call. "
                    "That address does not identify which functions the slot can hold."
                ),
                "reviewed": False,
            }
        break
    return {
        "mechanism_id": "untraced_reg_indirect" if reg_only else "untraced_mem_indirect",
        "classification": "unresolved",
        "confidence": "untraced",
        "target_evidence": [norm_rva(ins["instruction_rva"])],
        "reason": "a nearby graphics pointer or GetProcAddress call does not identify this operand",
        "reviewed": False,
    }


def evidence_rvas(static_row: dict[str, Any]) -> set[str]:
    found = set()
    for ins in static_row.get("instructions") or []:
        if ins.get("instruction_rva"):
            found.add(norm_rva(ins["instruction_rva"]))
        for ref in ins.get("references_from") or []:
            if ref.get("target_rva"):
                found.add(norm_rva(ref["target_rva"]))
    return found


def site_instruction(static_row: dict[str, Any], callsite_rva: str) -> dict[str, Any]:
    want = norm_rva(callsite_rva)
    for ins in static_row.get("instructions") or []:
        if norm_rva(ins["instruction_rva"]) != want:
            continue
        if not (ins.get("pcode_is_call") or ins.get("flow_type_is_call")):
            raise ReError(f"{want} is not a call-like instruction in {static_row.get('entry_rva')}")
        return ins
    raise ReError(f"{want} is not an instruction of {static_row.get('entry_rva')}")


def check_classification(row: dict[str, Any], known_rvas: set[str]) -> None:
    if row["classification"] in {"confirmed_graphics_dispatch", "bounded_candidates"} and not row.get("reviewed"):
        raise ReError(f"{row['classification']} requires a reviewed closed target list")
    for rva in row.get("target_evidence") or []:
        if norm_rva(rva) not in known_rvas:
            raise ReError(f"evidence RVA {rva} is not in the validated function")


def classify_static_site(static_row: dict[str, Any], callsite_rva: str, *, prior: dict[str, Any] | None) -> dict[str, Any]:
    insns = static_row.get("instructions") or []
    ins = site_instruction(static_row, callsite_rva)
    index = next(i for i, item in enumerate(insns) if norm_rva(item["instruction_rva"]) == norm_rva(callsite_rva))
    auto = trace_call_operand(insns, index)
    row = {
        "caller_rva": norm_rva(static_row["entry_rva"]),
        "callsite_rva": norm_rva(callsite_rva),
        "classification": auto["classification"],
        "target_evidence": auto["target_evidence"],
        "reason": auto["reason"],
        "confidence": auto["confidence"],
        "mechanism_id": auto["mechanism_id"],
        "reviewed": False,
    }
    if auto.get("operand_source"):
        row["operand_source"] = auto["operand_source"]
    if prior and prior.get("reviewed"):
        row.update(
            {
                "classification": prior["classification"],
                "target_evidence": prior.get("target_evidence") or [],
                "reason": prior.get("reason") or "",
                "confidence": prior.get("confidence") or "",
                "mechanism_id": prior.get("mechanism_id") or row["mechanism_id"],
                "reviewed": True,
            }
        )
    check_classification(row, evidence_rvas(static_row))
    return row


def _load_static_by_rva(exp: Path, summary: dict[str, Any], needed: set[str]) -> dict[str, dict[str, Any]]:
    plan = load_batch_plan(exp)
    inventory = load_inventory_index(exp)
    callsites = load_callsites_by_caller(exp)
    found: dict[str, dict[str, Any]] = {}
    batches = plan.get("batches") or {}
    for batch_id, rvas in sorted(batches.items()):
        if not any(norm_rva(rva) in needed for rva in rvas):
            continue
        static_text, decomp_text, receipt = read_zip_package(exp / "renderer_snapshot" / "v1" / "batches" / f"{batch_id}.zip")
        validate_batch_package(
            batch_id=batch_id,
            static_text=static_text,
            decomp_text=decomp_text,
            receipt=receipt,
            rvas=rvas,
            inventory=inventory,
            callsites_by_caller=callsites,
            plan=plan,
            summary=summary,
        )
        for line in static_text.splitlines():
            if not line.strip():
                continue
            row = json.loads(line)
            key = norm_rva(row["entry_rva"])
            if key in needed:
                found[key] = row
        for line in decomp_text.splitlines():
            if not line.strip():
                continue
            row = json.loads(line)
            key = norm_rva(row["entry_rva"])
            if key in found and row.get("status") == "success":
                found[key]["pseudocode"] = row.get("pseudocode") or ""
    missing = needed - set(found)
    if missing:
        raise ReError(f"snapshot missing selected functions: {sorted(missing)[:5]}")
    return found


def _effort(bundle: dict[str, Any], static_by_rva: dict[str, dict[str, Any]]) -> dict[str, Any]:
    candidates = bundle["direct_gl_candidates"]
    total_lines = 0
    counted = 0
    total_ins = 0
    for row in candidates:
        total_ins += row.get("instruction_count") or 0
        text = (static_by_rva.get(row["caller_rva"]) or {}).get("pseudocode") or ""
        if text:
            total_lines += len(text.splitlines())
            counted += 1
    surface = len(bundle["implementation_surface"])
    summary = (
        f"Minimum direct-GL set: {len(candidates)} functions and {total_ins} instructions. "
        f"Decompiled lines for that set, where the snapshot has a body: {total_lines} across {counted} functions. "
        f"The implementation surface is {surface} functions (the `_Gfx*` cut plus behind-cut and shared helpers) "
        "and is not classified as retain or replace. "
        "Faithful C++ and Metal infrastructure are not estimated. Shader conversion is deferred to stage D."
    )
    return {
        "status": "descriptive",
        "direct_gl_candidate_count": len(candidates),
        "direct_gl_instructions": total_ins,
        "decompiled_lines": total_lines,
        "decompiled_functions": counted,
        "implementation_surface_count": surface,
        "faithful_cpp": "not_estimated",
        "metal_infrastructure": "not_estimated",
        "shader_conversion": "deferred to stage D",
        "summary": summary,
    }


def apply_indirect(
    bundle: dict[str, Any],
    static_by_rva: dict[str, dict[str, Any]],
    prior_sites: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    prior = {(row["caller_rva"], row["callsite_rva"]): row for row in prior_sites}
    rows = []
    for site in bundle["indirect_sites"]:
        static = static_by_rva.get(site["caller_rva"])
        if static is None:
            raise ReError(f"no snapshot row for {site['caller_rva']}")
        rows.append(
            classify_static_site(
                static,
                site["callsite_rva"],
                prior=prior.get((site["caller_rva"], site["callsite_rva"])),
            )
        )
    slot_sources = sum(1 for row in rows if row.get("operand_source"))
    bundle["indirect_dispatch_coverage"] = {
        "selected_sites": len(rows),
        "reviewed": sum(1 for row in rows if row["reviewed"]),
        "confirmed_graphics_dispatch": sum(1 for row in rows if row["classification"] == "confirmed_graphics_dispatch"),
        "confirmed_non_graphics": sum(1 for row in rows if row["classification"] == "confirmed_non_graphics"),
        "bounded_candidates": sum(1 for row in rows if row["classification"] == "bounded_candidates"),
        "unresolved": sum(1 for row in rows if row["classification"] == "unresolved"),
        "operand_source_slots": slot_sources,
        # shortcut: names the two callsites checked on this pinned binary; re-read them if the snapshot changes.
        "note": (
            "Counts of indirect sites only. They do not measure shader behavior, "
            "resource lifetimes, Metal init, or presentation. "
            f"{slot_sources} unresolved sites record an operand_source slot address. "
            "That address is not a closed set of call targets. "
            "A slot-load site and a _GfxInit displacement call were checked; "
            "their machine operands do not name the callee."
        ),
    }
    bundle["effort"] = _effort(bundle, static_by_rva)
    return rows


def assert_prior_matches(prior: dict[str, Any], digest: str) -> None:
    if prior.get("inventory_bundle_digest") != digest:
        raise ReError("stored census digest does not match the live inventory")


def _scope_rows(scope_dir: Path) -> list[dict[str, Any]]:
    path = scope_dir / "renderer-functions.jsonl"
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def _thunk_symbols(scope_dir: Path) -> list[str]:
    path = scope_dir / "graphics-boundary-thunks.jsonl"
    return [
        json.loads(line)["name_mangled"]
        for line in path.read_text().splitlines()
        if line.strip()
    ]


def _export(args: argparse.Namespace) -> Path:
    if args.export_dir:
        return args.export_dir
    return resolve_export_dir(local_binary_root(binary_sha256()), args.baseline_revision)


def _window_pseudocode(exp: Path, summary: dict[str, Any], graph: CallGraph) -> str | None:
    hits = [rva for rva, row in graph.functions.items() if fn_name(row) == WINDOW_NAME]
    if len(hits) != 1:
        return None
    key = norm_rva(hits[0])
    plan = load_batch_plan(exp)
    in_snapshot = {
        norm_rva(rva)
        for rvas in (plan.get("batches") or {}).values()
        for rva in rvas
    }
    if key not in in_snapshot:
        return None
    loaded = _load_static_by_rva(exp, summary, {key})
    return loaded[key].get("pseudocode") or ""


def cmd_direct(args: argparse.Namespace) -> int:
    scope_dir = Path(args.scope_dir) if args.scope_dir else DEFAULT_SCOPE_DIR
    exp = _export(args)
    summary, digest, _ordered = validate_scope(
        scope_dir=scope_dir, exp=exp, baseline_revision=args.baseline_revision
    )
    graph = load_call_graph(exp / "inventory")
    bundle = build_census(
        graph,
        scope_rows=_scope_rows(scope_dir),
        thunk_symbols=_thunk_symbols(scope_dir),
        window_text=_window_pseudocode(exp, summary, graph),
    )
    out = Path(args.out_dir) if args.out_dir else DEFAULT_OUT
    publish_census(out, bundle, computed_digest=digest, expected_digest=summary["inventory_bundle_digest"])
    print(
        json.dumps(
            {
                "boundary_verdict": bundle["boundary_verdict"],
                "direct_gl_caller_count": bundle["direct_gl_caller_count"],
                "outside_gfx_gl_callers": len(bundle["outside_gfx_gl_callers"]),
                "cut_function_count": bundle["cut_function_count"],
                "selected_indirect_sites": bundle["indirect_dispatch_coverage"]["selected_sites"],
                "written": str(out),
            },
            indent=2,
        )
    )
    return 0


def cmd_indirect(args: argparse.Namespace) -> int:
    scope_dir = Path(args.scope_dir) if args.scope_dir else DEFAULT_SCOPE_DIR
    exp = _export(args)
    summary, digest, _ordered = validate_scope(
        scope_dir=scope_dir, exp=exp, baseline_revision=args.baseline_revision
    )
    out = Path(args.out_dir) if args.out_dir else DEFAULT_OUT
    prior_path = out / "boundary.json"
    if not prior_path.is_file():
        raise ReError(f"missing {prior_path}; run direct first")
    prior = json.loads(prior_path.read_text())
    graph = load_call_graph(exp / "inventory")
    fresh = build_census(
        graph,
        scope_rows=_scope_rows(scope_dir),
        thunk_symbols=_thunk_symbols(scope_dir),
        window_text=_window_pseudocode(exp, summary, graph),
    )
    assert_prior_matches(prior, digest)
    plan = load_batch_plan(exp)
    in_snapshot = {
        norm_rva(rva)
        for rvas in (plan.get("batches") or {}).values()
        for rva in rvas
    }
    needed = {site["caller_rva"] for site in fresh["indirect_sites"]}
    needed |= {rva for rva, row in graph.functions.items() if is_gfx_helper(row)}
    needed |= {row["caller_rva"] for row in fresh["outside_gfx_gl_callers"]}
    needed &= in_snapshot
    static_by_rva = _load_static_by_rva(exp, summary, needed)
    prior_sites = []
    sites_path = out / "indirect-sites.jsonl"
    if sites_path.is_file():
        prior_sites = [json.loads(line) for line in sites_path.read_text().splitlines() if line.strip()]
    rows = apply_indirect(fresh, static_by_rva, prior_sites)
    evidence_dir = exp / "renderer_snapshot" / "v1" / "logs"
    evidence_dir.mkdir(parents=True, exist_ok=True)
    evidence_rows = []
    for site in fresh["indirect_sites"]:
        ins = site_instruction(static_by_rva[site["caller_rva"]], site["callsite_rva"])
        evidence_rows.append(
            {
                "caller_rva": site["caller_rva"],
                "callsite_rva": site["callsite_rva"],
                "mnemonic": ins.get("mnemonic"),
                "operands": ins.get("operands"),
                "references_from": ins.get("references_from") or [],
                "pcode_is_call": ins.get("pcode_is_call"),
                "flow_type_is_call": ins.get("flow_type_is_call"),
            }
        )
    (evidence_dir / "indirect-evidence.jsonl").write_text(
        "".join(json.dumps(row, sort_keys=True) + "\n" for row in evidence_rows)
    )
    publish_census(out, fresh, computed_digest=digest, expected_digest=summary["inventory_bundle_digest"])
    sites_path.write_text("".join(json.dumps(row, sort_keys=True) + "\n" for row in rows))
    print(
        json.dumps(
            {
                "boundary_verdict": fresh["boundary_verdict"],
                "indirect_dispatch_coverage": fresh["indirect_dispatch_coverage"],
                "written": str(out),
            },
            indent=2,
        )
    )
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    for name, func in (("direct", cmd_direct), ("indirect", cmd_indirect)):
        command = sub.add_parser(name)
        command.add_argument("--baseline-revision", default="auto-v1-20261013")
        command.add_argument("--export-dir", type=Path, default=None)
        command.add_argument("--out-dir", type=Path, default=None)
        command.add_argument("--scope-dir", type=Path, default=None)
        command.set_defaults(func=func)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except (ReError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
