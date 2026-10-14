"""Deterministic resolution of evidence IDs to registered artifacts."""

from __future__ import annotations

import sqlite3
from typing import Any

from reverse_engineering.graphics_agent.research_store import resolve_evidence_artifact


def resolve_evidence(conn: sqlite3.Connection, evidence_id: str) -> dict[str, Any] | None:
    return resolve_evidence_artifact(conn, evidence_id)


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
