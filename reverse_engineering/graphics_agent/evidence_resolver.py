"""Deterministic resolution of evidence IDs to registered artifacts and content."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path
from typing import Any

from reverse_engineering.graphics_agent.research_store import resolve_evidence_artifact
from reverse_engineering.intervals import format_rva, parse_rva

MAX_CONTENT_BYTES = 32_000


def _truncate_json_array(items: list[Any], max_bytes: int) -> tuple[list[Any], bool]:
    truncated = False
    while items:
        blob = json.dumps(items)
        if len(blob.encode("utf-8")) <= max_bytes:
            return items, truncated
        items = items[: max(1, len(items) // 2)]
        truncated = True
    return items, True


def resolve_evidence(conn: sqlite3.Connection, evidence_id: str) -> dict[str, Any] | None:
    return resolve_evidence_artifact(conn, evidence_id)


def load_evidence_content(conn: sqlite3.Connection, evidence_id: str) -> dict[str, Any] | None:
    resolved = resolve_evidence(conn, evidence_id)
    if not resolved:
        return None
    kind = resolved["kind"]
    artifact = resolved["artifact"] or {}
    content: dict[str, Any] = {"evidence_id": evidence_id, "kind": kind, "artifact": artifact}
    if kind == "ghidra":
        path = Path(artifact.get("output_path", ""))
        out_hash = artifact.get("output_hash")
        if not path.is_file() or not out_hash:
            content["error"] = "missing_ghidra_artifact"
            return content
        raw = path.read_bytes()
        if hashlib.sha256(raw).hexdigest() != out_hash:
            content["error"] = "ghidra_artifact_hash_mismatch"
            return content
        rows = []
        for line in raw.decode("utf-8", errors="replace").splitlines():
            if line.strip():
                rows.append(json.loads(line))
        target_rvas = artifact.get("rvas") or []
        filtered = rows
        if target_rvas:
            want = {format_rva(parse_rva(r)) for r in target_rvas}
            filtered = [r for r in rows if format_rva(parse_rva(r.get("entry_rva", "0"))) in want]
        sliced, content_truncated = _truncate_json_array(filtered, MAX_CONTENT_BYTES)
        content["content"] = sliced
        content["content_truncated"] = content_truncated or len(json.dumps(filtered)) > MAX_CONTENT_BYTES
    elif kind == "call-edge":
        content["content"] = artifact
    elif kind == "function":
        content["content"] = artifact
    else:
        content["content"] = artifact
    return content


def evidence_summary_for_verifier(resolved: dict[str, Any]) -> dict[str, Any]:
    kind = resolved.get("kind")
    artifact = resolved.get("artifact") or {}
    if kind == "call-edge":
        return {
            "kind": kind,
            "ultimate_external_symbol": artifact.get("ultimate_external_symbol"),
            "callee_external_symbol": artifact.get("callee_external_symbol"),
            "caller_rva": artifact.get("caller_rva"),
            "callsite_rva": artifact.get("callsite_rva"),
            "resolution": artifact.get("resolution"),
        }
    if kind == "function":
        fn = artifact.get("function") or {}
        return {"kind": kind, "entry_rva": artifact.get("entry_rva"), "name": fn.get("name_mangled")}
    if kind == "ghidra":
        return {
            "kind": kind,
            "operation": artifact.get("operation"),
            "rvas": artifact.get("rvas"),
            "output_hash": artifact.get("output_hash"),
        }
    return {"kind": kind, "artifact": artifact}
