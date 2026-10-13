"""Address-first reconciliation of historical GfxDraw* call sites."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from reverse_engineering.intervals import intervals_from_ranges, parse_rva

GFX_OPERATIONS = {
    "GfxDraw": "_GfxDraw",
    "GfxDrawIndexed": "_GfxDrawIndexed",
    "GfxDrawInstanced": "_GfxDrawInstanced",
    "GfxDrawIndexedInstanced": "_GfxDrawIndexedInstanced",
    "GfxDraw2dLines": "_GfxDraw2dLines",
}


def load_functions_by_rva(inv: Path) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for line in (inv / "functions.jsonl").read_text().splitlines():
        if line.strip():
            row = json.loads(line)
            out[row["entry_rva"]] = row
    return out


def gfx_helper_rvas(functions: dict[str, dict[str, Any]]) -> dict[str, str]:
    """Map operation name -> helper entry_rva."""
    op_to_rva: dict[str, str] = {}
    for rva, row in functions.items():
        name = row.get("name_mangled") or ""
        for op, sym in GFX_OPERATIONS.items():
            if name == sym or name.endswith(op):
                op_to_rva[op] = rva
    return op_to_rva


def function_containing_rva(functions: dict[str, dict[str, Any]], addr: int) -> str | None:
    for rva, row in functions.items():
        for start, end in intervals_from_ranges(row.get("instruction_ranges", [])):
            if start <= addr < end:
                return rva
    return None


def resolve_target_rva(edge: dict[str, Any], functions: dict[str, dict[str, Any]]) -> str | None:
    callee = edge.get("callee_internal_rva")
    if callee:
        return callee
    return None


def reconcile_draw_sites(draw: dict[str, Any], inv: Path) -> dict[str, Any]:
    functions = load_functions_by_rva(inv)
    helpers = gfx_helper_rvas(functions)
    edges = [
        json.loads(line)
        for line in (inv / "call_edges.jsonl").read_text().splitlines()
        if line.strip()
    ]
    by_callsite: dict[str, list[dict[str, Any]]] = {}
    for edge in edges:
        by_callsite.setdefault(edge["callsite_rva"], []).append(edge)

    results: list[dict[str, Any]] = []
    counts = {k: 0 for k in ("matched", "target_mismatch", "missing_callsite", "unresolved", "owner_mismatch")}

    for site in draw["direct_sites"]:
        callsite = f"0x{site['callsite_offset']:016x}"
        op = site["operation"]
        expected_helper = helpers.get(op)
        llvm_owner = site.get("owner_symbol")
        site_edges = by_callsite.get(callsite, [])
        if not site_edges:
            counts["missing_callsite"] += 1
            results.append({"status": "missing_callsite", "callsite_rva": callsite, "operation": op})
            continue
        owner_rva = function_containing_rva(functions, site["callsite_offset"])
        owner_row = functions.get(owner_rva or "", {})
        owner_mangled = owner_row.get("name_mangled")
        owner_note = None
        if llvm_owner and owner_mangled and llvm_owner != owner_mangled:
            owner_note = {"llvm": llvm_owner, "ghidra": owner_mangled}

        resolved_target: str | None = None
        unresolved = False
        for edge in site_edges:
            res = edge.get("resolution")
            if res == "unresolved_indirect":
                unresolved = True
                continue
            target = resolve_target_rva(edge, functions)
            if target:
                resolved_target = target
        if unresolved and not resolved_target:
            counts["unresolved"] += 1
            results.append(
                {
                    "status": "unresolved",
                    "callsite_rva": callsite,
                    "operation": op,
                    "owner_note": owner_note,
                }
            )
            continue
        if expected_helper and resolved_target == expected_helper:
            if owner_note:
                counts["owner_mismatch"] += 1
                results.append(
                    {
                        "status": "owner_mismatch",
                        "callsite_rva": callsite,
                        "operation": op,
                        "owner_note": owner_note,
                        "target_rva": resolved_target,
                    }
                )
            else:
                counts["matched"] += 1
                results.append({"status": "matched", "callsite_rva": callsite, "operation": op})
        else:
            counts["target_mismatch"] += 1
            results.append(
                {
                    "status": "target_mismatch",
                    "callsite_rva": callsite,
                    "operation": op,
                    "expected_helper_rva": expected_helper,
                    "resolved_target_rva": resolved_target,
                    "owner_note": owner_note,
                }
            )

    return {
        "draw_callers_count": len(draw["direct_sites"]),
        "summary": counts,
        "sites": results,
    }
