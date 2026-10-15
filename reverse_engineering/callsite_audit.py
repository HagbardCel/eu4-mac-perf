#!/usr/bin/env python3
"""Read-only callsite census and reconciliation against inventory call edges."""

from __future__ import annotations

import argparse
import json
import re
import shutil
import sys
import tempfile
from collections import defaultdict
from pathlib import Path
from typing import Any

from reverse_engineering.corpus import (
    analyze_headless,
    load_ghidra_meta,
    load_provenance,
    script_hash,
    verify_binary,
)
from reverse_engineering.gfx_subgraph import PINNED_BORDER_RVA
from reverse_engineering.manifest import inventory_bundle_digest
from reverse_engineering.paths import (
    ReError,
    binary_sha256,
    local_binary_root,
    resolve_export_dir,
)

RE_DIR = Path(__file__).resolve().parent
GRAPHICS_SEEDS_PATH = RE_DIR / "graphics_seeds.json"
RVA_RE = re.compile(r"^0x[0-9a-f]{16}$")
AUDITS_CALLSITES_FILE = "callsites-v1.jsonl"
AUDITS_REPORT_FILE = "callsite-audit.json"
DIAGNOSTIC_SAMPLE_CAP = 10
DRAW_BORDERS_DEMANGLED = "CPdxMapBorderLayer::DrawBorders"

CALLSITE_FIELDS = (
    "callsite_rva",
    "caller_rva",
    "mnemonic",
    "pcode_kind",
    "flow_type_is_call",
    "pcode_is_call",
    "flow_instruction_class",
    "classification_conflict",
    "classifier_status",
    "instruction_class",
    "call_reference_count",
)
INSTRUCTION_CLASSES = frozenset({"direct", "indirect", "ambiguous"})
CLASSIFIER_STATUSES = frozenset({"agreement", "disagreement", "pcode_unavailable"})


def load_graphics_seeds() -> dict[str, Any]:
    return json.loads(GRAPHICS_SEEDS_PATH.read_text())


def mandatory_anchor_symbols() -> list[str]:
    seeds = load_graphics_seeds()
    return list(seeds.get("mandatory_identity", []))


# EU IV inventory often stores short Ghidra names in name_mangled; fall back to demangled.
ANCHOR_DEMANGLED_BY_SEED: dict[str, str] = {
    "__ZN13SShaderOpenGL6SetAllEv": "SShaderOpenGL::SetAll",
    "_ZN14CPdxMeshObject13RenderBucketsEP9CGraphicsP21GfxDeferredContextGFXPK7CCameraibb": (
        "CPdxMeshObject::RenderBuckets"
    ),
    "__ZN16CEU3GraphicalMap6RenderEP21GfxDeferredContextGFXRK10CEU3Camerafb": (
        "CEU3GraphicalMap::Render"
    ),
}


def normalize_sha256(value: str | None) -> str:
    if not value:
        raise ReError("executable_sha256 missing from scan-meta")
    return value.strip().lower()


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for line in path.read_text().splitlines():
        if line.strip():
            rows.append(json.loads(line))
    return rows


def validate_callsite_row(row: dict[str, Any], line_no: int) -> None:
    for key in CALLSITE_FIELDS:
        if key not in row:
            raise ReError(f"callsite line {line_no}: missing field {key}")
    rva = row["callsite_rva"]
    if not RVA_RE.match(rva):
        raise ReError(f"callsite line {line_no}: invalid callsite_rva {rva}")
    caller = row["caller_rva"]
    if caller is not None and not RVA_RE.match(caller):
        raise ReError(f"callsite line {line_no}: invalid caller_rva {caller}")
    if row["instruction_class"] not in INSTRUCTION_CLASSES:
        raise ReError(f"callsite line {line_no}: bad instruction_class")
    if row["classifier_status"] not in CLASSIFIER_STATUSES:
        raise ReError(f"callsite line {line_no}: bad classifier_status")
    if not isinstance(row["flow_type_is_call"], bool):
        raise ReError(f"callsite line {line_no}: flow_type_is_call must be bool")
    if not isinstance(row["call_reference_count"], int):
        raise ReError(f"callsite line {line_no}: call_reference_count must be int")
    if not isinstance(row["pcode_is_call"], bool):
        raise ReError(f"callsite line {line_no}: pcode_is_call must be bool")
    if not isinstance(row["classification_conflict"], bool):
        raise ReError(f"callsite line {line_no}: classification_conflict must be bool")
    fic = row["flow_instruction_class"]
    if fic is not None and fic not in INSTRUCTION_CLASSES:
        raise ReError(f"callsite line {line_no}: bad flow_instruction_class")


