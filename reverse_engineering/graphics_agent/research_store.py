"""Transactional research database (authoritative findings and Ghidra op ledger)."""

from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path
from typing import Any

from reverse_engineering.graphics_agent.budgets import ensure_budget_tables
from reverse_engineering.graphics_agent.validation import REVIEW_HUMAN_ACCEPTED, REVIEW_MODEL_VERIFIED
from reverse_engineering.paths import ReError

SCHEMA_VERSION = 3


def connect(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path, check_same_thread=False)
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
          output_path TEXT,
          error TEXT,
          lease_owner TEXT,
          lease_expires_at REAL,
          created_at REAL NOT NULL,
          updated_at REAL NOT NULL
        );
        CREATE TABLE IF NOT EXISTS verification_queue (
          claim_id TEXT PRIMARY KEY,
          investigation_id TEXT NOT NULL,
          status TEXT NOT NULL,
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
    _ensure_ghidra_columns(conn)
    from reverse_engineering.graphics_agent.ghidra_acquire import ensure_ghidra_schema

    ensure_ghidra_schema(conn)
    conn.commit()


def _ensure_ghidra_columns(conn: sqlite3.Connection) -> None:
    cols = {r[1] for r in conn.execute("PRAGMA table_info(ghidra_operations)").fetchall()}
    for name, ddl in (
        ("output_path", "ALTER TABLE ghidra_operations ADD COLUMN output_path TEXT"),
        ("lease_owner", "ALTER TABLE ghidra_operations ADD COLUMN lease_owner TEXT"),
        ("lease_expires_at", "ALTER TABLE ghidra_operations ADD COLUMN lease_expires_at REAL"),
    ):
        if name not in cols:
            conn.execute(ddl)


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
    row = conn.execute("SELECT * FROM investigations WHERE id = ?", (investigation_id,)).fetchone()
    if row:
        if row["binary_sha256"] != binary_sha256 or row["inventory_digest"] != inventory_digest:
            raise ReError("investigation corpus identity mismatch (binary or inventory digest)")
        if baseline_revision and row["baseline_revision"] and row["baseline_revision"] != baseline_revision:
            raise ReError("investigation baseline_revision mismatch")
        return
    conn.execute(
        """
        INSERT INTO investigations (id, state, binary_sha256, inventory_digest, baseline_revision, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
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


def get_claim(conn: sqlite3.Connection, claim_id: str) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM claims WHERE claim_id = ?", (claim_id,)).fetchone()


def upsert_claim(conn: sqlite3.Connection, claim: dict[str, Any]) -> None:
    existing = get_claim(conn, claim["claim_id"])
    if existing:
        if existing["investigation_id"] != claim["investigation_id"]:
            raise ReError("claim_id already used by another investigation")
        if existing["review_status"] in (REVIEW_HUMAN_ACCEPTED, REVIEW_MODEL_VERIFIED):
            raise ReError("cannot overwrite accepted or model-verified claim")
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
    *,
    lease_owner: str | None = None,
    lease_ttl_seconds: float = 600.0,
    commit: bool = True,
) -> str:
    """Return state: reserved, running, published, failed (existing), or newly reserved."""
    existing = ghidra_op_get(conn, idempotency_key)
    if existing:
        return existing["state"]
    now = time.time()
    lease_exp = now + lease_ttl_seconds if lease_owner else None
    conn.execute(
        """
        INSERT INTO ghidra_operations (
          idempotency_key, investigation_id, state, operation, args_json,
          output_artifact_hash, output_path, error, lease_owner, lease_expires_at,
          created_at, updated_at
        ) VALUES (?, ?, 'reserved', ?, ?, NULL, NULL, NULL, ?, ?, ?, ?)
        """,
        (
            idempotency_key,
            investigation_id,
            operation,
            json.dumps(args),
            lease_owner,
            lease_exp,
            now,
            now,
        ),
    )
    if commit:
        conn.commit()
    return "reserved"


def ghidra_acquire_lease(
    conn: sqlite3.Connection,
    investigation_id: str,
    idempotency_key: str,
    owner: str,
    lease_ttl_seconds: float = 600.0,
    max_concurrent: int = 1,
) -> None:
    raise RuntimeError("ghidra_acquire_lease is removed; use ghidra_try_acquire in ghidra_acquire.py")
    """Atomically reserve op row + investigation Ghidra lease (BEGIN IMMEDIATE)."""
    conn.execute("BEGIN IMMEDIATE")
    try:
        now = time.time()
        active = conn.execute(
            """
            SELECT idempotency_key FROM ghidra_operations
            WHERE investigation_id = ? AND state IN ('reserved', 'running')
              AND lease_expires_at IS NOT NULL AND lease_expires_at > ?
            """,
            (investigation_id, now),
        ).fetchall()
        if len(active) >= max_concurrent:
            other = [r["idempotency_key"] for r in active if r["idempotency_key"] != idempotency_key]
            if other:
                raise ReError("ghidra lease unavailable (concurrent acquisition limit)")
        row = ghidra_op_get(conn, idempotency_key)
        if row and row["state"] == "published":
            conn.execute("COMMIT")
            return
        if not row:
            now = time.time()
            conn.execute(
                """
                INSERT INTO ghidra_operations (
                  idempotency_key, investigation_id, state, operation, args_json,
                  output_artifact_hash, output_path, error, lease_owner, lease_expires_at,
                  created_at, updated_at
                ) VALUES (?, ?, 'reserved', ?, ?, NULL, NULL, NULL, ?, ?, ?, ?)
                """,
                (
                    idempotency_key,
                    investigation_id,
                    "pending",
                    json.dumps({}),
                    owner,
                    now + lease_ttl_seconds,
                    now,
                    now,
                ),
            )
            row = ghidra_op_get(conn, idempotency_key)
        conn.execute(
            """
            UPDATE ghidra_operations
            SET lease_owner = ?, lease_expires_at = ?, updated_at = ?
            WHERE idempotency_key = ?
            """,
            (owner, now + lease_ttl_seconds, now, idempotency_key),
        )
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise


def reconcile_uncommitted_publish(
    conn: sqlite3.Connection,
    idempotency_key: str,
    output_path: Path,
    output_hash: str,
) -> bool:
    """If artifact exists on disk but DB not published, complete step 5 without re-running Ghidra."""
    row = ghidra_op_get(conn, idempotency_key)
    if not row:
        return False
    if row["state"] == "published":
        if row["output_artifact_hash"] and row["output_artifact_hash"] != output_hash:
            ghidra_op_transition(conn, idempotency_key, "failed", error="artifact hash mismatch")
            raise ReError("published operation hash mismatch vs filesystem")
        return True
    if not output_path.is_file():
        return False
    ghidra_op_transition(
        conn,
        idempotency_key,
        "published",
        output_hash=output_hash,
        output_path=str(output_path),
    )
    return True


def verify_published_artifact(conn: sqlite3.Connection, idempotency_key: str) -> None:
    row = ghidra_op_get(conn, idempotency_key)
    if not row or row["state"] != "published":
        return
    path_text = row["output_path"]
    if not path_text:
        ghidra_op_transition(conn, idempotency_key, "failed", error="published row missing output_path")
        raise ReError("ghidra artifact missing for published operation")
    path = Path(path_text)
    if not path.is_file():
        ghidra_op_transition(conn, idempotency_key, "failed", error="artifact file missing")
        raise ReError("ghidra artifact file missing for published operation")
    import hashlib

    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    if row["output_artifact_hash"] and digest != row["output_artifact_hash"]:
        ghidra_op_transition(conn, idempotency_key, "failed", error="artifact hash mismatch")
        raise ReError("ghidra artifact hash mismatch vs database")


def enqueue_verification(conn: sqlite3.Connection, investigation_id: str, claim_id: str) -> None:
    now = time.time()
    conn.execute(
        """
        INSERT INTO verification_queue (claim_id, investigation_id, status, updated_at)
        VALUES (?, ?, 'pending', ?)
        ON CONFLICT(claim_id) DO UPDATE SET status = 'pending', updated_at = excluded.updated_at
        """,
        (claim_id, investigation_id, now),
    )
    conn.commit()


def set_verification_status(conn: sqlite3.Connection, claim_id: str, status: str) -> None:
    conn.execute(
        "UPDATE verification_queue SET status = ?, updated_at = ? WHERE claim_id = ?",
        (status, time.time(), claim_id),
    )
    conn.commit()


def resolve_evidence_artifact(conn: sqlite3.Connection, evidence_id: str) -> dict[str, Any] | None:
    row = conn.execute(
        "SELECT kind, artifact_json FROM evidence_registry WHERE evidence_id = ?",
        (evidence_id,),
    ).fetchone()
    if not row:
        return None
    return {"evidence_id": evidence_id, "kind": row["kind"], "artifact": json.loads(row["artifact_json"])}


def ghidra_op_transition(
    conn: sqlite3.Connection,
    idempotency_key: str,
    state: str,
    output_hash: str | None = None,
    error: str | None = None,
    output_path: str | None = None,
    release_lease: bool = False,
) -> None:
    lease_owner = None
    lease_expires = None
    if release_lease:
        lease_owner = None
        lease_expires = None
    conn.execute(
        """
        UPDATE ghidra_operations SET state = ?, output_artifact_hash = ?, output_path = COALESCE(?, output_path),
          error = ?, lease_owner = COALESCE(?, lease_owner), lease_expires_at = ?,
          updated_at = ?
        WHERE idempotency_key = ?
        """,
        (
            state,
            output_hash,
            output_path,
            error,
            lease_owner if release_lease else None,
            lease_expires if release_lease else None,
            time.time(),
            idempotency_key,
        ),
    )
    if release_lease:
        conn.execute(
            """
            UPDATE ghidra_operations SET lease_owner = NULL, lease_expires_at = NULL
            WHERE idempotency_key = ?
            """,
            (idempotency_key,),
        )
    conn.commit()
