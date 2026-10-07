"""Python mirror of benchmark/eu4_r0_control.h."""

from __future__ import annotations

import struct

EU4_R0_CONTROL_MAGIC = 0x5230304D
EU4_R0_CONTROL_PAGE_SIZE = 4096
EU4_R0_MODE_PASSIVE = 0
EU4_R0_MODE_CENSUS = 1

_CONTROL_STRUCT = struct.Struct("<IIII")


def pack_control_snapshot(*, generation: int, mode: int, scenario_id: int) -> bytes:
    return _CONTROL_STRUCT.pack(EU4_R0_CONTROL_MAGIC, generation, mode, scenario_id)


def unpack_control_snapshot(data: bytes) -> tuple[int, int, int, int]:
    magic, generation, mode, scenario_id = _CONTROL_STRUCT.unpack_from(data, 0)
    if magic != EU4_R0_CONTROL_MAGIC:
        raise ValueError("invalid R0 control magic")
    return generation, mode, scenario_id, magic
