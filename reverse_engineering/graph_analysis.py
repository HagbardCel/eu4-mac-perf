#!/usr/bin/env python3
"""CLI for call-graph audit and graphics subgraph extraction."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from reverse_engineering.call_graph import build_audit_report
from reverse_engineering.gfx_subgraph import SeedResolutionError, build_extract
from reverse_engineering.paths import ReError, binary_sha256, local_binary_root, resolve_export_dir

ROOT = Path(__file__).resolve().parents[1]
DRAW_CALLERS = ROOT / "analysis" / "draw-callers.json"


def _export_paths(exp: Path) -> tuple[Path, Path]:
    logs = exp / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    return exp / "inventory", logs


def cmd_audit(args: argparse.Namespace) -> int:
    try:
        digest = binary_sha256()
        binary_root = local_binary_root(digest)
        exp = args.export_dir or resolve_export_dir(binary_root, args.baseline_revision)
        inv, logs = _export_paths(exp)
        report = build_audit_report(inv, DRAW_CALLERS, exp / "provenance.json")
        out = logs / "call-graph-audit.json"
        out.write_text(json.dumps(report, indent=2) + "\n")
        print(
            json.dumps(
                {"written": str(out), "digest_match": report["inventory_provenance"]["digest_match"]},
                indent=2,
            )
        )
        return 0
    except (ReError, FileNotFoundError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


def cmd_extract(args: argparse.Namespace) -> int:
    try:
        digest = binary_sha256()
        binary_root = local_binary_root(digest)
        exp = args.export_dir or resolve_export_dir(binary_root, args.baseline_revision)
        inv, logs = _export_paths(exp)
        bundle = build_extract(
            inv,
            provenance_path=exp / "provenance.json",
            allow_digest_mismatch=args.allow_digest_mismatch,
        )
        out = logs / "graphics-subgraph.json"
        out.write_text(json.dumps(bundle, indent=2) + "\n")
        if args.write_report:
            report_path = logs / "graphics-architecture-report.md"
            report_path.write_text(_render_report(bundle, graph_functions_path=inv / "functions.jsonl"))
            print(f"wrote {report_path}")
        print(
            json.dumps(
                {
                    "written": str(out),
                    "authoritative": bundle.get("authoritative"),
                    "overall_boundary_verdict": bundle.get("overall_boundary_verdict"),
                },
                indent=2,
            )
        )
        return 0
    except (SeedResolutionError, ReError, FileNotFoundError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


def _name_for_rva(functions_path: Path, rva: str) -> str:
    if not functions_path.is_file():
        return rva
    for line in functions_path.read_text().splitlines():
        if line.strip():
            row = json.loads(line)
            if row.get("entry_rva") == rva:
                return row.get("name_demangled") or row.get("name_mangled") or rva
    return rva


def _render_report(bundle: dict, graph_functions_path: Path) -> str:
    lines = [
        "# Graphics architecture report (local)",
        "",
        f"**Authoritative:** `{bundle.get('authoritative')}`",
        f"**Overall boundary verdict:** `{bundle.get('overall_boundary_verdict')}`",
        f"**Unknown target risk:** `{bundle.get('unknown_target_risk')}`",
        "",
        "## Family verdicts (Gfx abstraction)",
        "",
        json.dumps(bundle.get("family_verdicts", {}), indent=2),
        "",
        "## Cut characterization",
        "",
        json.dumps(bundle.get("cut_characterization", {}), indent=2),
        "",
        "## Representative witnesses (Gfx overall cut)",
        "",
    ]
    overall = (
        bundle.get("cuts", {})
        .get("gfx_abstraction", {})
        .get("overall_cut_coverage", {})
        .get("endpoint_coverage", [])
    )
    shown = 0
    for row in overall:
        if shown >= 8:
            break
        if row.get("witness_before_cut") or row.get("witness_after_cut"):
            lines.append(
                f"- {row.get('status')} {row.get('endpoint')} from "
                f"`{_name_for_rva(graph_functions_path, row.get('root_rva', ''))}`"
            )
            shown += 1
    lines.extend(["", "## Top decompilation priorities", ""])
    for row in bundle.get("decompile_priorities", [])[:25]:
        lines.append(
            f"- score {row.get('priority_score')} `{row.get('entry_rva')}` "
            f"{row.get('name_mangled')}: {', '.join(row.get('reasons', []))}"
        )
        if row.get("unresolved_questions"):
            lines.append(f"  - questions: {row['unresolved_questions']}")
    lines.append("")
    return "\n".join(lines)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="EU IV call-graph audit and graphics extraction")
    sub = parser.add_subparsers(dest="command", required=True)
    p_audit = sub.add_parser("audit")
    p_audit.add_argument("--baseline-revision", default=None)
    p_audit.add_argument("--export-dir", type=Path, default=None)
    p_audit.set_defaults(func=cmd_audit)
    p_ext = sub.add_parser("extract")
    p_ext.add_argument("--baseline-revision", default=None)
    p_ext.add_argument("--export-dir", type=Path, default=None)
    p_ext.add_argument("--write-report", action="store_true")
    p_ext.add_argument("--allow-digest-mismatch", action="store_true")
    p_ext.set_defaults(func=cmd_extract)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
