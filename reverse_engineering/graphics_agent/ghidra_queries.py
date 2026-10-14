"""Bounded Ghidra headless queries with cache and op ledger."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import tempfile
from pathlib import Path
from typing import Any

from reverse_engineering.corpus import analyze_headless, load_ghidra_meta, script_hash
from reverse_engineering.graphics_agent.evidence_ids import args_canonical_hash, ghidra_artifact_id
from reverse_engineering.graphics_agent.research_store import ghidra_op_reserve, ghidra_op_transition
from reverse_engineering.intervals import format_rva, parse_rva
from reverse_engineering.paths import GHIDRA_SCRIPTS, ReError

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


def run_query_evidence(
    conn: sqlite3.Connection,
    investigation_id: str,
    inventory_digest: str,
    binary_root: Path,
    cache_dir: Path,
    operation: str,
    rvas: list[str],
    max_insns: int = 128,
    project_id: str = "unknown",
    ghidra_version: str = "unknown",
) -> list[dict[str, Any]]:
    if operation not in ALLOWED_OPERATIONS:
        raise ReError(f"unsupported Ghidra operation: {operation}")
    script = "QueryFunctionEvidence.java"
    if script not in ALLOWED_SCRIPTS:
        raise ReError("script not allowlisted")
    norm_rvas = [format_rva(parse_rva(r)) for r in rvas]
    ikey = idempotency_key(inventory_digest, operation, norm_rvas, {"max_insns": max_insns}, script)
    state = ghidra_op_reserve(
        conn,
        ikey,
        investigation_id,
        operation,
        {"rvas": norm_rvas, "max_insns": max_insns},
    )
    out_path = cache_path(cache_dir, ikey)
    if state == "published" and out_path.is_file():
        return _read_jsonl(out_path)
    if state in ("running", "failed") and out_path.is_file():
        return _read_jsonl(out_path)

    if state == "published" and not out_path.is_file():
        ghidra_op_transition(conn, ikey, "reserved")

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
                operation,
                str(staging),
                str(max_insns),
            )
        content = staging.read_bytes()
        out_hash = hashlib.sha256(content).hexdigest()
        publish_atomic(staging, out_path)
        ghidra_op_transition(conn, ikey, "published", output_hash=out_hash)
        args_hash = args_canonical_hash({"operation": operation, "rvas": norm_rvas, "max_insns": max_insns})
        _ = ghidra_artifact_id(
            inventory_digest,
            project_id,
            ghidra_version,
            script_hash(script),
            operation,
            args_hash,
            out_hash,
        )
        return _read_jsonl(out_path)
    except Exception as exc:
        ghidra_op_transition(conn, ikey, "failed", error=str(exc))
        raise


def run_decompile_batch(
    conn: sqlite3.Connection,
    investigation_id: str,
    inventory_digest: str,
    binary_root: Path,
    cache_dir: Path,
    rvas: list[str],
    timeout_seconds: int,
) -> list[dict[str, Any]]:
    script = "ExportDecompilation.java"
    norm_rvas = [format_rva(parse_rva(r)) for r in rvas]
    ikey = idempotency_key(inventory_digest, "decompile", norm_rvas, {"timeout": timeout_seconds}, script)
    state = ghidra_op_reserve(
        conn, ikey, investigation_id, "decompile", {"rvas": norm_rvas, "timeout": timeout_seconds}
    )
    cache_dir.mkdir(parents=True, exist_ok=True)
    out_path = cache_dir / f"{ikey[:16]}.jsonl"
    if state == "published" and out_path.is_file():
        return _read_jsonl(out_path)
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
            )
        content = staging.read_bytes()
        out_hash = hashlib.sha256(content).hexdigest()
        publish_atomic(staging, out_path)
        ghidra_op_transition(conn, ikey, "published", output_hash=out_hash)
        rows = _read_jsonl(out_path)
        for row in rows:
            if row.get("status") == "success" and row.get("pseudocode"):
                per = cache_dir / f"{row['entry_rva']}.json"
                per.write_text(json.dumps(row) + "\n")
        return rows
    except Exception as exc:
        ghidra_op_transition(conn, ikey, "failed", error=str(exc))
        raise


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows = []
    for line in path.read_text().splitlines():
        if line.strip():
            rows.append(json.loads(line))
    return rows
