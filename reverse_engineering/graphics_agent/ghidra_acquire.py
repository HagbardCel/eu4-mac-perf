"""Atomic Ghidra acquisition, fencing, and publication."""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import time
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any

from reverse_engineering.graphics_agent import budgets
from reverse_engineering.paths import ReError


class AcquireOutcome(str, Enum):
    CACHE_HIT = "cache_hit"
    ACQUIRED = "acquired"
    SAME_OWNER_CONTINUE = "same_owner_continue"


@dataclass
class AcquireResult:
    outcome: AcquireOutcome
    lease_generation: int = 0
    owner: str = ""
    output_path: Path | None = None


def _pid_alive(pid: int | None) -> bool:
    if not pid or pid <= 0:
        return False
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def ensure_ghidra_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS ghidra_project_leases (
          project_key TEXT PRIMARY KEY,
          idempotency_key TEXT NOT NULL,
          lease_owner TEXT NOT NULL,
          lease_generation INTEGER NOT NULL,
          worker_pid INTEGER,
          lease_expires_at REAL NOT NULL,
          updated_at REAL NOT NULL
        );
        """
    )
    cols = {r[1] for r in conn.execute("PRAGMA table_info(ghidra_operations)").fetchall()}
    for name, ddl in (
        ("lease_generation", "ALTER TABLE ghidra_operations ADD COLUMN lease_generation INTEGER"),
        ("worker_pid", "ALTER TABLE ghidra_operations ADD COLUMN worker_pid INTEGER"),
        ("evidence_id", "ALTER TABLE ghidra_operations ADD COLUMN evidence_id TEXT"),
        ("project_key", "ALTER TABLE ghidra_operations ADD COLUMN project_key TEXT"),
    ):
        if name not in cols:
            conn.execute(ddl)


def project_key_from_identity(identity: dict[str, Any]) -> str:
    raw = "|".join(
        [
            identity.get("ghidra_version", ""),
            identity.get("project_name", ""),
            identity.get("project_meta_hash", ""),
            identity.get("program_name", ""),
        ]
    )
    return hashlib.sha256(raw.encode()).hexdigest()[:32]


def _active_project_lease(conn: sqlite3.Connection, project_key: str, now: float) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM ghidra_project_leases WHERE project_key = ? AND lease_expires_at > ?",
        (project_key, now),
    ).fetchone()


def ghidra_try_acquire(
    conn: sqlite3.Connection,
    *,
    project_key: str,
    idempotency_key: str,
    investigation_id: str,
    operation: str,
    args: dict[str, Any],
    owner: str,
    worker_pid: int,
    quota_key: str,
    quota_limit: int,
    quota_delta: int,
    max_concurrent: int,
    lease_ttl: float = 600.0,
    published_path: Path | None = None,
    published_hash: str | None = None,
) -> AcquireResult:
    """Single BEGIN IMMEDIATE: cache hit, reject competing owners, or acquire with new generation."""
    from reverse_engineering.graphics_agent.research_store import ghidra_op_get

    now = time.time()
    if published_path and published_path.is_file() and published_hash:
        on_disk = hashlib.sha256(published_path.read_bytes()).hexdigest()
        if on_disk != published_hash:
            raise ReError("artifact on disk hash mismatch")
        row = ghidra_op_get(conn, idempotency_key)
        if row and row["state"] == "published" and row["output_artifact_hash"] == published_hash:
            return AcquireResult(AcquireOutcome.CACHE_HIT, output_path=published_path)

    conn.execute("BEGIN IMMEDIATE")
    try:
        row = ghidra_op_get(conn, idempotency_key)
        if row and row["state"] == "published":
            if not row["output_artifact_hash"]:
                raise ReError("published operation missing output hash")
            if published_path and published_path.is_file():
                on_disk = hashlib.sha256(published_path.read_bytes()).hexdigest()
                if on_disk != row["output_artifact_hash"]:
                    raise ReError("cached ghidra artifact hash mismatch")
            conn.execute("COMMIT")
            return AcquireResult(AcquireOutcome.CACHE_HIT, output_path=published_path)

        proj = _active_project_lease(conn, project_key, now)
        if proj:
            if proj["idempotency_key"] != idempotency_key:
                if _pid_alive(proj["worker_pid"]):
                    conn.execute("ROLLBACK")
                    raise ReError("ghidra project lease held by another operation")
            elif proj["lease_owner"] != owner:
                if _pid_alive(proj["worker_pid"]):
                    conn.execute("ROLLBACK")
                    raise ReError("ghidra operation lease held by another owner")

        if row and row["state"] in ("reserved", "running"):
            gen = int(row["lease_generation"] or 0)
            if row["lease_owner"] and row["lease_owner"] != owner:
                if row["lease_expires_at"] and row["lease_expires_at"] > now and _pid_alive(row["worker_pid"]):
                    conn.execute("ROLLBACK")
                    raise ReError("ghidra operation in progress by another owner")
            conn.execute(
                """
                UPDATE ghidra_operations SET lease_owner = ?, worker_pid = ?, lease_expires_at = ?, updated_at = ?
                WHERE idempotency_key = ?
                """,
                (owner, worker_pid, now + lease_ttl, now, idempotency_key),
            )
            conn.execute("COMMIT")
            return AcquireResult(AcquireOutcome.SAME_OWNER_CONTINUE, lease_generation=gen, owner=owner)

        active_count = conn.execute(
            """
            SELECT COUNT(*) FROM ghidra_project_leases
            WHERE project_key = ? AND lease_expires_at > ?
            """,
            (project_key, now),
        ).fetchone()[0]
        if active_count >= max_concurrent and not (proj and proj["idempotency_key"] == idempotency_key):
            conn.execute("ROLLBACK")
            raise ReError("ghidra project concurrent limit reached")

        qrow = conn.execute(
            "SELECT value FROM budget_counters WHERE investigation_id = ? AND counter_key = ?",
            (investigation_id, quota_key),
        ).fetchone()
        qcur = int(qrow[0]) if qrow else 0
        if qcur + quota_delta > quota_limit:
            conn.execute("ROLLBACK")
            raise budgets.BudgetExceeded(f"{quota_key} would exceed limit {quota_limit}")
        if qrow:
            conn.execute(
                "UPDATE budget_counters SET value = value + ? WHERE investigation_id = ? AND counter_key = ?",
                (quota_delta, investigation_id, quota_key),
            )
        else:
            budgets.ensure_budget_tables(conn)
            conn.execute(
                "INSERT INTO budget_counters (investigation_id, counter_key, value) VALUES (?, ?, ?)",
                (investigation_id, quota_key, quota_delta),
            )

        gen_row = conn.execute("SELECT COALESCE(MAX(lease_generation), 0) FROM ghidra_operations").fetchone()
        new_gen = int(gen_row[0]) + 1
        if not row:
            conn.execute(
                """
                INSERT INTO ghidra_operations (
                  idempotency_key, investigation_id, state, operation, args_json,
                  output_artifact_hash, output_path, error, lease_owner, lease_expires_at,
                  lease_generation, worker_pid, project_key, created_at, updated_at
                ) VALUES (?, ?, 'reserved', ?, ?, NULL, NULL, NULL, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    idempotency_key,
                    investigation_id,
                    operation,
                    json.dumps(args),
                    owner,
                    now + lease_ttl,
                    new_gen,
                    worker_pid,
                    project_key,
                    now,
                    now,
                ),
            )
        else:
            conn.execute(
                """
                UPDATE ghidra_operations SET state = 'reserved', lease_owner = ?, worker_pid = ?,
                  lease_expires_at = ?, lease_generation = ?, project_key = ?, updated_at = ?
                WHERE idempotency_key = ?
                """,
                (owner, worker_pid, now + lease_ttl, new_gen, project_key, now, idempotency_key),
            )

        conn.execute(
            """
            INSERT INTO ghidra_project_leases (
              project_key, idempotency_key, lease_owner, lease_generation, worker_pid,
              lease_expires_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(project_key) DO UPDATE SET
              idempotency_key = excluded.idempotency_key,
              lease_owner = excluded.lease_owner,
              lease_generation = excluded.lease_generation,
              worker_pid = excluded.worker_pid,
              lease_expires_at = excluded.lease_expires_at,
              updated_at = excluded.updated_at
            """,
            (project_key, idempotency_key, owner, new_gen, worker_pid, now + lease_ttl, now),
        )
        conn.execute("COMMIT")
        return AcquireResult(AcquireOutcome.ACQUIRED, lease_generation=new_gen, owner=owner)
    except budgets.BudgetExceeded:
        conn.execute("ROLLBACK")
        raise
    except Exception:
        conn.execute("ROLLBACK")
        raise


