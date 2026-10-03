#!/usr/bin/env python3
"""Nearest-symbol resolution for pinned GOG EU IV x86-64 offsets."""

from __future__ import annotations

import bisect
import re
import subprocess
from functools import lru_cache
from pathlib import Path

import eu4_benchmark as base

ROOT = Path(__file__).resolve().parents[1]
IMAGE_BASE = 0x100000000
EXPECTED_SHA256 = "b3d38876abf4e61cdae57509186d7cb7dcb03bfeaca4c95c10c713794715141d"
_SYMBOL = re.compile(r"^([0-9a-f]{16}) \(__TEXT,__text\) .*? (\S+)$")


@lru_cache(maxsize=1)
def _symbol_table() -> tuple[list[int], list[str]]:
    if base.sha256(base.GOG_EXE) != EXPECTED_SHA256:
        raise base.BenchmarkError("Installed GOG EU IV is not the pinned executable for symbolization")
    listed = subprocess.run(
        ["nm", "-nm", str(base.GOG_EXE)],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    addresses: list[int] = []
    names: list[str] = []
    for line in listed.splitlines():
        match = _SYMBOL.match(line)
        if match:
            addresses.append(int(match[1], 16))
            names.append(match[2])
    return addresses, names


def eu4_symbolize_offset(offset: int, *, demangle: bool = True) -> dict:
    """Resolve a file-relative offset (eu4+0x…) to the nearest symbol."""
    if offset < 0:
        return {"offset": offset, "symbol": None, "demangled": None}
    addresses, names = _symbol_table()
    if not addresses:
        return {"offset": offset, "symbol": "unknown", "demangled": "unknown"}
    position = bisect.bisect_right(addresses, IMAGE_BASE + offset) - 1
    symbol = names[position] if position >= 0 else "unknown"
    demangled = symbol
    if demangle and symbol != "unknown":
        result = subprocess.run(
            ["c++filt", symbol],
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode == 0 and result.stdout.strip():
            demangled = result.stdout.strip()
    return {
        "offset": offset,
        "symbol": symbol,
        "demangled": demangled,
        "eu4": f"eu4+0x{offset:x}",
    }