def validate_census(callsites_path: Path, scan_meta: dict[str, Any]) -> list[dict[str, Any]]:
    if scan_meta.get("exporter_status") != "ok":
        raise ReError(f"exporter failed: {scan_meta.get('exporter_status')}")
    rows = load_jsonl(callsites_path)
    expected = scan_meta.get("total_callsites")
    if expected is None:
        raise ReError("scan-meta missing total_callsites")
    if len(rows) != int(expected):
        raise ReError(
            f"callsite count mismatch: jsonl has {len(rows)}, scan-meta says {expected}"
        )
    seen: set[str] = set()
    for i, row in enumerate(rows, start=1):
        validate_callsite_row(row, i)
        rva = row["callsite_rva"]
        if rva in seen:
            raise ReError(f"duplicate callsite_rva {rva}")
        seen.add(rva)
    prev = ""
    for row in rows:
        rva = row["callsite_rva"]
        if rva < prev:
            raise ReError("callsites-v1.jsonl not sorted by callsite_rva")
        prev = rva
    return rows


def validate_scan_meta(scan_meta: dict[str, Any]) -> None:
    required = (
        "total_instructions_scanned",
        "total_callsites",
        "pcode_decode_failures",
        "pcode_unimplemented_count",
        "pcode_failure_sample_rvas",
        "executable_sha256",
        "program_name",
        "image_base",
        "exporter_status",
    )
    for key in required:
        if key not in scan_meta:
            raise ReError(f"scan-meta missing {key}")


def coverage_bucket(row: dict[str, Any], edges_at_site: list[dict[str, Any]]) -> str:
    if edges_at_site:
        return "matched"
    iclass = row["instruction_class"]
    if iclass == "direct":
        return "missing_direct"
    if iclass == "indirect":
        return "missing_indirect"
    return "missing_ambiguous"


def _append_sample(samples: list[str], rva: str) -> None:
    if len(samples) < DIAGNOSTIC_SAMPLE_CAP:
        samples.append(rva)


def reconcile_callsites(
    callsites: list[dict[str, Any]],
    edges: list[dict[str, Any]],
) -> dict[str, Any]:
    by_callsite: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for edge in edges:
        site = edge.get("callsite_rva")
        if site:
            by_callsite[site].append(edge)

    census_rvas = {row["callsite_rva"] for row in callsites}
    counts = {
        "matched": 0,
        "missing_direct": 0,
        "missing_indirect": 0,
        "missing_ambiguous": 0,
    }
    pcode_only_count = 0
    flow_only_count = 0
    flow_with_pcode_unavailable_count = 0
    classification_conflict_count = 0
    outside_recognized_function = 0
    pcode_only_sample: list[str] = []
    flow_only_sample: list[str] = []
    flow_unavail_sample: list[str] = []
    conflict_sample: list[str] = []

    for row in callsites:
        site = row["callsite_rva"]
        site_edges = by_callsite.get(site, [])
        counts[coverage_bucket(row, site_edges)] += 1

        status = row["classifier_status"]
        flow_is_call = row["flow_type_is_call"]
        pcode_is_call = row["pcode_is_call"]

        if status == "pcode_unavailable":
            if flow_is_call:
                flow_with_pcode_unavailable_count += 1
                _append_sample(flow_unavail_sample, site)
        elif pcode_is_call and not flow_is_call:
            pcode_only_count += 1
            _append_sample(pcode_only_sample, site)
        elif flow_is_call and not pcode_is_call:
            flow_only_count += 1
            _append_sample(flow_only_sample, site)

        if row.get("classification_conflict"):
            classification_conflict_count += 1
            _append_sample(conflict_sample, site)

        if row.get("caller_rva") is None:
            outside_recognized_function += 1

    n_callsites = len(callsites)
    partition_sum = sum(counts.values())
    if partition_sum != n_callsites:
        raise ReError(
            f"partition identity failed: {partition_sum} != {n_callsites} "
            f"({counts})"
        )

    classifier_disagreement_count = pcode_only_count + flow_only_count

    orphan_edge_callsites: list[str] = []
    for site in sorted(by_callsite):
        if site not in census_rvas:
            orphan_edge_callsites.append(site)

    return {
        "counts": counts,
        "total_callsites": n_callsites,
        "classifier_disagreement_count": classifier_disagreement_count,
        "pcode_only_callsites": pcode_only_count,
        "flow_only_callsites": flow_only_count,
        "flow_with_pcode_unavailable": flow_with_pcode_unavailable_count,
        "classification_conflict_count": classification_conflict_count,
        "classifier_samples": {
            "pcode_only_rvas": sorted(pcode_only_sample),
            "flow_only_rvas": sorted(flow_only_sample),
            "flow_with_pcode_unavailable_rvas": sorted(flow_unavail_sample),
            "classification_conflict_rvas": sorted(conflict_sample),
        },
        "outside_recognized_function": outside_recognized_function,
        "orphan_edge_callsites": orphan_edge_callsites,
        "orphan_edge_callsite_count": len(orphan_edge_callsites),
    }


