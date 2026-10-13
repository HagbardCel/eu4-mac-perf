#!/usr/bin/env python3
"""Renderer census from classified corpus metrics."""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

from reverse_engineering.intervals import intervals_from_ranges, union_size
from reverse_engineering.paths import RE_DIR, ReError, binary_sha256, local_binary_root, resolve_export_dir

REPORTS = RE_DIR / "reports"

OPENGL_RE = re.compile(r"^(_)?gl[A-Z]", re.I)
GFX_RE = re.compile(r"Gfx|OpenGL|SShader|CGraphics|CPdxMesh|CEU3Graphical|TextureGFX|SGfxEffect", re.I)

SUBSYSTEM_RULES: list[tuple[str, str, re.Pattern[str]]] = [
    ("B0", "confirmed", re.compile(r"SShaderOpenGL|OpenGL|SVertexBufferOpenGL|gl[A-Z]", re.I)),
    ("B2", "probable", re.compile(r"CEU3GraphicalMap|GraphicalMap|CCountryName", re.I)),
    ("B3", "probable", re.compile(r"BitmapFont|CEU3Idler|Gui|UI", re.I)),
    ("B1", "probable", re.compile(r"CGraphics|GfxDeferred|SGfxEffect|TextureGFX|ConstantBufferGFX", re.I)),
]


def load_functions(inventory_dir: Path) -> list[dict[str, Any]]:
    rows = []
    for line in (inventory_dir / "functions.jsonl").read_text().splitlines():
        if line.strip():
            rows.append(json.loads(line))
    return rows


def load_edges(inventory_dir: Path) -> list[dict[str, Any]]:
    path = inventory_dir / "call_edges.jsonl"
    if not path.is_file():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def classify_function(row: dict[str, Any], edges: list[dict[str, Any]], pseudocode: str | None) -> dict[str, Any]:
    rva = row["entry_rva"]
    text = " ".join(
        filter(
            None,
            [
                row.get("name_mangled"),
                row.get("name_demangled"),
                row.get("namespace"),
                pseudocode or "",
            ],
        )
    )
    tags: list[str] = []
    fn_edges = [e for e in edges if e.get("caller_rva") == rva]
    for edge in fn_edges:
        sym = edge.get("callee_external_symbol") or ""
        if OPENGL_RE.match(sym):
            if edge.get("resolution") == "external_via_stub":
                tags.append("opengl_via_stub")
            else:
                tags.append("direct_opengl_caller")
        if edge.get("resolution") == "unresolved_indirect":
            tags.append("unresolved_indirect_callsite")
    primary = "U"
    confidence = "possible"
    for subsystem, conf, pattern in SUBSYSTEM_RULES:
        if pattern.search(text):
            primary = subsystem
            confidence = conf
            break
    if row.get("kind") == "thunk" and "opengl" in text.lower():
        primary = "B0"
        confidence = "probable"
        tags.append("graphics_thunk")
    if GFX_RE.search(text) and primary == "U":
        primary = "S"
        confidence = "possible"
    return {"primary_subsystem": primary, "confidence": confidence, "tags": sorted(set(tags))}


def subsystem_bytes(functions: list[dict], classifications: dict[str, dict]) -> dict[str, Any]:
    by_sub: dict[str, list[tuple[int, int]]] = defaultdict(list)
    by_conf: dict[str, list[tuple[int, int]]] = defaultdict(list)
    for row in functions:
        if row.get("kind") not in ("internal", "thunk"):
            continue
        cls = classifications[row["entry_rva"]]
        if cls["confidence"] == "possible" and cls["primary_subsystem"] != "S":
            continue
        intervals = intervals_from_ranges(row.get("instruction_ranges", []))
        by_sub[cls["primary_subsystem"]].extend(intervals)
        by_conf[cls["confidence"]].extend(intervals)
    table = {}
    for key, intervals in by_sub.items():
        table[key] = {
            "functions": sum(
                1 for r in functions if classifications[r["entry_rva"]]["primary_subsystem"] == key
            ),
            "code_bytes": union_size(intervals),
        }
    return {"by_subsystem": table, "by_confidence": {k: union_size(v) for k, v in by_conf.items()}}


