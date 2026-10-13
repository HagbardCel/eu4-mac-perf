"""SQLite FTS5 corpus index (rebuildable from inventory + decompilation batches)."""

from __future__ import annotations

import json
import re
import sqlite3
import sys
from pathlib import Path
from typing import Any, Iterable

from reverse_engineering.intervals import parse_rva
from reverse_engineering.manifest import validate_batch_file


SCHEMA = """
PRAGMA journal_mode=WAL;
CREATE TABLE IF NOT EXISTS provenance (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS functions (
  entry_rva TEXT PRIMARY KEY,
  entry_rva_int INTEGER NOT NULL,
  kind TEXT NOT NULL,
  name_mangled TEXT,
  name_demangled TEXT,
  namespace TEXT,
  instruction_count INTEGER,
  decoded_instruction_bytes INTEGER,
  decompile_status TEXT,
  primary_subsystem TEXT,
  confidence TEXT,
  tags_json TEXT
);
CREATE TABLE IF NOT EXISTS pseudocode (
  entry_rva TEXT PRIMARY KEY,
  body TEXT NOT NULL
);
CREATE VIRTUAL TABLE IF NOT EXISTS pseudocode_fts USING fts5(
  entry_rva UNINDEXED,
  body
);
CREATE TABLE IF NOT EXISTS call_edges (
  id INTEGER PRIMARY KEY,
  callsite_rva TEXT NOT NULL,
  caller_rva TEXT,
  callee_internal_rva TEXT,
  callee_external_symbol TEXT,
  ultimate_external_symbol TEXT,
  resolution TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_functions_mangled ON functions(name_mangled);
CREATE INDEX IF NOT EXISTS idx_functions_demangled ON functions(name_demangled);
CREATE INDEX IF NOT EXISTS idx_edges_caller ON call_edges(caller_rva);
CREATE INDEX IF NOT EXISTS idx_edges_callee ON call_edges(callee_internal_rva);
CREATE INDEX IF NOT EXISTS idx_edges_callsite ON call_edges(callsite_rva);
"""