def ghidra_mark_running(conn: sqlite3.Connection, idempotency_key: str, lease_generation: int) -> None:
    row = conn.execute(
        "SELECT lease_generation FROM ghidra_operations WHERE idempotency_key = ?", (idempotency_key,)
    ).fetchone()
    if not row or int(row["lease_generation"] or 0) != lease_generation:
        raise ReError("ghidra fencing token mismatch at running transition")
    conn.execute(
        "UPDATE ghidra_operations SET state = 'running', updated_at = ? WHERE idempotency_key = ?",
        (time.time(), idempotency_key),
    )
    conn.commit()


def register_evidence_no_commit(
    conn: sqlite3.Connection, evidence_id: str, kind: str, artifact: dict[str, Any]
) -> None:
    conn.execute(
        """
        INSERT INTO evidence_registry (evidence_id, kind, artifact_json, registered_at)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(evidence_id) DO UPDATE SET artifact_json = excluded.artifact_json
        """,
        (evidence_id, kind, json.dumps(artifact), time.time()),
    )


def ghidra_complete_publish(
    conn: sqlite3.Connection,
    *,
    idempotency_key: str,
    project_key: str,
    lease_generation: int,
    output_hash: str,
    output_path: str,
    evidence_id: str,
    evidence_kind: str,
    evidence_artifact: dict[str, Any],
) -> None:
    conn.execute("BEGIN IMMEDIATE")
    try:
        row = conn.execute(
            "SELECT lease_generation, state FROM ghidra_operations WHERE idempotency_key = ?",
            (idempotency_key,),
        ).fetchone()
        if not row or int(row["lease_generation"] or 0) != lease_generation:
            conn.execute("ROLLBACK")
            raise ReError("ghidra publish rejected: stale fencing token")
        register_evidence_no_commit(conn, evidence_id, evidence_kind, evidence_artifact)
        now = time.time()
        conn.execute(
            """
            UPDATE ghidra_operations SET state = 'published', output_artifact_hash = ?, output_path = ?,
              evidence_id = ?, lease_owner = NULL, lease_expires_at = NULL, updated_at = ?
            WHERE idempotency_key = ? AND lease_generation = ?
            """,
            (output_hash, output_path, evidence_id, now, idempotency_key, lease_generation),
        )
        conn.execute("DELETE FROM ghidra_project_leases WHERE project_key = ?", (project_key,))
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise


