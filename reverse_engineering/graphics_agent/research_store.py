"""Transactional research database (authoritative findings and Ghidra op ledger)."""

from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path
from typing import Any

from reverse_engineering.graphics_agent.budgets import ensure_budget_tables

SCHEMA_VERSION = 1


def connect(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    migrate(conn)
    return conn


def migrate(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS meta (
          key TEXT PRIMARY KEY,
          value TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS investigations (
          id TEXT PRIMARY KEY,
          state TEXT NOT NULL,
          binary_sha256 TEXT NOT NULL,
          inventory_digest TEXT NOT NULL,
          baseline_revision TEXT,
          created_at REAL NOT NULL,
          updated_at REAL NOT NULL
        );
        CREATE TABLE IF NOT EXISTS claims (
          claim_id TEXT PRIMARY KEY,
          investigation_id TEXT NOT NULL,
          statement TEXT NOT NULL,
          scientific_status TEXT NOT NULL,
          review_status TEXT NOT NULL,
          evidence_ids_json TEXT NOT NULL,
          limitations_json TEXT,
          superseded_by TEXT,
          created_at REAL NOT NULL,
          updated_at REAL NOT NULL
        );
        CREATE TABLE IF NOT EXISTS dossiers (
          entry_rva TEXT PRIMARY KEY,
          investigation_id TEXT NOT NULL,
          subsystem TEXT,
          role TEXT,
          membership_status TEXT,
          body_json TEXT NOT NULL,
          updated_at REAL NOT NULL
        );
        CREATE TABLE IF NOT EXISTS open_questions (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          investigation_id TEXT NOT NULL,
          domain TEXT,
          question TEXT NOT NULL,
          status TEXT NOT NULL DEFAULT 'open'
        );
        CREATE TABLE IF NOT EXISTS evidence_registry (
          evidence_id TEXT PRIMARY KEY,
          kind TEXT NOT NULL,
          artifact_json TEXT NOT NULL,
          registered_at REAL NOT NULL
        );
        CREATE TABLE IF NOT EXISTS ghidra_operations (
          idempotency_key TEXT PRIMARY KEY,
          investigation_id TEXT NOT NULL,
          state TEXT NOT NULL,
          operation TEXT NOT NULL,
          args_json TEXT NOT NULL,
          output_artifact_hash TEXT,
          error TEXT,
          created_at REAL NOT NULL,
          updated_at REAL NOT NULL
        );
        CREATE TABLE IF NOT EXISTS events (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          investigation_id TEXT,
          kind TEXT NOT NULL,
          payload_json TEXT NOT NULL,
          created_at REAL NOT NULL
        );
        """
    )
    ensure_budget_tables(conn)
    row = conn.execute("SELECT value FROM meta WHERE key = 'schema_version'").fetchone()
    if not row:
        conn.execute(
            "INSERT INTO meta (key, value) VALUES ('schema_version', ?)",
            (str(SCHEMA_VERSION),),
        )
    conn.commit()


def research_revision(conn: sqlite3.Connection) -> int:
    row = conn.execute("SELECT value FROM meta WHERE key = 'revision'").fetchone()
    if not row:
        conn.execute("INSERT INTO meta (key, value) VALUES ('revision', '0')")
        conn.commit()
        return 0
    return int(row["value"])


def bump_revision(conn: sqlite3.Connection) -> int:
    rev = research_revision(conn) + 1
    conn.execute(
        "INSERT INTO meta (key, value) VALUES ('revision', ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (str(rev),),
    )
    conn.commit()
    return rev


def ensure_investigation(
    conn: sqlite3.Connection,
    investigation_id: str,
    binary_sha256: str,
    inventory_digest: str,
    baseline_revision: str | None,
    initial_state: str = "PENDING",
) -> None:
    now = time.time()
    conn.execute(
        """
        INSERT INTO investigations (id, state, binary_sha256, inventory_digest, baseline_revision, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(id) DO NOTHING
        """,
        (investigation_id, initial_state, binary_sha256, inventory_digest, baseline_revision, now, now),
    )
    conn.commit()


def set_investigation_state(conn: sqlite3.Connection, investigation_id: str, state: str) -> None:
    conn.execute(
        "UPDATE investigations SET state = ?, updated_at = ? WHERE id = ?",
        (state, time.time(), investigation_id),
    )
    conn.commit()


def register_evidence(conn: sqlite3.Connection, evidence_id: str, kind: str, artifact: dict[str, Any]) -> None:
    conn.execute(
        """
        INSERT INTO evidence_registry (evidence_id, kind, artifact_json, registered_at)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(evidence_id) DO UPDATE SET artifact_json = excluded.artifact_json
        """,
        (evidence_id, kind, json.dumps(artifact), time.time()),
    )
    conn.commit()


def known_evidence_ids(conn: sqlite3.Connection) -> set[str]:
    rows = conn.execute("SELECT evidence_id FROM evidence_registry").fetchall()
    return {r["evidence_id"] for r in rows}


def upsert_claim(conn: sqlite3.Connection, claim: dict[str, Any]) -> None:
    now = time.time()
    conn.execute(
        """
        INSERT INTO claims (
          claim_id, investigation_id, statement, scientific_status, review_status,
          evidence_ids_json, limitations_json, superseded_by, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(claim_id) DO UPDATE SET
          statement = excluded.statement,
          scientific_status = excluded.scientific_status,
          review_status = excluded.review_status,
          evidence_ids_json = excluded.evidence_ids_json,
          limitations_json = excluded.limitations_json,
          superseded_by = excluded.superseded_by,
          updated_at = excluded.updated_at
        """,
        (
            claim["claim_id"],
            claim["investigation_id"],
            claim["statement"],
            claim["scientific_status"],
            claim["review_status"],
            json.dumps(claim.get("evidence_ids", [])),
            json.dumps(claim.get("limitations")),
            claim.get("superseded_by"),
            claim.get("created_at", now),
            now,
        ),
    )
    log_event(conn, claim["investigation_id"], "claim_upsert", claim)
    bump_revision(conn)


def log_event(conn: sqlite3.Connection, investigation_id: str | None, kind: str, payload: dict[str, Any]) -> None:
    conn.execute(
        "INSERT INTO events (investigation_id, kind, payload_json, created_at) VALUES (?, ?, ?, ?)",
        (investigation_id, kind, json.dumps(payload), time.time()),
    )
    conn.commit()


def export_events_jsonl(conn: sqlite3.Connection, out_path: Path) -> None:
    rows = conn.execute("SELECT kind, payload_json, created_at FROM events ORDER BY id").fetchall()
    lines = []
    for r in rows:
        lines.append(
            json.dumps(
                {"kind": r["kind"], "payload": json.loads(r["payload_json"]), "created_at": r["created_at"]}
            )
        )
    out_path.write_text("\n".join(lines) + ("\n" if lines else ""))


def ghidra_op_get(conn: sqlite3.Connection, idempotency_key: str) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM ghidra_operations WHERE idempotency_key = ?",
        (idempotency_key,),
    ).fetchone()


def ghidra_op_reserve(
    conn: sqlite3.Connection,
    idempotency_key: str,
    investigation_id: str,
    operation: str,
    args: dict[str, Any],
) -> str:
    """Return state: reserved, running, published, failed (existing), or newly reserved."""
    existing = ghidra_op_get(conn, idempotency_key)
    if existing:
        return existing["state"]
    now = time.time()
    conn.execute(
        """
        INSERT INTO ghidra_operations (
          idempotency_key, investigation_id, state, operation, args_json,
          output_artifact_hash, error, created_at, updated_at
        ) VALUES (?, ?, 'reserved', ?, ?, NULL, NULL, ?, ?)
        """,
        (idempotency_key, investigation_id, operation, json.dumps(args), now, now),
    )
    conn.commit()
    return "reserved"


def ghidra_op_transition(
    conn: sqlite3.Connection,
    idempotency_key: str,
    state: str,
    output_hash: str | None = None,
    error: str | None = None,
) -> None:
    conn.execute(
        """
        UPDATE ghidra_operations SET state = ?, output_artifact_hash = ?, error = ?, updated_at = ?
        WHERE idempotency_key = ?
        """,
        (state, output_hash, error, time.time(), idempotency_key),
    )
    conn.commit()
