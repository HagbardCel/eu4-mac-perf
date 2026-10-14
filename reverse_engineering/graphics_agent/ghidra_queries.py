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
from reverse_engineering.graphics_agent.ghidra_acquire import (
    AcquireOutcome,
    ghidra_complete_publish,
    ghidra_mark_running,
    ghidra_try_acquire,
    project_key_from_identity,
    reconcile_published_evidence,
)
from reverse_engineering.graphics_agent.provenance_guard import assert_authoritative_acquisition
from reverse_engineering.graphics_agent.research_store import ghidra_op_get, ghidra_op_transition
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
    identity: dict[str, Any],
    inventory_digest: str,
    operation: str,
    rvas: list[str],
    extra: dict[str, Any],
    script_name: str,
) -> str:
    payload = {
        "digest": inventory_digest,
        "ghidra_version": identity.get("ghidra_version"),
        "project_name": identity.get("project_name"),
        "project_meta_hash": identity.get("project_meta_hash"),
        "program_name": identity.get("program_name"),
        "operation": operation,
        "rvas": sorted(rvas),
        "extra": extra,
        "script_hash": script_hash(script_name),
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()


def cache_path(cache_dir: Path, key: str, suffix: str = ".jsonl") -> Path:
    return cache_dir / f"{key[:32]}{suffix}"


def meta_path(cache_dir: Path, key: str) -> Path:
    return cache_dir / f"{key[:32]}.meta.json"


def publish_atomic(staging: Path, final: Path) -> None:
    final.parent.mkdir(parents=True, exist_ok=True)
    staging.replace(final)


def write_meta(meta_file: Path, identity: dict[str, Any], output_hash: str) -> None:
    meta_file.write_text(
        json.dumps(
            {
                "ghidra_version": identity.get("ghidra_version"),
                "project_meta_hash": identity.get("project_meta_hash"),
                "output_hash": output_hash,
            },
            indent=2,
        )
        + "\n"
    )


def validate_meta(meta_file: Path, identity: dict[str, Any], output_hash: str) -> None:
    if not meta_file.is_file():
        raise ReError("missing ghidra cache metadata sidecar")
    meta = json.loads(meta_file.read_text())
    if meta.get("output_hash") != output_hash:
        raise ReError("cache metadata hash mismatch")
    if meta.get("project_meta_hash") != identity.get("project_meta_hash"):
        raise ReError("cache metadata project identity mismatch")


def validate_stored_ghidra_artifact(
    out_path: Path,
    meta_file: Path,
    identity: dict[str, Any],
    operation: str,
    requested_rvas: list[str],
    output_hash: str,
) -> list[dict[str, Any]]:
    """Re-validate recovered or cached artifacts before they become authoritative."""
    if not out_path.is_file():
        raise ReError("ghidra artifact file missing")
    on_disk = hashlib.sha256(out_path.read_bytes()).hexdigest()
    if on_disk != output_hash:
        raise ReError("ghidra artifact hash mismatch")
    validate_meta(meta_file, identity, output_hash)
    rows = _read_jsonl(out_path)
    _validate_jsonl_rows(rows, operation, requested_rvas)
    return rows


def _validate_jsonl_rows(rows: list[dict[str, Any]], operation: str, requested_rvas: list[str]) -> None:
    seen = {format_rva(parse_rva(r["entry_rva"])) for r in rows if r.get("entry_rva")}
    for rva in requested_rvas:
        if rva not in seen:
            raise ReError(f"ghidra output missing requested RVA {rva}")
    for row in rows:
        if row.get("operation") and row["operation"] != operation:
            raise ReError("ghidra output operation mismatch")


def _assert_rvas_in_inventory(export_dir: Path, rvas: list[str]) -> None:
    fn_path = export_dir / "inventory" / "functions.jsonl"
    if not fn_path.is_file():
        return
    known = set()
    for line in fn_path.read_text().splitlines():
        if line.strip():
            known.add(json.loads(line).get("entry_rva"))
    for rva in rvas:
        if rva not in known:
            raise ReError(f"RVA not in inventory functions: {rva}")


def _cache_hit(
    conn: sqlite3.Connection,
    ikey: str,
    out_path: Path,
    meta_file: Path,
    identity: dict[str, Any],
) -> list[dict[str, Any]] | None:
    row = ghidra_op_get(conn, ikey)
    if not row or row["state"] != "published" or not out_path.is_file():
        return None
    stored = row["output_artifact_hash"]
    if not stored:
        raise ReError("published ghidra op missing output hash")
    on_disk = hashlib.sha256(out_path.read_bytes()).hexdigest()
    if stored != on_disk:
        raise ReError("cached ghidra artifact hash mismatch")
    validate_meta(meta_file, identity, stored)
    return _read_jsonl(out_path)


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
    limits: budgets.BudgetLimits | None = None,
) -> list[dict[str, Any]]:
    if operation not in ALLOWED_OPERATIONS:
        raise ReError(f"unsupported Ghidra operation: {operation}")
    script = "QueryFunctionEvidence.java"
    norm_rvas = [format_rva(parse_rva(r)) for r in rvas]
    _assert_rvas_in_inventory(export_dir, norm_rvas)

    identity = assert_authoritative_acquisition(export_dir, binary_root)
    project_key = project_key_from_identity(identity)
    ikey = idempotency_key(identity, inventory_digest, operation, norm_rvas, {"max_insns": max_insns}, script)
    out_path = cache_path(cache_dir, ikey).resolve()
    meta_file = meta_path(cache_dir, ikey)

    hit = _cache_hit(conn, ikey, out_path, meta_file, identity)
    if hit is not None:
        return hit

    staging_guess = out_path.with_suffix(".staging.jsonl")
    if staging_guess.is_file() and not out_path.is_file():
        publish_atomic(staging_guess, out_path)
    if out_path.is_file():
        out_hash = hashlib.sha256(out_path.read_bytes()).hexdigest()
        args_hash = args_canonical_hash({"operation": operation, "rvas": norm_rvas, "max_insns": max_insns})
        eid = ghidra_artifact_id(
            inventory_digest,
            identity.get("project_name", "unknown"),
            identity.get("ghidra_version", "unknown"),
            script_hash(script),
            operation,
            args_hash,
            out_hash,
        )
        reconcile_published_evidence(
            conn,
            ikey,
            out_path,
            out_hash,
            eid,
            "ghidra",
            {
                "operation": operation,
                "rvas": norm_rvas,
                "output_path": str(out_path),
                "output_hash": out_hash,
            },
            project_key,
            meta_file=meta_file,
            identity=identity,
        )
        hit = _cache_hit(conn, ikey, out_path, meta_file, identity)
        if hit is not None:
            return hit

    lim = limits or budgets.BudgetLimits()
    owner = f"{os.getpid()}-{uuid.uuid4().hex[:8]}"
    acquire = ghidra_try_acquire(
        conn,
        project_key=project_key,
        idempotency_key=ikey,
        investigation_id=investigation_id,
        operation=operation,
        args={"rvas": norm_rvas, "max_insns": max_insns},
        owner=owner,
        worker_pid=os.getpid(),
        quota_key="ghidra_queries",
        quota_limit=lim.max_ghidra_queries,
        quota_delta=1,
        max_concurrent=lim.max_concurrent_ghidra,
    )
    if acquire.outcome == AcquireOutcome.CACHE_HIT:
        return _read_jsonl(out_path)

    ghidra_mark_running(conn, ikey, acquire.lease_generation)
    meta = load_ghidra_meta(binary_root) if not fixture_mode() else {
        "project_dir": str(binary_root / "ghidra-project"),
        "project_name": "fixture",
        "program_name": "fixture",
    }
    staging = out_path.with_suffix(".staging.jsonl")
    timeout = int(os.environ.get("EU4_GHIDRA_TIMEOUT", str(DEFAULT_HEADLESS_TIMEOUT)))
    try:
        with tempfile.TemporaryDirectory() as tmp:
            rva_file = Path(tmp) / "rvas.txt"
            rva_file.write_text("\n".join(norm_rvas) + "\n")
            if fixture_mode():
                raise ReError("headless ghidra not available in fixture mode")
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
        write_meta(meta_file, identity, out_hash)
        args_hash = args_canonical_hash({"operation": operation, "rvas": norm_rvas, "max_insns": max_insns})
        eid = ghidra_artifact_id(
            inventory_digest,
            identity.get("project_name", "unknown"),
            identity.get("ghidra_version", "unknown"),
            script_hash(script),
            operation,
            args_hash,
            out_hash,
        )
        ghidra_complete_publish(
            conn,
            idempotency_key=ikey,
            project_key=project_key,
            lease_generation=acquire.lease_generation,
            output_hash=out_hash,
            output_path=str(out_path),
            evidence_id=eid,
            evidence_kind="ghidra",
            evidence_artifact={
                "operation": operation,
                "rvas": norm_rvas,
                "output_path": str(out_path),
                "output_hash": out_hash,
            },
        )
        return rows
    except Exception as exc:
        ghidra_op_transition(conn, ikey, "failed", error=str(exc), release_lease=True)
        raise