def reconcile_published_evidence(
    conn: sqlite3.Connection,
    idempotency_key: str,
    output_path: Path,
    output_hash: str,
    evidence_id: str,
    evidence_kind: str,
    evidence_artifact: dict[str, Any],
    project_key: str,
) -> bool:
    """Complete DB when artifact exists but evidence or publish incomplete."""
    from reverse_engineering.graphics_agent.research_store import ghidra_op_get

    if not output_path.is_file():
        return False
    on_disk = hashlib.sha256(output_path.read_bytes()).hexdigest()
    if on_disk != output_hash:
        raise ReError("reconcile: artifact hash mismatch")
    row = ghidra_op_get(conn, idempotency_key)
    if not row:
        return False
    if row["state"] == "published":
        if row["evidence_id"]:
            return True
        register_evidence_no_commit(conn, evidence_id, evidence_kind, evidence_artifact)
        conn.execute(
            "UPDATE ghidra_operations SET evidence_id = ?, updated_at = ? WHERE idempotency_key = ?",
            (evidence_id, time.time(), idempotency_key),
        )
        conn.commit()
        return True
    gen = int(row["lease_generation"] or 1)
    ghidra_complete_publish(
        conn,
        idempotency_key=idempotency_key,
        project_key=project_key,
        lease_generation=gen,
        output_hash=output_hash,
        output_path=str(output_path),
        evidence_id=evidence_id,
        evidence_kind=evidence_kind,
        evidence_artifact=evidence_artifact,
    )
    return True