def load_functions_index(inv: Path) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    path = inv / "functions.jsonl"
    for line in path.read_text().splitlines():
        if line.strip():
            row = json.loads(line)
            out[row["entry_rva"]] = row
    return out


def resolve_mandatory_anchors(functions: dict[str, dict[str, Any]]) -> dict[str, str]:
    """Map mandatory symbol -> entry_rva; raises if missing or ambiguous."""
    by_mangled: dict[str, list[str]] = defaultdict(list)
    by_demangled: dict[str, list[str]] = defaultdict(list)
    for rva, row in functions.items():
        name = row.get("name_mangled") or ""
        by_mangled[name].append(rva)
        demangled = row.get("name_demangled") or ""
        by_demangled[demangled].append(rva)
    resolved: dict[str, str] = {}
    for sym in mandatory_anchor_symbols():
        matches = list(by_mangled.get(sym, []))
        if not matches:
            demangled = ANCHOR_DEMANGLED_BY_SEED.get(sym)
            if demangled:
                matches = list(by_demangled.get(demangled, []))
        if len(matches) == 0:
            raise ReError(f"mandatory graphics anchor missing from inventory: {sym}")
        if len(matches) > 1:
            raise ReError(f"mandatory graphics anchor ambiguous in inventory: {sym}")
        resolved[sym] = matches[0]
    return resolved


def gfx_functions(functions: dict[str, dict[str, Any]]) -> dict[str, str]:
    """name_mangled -> entry_rva for _Gfx* symbols."""
    out: dict[str, str] = {}
    for rva, row in functions.items():
        name = row.get("name_mangled") or ""
        if name.startswith("_Gfx"):
            out[name] = rva
    return out


def _function_identity_matches_draw_borders(row: dict[str, Any]) -> bool:
    demangled = row.get("name_demangled") or ""
    if demangled == DRAW_BORDERS_DEMANGLED:
        return True
    mangled = row.get("name_mangled") or ""
    return mangled == "DrawBorders" and row.get("namespace") == "CPdxMapBorderLayer"


def resolve_draw_borders_entry(
    functions: dict[str, dict[str, Any]],
) -> tuple[str | None, str | None]:
    """Return (entry_rva, identity_warning)."""
    pinned_row = functions.get(PINNED_BORDER_RVA)
    if pinned_row is not None:
        if _function_identity_matches_draw_borders(pinned_row):
            return PINNED_BORDER_RVA, None
        return PINNED_BORDER_RVA, (
            f"pinned RVA {PINNED_BORDER_RVA} is "
            f"{pinned_row.get('name_demangled')!r}, expected {DRAW_BORDERS_DEMANGLED}"
        )
    matches = [
        rva
        for rva, row in functions.items()
        if _function_identity_matches_draw_borders(row)
    ]
    if len(matches) == 1:
        return matches[0], None
    if len(matches) > 1:
        return None, "multiple DrawBorders symbols in inventory"
    return None, "DrawBorders not found in inventory"


