"""Certification validation for Ghidra six-operation JSONL (merge gate)."""

from __future__ import annotations

import json
from typing import Any

from reverse_engineering.intervals import format_rva, parse_rva
from reverse_engineering.paths import ReError

SIX_SEMANTIC_OPERATIONS = (
    "disassembly",
    "references",
    "signature",
    "type_layout",
    "data_accesses",
    "indirect_dispatch",
)

ALLOWED_PARTIAL_STATUSES = frozenset(
    {
        "partial",
        "complete_for_requested_scope",
    }
)

ALLOWED_UNRESOLVED_STATUSES = frozenset({"unresolved"})


class SixOpCertificationError(ReError):
    pass


def _parse_facts(row: dict[str, Any]) -> list[dict[str, Any]]:
    raw = row.get("facts") or []
    out: list[dict[str, Any]] = []
    for item in raw:
        if isinstance(item, str):
            out.append(json.loads(item))
        elif isinstance(item, dict):
            out.append(item)
    return out


def _has_instruction_facts(facts: list[dict[str, Any]]) -> bool:
    return any(
        f.get("kind") == "instruction" and (f.get("mnemonic") or f.get("text"))
        for f in facts
    )


def _has_signature_facts(facts: list[dict[str, Any]]) -> bool:
    return any(f.get("kind") == "signature" and f.get("name") for f in facts)


def _validated_partial(row: dict[str, Any]) -> bool:
    status = row.get("status")
    detail = (row.get("status_detail") or "").strip()
    if status in ALLOWED_PARTIAL_STATUSES:
        return bool(_parse_facts(row)) or bool(detail)
    if status in ALLOWED_UNRESOLVED_STATUSES:
        return bool(detail) and detail != "no_function_at_rva"
    return False


def certify_operation_row(
    operation: str,
    row: dict[str, Any],
    requested_rva: str,
    *,
    inventory_has_function: bool = True,
) -> None:
    """Require meaningful output or an explicit, validated partial/unresolved reason."""
    want = format_rva(parse_rva(requested_rva))
    entry = format_rva(parse_rva(row.get("entry_rva", "0")))
    if entry != want:
        raise SixOpCertificationError(
            f"{operation}: entry_rva {entry} does not match requested function {want}"
        )
    if row.get("operation") and row["operation"] != operation:
        raise SixOpCertificationError(f"{operation}: row operation mismatch {row.get('operation')}")

    status = row.get("status")
    detail = row.get("status_detail") or ""

    if status == "unresolved" and detail == "no_function_at_rva":
        if inventory_has_function:
            raise SixOpCertificationError(
                f"{operation}: unresolved no_function_at_rva for inventory-known RVA {want}"
            )
        return

    if status == "unsupported":
        raise SixOpCertificationError(f"{operation}: unsupported operation output")

    facts = _parse_facts(row)

    if operation == "disassembly":
        if not _has_instruction_facts(facts):
            raise SixOpCertificationError(
                f"disassembly: missing instruction facts with mnemonic for {want}"
            )
        return

    if operation == "signature":
        if not _has_signature_facts(facts):
            raise SixOpCertificationError(f"signature: missing signature facts for {want}")
        return

    if facts:
        return
    if not _validated_partial(row):
        raise SixOpCertificationError(
            f"{operation}: empty facts without validated partial/unresolved reason for {want}"
        )


def certify_six_op_bundle(
    results: dict[str, list[dict[str, Any]]],
    requested_rva: str,
    *,
    inventory_has_function: bool = True,
) -> None:
    missing = [op for op in SIX_SEMANTIC_OPERATIONS if op not in results]
    if missing:
        raise SixOpCertificationError(f"missing operations: {missing}")
    for op, rows in results.items():
        if not rows:
            raise SixOpCertificationError(f"{op}: empty result rows")
        certify_operation_row(op, rows[0], requested_rva, inventory_has_function=inventory_has_function)
