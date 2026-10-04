#!/usr/bin/env python3
"""Validate mesh record layout JSON and export range tables for C parity checks."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]

VALID_ACCESS = frozenset(
    {
        "known_read",
        "known_unused_in_target_path",
        "padding/proven_unused",
        "unknown_read_or_indirect",
    }
)
VALID_POLICY = frozenset({"equal", "ignore", "barrier"})
POLICY_BY_ACCESS = {
    "known_read": frozenset({"equal"}),
    "known_unused_in_target_path": frozenset({"ignore"}),
    "padding/proven_unused": frozenset({"ignore"}),
    "unknown_read_or_indirect": frozenset({"barrier"}),
}

LAYOUT_PATHS = {
    "sflushdata_0x50": ROOT / "analysis/sflushdata-0x50-layout.json",
    "mesh_draw_subrecord_0xe8": ROOT / "analysis/mesh-draw-subrecord-0xe8-layout.json",
}


def _parse_offset(text: str) -> int:
    return int(text, 16) if text.startswith("0x") else int(text)


def validate_layout(doc: dict[str, Any]) -> list[dict[str, Any]]:
    size = int(doc["record_size_bytes"])
    ranges: list[dict[str, Any]] = []
    cursor = 0
    for field in doc["fields"]:
        offset = _parse_offset(field["offset"])
        length = int(field["size"])
        access = field["access"]
        policy = field["comparison_policy"]
        if access not in VALID_ACCESS:
            raise ValueError(f"invalid access {access}")
        if policy not in VALID_POLICY:
            raise ValueError(f"invalid policy {policy}")
        if policy not in POLICY_BY_ACCESS[access]:
            raise ValueError(f"policy {policy} invalid for access {access}")
        if offset != cursor:
            raise ValueError(f"gap or overlap at {offset:#x} expected {cursor:#x}")
        if offset + length > size:
            raise ValueError("field extends past record_size_bytes")
        ranges.append(
            {
                "offset": offset,
                "size": length,
                "access": access,
                "comparison_policy": policy,
                "role": field.get("role", ""),
            }
        )
        cursor += length
    if cursor != size:
        raise ValueError(f"incomplete coverage: {cursor} != {size}")
    return ranges


def load_layout(name: str) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    path = LAYOUT_PATHS[name]
    doc = json.loads(path.read_text(encoding="utf-8"))
    return doc, validate_layout(doc)


def export_tables() -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key in LAYOUT_PATHS:
        doc, ranges = load_layout(key)
        out[key] = {
            "record_size_bytes": doc["record_size_bytes"],
            "ranges": ranges,
        }
    return out
