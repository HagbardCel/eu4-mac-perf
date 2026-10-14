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
from reverse_engineering.export_identity import assert_qualification_current
from reverse_engineering.paths import RE_DIR, ReError, binary_sha256, local_binary_root, resolve_export_dir

REPORTS = RE_DIR / "reports"

# OpenGL API names are case-sensitive: gl followed by uppercase.
OPENGL_RE = re.compile(r"^_?gl[A-Z]")
GFX_RE = re.compile(r"Gfx|OpenGL|SShader|CGraphics|CPdxMesh|CEU3Graphical|TextureGFX|SGfxEffect", re.I)

SUBSYSTEM_RULES: list[tuple[str, str, re.Pattern[str]]] = [
    (
        "B0",
        "confirmed",
        re.compile(r"SShaderOpenGL|OpenGL|SVertexBufferOpenGL|(?-i:gl[A-Z])", re.I),
    ),
    ("B2", "probable", re.compile(r"CEU3GraphicalMap|GraphicalMap|CCountryName|CPdxMapBorderLayer", re.I)),
    ("B3", "probable", re.compile(r"BitmapFont|CEU3Idler", re.I)),
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


def edges_by_caller(edges: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for edge in edges:
        caller = edge.get("caller_rva")
        if caller:
            grouped[caller].append(edge)
    return grouped


def classify_function(
    row: dict[str, Any], fn_edges: list[dict[str, Any]], pseudocode: str | None
) -> dict[str, Any]:
    identity_text = " ".join(
        filter(None, [row.get("name_mangled"), row.get("name_demangled"), row.get("namespace")])
    )
    tags: list[str] = []
    for edge in fn_edges:
        sym = edge.get("ultimate_external_symbol") or edge.get("callee_external_symbol") or ""
        if OPENGL_RE.match(sym):
            res = edge.get("resolution")
            if res == "external_via_stub":
                tags.append("opengl_via_stub")
            elif res == "thunk":
                tags.append("opengl_via_thunk")
            else:
                tags.append("direct_opengl_caller")
        if edge.get("resolution") == "unresolved_indirect":
            tags.append("unresolved_indirect_callsite")
    primary = "U"
    confidence = "possible"
    for subsystem, conf, pattern in SUBSYSTEM_RULES:
        if pattern.search(identity_text):
            primary = subsystem
            confidence = conf
            break
    if row.get("kind") == "thunk" and primary == "U" and GFX_RE.search(identity_text):
        tags.append("graphics_thunk")
    if GFX_RE.search(identity_text) and primary == "U":
        primary = "S"
        confidence = "possible"
    return {"primary_subsystem": primary, "confidence": confidence, "tags": sorted(set(tags))}


def subsystem_metric_eligible(row: dict[str, Any], cls: dict[str, Any]) -> bool:
    if row.get("kind") not in ("internal", "thunk"):
        return False
    if cls["primary_subsystem"] == "U":
        return False
    if cls["confidence"] == "possible" and cls["primary_subsystem"] not in ("S",):
        return False
    return True


def subsystem_bytes(functions: list[dict], classifications: dict[str, dict]) -> dict[str, Any]:
    by_sub: dict[str, list[tuple[int, int]]] = defaultdict(list)
    by_conf: dict[str, list[tuple[int, int]]] = defaultdict(list)
    counts: dict[str, int] = defaultdict(int)
    for row in functions:
        cls = classifications[row["entry_rva"]]
        if not subsystem_metric_eligible(row, cls):
            continue
        intervals = intervals_from_ranges(row.get("instruction_ranges", []))
        by_sub[cls["primary_subsystem"]].extend(intervals)
        by_conf[cls["confidence"]].extend(intervals)
        counts[cls["primary_subsystem"]] += 1
    table = {}
    for key, intervals in by_sub.items():
        table[key] = {"functions": counts[key], "code_bytes": union_size(intervals)}
    return {"by_subsystem": table, "by_confidence": {k: union_size(v) for k, v in by_conf.items()}}


def _opengl_symbol(edge: dict[str, Any]) -> str:
    return edge.get("ultimate_external_symbol") or edge.get("callee_external_symbol") or ""


def coupling_stats(edges: list[dict[str, Any]]) -> dict[str, Any]:
    owners_direct = set()
    owners_stub = set()
    owners_thunk = set()
    for edge in edges:
        sym = _opengl_symbol(edge)
        if not OPENGL_RE.match(sym):
            continue
        caller = edge.get("caller_rva")
        res = edge.get("resolution")
        if res == "external_via_stub":
            owners_stub.add(caller)
        elif res == "thunk":
            owners_thunk.add(caller)
        elif res == "direct_external":
            owners_direct.add(caller)
    unresolved = sum(1 for e in edges if e.get("resolution") == "unresolved_indirect")
    return {
        "internal_functions_direct_opengl": len(owners_direct),
        "internal_functions_opengl_via_stub": len(owners_stub),
        "internal_functions_opengl_via_thunk": len(owners_thunk),
        "unresolved_indirect_call_sites": unresolved,
    }


def build_census(exp: Path) -> dict[str, Any]:
    inv = exp / "inventory"
    coverage = json.loads((inv / "coverage.json").read_text())
    functions = load_functions(inv)
    edges = load_edges(inv)
    by_caller = edges_by_caller(edges)
    pseudocode: dict[str, str] = {}

    for batch in sorted((exp / "decompiled").glob("batch-*.jsonl")):
        if not re.fullmatch(r"batch-\d{5}\.jsonl", batch.name):
            continue
        for line in batch.read_text().splitlines():
            if not line.strip():
                continue
            row = json.loads(line)
            if row.get("status") == "success":
                pseudocode[row["entry_rva"]] = row.get("pseudocode", "")
    classifications = {
        row["entry_rva"]: classify_function(
            row, by_caller.get(row["entry_rva"], []), pseudocode.get(row["entry_rva"])
        )
        for row in functions
    }
    sizes = subsystem_bytes(functions, classifications)
    reconcile_path = exp / "logs" / "gfx-reconcile.json"
    reconcile = json.loads(reconcile_path.read_text()) if reconcile_path.is_file() else {}
    decomp_report = exp / "logs" / "decompile-report.json"
    decomp = json.loads(decomp_report.read_text()) if decomp_report.is_file() else {}
    return {
        "measurement_source": "local_ghidra_export",
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
    parser.add_argument("--allow-unqualified", action="store_true")
    args = parser.parse_args(argv)
    try:
        exp = args.export_dir or resolve_export_dir(local_binary_root(binary_sha256()), args.baseline_revision)
        provenance_path = exp / "provenance.json"
        provenance = json.loads(provenance_path.read_text()) if provenance_path.is_file() else {}
        if args.write_reports:
            if args.allow_unqualified:
                census = build_census(exp)
                local = exp / ".local" / "renderer-size-census.json"
                local.parent.mkdir(parents=True, exist_ok=True)
                local.write_text(json.dumps(census, indent=2) + "\n")
                print(f"wrote diagnostic census to {local}")
            else:
                from reverse_engineering.corpus import SMOKE_ANCHORS, mandatory_anchors_check

                assert_qualification_current(
                    exp,
                    provenance,
                    lambda: mandatory_anchors_check(exp),
                    smoke_needles=SMOKE_ANCHORS,
                )
                census = build_census(exp)
                write_reports(census)
                print(f"wrote reports under {REPORTS}")
        else:
            census = build_census(exp)
            print(json.dumps(census, indent=2))
        return 0
    except ReError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
