"""Bounded Ghidra headless queries with cache and op ledger."""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import tempfile
import uuid
from pathlib import Path
from typing import Any

from reverse_engineering.corpus import analyze_headless, load_ghidra_meta, script_hash
from reverse_engineering.graphics_agent import budgets
from reverse_engineering.graphics_agent.evidence_ids import args_canonical_hash, ghidra_artifact_id
from reverse_engineering.graphics_agent.provenance_guard import assert_authoritative_acquisition
from reverse_engineering.graphics_agent.research_store import (
    ghidra_acquire_lease,
    ghidra_op_get,
    ghidra_op_reserve,
    ghidra_op_transition,
    reconcile_uncommitted_publish,
    register_evidence,
    verify_published_artifact,
)
from reverse_engineering.intervals import format_rva, parse_rva
from reverse_engineering.paths import ReError

ALLOWED_OPERATIONS = frozenset(
    {
        "disassembly",
        "references",
        "signature",
        "type_layout",
        "data_accesses",
        "indirect_dispatch",
    }
)
ALLOWED_SCRIPTS = frozenset({"QueryFunctionEvidence.java", "ExportDecompilation.java"})
DEFAULT_HEADLESS_TIMEOUT = 600


def idempotency_key(
    inventory_digest: str,
    operation: str,
    rvas: list[str],
    extra: dict[str, Any],
    script_name: str,
) -> str:
    payload = {
        "digest": inventory_digest,
        "operation": operation,
        "rvas": sorted(rvas),
        "extra": extra,
        "script_hash": script_hash(script_name),
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()


def cache_path(cache_dir: Path, key: str, suffix: str = ".jsonl") -> Path:
    return cache_dir / f"{key[:32]}{suffix}"


def publish_atomic(staging: Path, final: Path) -> None:
    final.parent.mkdir(parents=True, exist_ok=True)
    staging.replace(final)


def _validate_jsonl_rows(rows: list[dict[str, Any]], operation: str, requested_rvas: list[str]) -> None:
    seen = {format_rva(parse_rva(r["entry_rva"])) for r in rows if r.get("entry_rva")}
    for rva in requested_rvas:
        if rva not in seen:
            raise ReError(f"ghidra output missing requested RVA {rva}")
    for row in rows:
        if row.get("operation") and row["operation"] != operation:
            raise ReError("ghidra output operation mismatch")


def run_query_evidence(
    conn: sqlite3.Connection,
    investigation_id: str,
    inventory_digest: str,
    export_dir: Path,
    binary_root: Path,
    cache_dir: Path,
    operation: str,
    rvas: list[str],
    max_insns: int = 128,
    project_id: str | None = None,
    ghidra_version: str | None = None,
    limits: budgets.BudgetLimits | None = None,
) -> list[dict[str, Any]]:
    if operation not in ALLOWED_OPERATIONS:
        raise ReError(f"unsupported Ghidra operation: {operation}")
    script = "QueryFunctionEvidence.java"
    if script not in ALLOWED_SCRIPTS:
        raise ReError("script not allowlisted")
    norm_rvas = [format_rva(parse_rva(r)) for r in rvas]

    ikey = idempotency_key(inventory_digest, operation, norm_rvas, {"max_insns": max_insns}, script)
    out_path = cache_path(cache_dir, ikey)
    out_path = out_path.resolve()

    # Step 1 — identity + cache
    identity = assert_authoritative_acquisition(export_dir, binary_root)
    project_id = project_id or identity.get("ghidra_project") or "unknown"
    ghidra_version = ghidra_version or identity.get("ghidra_version") or "unknown"

    row = ghidra_op_get(conn, ikey)
    if row and row["state"] == "published" and out_path.is_file():
        stored_hash = row["output_artifact_hash"]
        on_disk_hash = hashlib.sha256(out_path.read_bytes()).hexdigest()
        if stored_hash is None or stored_hash == on_disk_hash:
            return _read_jsonl(out_path)
        raise ReError("cached ghidra artifact hash mismatch")

    staging_guess = out_path.with_suffix(".staging.jsonl")
    if staging_guess.is_file():
        content = staging_guess.read_bytes()
        out_hash = hashlib.sha256(content).hexdigest()
        if not out_path.is_file():
            publish_atomic(staging_guess, out_path)
        reconcile_uncommitted_publish(conn, ikey, out_path, out_hash)

    row = ghidra_op_get(conn, ikey)
    if row and row["state"] == "published" and out_path.is_file():
        stored_hash = row["output_artifact_hash"]
        on_disk_hash = hashlib.sha256(out_path.read_bytes()).hexdigest()
        if stored_hash is None or stored_hash == on_disk_hash:
            return _read_jsonl(out_path)

    lim = limits or budgets.BudgetLimits()
    owner = f"{os.getpid()}-{uuid.uuid4().hex[:8]}"

    ghidra_op_reserve(
        conn,
        ikey,
        investigation_id,
        operation,
        {"rvas": norm_rvas, "max_insns": max_insns},
        lease_owner=owner,
    )
    budgets.check_and_increment(conn, investigation_id, "ghidra_queries", lim.max_ghidra_queries)
    ghidra_acquire_lease(conn, investigation_id, ikey, owner, max_concurrent=lim.max_concurrent_ghidra)

    ghidra_op_transition(conn, ikey, "running")

    meta = load_ghidra_meta(binary_root)
    staging = out_path.with_suffix(".staging.jsonl")
    timeout = int(os.environ.get("EU4_GHIDRA_TIMEOUT", str(DEFAULT_HEADLESS_TIMEOUT)))
    try:
        with tempfile.TemporaryDirectory() as tmp:
            rva_file = Path(tmp) / "rvas.txt"
            rva_file.write_text("\n".join(norm_rvas) + "\n")
            analyze_headless(
                Path(meta["project_dir"]),
                meta["project_name"],
                "-process",
                meta["program_name"],
                "-noanalysis",
                "-readOnly",
                "-postScript",
                script,
                str(rva_file),
                operation,
                str(staging),
                str(max_insns),
                timeout_seconds=timeout,
            )
        content = staging.read_bytes()
        out_hash = hashlib.sha256(content).hexdigest()
        rows = _read_jsonl(staging)
        _validate_jsonl_rows(rows, operation, norm_rvas)
        publish_atomic(staging, out_path)
        args_hash = args_canonical_hash({"operation": operation, "rvas": norm_rvas, "max_insns": max_insns})
        eid = ghidra_artifact_id(
            inventory_digest,
            project_id,
            ghidra_version,
            script_hash(script),
            operation,
            args_hash,
            out_hash,
        )
        ghidra_op_transition(
            conn,
            ikey,
            "published",
            output_hash=out_hash,
            output_path=str(out_path),
            release_lease=True,
        )
        register_evidence(
            conn,
            eid,
            "ghidra",
            {
                "operation": operation,
                "rvas": norm_rvas,
                "output_path": str(out_path),
                "output_hash": out_hash,
                "project_id": project_id,
                "ghidra_version": ghidra_version,
            },
        )
        return rows
    except Exception as exc:
        ghidra_op_transition(conn, ikey, "failed", error=str(exc), release_lease=True)
        raise


def run_decompile_batch(
    conn: sqlite3.Connection,
    investigation_id: str,
    inventory_digest: str,
    export_dir: Path,
    binary_root: Path,
    cache_dir: Path,
    rvas: list[str],
    timeout_seconds: int,
    limits: budgets.BudgetLimits | None = None,
) -> list[dict[str, Any]]:
    script = "ExportDecompilation.java"
    norm_rvas = [format_rva(parse_rva(r)) for r in rvas]
    ikey = idempotency_key(inventory_digest, "decompile", norm_rvas, {"timeout": timeout_seconds}, script)
    cache_dir.mkdir(parents=True, exist_ok=True)
    out_path = (cache_dir / f"{ikey[:16]}.jsonl").resolve()
    assert_authoritative_acquisition(export_dir, binary_root)

    row = ghidra_op_get(conn, ikey)
    if row and row["state"] == "published" and out_path.is_file():
        verify_published_artifact(conn, ikey)
        return _read_jsonl(out_path)

    lim = limits or budgets.BudgetLimits()
    owner = f"{os.getpid()}-{uuid.uuid4().hex[:8]}"
    ghidra_op_reserve(
        conn,
        ikey,
        investigation_id,
        "decompile",
        {"rvas": norm_rvas, "timeout": timeout_seconds},
        lease_owner=owner,
    )
    budgets.check_and_increment(
        conn, investigation_id, "decompilations", lim.max_new_decompilations, delta=len(norm_rvas)
    )
    ghidra_acquire_lease(conn, investigation_id, ikey, owner, max_concurrent=lim.max_concurrent_ghidra)
    ghidra_op_transition(conn, ikey, "running")
    meta = load_ghidra_meta(binary_root)
    staging = out_path.with_suffix(".staging.jsonl")
    try:
        with tempfile.TemporaryDirectory() as tmp:
            rva_file = Path(tmp) / "rvas.txt"
            rva_file.write_text("\n".join(norm_rvas) + "\n")
            analyze_headless(
                Path(meta["project_dir"]),
                meta["project_name"],
                "-process",
                meta["program_name"],
                "-noanalysis",
                "-readOnly",
                "-postScript",
                script,
                str(rva_file),
                str(staging),
                str(timeout_seconds),
                timeout_seconds=timeout_seconds,
            )
        content = staging.read_bytes()
        out_hash = hashlib.sha256(content).hexdigest()
        publish_atomic(staging, out_path)
        ghidra_op_transition(
            conn,
            ikey,
            "published",
            output_hash=out_hash,
            output_path=str(out_path),
            release_lease=True,
        )
        rows = _read_jsonl(out_path)
        for row in rows:
            if row.get("status") == "success" and row.get("pseudocode"):
                per = cache_dir / f"{row['entry_rva']}.json"
                per.write_text(json.dumps(row) + "\n")
        return rows
    except Exception as exc:
        ghidra_op_transition(conn, ikey, "failed", error=str(exc), release_lease=True)
        raise


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows = []
    for line in path.read_text().splitlines():
        if line.strip():
            rows.append(json.loads(line))
    return rows
