"""Parse and validate the graphics-observation registry."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PATH = ROOT / "benchmark" / "eu4_gfx_observe_entries.def"
RELOC_SAFE = "EU4_GFX_OBSERVE_RELOC_SAFE"
RELOC_UNSAFE = "EU4_GFX_OBSERVE_RELOC_UNSAFE"

_OBS_ENTRY = re.compile(
    r'OBS_ENTRY\(\s*"([^"]+)"\s*,\s*(0x[0-9a-fA-F]+)\s*,\s*'
    r'"((?:\\x[0-9a-fA-F]{2})*)"\s*,\s*'
    r"([A-Za-z_][A-Za-z0-9_]*)\s*,\s*"
    r"([A-Za-z_][A-Za-z0-9_]*)\s*,\s*"
    r"([A-Za-z_][A-Za-z0-9_]*)\s*\)"
)


@dataclass(frozen=True)
class ObserveEntry:
    symbol: str
    rva: int
    prefix: bytes
    relocation: str
    entry_cb: str
    exit_cb: str


def parse_observe_entries(text: str) -> tuple[ObserveEntry, ...]:
    found: list[ObserveEntry] = []
    for number, raw in enumerate(text.splitlines(), 1):
        line = raw.strip()
        if line == "" or line.startswith("//"):
            continue
        match = _OBS_ENTRY.fullmatch(line)
        if match is None:
            raise ValueError(f"observe registry line {number}")
        symbol, rva_text, encoded, relocation, entry_cb, exit_cb = match.groups()
        prefix = bytes.fromhex(encoded.replace("\\x", ""))
        if not 5 <= len(prefix) <= 13:
            raise ValueError(f"observe registry line {number}: prefix length")
        if relocation not in (RELOC_SAFE, RELOC_UNSAFE):
            raise ValueError(f"observe registry line {number}: relocation class")
        found.append(
            ObserveEntry(
                symbol=symbol,
                rva=int(rva_text, 16),
                prefix=prefix,
                relocation=relocation,
                entry_cb=entry_cb,
                exit_cb=exit_cb,
            )
        )
    if not found:
        raise ValueError("observe registry is empty")
    if len({item.symbol for item in found}) != len(found):
        raise ValueError("duplicate observe symbol")
    if len({item.rva for item in found}) != len(found):
        raise ValueError("duplicate observe rva")
    return tuple(found)


def load_entries(path: Path = DEFAULT_PATH) -> tuple[ObserveEntry, ...]:
    return parse_observe_entries(path.read_text())


def installable(entry: ObserveEntry) -> bool:
    return entry.relocation == RELOC_SAFE
