"""One `_GfxSetConstantBuffer` observation from a single run.

`handwritten-synthetic` may use regions the test built. `qbdi-rosetta` may use
only the regions, bytes, and edges in that capture's entry state. A loaded
qword is not a context base. This module does not launch EU4.
"""
from __future__ import annotations

import copy
import hashlib

from reconstruction_trace import schema
from reconstruction_trace.tr3 import image, observe, path

FUNCTION_ID = "_GfxSetConstantBuffer"
PIN_FILE = "b3d38876abf4e61cdae57509186d7cb7dcb03bfeaca4c95c10c713794715141d"
PIN_WINDOW = "8e5f94bf3f132cbf17397928c72df44b7fb6d24bcc95575702c457fb48e32943"
PIN_ENTRY = 0x15EC36A
PIN_END = 0x15EC3B5
PIN_BASE = 0x100000000
LIMITS = ("another thread entering while the hook is removed is untraced; "
          "register restore has not been shown inside EU4")


def interpret(candidates: list[dict]) -> dict:
    """Interpret the first candidate. Later candidates are not read."""
    selected = observe.select_one(candidates)
    refusal = _never_ran(selected)
    if refusal:
        return {"diagnostic": refusal, "observation": None, "limits": LIMITS}
    if selected.get("evidence") == "qbdi-rosetta":
        return _native(selected)
    if selected.get("evidence") == "handwritten-synthetic":
        return _synthetic(selected)
    return {"diagnostic": "unauthenticated-target", "observation": None, "limits": LIMITS}


def _never_ran(selected: dict) -> str | None:
    if selected.get("refusal"):
        return selected["refusal"]
    run = selected.get("run") or {}
    if not run.get("ran") or not run.get("entered"):
        return "QBDI executed no block"
    return None


def _contains(regions: list[dict], start: int, length: int) -> bool:
    end = start + length
    return any(int(region["base"], 16) <= start and end <= int(region["base"], 16) + region["size"]
               for region in regions)


def _fill(window: bytes, records: list[dict]) -> list[dict]:
    filled = []
    for record in records:
        rva, size = record["rva"], record["size"]
        offset = rva - PIN_ENTRY
        chunk = window[offset:offset + size] if 0 <= offset <= len(window) - size else b""
        filled.append({"rva": rva, "size": size, "bytes": chunk})
    return filled


def _finish(trace: dict, extra: list[str], unresolved: list[dict], run: dict) -> dict:
    traced = observe.apply_accounting(
        trace, dropped_instructions=run.get("dropped_instructions", 0),
        dropped_accesses=run.get("dropped_accesses", 0),
        dropped_transfers=run.get("dropped_transfers", 0))
    reasons = list(traced["incomplete_reasons"])
    for reason in extra:
        if reason not in reasons:
            reasons.append(reason)
    for item in unresolved:
        text = f"{item['reason']} at {item['address']} size {item['size']}"
        if text not in reasons:
            reasons.append(text)
    traced["incomplete_reasons"] = reasons
    traced["unresolved_dependencies"] = unresolved
    traced["completeness"] = "incomplete" if reasons or unresolved else "complete"
    return traced


def _outside(events: list[dict], regions: list[dict], reason: str) -> list[dict]:
    unresolved = []
    for event in events:
        if event.get("kind") not in ("read", "write"):
            continue
        start, length = int(event["address"], 16), event["size"]
        if not _contains(regions, start, length):
            unresolved.append({"address": hex(start), "size": length, "reason": reason})
    return unresolved


def _synthetic(selected: dict) -> dict:
    window = selected["window"]
    gap = path.path_gap(_fill(window, selected["records"]), path.load_inventory(), window)
    trace = copy.deepcopy(selected["trace"])
    trace["function_id"] = FUNCTION_ID
    trace["evidence"] = "handwritten-synthetic"
    trace["binary"] = None
    trace["completeness"] = "complete"
    trace["incomplete_reasons"] = []
    trace["unresolved_dependencies"] = []
    extra = [] if gap is None else [gap]
    unresolved = _outside(trace["events"], trace["entry_state"]["regions"],
                          "access outside the supplied regions")
    traced = _finish(trace, extra, unresolved, selected["run"])
    diagnostic = gap or (unresolved[0]["reason"] if unresolved else None)
    return {"diagnostic": diagnostic, "observation": traced, "limits": LIMITS}


