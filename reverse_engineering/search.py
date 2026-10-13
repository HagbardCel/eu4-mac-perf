#!/usr/bin/env python3
"""CLI search over the local EU IV reverse-engineering corpus."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from reverse_engineering import index_db
from reverse_engineering.intervals import format_rva, parse_rva
from reverse_engineering.paths import ReError, binary_sha256, local_binary_root, resolve_export_dir


def _db_path(baseline: str | None) -> Path:
    binary_root = local_binary_root(binary_sha256())
    exp = resolve_export_dir(binary_root, baseline)
    db = exp / "index.sqlite"
    if not db.is_file():
        raise ReError(f"missing index at {db}; run corpus index")
    return db


def _normalize_rva(text: str) -> str:
    return format_rva(parse_rva(text))


def cmd_symbol(args: argparse.Namespace) -> int:
    conn = index_db.connect(_db_path(args.baseline_revision))
    rows = index_db.search_symbol(conn, args.query, args.limit)
    for row in rows:
        print(
            f"{row['entry_rva']}  {row['kind']:8}  "
            f"{row['decoded_instruction_bytes'] or 0:6} B  "
            f"{row['name_demangled'] or row['name_mangled']}"
        )
    return 0


def cmd_text(args: argparse.Namespace) -> int:
    conn = index_db.connect(_db_path(args.baseline_revision))
    rows = index_db.search_text(conn, args.query, args.limit)
    for row in rows:
        print(f"{row['entry_rva']}  {row['snippet']}")
    return 0


def cmd_function(args: argparse.Namespace) -> int:
    conn = index_db.connect(_db_path(args.baseline_revision))
    rva = _normalize_rva(args.rva)
    row = index_db.function_row(conn, rva)
    if row is None:
        print(f"no function at {rva}", file=sys.stderr)
        return 1
    print(json.dumps(dict(row), indent=2))
    pseudo = conn.execute("SELECT body FROM pseudocode WHERE entry_rva = ?", (rva,)).fetchone()
    if pseudo:
        print("\n--- pseudocode ---\n")
        print(pseudo["body"])
    return 0


def cmd_callers(args: argparse.Namespace) -> int:
    conn = index_db.connect(_db_path(args.baseline_revision))
    rva = _normalize_rva(args.rva)
    for row in index_db.callers(conn, rva):
        print(json.dumps(dict(row)))
    return 0


def cmd_callees(args: argparse.Namespace) -> int:
    conn = index_db.connect(_db_path(args.baseline_revision))
    rva = _normalize_rva(args.rva)
    for row in index_db.callees(conn, rva):
        print(json.dumps(dict(row)))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="reverse_engineering.search")
    parser.add_argument("--baseline-revision", default=None)
    sub = parser.add_subparsers(dest="command", required=True)
    p_sym = sub.add_parser("symbol")
    p_sym.add_argument("query")
    p_sym.add_argument("--limit", type=int, default=50)
    p_sym.set_defaults(func=cmd_symbol)
    p_txt = sub.add_parser("text")
    p_txt.add_argument("query")
    p_txt.add_argument("--limit", type=int, default=30)
    p_txt.set_defaults(func=cmd_text)
    p_fn = sub.add_parser("function")
    p_fn.add_argument("rva")
    p_fn.set_defaults(func=cmd_function)
    p_callers = sub.add_parser("callers")
    p_callers.add_argument("rva")
    p_callers.set_defaults(func=cmd_callers)
    p_callees = sub.add_parser("callees")
    p_callees.add_argument("rva")
    p_callees.set_defaults(func=cmd_callees)
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except ReError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