def connect(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def rebuild_index(
    db_path: Path,
    inventory_dir: Path,
    decompiled_dir: Path,
    provenance: dict[str, str] | None = None,
    manifest: dict[str, Any] | None = None,
) -> None:
    """Rebuild SQLite corpus from inventory and optional decompilation batches.

    manifest=None discovers all batch-NNNNN.jsonl under decompiled_dir (unrestricted).
    manifest with batches={} loads no batch files (inventory-only index).
    """
    functions_path = inventory_dir / "functions.jsonl"
    edges_path = inventory_dir / "call_edges.jsonl"
    if db_path.exists():
        db_path.unlink()
    conn = connect(db_path)
    conn.executescript(SCHEMA)
    decomp_status = _load_decompile_status(decompiled_dir, manifest)

    for line in functions_path.read_text().splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        rva = row["entry_rva"]
        conn.execute(
            """
            INSERT INTO functions (
              entry_rva, entry_rva_int, kind, name_mangled, name_demangled, namespace,
              instruction_count, decoded_instruction_bytes, decompile_status,
              primary_subsystem, confidence, tags_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, NULL, NULL)
            """,
            (
                rva,
                parse_rva(rva),
                row.get("kind"),
                row.get("name_mangled"),
                row.get("name_demangled"),
                row.get("namespace"),
                row.get("instruction_count"),
                row.get("decoded_instruction_bytes"),
                decomp_status.get(rva),
            ),
        )
        pseudo = decomp_status.get(f"{rva}:body")
        if pseudo:
            conn.execute("INSERT INTO pseudocode (entry_rva, body) VALUES (?, ?)", (rva, pseudo))
            conn.execute(
                "INSERT INTO pseudocode_fts (entry_rva, body) VALUES (?, ?)",
                (rva, pseudo),
            )

    if edges_path.is_file():
        for line in edges_path.read_text().splitlines():
            if not line.strip():
                continue
            edge = json.loads(line)
            conn.execute(
                """
                INSERT INTO call_edges (
                  callsite_rva, caller_rva, callee_internal_rva,
                  callee_external_symbol, ultimate_external_symbol, resolution
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    edge.get("callsite_rva"),
                    edge.get("caller_rva"),
                    edge.get("callee_internal_rva"),
                    edge.get("callee_external_symbol"),
                    edge.get("ultimate_external_symbol"),
                    edge.get("resolution"),
                ),
            )

    if provenance:
        for key, value in provenance.items():
            conn.execute(
                "INSERT INTO provenance (key, value) VALUES (?, ?)",
                (key, value if isinstance(value, str) else json.dumps(value)),
            )
    conn.commit()
    conn.close()


def _manifest_batch_paths(decompiled_dir: Path, manifest: dict[str, Any] | None) -> list[Path]:
    """None → glob all batch-NNNNN.jsonl; empty batches → none."""
    if manifest is None:
        return sorted(
            p
            for p in decompiled_dir.glob("batch-*.jsonl")
            if re.fullmatch(r"batch-\d{5}\.jsonl", p.name)
        )
    paths: list[Path] = []
    for batch_id in manifest.get("batches", {}):
        if re.fullmatch(r"batch-\d{5}", batch_id):
            paths.append(decompiled_dir / f"{batch_id}.jsonl")
    return sorted(paths)


def _load_decompile_status(decompiled_dir: Path, manifest: dict[str, Any] | None = None) -> dict[str, str]:
    out: dict[str, str] = {}
    if not decompiled_dir.is_dir():
        return out
    missing = 0
    for path in _manifest_batch_paths(decompiled_dir, manifest):
        if not path.is_file():
            missing += 1
            continue
        batch_id = path.stem
        expected = manifest.get("batches", {}).get(batch_id) if manifest else None
        if expected is not None:
            errors = validate_batch_file(path, expected)
            if errors:
                print(f"warning: skipping {path.name}: {errors[0]}", file=sys.stderr)
                continue
        for line in path.read_text().splitlines():
            if not line.strip():
                continue
            row = json.loads(line)
            rva = row["entry_rva"]
            out[rva] = row.get("status")
            if row.get("status") == "success" and row.get("pseudocode"):
                out[f"{rva}:body"] = row["pseudocode"]
    if missing:
        print(
            f"warning: {missing} manifest batch file(s) missing under {decompiled_dir}",
            file=sys.stderr,
        )
    return out


def search_symbol(conn: sqlite3.Connection, needle: str, limit: int = 50) -> list[sqlite3.Row]:
    pattern = f"%{needle}%"
    return list(
        conn.execute(
            """
            SELECT entry_rva, name_mangled, name_demangled, kind, decoded_instruction_bytes
            FROM functions
            WHERE name_mangled LIKE ? OR name_demangled LIKE ? OR namespace LIKE ?
            ORDER BY decoded_instruction_bytes DESC
            LIMIT ?
            """,
            (pattern, pattern, pattern, limit),
        )
    )


def search_text(conn: sqlite3.Connection, needle: str, limit: int = 30) -> list[sqlite3.Row]:
    return list(
        conn.execute(
            """
            SELECT entry_rva, snippet(pseudocode_fts, 1, '>>>', '<<<', '…', 48) AS snippet
            FROM pseudocode_fts
            WHERE pseudocode_fts MATCH ?
            LIMIT ?
            """,
            (needle, limit),
        )
    )


def function_row(conn: sqlite3.Connection, rva: str) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM functions WHERE entry_rva = ?", (rva,)).fetchone()


def callers(conn: sqlite3.Connection, rva: str) -> Iterable[sqlite3.Row]:
    return conn.execute(
        """
        SELECT * FROM call_edges
        WHERE callee_internal_rva = ?
        ORDER BY callsite_rva
        """,
        (rva,),
    )


def callees(conn: sqlite3.Connection, rva: str) -> Iterable[sqlite3.Row]:
    return conn.execute(
        """
        SELECT * FROM call_edges
        WHERE caller_rva = ?
        ORDER BY callsite_rva
        """,
        (rva,),
    )
