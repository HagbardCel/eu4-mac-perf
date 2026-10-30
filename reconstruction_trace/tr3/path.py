"""One legal execution of the frozen `_GfxSetConstantBuffer` inventory.

An ordered subset is not a path. Each record has to be a fallthrough or the
recorded target of the preceding `JZ` or `JMP`, and the trace has to finish
on the inventory's `RET`.
"""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
INVENTORY = ROOT / "analysis" / "reconstruction" / "_GfxSetConstantBuffer" / "instructions.json"
ENTRY_RVA = 0x15EC36A
RET_RVA = 0x15EC3B4


def load_inventory(path: Path | None = None) -> dict:
    return json.loads((path or INVENTORY).read_text())


def _rva(text: str) -> int:
    return int(text, 16)


def _target(operands: str, image_base: int) -> int:
    return int(operands.split()[-1], 16) - image_base


def successors(inst: dict, image_base: int) -> list[int]:
    """Legal next RVAs. `JMP` has no fallthrough. `RET` has none."""
    rva = _rva(inst["rva"])
    if inst["mnemonic"] == "RET":
        return []
    if inst["mnemonic"] == "JMP":
        return [_target(inst["operands"], image_base)]
    fallthrough = rva + inst["length"]
    if inst["mnemonic"] == "JZ":
        return [fallthrough, _target(inst["operands"], image_base)]
    return [fallthrough]


def path_gap(records: list[dict], inventory: dict, window: bytes) -> str | None:
    """Reason `records` is not one complete branch, or None."""
    entry = _rva(inventory["entry_rva"])
    end = _rva(inventory["end_rva"])
    if entry != ENTRY_RVA or len(window) != end - entry:
        return "inventory window is not the pinned function"
    by_rva = {_rva(inst["rva"]): inst for inst in inventory["instructions"]}
    ret = next(inst for inst in inventory["instructions"] if inst["mnemonic"] == "RET")
    if _rva(ret["rva"]) != RET_RVA:
        return "inventory ret is not at the pinned address"
    if not records or records[0]["rva"] != entry:
        return "path does not start at the entry"
    image_base = inventory["image_base"]
    entries = 0
    for index, record in enumerate(records):
        rva = record["rva"]
        if rva == entry:
            entries += 1
        if entries > 1:
            return "second execution"
        inst = by_rva.get(rva)
        if inst is None:
            return f"unknown instruction at {rva:#x}"
        if record["size"] != inst["length"]:
            return f"size mismatch at {rva:#x}"
        offset = rva - entry
        if record["bytes"] != window[offset:offset + inst["length"]]:
            return f"bytes mismatch at {rva:#x}"
        if index + 1 == len(records):
            if rva != RET_RVA:
                return "path does not end at the ret"
            return None
        nxt = records[index + 1]["rva"]
        if nxt not in successors(inst, image_base):
            return f"{rva:#x} does not continue to {nxt:#x}"
    return "path does not end at the ret"