def unresolved_callsite_rvas(
    caller_callsites: list[dict[str, Any]],
    by_callsite: dict[str, list[dict[str, Any]]],
) -> list[str]:
    unresolved: list[str] = []
    for row in caller_callsites:
        site = row["callsite_rva"]
        edges = by_callsite.get(site, [])
        if not edges:
            unresolved.append(site)
            continue
        if all(
            (e.get("resolution") or "").startswith("unresolved")
            for e in edges
        ):
            unresolved.append(site)
    return sorted(unresolved)


def _caller_stats_report(
    name_mangled: str,
    entry_rva: str,
    caller_sites: list[dict[str, Any]],
    by_callsite: dict[str, list[dict[str, Any]]],
    *,
    identity_warning: str | None = None,
) -> dict[str, Any]:
    report: dict[str, Any] = {
        "name_mangled": name_mangled,
        "entry_rva": entry_rva,
        "direct_callsites": sum(1 for c in caller_sites if c["instruction_class"] == "direct"),
        "indirect_callsites": sum(
            1 for c in caller_sites if c["instruction_class"] == "indirect"
        ),
        "ambiguous_callsites": sum(
            1 for c in caller_sites if c["instruction_class"] == "ambiguous"
        ),
        "missing_graph_edges": sum(
            1 for c in caller_sites if not by_callsite.get(c["callsite_rva"])
        ),
        "unresolved_callsite_rvas": unresolved_callsite_rvas(caller_sites, by_callsite),
    }
    if identity_warning:
        report["identity_warning"] = identity_warning
    return report


