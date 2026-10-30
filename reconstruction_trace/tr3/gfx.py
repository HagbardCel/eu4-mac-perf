"""One `_GfxSetConstantBuffer` observation from a single run.

`handwritten-synthetic` may use regions the test built. `qbdi-rosetta` keeps
the observation produced by `native.observation_from_run`. A loaded qword is
not a context base. `image.check_mapped` checks a supplied file and window; it
does not show that those bytes were read from a running EU4 process. This
module does not launch EU4.
"""
from __future__ import annotations

import copy
from pathlib import Path

from reconstruction_trace.tr3 import image, native, observe, path

FUNCTION_ID = "_GfxSetConstantBuffer"
PIN_FILE = "b3d38876abf4e61cdae57509186d7cb7dcb03bfeaca4c95c10c713794715141d"
PIN_WINDOW = "8e5f94bf3f132cbf17397928c72df44b7fb6d24bcc95575702c457fb48e32943"
PIN_ENTRY = 0x15EC36A
PIN_END = 0x15EC3B5
PIN_BASE = 0x100000000
LIMITS = ("another thread entering while the hook is removed is untraced; "
          "register restore has not been shown inside EU4")
_PIN = {"file_sha256": PIN_FILE, "window_sha256": PIN_WINDOW, "image_base": PIN_BASE,
        "entry_rva": PIN_ENTRY, "end_rva": PIN_END}


def interpret(candidates: list[dict], *, image_path: Path | None = None,
              mapped: bytes | None = None) -> dict:
    """Interpret the first candidate. Later candidates are not read."""
    selected = observe.select_one(candidates)
    refusal = _never_ran(selected)
    if refusal:
        return {"diagnostic": refusal, "observation": None, "limits": LIMITS}
    if selected.get("evidence") == "qbdi-rosetta":
        return _native(selected, image_path, mapped)
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


def _remember(trace: dict, extras: list[str], unresolved: list[dict] | None = None) -> str | None:
    """Append reasons. An existing incomplete reason is kept, and completeness is not restored."""
    reasons = list(trace["incomplete_reasons"])
    for reason in extras:
        if reason and reason not in reasons:
            reasons.append(reason)
    kept = list(trace["unresolved_dependencies"])
    for item in unresolved or []:
        if item not in kept:
            kept.append(item)
        text = f"{item['reason']} at {item['address']} size {item['size']}"
        if text not in reasons:
            reasons.append(text)
    trace["incomplete_reasons"] = reasons
    trace["unresolved_dependencies"] = kept
    if reasons or kept:
        trace["completeness"] = "incomplete"
    if trace["completeness"] == "incomplete":
        return reasons[0]
    return None


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
    prior = list(trace["incomplete_reasons"])
    trace["function_id"] = FUNCTION_ID
    trace["evidence"] = "handwritten-synthetic"
    trace["binary"] = None
    traced = observe.apply_accounting(
        trace, dropped_instructions=selected["run"].get("dropped_instructions", 0),
        dropped_accesses=selected["run"].get("dropped_accesses", 0),
        dropped_transfers=selected["run"].get("dropped_transfers", 0))
    accounted = [reason for reason in traced["incomplete_reasons"] if reason not in prior]
    traced["incomplete_reasons"] = prior + accounted
    traced["unresolved_dependencies"] = list(trace["unresolved_dependencies"])
    extras = [] if gap is None else [gap]
    unresolved = _outside(traced["events"], traced["entry_state"]["regions"],
                          "access outside the supplied regions")
    diagnostic = _remember(traced, extras, unresolved)
    return {"diagnostic": diagnostic, "observation": traced, "limits": LIMITS}


def _identity(raw: dict, image_path: Path | None, mapped: bytes | None) -> str | None:
    """Reason this capture is not bound to the pinned file and mapped window, or None.

    ``check_mapped`` compares a supplied file and supplied bytes. It does not
    show that the bytes were read from a running process. The mapped window is
    never read out of the file to satisfy the check.
    """
    if image_path is None or mapped is None:
        return "image was not authenticated"
    mismatch = image.check_mapped(image_path, _PIN, mapped)
    if mismatch:
        return mismatch
    if (raw.get("file_sha256") != PIN_FILE or raw.get("expected_file_sha256") != PIN_FILE
            or raw.get("window_sha256") != PIN_WINDOW
            or raw.get("expected_window_sha256") != PIN_WINDOW):
        return "capture digest does not match the authenticated image"
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
    return None


def _binary() -> dict:
    return {"sha256": PIN_FILE, "image_base": hex(PIN_BASE), "entry_rva": hex(PIN_ENTRY)}


def _native_gap(raw: dict, mapped: bytes | None) -> str | None:
    meta = raw.get("image") or {}
    try:
        site = int(meta["runtime_site"], 16)
    except (KeyError, TypeError, ValueError):
        return None
    if not isinstance(mapped, (bytes, bytearray)):
        return None
    records = [{"rva": int(inst["pc"], 16) - site + PIN_ENTRY, "size": inst["inst_size"]}
               for inst in raw["run"].get("instructions") or []]
    return path.path_gap(_fill(mapped, records), path.load_inventory(), mapped)


def _native(selected: dict, image_path: Path | None, mapped: bytes | None) -> dict:
    trace = native.observation_from_run(selected["run"], selected.get("qbdi_version", "unverified"))
    reason = _identity(selected, image_path, mapped)
    gap = _native_gap(selected, mapped) if reason is None else None
    if reason is None:
        trace["function_id"] = FUNCTION_ID
        trace["binary"] = _binary()
    primary = reason or gap
    if primary:
        prior = trace["incomplete_reasons"]
        trace["incomplete_reasons"] = [primary, *(item for item in prior if item != primary)]
        trace["completeness"] = "incomplete"
    diagnostic = trace["incomplete_reasons"][0] if trace["incomplete_reasons"] else None
    return {"diagnostic": diagnostic, "observation": trace, "limits": LIMITS}
