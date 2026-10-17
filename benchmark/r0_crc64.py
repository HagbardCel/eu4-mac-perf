#!/usr/bin/env python3
"""ECMA CRC-64 matching eu4_r0_crc64_ecma in eu4_r0_readback.c."""

from __future__ import annotations

_POLY = 0xC96C5795D7870F42
_TABLE: list[int] | None = None


def _init_table() -> list[int]:
    global _TABLE
    if _TABLE is not None:
        return _TABLE
    table: list[int] = []
    for byte in range(256):
        crc = byte
        for _ in range(8):
            if crc & 1:
                crc = (crc >> 1) ^ _POLY
            else:
                crc >>= 1
        table.append(crc & 0xFFFFFFFFFFFFFFFF)
    _TABLE = table
    return table


def crc64_ecma(data: bytes) -> int:
    table = _init_table()
    crc = 0
    for byte in data:
        crc = table[(crc ^ byte) & 0xFF] ^ (crc >> 8)
        crc &= 0xFFFFFFFFFFFFFFFF
    return crc