def fixture_mode() -> bool:
    from reverse_engineering.graphics_agent.provenance_guard import fixture_mode as _fm

    return _fm()


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
    _assert_rvas_in_inventory(export_dir, norm_rvas)
    identity = assert_authoritative_acquisition(export_dir, binary_root)
    project_key = project_key_from_identity(identity)
    ikey = idempotency_key(
        identity, inventory_digest, "decompile", norm_rvas, {"timeout": timeout_seconds}, script
    )
    cache_dir.mkdir(parents=True, exist_ok=True)
    out_path = cache_path(cache_dir, ikey).resolve()
    meta_file = meta_path(cache_dir, ikey)

    hit = _cache_hit(conn, ikey, out_path, meta_file, identity)
    if hit is not None:
        return hit

    lim = limits or budgets.BudgetLimits()
    owner = f"{os.getpid()}-{uuid.uuid4().hex[:8]}"
    acquire = ghidra_try_acquire(
        conn,
        project_key=project_key,
        idempotency_key=ikey,
        investigation_id=investigation_id,
        operation="decompile",
        args={"rvas": norm_rvas, "timeout": timeout_seconds},
        owner=owner,
        worker_pid=os.getpid(),
        quota_key="decompilations",
        quota_limit=lim.max_new_decompilations,
        quota_delta=len(norm_rvas),
        max_concurrent=lim.max_concurrent_ghidra,
    )
    if acquire.outcome == AcquireOutcome.CACHE_HIT:
        return _read_jsonl(out_path)

    ghidra_mark_running(conn, ikey, acquire.lease_generation)
    meta = load_ghidra_meta(binary_root)
    staging = out_path.with_suffix(".staging.jsonl")
    try:
        with tempfile.TemporaryDirectory() as tmp:
            rva_file = Path(tmp) / "rvas.txt"
            rva_file.write_text("\n".join(norm_rvas) + "\n")
            if fixture_mode():
                raise ReError("headless ghidra not available in fixture mode")
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
        rows = _read_jsonl(staging)
        for rva in norm_rvas:
            if not any(format_rva(parse_rva(r.get("entry_rva", "0"))) == rva for r in rows):
                raise ReError(f"decompile output missing {rva}")
        publish_atomic(staging, out_path)
        write_meta(meta_file, identity, out_hash)
        args_hash = args_canonical_hash({"operation": "decompile", "rvas": norm_rvas})
        eid = ghidra_artifact_id(
            inventory_digest,
            identity.get("project_name", "unknown"),
            identity.get("ghidra_version", "unknown"),
            script_hash(script),
            "decompile",
            args_hash,
            out_hash,
        )
        ghidra_complete_publish(
            conn,
            idempotency_key=ikey,
            project_key=project_key,
            lease_generation=acquire.lease_generation,
            output_hash=out_hash,
            output_path=str(out_path),
            evidence_id=eid,
            evidence_kind="ghidra",
            evidence_artifact={
                "operation": "decompile",
                "rvas": norm_rvas,
                "output_path": str(out_path),
                "output_hash": out_hash,
            },
        )
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


def parse_jsonl_file(path: Path) -> list[dict[str, Any]]:
    """Validate every line parses as JSON (for tests and pilot)."""
    return _read_jsonl(path)
