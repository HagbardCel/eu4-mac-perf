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
        print(json.dumps({"written": str(out), "digest_match": report["inventory_provenance"]["digest_match"]}, indent=2))
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
            report_md = _render_report(bundle)
            report_path = logs / "graphics-architecture-report.md"
            report_path.write_text(report_md)
            print(f"wrote {report_path}")
        print(json.dumps({"written": str(out), "boundary_conclusion": bundle["boundary_conclusion"]}, indent=2))
        return 0
    except SeedResolutionError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except (ReError, FileNotFoundError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


def _render_report(bundle: dict) -> str:
    lines = [
        "# Graphics architecture report (local)",
        "",
        "Generated from recovered static call graph. Not an exhaustive executable model.",
        "",
        f"**Boundary conclusion:** `{bundle.get('boundary_conclusion')}`",
        "",
        "## Inventory provenance",
        "",
        json.dumps(bundle.get("inventory_provenance", {}), indent=2),
        "",
        "## Structural sets",
        "",
        json.dumps(bundle.get("structural", {}), indent=2),
        "",
        "## Gfx abstraction cut summary",
        "",
    ]
    gfx = bundle.get("cuts", {}).get("gfx_abstraction", {})
    lines.append(json.dumps(gfx.get("status_summary", {}), indent=2))
    lines.append("")
    lines.append("## Backend implementation cut summary")
    lines.append("")
    backend = bundle.get("cuts", {}).get("backend_implementation", {})
    lines.append(json.dumps(backend.get("status_summary", {}), indent=2))
    lines.append("")
    lines.append("## Missing connections")
    lines.append("")
    lines.append(json.dumps(bundle.get("missing_connections", []), indent=2))
    lines.append("")
    lines.append("## Top decompilation priorities")
    lines.append("")
    for row in bundle.get("decompile_priorities", [])[:25]:
        lines.append(f"- `{row.get('entry_rva')}` {row.get('name_mangled')}: {', '.join(row.get('reasons', []))}")
    lines.append("")
    return "\n".join(lines)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="EU IV call-graph audit and graphics extraction")
    sub = parser.add_subparsers(dest="command", required=True)
    p_audit = sub.add_parser("audit", help="write call-graph-audit.json")
    p_audit.add_argument("--baseline-revision", default=None)
    p_audit.add_argument("--export-dir", type=Path, default=None)
    p_audit.set_defaults(func=cmd_audit)
    p_ext = sub.add_parser("extract", help="write graphics-subgraph.json")
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
