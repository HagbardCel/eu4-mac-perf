"""Range tuples derived from layout JSON (same source as codegen_subrecord_ranges.py)."""

from __future__ import annotations

from mesh_record_layout import export_tables


def _ranges(name: str) -> tuple[tuple[int, int, str], ...]:
    return tuple(
        (item["offset"], item["size"], item["comparison_policy"])
        for item in export_tables()[name]["ranges"]
    )


SFLUSHDATA_C_RANGES = _ranges("sflushdata_0x50")
MESH_DRAW_C_RANGES = _ranges("mesh_draw_subrecord_0xe8")

C_TABLES = {
    "sflushdata_0x50": SFLUSHDATA_C_RANGES,
    "mesh_draw_subrecord_0xe8": MESH_DRAW_C_RANGES,
}
