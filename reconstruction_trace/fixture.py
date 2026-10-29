"""Fixture consumer for ``execution-observation/v1`` (TRACE_V1.md, Fixture consumer).

``qualify`` reads one validated trace over the generic entry inventory and derives,
from the ordered events alone:

- initial read dependencies: bytes read before this invocation wrote them;
- observable write effects: the final value of every byte the invocation wrote;
- the pointer edges replay must relocate.

Bookkeeping is per byte. A later read of a byte the invocation already wrote is
not an initial dependency, and a write that partly overlaps an earlier one
leaves both contributions in the final bytes. Alias identity is the harness
allocation id carried by the pointer edges; nothing here scans bytes for
addresses or compares raw values to find an alias.

An edge is kept only when the trace reads or writes the stored pointer, or when a
later access depends on it: an access inside a register edge's target region, or
the return boundary that dereferences ``rsp``. A dereferenced region must then be
reachable from a kept register edge through kept stored edges. Every unresolved
byte range fails qualification and names its address and size. An exit register
that has a kept edge must be that entry pointer, or ``rsp`` at that address plus
8; any other value fails qualification. An incomplete trace never becomes a
fixture. This module does not emit ``FUNCTION-CAPTURE v1``.
"""
from __future__ import annotations

from typing import Any

from reconstruction_trace import schema


def _ranges(addresses: Any) -> list[tuple[int, int]]:
    """Contiguous ``(start, size)`` runs of a set of byte addresses."""
    runs: list[tuple[int, int]] = []
    for at in sorted(addresses):
        if runs and runs[-1][0] + runs[-1][1] == at:
            runs[-1] = (runs[-1][0], runs[-1][1] + 1)
        else:
            runs.append((at, 1))
    return runs


def _memory_events(events: list[dict]):
    """Memory accesses in execution order, including a call's complete effects."""
    for event in events:
        if event["kind"] in ("read", "write"):
            yield event
        elif event["kind"] == "call_exit" and event.get("effects_complete"):
            yield from event.get("effects", [])


class _Inventory:
    def __init__(self, regions: list[dict]):
        self.regions = regions
        self.spans = [(int(r["base"], 16), r["size"], r) for r in regions]
        self.initial: dict[int, int] = {}
        for base, _, region in reversed(self.spans):
            if "bytes" in region:
                for index, value in enumerate(bytes.fromhex(region["bytes"])):
                    self.initial[base + index] = value

    def region_at(self, at: int) -> dict | None:
        for base, length, region in self.spans:
            if base <= at < base + length:
                return region
        return None

    def base(self, region_id: str) -> int:
        return next(base for base, _, r in self.spans if r["id"] == region_id)


def _per_region(inventory: _Inventory, values: dict[int, int]) -> list[dict]:
    rows = []
    for start, length in _ranges(values):
        at = start
        while at < start + length:
            region = inventory.region_at(at)
            base = int(region["base"], 16)
            end = min(start + length, base + region["size"])
            rows.append({"region": region["id"], "offset": at - base, "size": end - at,
                         "bytes": bytes(values[i] for i in range(at, end)).hex()})
            at = end
    return rows


def _rejected(reasons: list[str], unresolved: list[dict]) -> dict:
    return {"fixture_qualified": False, "reasons": reasons,
            "unresolved_dependencies": unresolved, "fixture": None}


