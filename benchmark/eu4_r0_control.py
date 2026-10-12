"""Python mirror of benchmark/eu4_r0_control.h (atomic word layout)."""

from __future__ import annotations

import struct

EU4_R0_CONTROL_PAGE_SIZE = 4096
EU4_R0_CONTROL_VERSION = 1
EU4_R0_MODE_PASSIVE = 0
EU4_R0_MODE_CENSUS = 1


def pack_control_word(*, generation: int, mode: int, scenario_id: int, version: int = EU4_R0_CONTROL_VERSION) -> int:
    if version == 0 or version > 255 or mode > 255 or scenario_id > 65535:
        raise ValueError("control field out of range")
    return (
        (version & 0xFF) << 56
        | (mode & 0xFF) << 48
        | (scenario_id & 0xFFFF) << 32
        | (generation & 0xFFFFFFFF)
    )


def unpack_control_word(word: int) -> tuple[int, int, int, int, bool]:
    generation = word & 0xFFFFFFFF
    scenario_id = (word >> 32) & 0xFFFF
    mode = (word >> 48) & 0xFF
    version = (word >> 56) & 0xFF
    valid = version == EU4_R0_CONTROL_VERSION and mode in (EU4_R0_MODE_PASSIVE, EU4_R0_MODE_CENSUS)
    return generation, mode, scenario_id, version, valid


def pack_control_snapshot(*, generation: int, mode: int, scenario_id: int) -> bytes:
    return struct.Struct("<Q").pack(
        pack_control_word(generation=generation, mode=mode, scenario_id=scenario_id)
    )


def unpack_control_snapshot(data: bytes) -> tuple[int, int, int, int]:
    word = struct.Struct("<Q").unpack_from(data, 0)[0]
    generation, mode, scenario_id, version, valid = unpack_control_word(word)
    if not valid:
        raise ValueError("invalid R0 control word")
    return generation, mode, scenario_id, version