def build_graphics_section(
    callsites: list[dict[str, Any]],
    edges: list[dict[str, Any]],
    functions: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    by_callsite: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for edge in edges:
        site = edge.get("callsite_rva")
        if site:
            by_callsite[site].append(edge)

    by_caller: dict[str | None, list[dict[str, Any]]] = defaultdict(list)
    for row in callsites:
        by_caller[row.get("caller_rva")].append(row)

    anchors = resolve_mandatory_anchors(functions)
    anchor_reports: list[dict[str, Any]] = []
    for sym, entry_rva in sorted(anchors.items(), key=lambda x: x[1]):
        anchor_reports.append(
            _caller_stats_report(
                sym,
                entry_rva,
                by_caller.get(entry_rva, []),
                by_callsite,
            )
        )

    gfx_reports: list[dict[str, Any]] = []
    for name, entry_rva in sorted(gfx_functions(functions).items(), key=lambda x: x[1]):
        gfx_reports.append(
            _caller_stats_report(
                name,
                entry_rva,
                by_caller.get(entry_rva, []),
                by_callsite,
            )
        )

    draw_rva, draw_warn = resolve_draw_borders_entry(functions)
    focused_renderers: list[dict[str, Any]] = []
    if draw_rva is not None:
        row = functions[draw_rva]
        label = row.get("name_demangled") or row.get("name_mangled") or DRAW_BORDERS_DEMANGLED
        focused_renderers.append(
            _caller_stats_report(
                label,
                draw_rva,
                by_caller.get(draw_rva, []),
                by_callsite,
                identity_warning=draw_warn,
            )
        )
    else:
        focused_renderers.append(
            {
                "name_mangled": DRAW_BORDERS_DEMANGLED,
                "entry_rva": None,
                "identity_warning": draw_warn,
                "direct_callsites": 0,
                "indirect_callsites": 0,
                "ambiguous_callsites": 0,
                "missing_graph_edges": 0,
                "unresolved_callsite_rvas": [],
            }
        )

    missing_by_caller: dict[str, int] = defaultdict(int)
    for row in callsites:
        caller = row.get("caller_rva")
        if not caller:
            continue
        if not by_callsite.get(row["callsite_rva"]):
            missing_by_caller[caller] += 1
    top_missing = sorted(
        [
            {
                "caller_rva": rva,
                "name_mangled": functions.get(rva, {}).get("name_mangled"),
                "missing_callsites": count,
            }
            for rva, count in missing_by_caller.items()
        ],
        key=lambda x: (-x["missing_callsites"], x["caller_rva"]),
    )[:25]

    return {
        "mandatory_anchors": anchor_reports,
        "gfx_helpers": gfx_reports,
        "focused_renderers": focused_renderers,
        "top_missing_by_function": top_missing,
    }


def build_audit_report(
    *,
    provenance: dict[str, Any],
    scan_meta: dict[str, Any],
    callsites: list[dict[str, Any]],
    reconciliation: dict[str, Any],
    graphics: dict[str, Any],
    ghidra_meta: dict[str, Any],
    pinned_sha: str,
) -> dict[str, Any]:
    inv_digest = provenance.get("inventory_bundle_digest")
    return {
        "provenance": {
            "baseline_revision": provenance.get("baseline_revision"),
            "binary_sha256": pinned_sha,
            "executable_sha256": normalize_sha256(scan_meta.get("executable_sha256")),
            "inventory_bundle_digest": inv_digest,
            "export_callsites_script_hash": script_hash("ExportCallsites.java"),
            "ghidra_project_dir": ghidra_meta.get("project_dir"),
            "ghidra_project_name": ghidra_meta.get("project_name"),
            "ghidra_program_name": ghidra_meta.get("program_name"),
        },
        "census_summary": {
            "total_instructions_scanned": scan_meta["total_instructions_scanned"],
            "total_callsites": scan_meta["total_callsites"],
            "direct_callsites": sum(
                1 for c in callsites if c["instruction_class"] == "direct"
            ),
            "indirect_callsites": sum(
                1 for c in callsites if c["instruction_class"] == "indirect"
            ),
            "ambiguous_callsites": sum(
                1 for c in callsites if c["instruction_class"] == "ambiguous"
            ),
            "pcode_decode_failures": scan_meta["pcode_decode_failures"],
            "pcode_unimplemented_count": scan_meta["pcode_unimplemented_count"],
            "pcode_failure_sample_rvas": scan_meta["pcode_failure_sample_rvas"],
            "classifier_disagreement_count": reconciliation[
                "classifier_disagreement_count"
            ],
            "pcode_only_callsites": reconciliation["pcode_only_callsites"],
            "flow_only_callsites": reconciliation["flow_only_callsites"],
            "flow_with_pcode_unavailable": reconciliation["flow_with_pcode_unavailable"],
            "classification_conflict_count": reconciliation["classification_conflict_count"],
            "outside_recognized_function": reconciliation[
                "outside_recognized_function"
            ],
        },
        "classifier_diagnostics": reconciliation["classifier_samples"],
        "graph_coverage": {
            "callsites_with_edges": reconciliation["counts"]["matched"],
            "missing_direct": reconciliation["counts"]["missing_direct"],
            "missing_indirect": reconciliation["counts"]["missing_indirect"],
            "missing_ambiguous": reconciliation["counts"]["missing_ambiguous"],
            "orphan_edge_callsite_count": reconciliation["orphan_edge_callsite_count"],
        },
        "reconciliation": {
            "counts": reconciliation["counts"],
            "orphan_edge_callsites": reconciliation["orphan_edge_callsites"][:100],
        },
        "graphics": graphics,
        "exclusions": {
            "tail_call_jmp_recovery": False,
            "indirect_target_resolution": False,
        },
    }


def canonical_json_bytes(payload: dict[str, Any]) -> bytes:
    return (json.dumps(payload, sort_keys=True, indent=2) + "\n").encode()


def canonical_jsonl_bytes(callsites: list[dict[str, Any]]) -> bytes:
    lines = [json.dumps(row, sort_keys=True) for row in callsites]
    return ("\n".join(lines) + "\n").encode() if lines else b""


def audits_complete(audits_dir: Path) -> bool:
    return (
        (audits_dir / AUDITS_CALLSITES_FILE).is_file()
        and (audits_dir / AUDITS_REPORT_FILE).is_file()
    )


def existing_audits_equal(
    audits_dir: Path, callsites_bytes: bytes, report_bytes: bytes
) -> bool:
    if not audits_complete(audits_dir):
        return False
    return (
        (audits_dir / AUDITS_CALLSITES_FILE).read_bytes() == callsites_bytes
        and (audits_dir / AUDITS_REPORT_FILE).read_bytes() == report_bytes
    )


def publish_audits(
    audits_parent: Path,
    callsites_bytes: bytes,
    report_bytes: bytes,
) -> None:
    audits_parent.mkdir(parents=True, exist_ok=True)
    audits_dir = audits_parent / "audits"
    if audits_dir.exists():
        if existing_audits_equal(audits_dir, callsites_bytes, report_bytes):
            return
        raise ReError(
            "audits/ exists with different or incomplete content; "
            "remove or rename before publishing"
        )
    staging = Path(tempfile.mkdtemp(dir=audits_parent, prefix="audits.publish-"))
    try:
        (staging / AUDITS_CALLSITES_FILE).write_bytes(callsites_bytes)
        (staging / AUDITS_REPORT_FILE).write_bytes(report_bytes)
        if audits_dir.exists():
            raise ReError("audits/ appeared during publish; aborting")
        staging.rename(audits_dir)
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise


def assert_inventory_identity(exp: Path, provenance: dict[str, Any]) -> str:
    inv = exp / "inventory"
    actual = inventory_bundle_digest(inv)
    expected = provenance.get("inventory_bundle_digest")
    if not expected:
        raise ReError("provenance missing inventory_bundle_digest")
    if actual != expected:
        raise ReError("inventory digest mismatch vs provenance")
    return actual


def assert_executable_sha(scan_meta: dict[str, Any], pinned: str) -> None:
    got = normalize_sha256(scan_meta.get("executable_sha256"))
    if got != pinned.lower():
        raise ReError(
            f"Ghidra executable_sha256 mismatch: got {got}, expected {pinned.lower()}"
        )


def run_ghidra_export(staging_dir: Path, ghidra_meta: dict[str, Any]) -> None:
    analyze_headless(
        Path(ghidra_meta["project_dir"]),
        ghidra_meta["project_name"],
        "-process",
        ghidra_meta["program_name"],
        "-noanalysis",
        "-readOnly",
        "-postScript",
        "ExportCallsites.java",
        str(staging_dir),
    )


def run_audit(exp: Path, baseline_revision: str) -> Path:
    provenance = load_provenance(exp)
    if not provenance:
        raise ReError("missing provenance; run corpus import first")
    if provenance.get("baseline_revision") != baseline_revision:
        raise ReError(
            f"baseline_revision mismatch: cli={baseline_revision} "
            f"provenance={provenance.get('baseline_revision')}"
        )

    verify_binary()
    pinned_sha = binary_sha256()
    digest_before = assert_inventory_identity(exp, provenance)

    binary_root = local_binary_root(pinned_sha)
    ghidra_meta = load_ghidra_meta(binary_root)

    staging_dir = Path(tempfile.mkdtemp(prefix="callsite-audit-"))
    try:
        run_ghidra_export(staging_dir, ghidra_meta)
        scan_meta = json.loads((staging_dir / "scan-meta.json").read_text())
        validate_scan_meta(scan_meta)
        callsites = validate_census(staging_dir / "callsites-v1.jsonl", scan_meta)
        assert_executable_sha(scan_meta, pinned_sha)

        edges = load_jsonl(exp / "inventory" / "call_edges.jsonl")
        reconciliation = reconcile_callsites(callsites, edges)
        functions = load_functions_index(exp / "inventory")
        graphics = build_graphics_section(callsites, edges, functions)
        report = build_audit_report(
            provenance=provenance,
            scan_meta=scan_meta,
            callsites=callsites,
            reconciliation=reconciliation,
            graphics=graphics,
            ghidra_meta=ghidra_meta,
            pinned_sha=pinned_sha,
        )

        callsites_bytes = canonical_jsonl_bytes(callsites)
        report_bytes = canonical_json_bytes(report)

        digest_after = inventory_bundle_digest(exp / "inventory")
        if digest_after != digest_before:
            raise ReError("inventory bundle digest changed during audit")
        publish_audits(exp, callsites_bytes, report_bytes)
    finally:
        shutil.rmtree(staging_dir, ignore_errors=True)

    return exp / "audits" / AUDITS_REPORT_FILE


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Callsite completeness audit")
    parser.add_argument("--baseline-revision", required=True)
    args = parser.parse_args(argv)

    try:
        digest = binary_sha256()
        binary_root = local_binary_root(digest)
        exp = resolve_export_dir(binary_root, args.baseline_revision)
        report_path = run_audit(exp, args.baseline_revision)
        print(f"callsite audit complete: {report_path}")
        return 0
    except ReError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