def qualify(trace: dict) -> dict:
    """Derive a fixture from one trace, or say why the trace cannot become one."""
    schema.validate(trace)
    if trace["completeness"] != "complete":
        return _rejected(["the trace is incomplete: " + reason
                          for reason in trace["incomplete_reasons"]],
                         list(trace["unresolved_dependencies"]))

    entry = trace["entry_state"]
    inventory = _Inventory(entry["regions"])
    written: dict[int, int] = {}
    observable: dict[int, int] = {}
    dependencies: dict[int, int] = {}
    accessed: set[int] = set()
    reasons: list[str] = []
    unresolved: list[dict] = []

    def unresolve(missing: set[int], reason: str) -> None:
        for start, length in _ranges(missing):
            unresolved.append({"address": hex(start), "size": length, "reason": reason})
            reasons.append(f"{reason}: address {hex(start)} size {length}")

    for event in _memory_events(trace["events"]):
        start = int(event["address"], 16)
        value = bytes.fromhex(event["value"])
        span = range(start, start + event["size"])
        accessed.update(span)
        outside = {at for at in span if inventory.region_at(at) is None}
        if event["kind"] == "write":
            if outside:
                unresolve(outside, "write outside the entry inventory")
            for at, byte in zip(span, value):
                written[at] = byte
                region = inventory.region_at(at)
                # preservation: excluded drops the final effect only. The write stays
                # in `written` so a later read is not a new input.
                if region is None or region.get("preservation", "required") != "excluded":
                    observable[at] = byte
            continue
        missing = set()
        for at, byte in zip(span, value):
            if at in written:
                known = written[at]
            elif at in inventory.initial:
                known = inventory.initial[at]
                dependencies[at] = known
            else:
                missing.add(at)
                continue
            if known != byte:
                reasons.append(f"read at {event['address']} (pc {event['pc']}) disagrees "
                               f"with the recorded bytes at {hex(at)}")
                break
        if missing:
            unresolve(missing, "missing input region" if missing <= outside
                      else "read of absent initial bytes")

    for region in entry["regions"]:
        if "bytes" in region or region.get("preservation", "required") == "excluded":
            continue
        base = int(region["base"], 16)
        unwritten = {at for at in range(base, base + region["size"]) if at not in written}
        if unwritten:
            unresolve(unwritten, f"preservation of region {region['id']} needs absent "
                                 "initial bytes")

    for region in trace["exit_state"].get("regions", []):
        entry_region = next(r for r in entry["regions"] if r["id"] == region["id"])
        base = int(region["base"], 16)
        required = entry_region.get("preservation", "required") == "required"
        for index, byte in enumerate(bytes.fromhex(region["bytes"])):
            at = base + index
            expected = written.get(at, inventory.initial.get(at) if required else None)
            if expected is not None and expected != byte:
                reasons.append(f"exit bytes of region {region['id']} at {hex(at)} disagree "
                               "with the trace's effects")
                break

    pointers = []
    for edge in entry.get("pointers", []):
        source = edge["from"]
        if "register" in source:
            target = edge["to"]["region"]
            base = inventory.base(target)
            size = next(r["size"] for r in entry["regions"] if r["id"] == target)
            keep = source["register"] == "rsp" or any(
                base <= at < base + size for at in accessed)
        else:
            stored = inventory.base(source["region"]) + source["offset"]
            keep = any(at in accessed for at in range(stored, stored + schema.POINTER_SIZE))
        if keep:
            pointers.append(edge)

    reachable = {edge["to"]["region"] for edge in pointers if "register" in edge["from"]}
    # shortcut: reachability over kept edges does not prove which pointer was loaded, upgrade when a trace records the pointer that produced each address.
    growing = True
    while growing:
        growing = False
        for edge in pointers:
            source = edge["from"]
            if "region" not in source or source["region"] not in reachable:
                continue
            target = edge["to"]["region"]
            if target not in reachable:
                reachable.add(target)
                growing = True
    # Any containing span counts. region_at returns only the first one.
    orphan = set()
    for at in accessed:
        covers = [region["id"] for base, length, region in inventory.spans
                  if base <= at < base + length]
        if covers and not any(region_id in reachable for region_id in covers):
            orphan.add(at)
    if orphan:
        unresolve(orphan, "dereferenced address has no relocation edge")

    for name, value in trace["exit_state"]["registers"].items():
        edge = next((item for item in pointers if item["from"].get("register") == name), None)
        if edge is None:
            continue
        original = inventory.base(edge["to"]["region"]) + edge["to"]["offset"]
        supported = original + 8 if name == "rsp" else original
        if int(value, 16) != supported:
            reasons.append(f"exit register {name} is not a supported relocated pointer")

    if reasons:
        return _rejected(reasons, unresolved)
    return {
        "fixture_qualified": True,
        "reasons": [],
        "unresolved_dependencies": [],
        "fixture": {
            "function_id": trace["function_id"],
            "evidence": trace["evidence"],
            "binary": trace["binary"],
            "registers": dict(entry["registers"]),
            "regions": [dict(region) for region in entry["regions"]],
            "pointers": pointers,
            "dependencies": _per_region(inventory, dependencies),
            "effects": _per_region(inventory, observable),
            "exit_registers": dict(trace["exit_state"]["registers"]),
        },
    }