def coupling_stats(edges: list[dict[str, Any]]) -> dict[str, Any]:
    owners_direct = set()
    owners_stub = set()
    for edge in edges:
        sym = edge.get("callee_external_symbol") or ""
        if not OPENGL_RE.match(sym):
            continue
        caller = edge.get("caller_rva")
        if edge.get("resolution") == "external_via_stub":
            owners_stub.add(caller)
        elif edge.get("resolution") in ("direct_external", "thunk"):
            owners_direct.add(caller)
    unresolved = sum(1 for e in edges if e.get("resolution") == "unresolved_indirect")
    return {
        "internal_functions_direct_opengl": len(owners_direct),
        "internal_functions_opengl_via_stub": len(owners_stub),
        "unresolved_indirect_call_sites": unresolved,
    }


def build_census(exp: Path) -> dict[str, Any]:
    inv = exp / "inventory"
    coverage = json.loads((inv / "coverage.json").read_text())
    functions = load_functions(inv)
    edges = load_edges(inv)
    pseudocode: dict[str, str] = {}
    for batch in (exp / "decompiled").glob("batch-*.jsonl"):
        for line in batch.read_text().splitlines():
            if not line.strip():
                continue
            row = json.loads(line)
            if row.get("status") == "success":
                pseudocode[row["entry_rva"]] = row.get("pseudocode", "")
    classifications = {
        row["entry_rva"]: classify_function(row, edges, pseudocode.get(row["entry_rva"]))
        for row in functions
    }
    sizes = subsystem_bytes(functions, classifications)
    reconcile_path = exp / "logs" / "gfx-reconcile.json"
    reconcile = json.loads(reconcile_path.read_text()) if reconcile_path.is_file() else {}
    decomp_report = exp / "logs" / "decompile-report.json"
    decomp = json.loads(decomp_report.read_text()) if decomp_report.is_file() else {}
    return {
        "measurement_source": "local_ghidra_export" if "exports" in str(exp) and "re_fixtures" not in str(exp) else "synthetic_integration_fixture",
        "coverage": coverage,
        "renderer_subsystems": sizes,
        "opengl_coupling": coupling_stats(edges),
        "decompilation_quality": decomp.get("decompilation_stats", {}),
        "gfx_reconciliation": reconcile,
        "methodology": "Positive-evidence classification; union of instruction-byte intervals; see reverse_engineering/README.md",
    }


def write_reports(census: dict[str, Any]) -> None:
    REPORTS.mkdir(parents=True, exist_ok=True)
    (REPORTS / "renderer-size-census.json").write_text(json.dumps(census, indent=2) + "\n")
    cov = census["coverage"]
    lines = [
        "# Renderer size census",
        "",
        "Aggregate metrics from Ghidra static export (no pseudocode in this report).",
        "",
        "## Executable coverage",
        "",
        f"- E (executable section bytes): {cov.get('E_executable_section_bytes')}",
        f"- D (decoded instruction bytes): {cov.get('D_decoded_instruction_bytes')}",
        f"- F_internal: {cov.get('F_internal_bytes')}",
        f"- F_thunk: {cov.get('F_thunk_bytes')}",
        f"- F_all (recovered internal+thunk): {cov.get('F_all_bytes')}",
        f"- C_section (F_all/E): {cov.get('C_section')}",
        f"- C_decoded (F_all/D): {cov.get('C_decoded')}",
        "",
        "## Subsystem sizes (confirmed + probable)",
        "",
        "| Subsystem | Code bytes |",
        "|-----------|------------|",
    ]
    for key, row in sorted(census["renderer_subsystems"]["by_subsystem"].items()):
        lines.append(f"| {key} | {row.get('code_bytes', 0)} |")
    lines.extend(
        [
            "",
            "## OpenGL coupling",
            "",
            json.dumps(census["opengl_coupling"], indent=2),
            "",
            "## Gfx draw-site reconciliation",
            "",
            json.dumps(census.get("gfx_reconciliation", {}), indent=2),
        ]
    )
    (REPORTS / "renderer-size-census.md").write_text("\n".join(lines) + "\n")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Generate renderer-size census reports")
    parser.add_argument("--baseline-revision", default=None)
    parser.add_argument("--export-dir", type=Path, default=None, help="override export tree (tests)")
    parser.add_argument("--write-reports", action="store_true")
    args = parser.parse_args(argv)
    try:
        exp = args.export_dir or resolve_export_dir(local_binary_root(binary_sha256()), args.baseline_revision)
        census = build_census(exp)
        if args.write_reports:
            write_reports(census)
            print(f"wrote reports under {REPORTS}")
        else:
            print(json.dumps(census, indent=2))
        return 0
    except ReError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
