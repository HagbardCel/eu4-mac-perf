#!/usr/bin/env python3
"""Parse and validate Ghidra JSONL from local headless runs (operator merge gate)."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from reverse_engineering.graphics_agent import ghidra_queries
from reverse_engineering.graphics_agent.ghidra_six_op_cert import certify_operation_row
from reverse_engineering.graphics_agent.tools import EvidenceService
from reverse_engineering.intervals import format_rva, parse_rva
from reverse_engineering.paths import binary_sha256, local_binary_root


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="ghidra_local_gate")
    p.add_argument("--baseline-revision", required=True)
    p.add_argument("--investigation-id", default="ghidra-gate")
    p.add_argument("--rva", required=True)
    p.add_argument("--operations", nargs="+", required=True)
    p.add_argument("--max-insns", type=int, default=32)
    args = p.parse_args(argv)
    norm = format_rva(parse_rva(args.rva))
    svc = EvidenceService.open(args.baseline_revision, args.investigation_id)
    export = svc.export_dir
    binary_root = local_binary_root(binary_sha256())
    cache = svc.cache_dir()
    conn = svc.research_conn
    digest = svc.inventory_digest()
    summary = []
    for op in args.operations:
        rows = ghidra_queries.run_query_evidence(
            conn,
            args.investigation_id,
            digest,
            export,
            binary_root,
            cache,
            op,
            [norm],
            max_insns=args.max_insns,
        )
        for row in rows:
            if not row.get("entry_rva"):
                raise SystemExit(f"operation {op}: row missing entry_rva")
        certify_operation_row(op, rows[0], norm, inventory_has_function=True)
        summary.append({"operation": op, "rows": len(rows), "status": rows[0].get("status")})
    print(json.dumps({"ok": True, "rva": norm, "operations": summary}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
