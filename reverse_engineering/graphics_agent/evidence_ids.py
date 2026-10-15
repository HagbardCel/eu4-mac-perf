"""Canonical evidence identity bound to pinned corpus artifacts."""

from __future__ import annotations

import hashlib
import json
from typing import Any


def digest_prefix(full_digest: str, n: int = 16) -> str:
    return full_digest.replace("-", "")[:n]


def call_edge_id(
    inventory_digest: str,
    callsite_rva: str,
    caller_rva: str | None,
    callee_key: str,
    edge_row_id: int | None = None,
) -> str:
    """Stable call-edge identity (multiple edges may share a callsite)."""
    parts = [
        digest_prefix(inventory_digest),
        callsite_rva,
        caller_rva or "",
        callee_key,
        str(edge_row_id) if edge_row_id is not None else "",
    ]
    disc = hashlib.sha256("|".join(parts).encode()).hexdigest()[:12]
    return f"call-edge:{digest_prefix(inventory_digest)}:{callsite_rva}:{disc}"


def function_id(inventory_digest: str, entry_rva: str) -> str:
    return f"fn:{digest_prefix(inventory_digest)}:{entry_rva}"


def ghidra_artifact_id(
    inventory_digest: str,
    project_id: str,
    ghidra_version: str,
    script_hash: str,
    operation: str,
    args_hash: str,
    output_artifact_hash: str,
) -> str:
    return (
        f"ghidra:{digest_prefix(inventory_digest)}:"
        f"{project_id}:{operation}:{args_hash[:12]}:{output_artifact_hash[:16]}"
    )


def args_canonical_hash(payload: dict[str, Any]) -> str:
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode()).hexdigest()