def _identity(raw: dict) -> str | None:
    """Reason this capture is not the pinned EU4 image, or None."""
    if (raw.get("expected_file_sha256") != PIN_FILE
            or raw.get("expected_window_sha256") != PIN_WINDOW):
        return "expected digest is not the immutable pin"
    if raw.get("file_sha256") != PIN_FILE:
        return "file hash mismatch"
    if raw.get("window_sha256") != PIN_WINDOW:
        return "window hash mismatch"
    meta = raw.get("image") or {}
    try:
        preferred = int(meta["preferred_base"], 16)
        slide = int(meta["slide"], 16)
        header = int(meta["header"], 16)
        entry = int(meta["entry_rva"], 16)
        site = int(meta["runtime_site"], 16)
    except (KeyError, TypeError, ValueError):
        return "capture address metadata is absent"
    if preferred != PIN_BASE or entry != PIN_ENTRY:
        return "capture is not the pinned image"
    try:
        loaded = image.runtime_site(preferred, slide, entry, header)
    except ValueError:
        return "loaded header is not the preferred base plus the slide"
    if loaded != site:
        return "runtime site does not match preferred base plus slide plus entry"
    for inst in raw["run"].get("instructions") or []:
        pc = int(inst["pc"], 16)
        if pc < site or pc >= site + (PIN_END - PIN_ENTRY):
            return "instruction pc is outside the pinned window"
    window = raw.get("window")
    if not isinstance(window, (bytes, bytearray)) or len(window) != PIN_END - PIN_ENTRY:
        return "window length is not 75"
    if hashlib.sha256(window).hexdigest() != PIN_WINDOW:
        return "mapped window is not the pin"
    return None


def _binary() -> dict:
    return {"sha256": PIN_FILE, "image_base": hex(PIN_BASE), "entry_rva": hex(PIN_ENTRY)}


def _native_events(run: dict, regions: list[dict]) -> tuple[list[dict], list[dict]]:
    events, unresolved = [], []
    for inst in run.get("instructions") or []:
        for access in inst.get("accesses") or []:
            kind = {1: "read", 2: "write"}.get(access.get("type"), access.get("kind"))
            if kind not in ("read", "write"):
                continue
            start, length = int(access["address"], 16), access["size"]
            event = {"kind": kind, "pc": inst["pc"], "address": hex(start), "size": length}
            if "value" in access:
                value = access["value"]
                event["value"] = value if isinstance(value, str) else value.to_bytes(length, "little").hex()
                event["value_source"] = "observed"
            events.append(event)
            if not _contains(regions, start, length):
                unresolved.append({"address": hex(start), "size": length,
                                   "reason": "access outside the captured entry state"})
    return events, unresolved


def _native(selected: dict) -> dict:
    reason = _identity(selected)
    run = selected["run"]
    regions = []
    for region in run.get("regions") or []:
        item = {"id": region["id"], "kind": region["kind"], "base": region["base"],
                "size": region["size"], "bytes": region["bytes"],
                "preservation": region.get("preservation",
                                           "excluded" if region["kind"] == "stack" else "required")}
        regions.append(item)
    events, unresolved = _native_events(run, regions)
    extra = [] if reason is None else [reason]
    if reason is None:
        site = int(selected["image"]["runtime_site"], 16)
        records = []
        for inst in run.get("instructions") or []:
            pc = int(inst["pc"], 16)
            records.append({"rva": pc - site + PIN_ENTRY, "size": inst["inst_size"]})
        gap = path.path_gap(_fill(selected["window"], records), path.load_inventory(), selected["window"])
        if gap:
            extra.append(gap)
    trace = {
        "schema": schema.SCHEMA,
        "function_id": FUNCTION_ID if reason is None else "unauthenticated-capture",
        "binary": _binary() if reason is None else None,
        "evidence": "qbdi-rosetta",
        "instrumentation": {"name": "QBDI", "version": selected.get("qbdi_version", "unverified")},
        "entry_state": {"registers": dict(run.get("entry_gpr") or {}), "regions": regions,
                        "pointers": list(run.get("pointers") or [])},
        "events": events,
        "exit_state": {"registers": dict(run.get("exit_gpr") or {})},
        "completeness": "complete",
        "incomplete_reasons": [],
        "unresolved_dependencies": [],
    }
    traced = _finish(trace, extra, unresolved, run)
    return {"diagnostic": reason, "observation": traced, "limits": LIMITS}
