"""C range tables for mesh records; must match subrecord_equivalence.c and layout JSON."""

from __future__ import annotations

# (offset, size, policy) where policy is equal|barrier|ignore
SFLUSHDATA_C_RANGES: tuple[tuple[int, int, str], ...] = (
    (0x00, 4, "equal"),
    (0x04, 4, "barrier"),
    (0x08, 8, "equal"),
    (0x10, 64, "equal"),
)

MESH_DRAW_C_RANGES: tuple[tuple[int, int, str], ...] = (
    (0x00, 48, "barrier"),
    (0x30, 8, "equal"),
    (0x38, 8, "equal"),
    (0x40, 8, "equal"),
    (0x48, 53, "barrier"),
    (0x7d, 1, "equal"),
    (0x7e, 106, "barrier"),
)

C_TABLES = {
    "sflushdata_0x50": SFLUSHDATA_C_RANGES,
    "mesh_draw_subrecord_0xe8": MESH_DRAW_C_RANGES,
}
