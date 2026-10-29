"""Validator for the frozen ``execution-observation/v1`` trace.

The contract is docs/oracle/TRACE_V1.md. This module checks one trace against it
and raises ``TraceError`` for anything the contract does not admit: an unknown
key, evidence value or event kind, a non-canonical hex string, a byte string of
the wrong length, unbalanced call frames, or a ``complete`` claim that breaks a
completeness invariant. It is the only schema; there is no generator.

A trace that declares ``incomplete`` may lack values and value sources and may
end with open frames: that is what incomplete means. It still must be well
formed, and it still never becomes a fixture.
"""
from __future__ import annotations

import re
from typing import Any

SCHEMA = "execution-observation/v1"
EVIDENCE = ("qbdi-rosetta", "ghidra-pcode", "handwritten-synthetic")
VALUE_SOURCES = ("observed", "derived-scenario")
KINDS = ("heap", "stack")
PRESERVATION = ("required", "excluded")
POINTER_SIZE = 8

TRACE_KEYS = frozenset({
    "schema", "function_id", "binary", "evidence", "instrumentation", "entry_state",
    "events", "exit_state", "completeness", "incomplete_reasons",
    "unresolved_dependencies",
})
BINARY_KEYS = frozenset({"sha256", "image_base", "entry_rva"})
INSTRUMENTATION_KEYS = frozenset({"name", "version"})
ENTRY_KEYS = frozenset({"registers", "regions", "pointers"})
EXIT_KEYS = frozenset({"registers", "regions"})
REGION_KEYS = frozenset({"id", "kind", "base", "size", "bytes", "bytes_absent",
                         "preservation"})
EXIT_REGION_KEYS = frozenset({"id", "base", "size", "bytes"})
MEMORY_KEYS = frozenset({"kind", "pc", "address", "size", "value", "value_source",
                         "provenance"})
CALL_ENTER_KEYS = frozenset({"kind", "pc", "target"})
CALL_EXIT_KEYS = frozenset({"kind", "target", "effects_complete", "effects"})
DEPENDENCY_KEYS = frozenset({"address", "size", "reason"})

_HEX = re.compile(r"\A0x(?:0|[1-9a-f][0-9a-f]*)\Z")
_BYTES = re.compile(r"\A(?:[0-9a-f]{2})*\Z")
_SHA256 = re.compile(r"\A[0-9a-f]{64}\Z")
_REGISTER = re.compile(r"\A[a-z][a-z0-9]*\Z")
_LIMIT = 1 << 64


class TraceError(ValueError):
    """A trace is not admitted by ``execution-observation/v1``."""


def address(value: Any, where: str) -> int:
    """Parse one canonical ``0x`` string: lowercase, no leading zeros, not a JSON number."""
    if not isinstance(value, str) or not _HEX.match(value):
        raise TraceError(f"{where}: {value!r} is not 0x-lowercase-hex without leading zeros")
    number = int(value, 16)
    if number >= _LIMIT:
        raise TraceError(f"{where}: {value} exceeds 64 bits")
    return number


def size(value: Any, where: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise TraceError(f"{where}: size must be a positive JSON integer")
    return value


def offset(value: Any, where: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise TraceError(f"{where}: offset must be a non-negative JSON integer")
    return value


def byte_string(value: Any, length: int, where: str) -> bytes:
    """Lowercase hex in address order, exactly ``2 * length`` digits."""
    if not isinstance(value, str) or not _BYTES.match(value) or len(value) != 2 * length:
        raise TraceError(f"{where}: bytes must be {2 * length} lowercase hex digits")
    return bytes.fromhex(value)


def _keys(obj: Any, allowed: frozenset[str], required: set[str], where: str) -> dict:
    if not isinstance(obj, dict):
        raise TraceError(f"{where}: expected an object")
    extra = set(obj) - allowed
    if extra:
        raise TraceError(f"{where}: keys outside the contract: {sorted(extra)}")
    missing = required - set(obj)
    if missing:
        raise TraceError(f"{where}: missing keys: {sorted(missing)}")
    return obj


def _text(value: Any, where: str) -> str:
    if not isinstance(value, str) or not value:
        raise TraceError(f"{where}: expected a non-empty string")
    return value


def _registers(obj: Any, where: str) -> dict[str, int]:
    if not isinstance(obj, dict):
        raise TraceError(f"{where}: expected an object")
    values = {}
    for name, value in obj.items():
        if not _REGISTER.match(name):
            raise TraceError(f"{where}: {name!r} is not a lowercase register name")
        values[name] = address(value, f"{where}.{name}")
    return values


def _span(base: int, length: int, where: str) -> None:
    if base + length > _LIMIT:
        raise TraceError(f"{where}: range wraps past 64 bits")


def _region(region: Any, where: str) -> None:
    _keys(region, REGION_KEYS, {"id", "kind", "base", "size"}, where)
    _text(region["id"], f"{where}.id")
    if region["kind"] not in KINDS:
        raise TraceError(f"{where}.kind: must be one of {KINDS}")
    base = address(region["base"], f"{where}.base")
    length = size(region["size"], f"{where}.size")
    _span(base, length, where)
    if ("bytes" in region) == ("bytes_absent" in region):
        raise TraceError(f"{where}: exactly one of bytes or bytes_absent is required")
    if "bytes" in region:
        byte_string(region["bytes"], length, f"{where}.bytes")
    elif region["bytes_absent"] is not True:
        raise TraceError(f"{where}.bytes_absent: must be true")
    if region.get("preservation", "required") not in PRESERVATION:
        raise TraceError(f"{where}.preservation: must be one of {PRESERVATION}")


def _target_address(target: dict, regions: dict[str, dict], where: str) -> int:
    region = regions.get(target["region"])
    if region is None:
        raise TraceError(f"{where}: unknown region {target['region']!r}")
    start = offset(target["offset"], f"{where}.offset")
    if start > region["size"]:
        raise TraceError(f"{where}: offset is past the end of its region")
    return int(region["base"], 16) + start


def _pointers(pointers: Any, regions: dict[str, dict], registers: dict, where: str) -> None:
    if not isinstance(pointers, list):
        raise TraceError(f"{where}: expected a list")
    sources = set()
    windows: list[tuple[int, int]] = []
    for index, edge in enumerate(pointers):
        at = f"{where}[{index}]"
        _keys(edge, frozenset({"from", "to"}), {"from", "to"}, at)
        source = edge["from"]
        target = _keys(edge["to"], frozenset({"region", "offset"}), {"region", "offset"},
                       f"{at}.to")
        expected = _target_address(target, regions, f"{at}.to")
        if isinstance(source, dict) and "register" in source:
            _keys(source, frozenset({"register"}), {"register"}, f"{at}.from")
            if source["register"] not in registers:
                raise TraceError(f"{at}.from: register {source['register']!r} has no entry value")
            key = ("register", source["register"])
            recorded = registers[source["register"]]
        else:
            _keys(source, frozenset({"region", "offset"}), {"region", "offset"}, f"{at}.from")
            region = regions.get(source["region"])
            if region is None:
                raise TraceError(f"{at}.from: unknown region {source['region']!r}")
            start = offset(source["offset"], f"{at}.from.offset")
            if start + POINTER_SIZE > region["size"]:
                raise TraceError(f"{at}.from: the stored pointer does not fit its region")
            if "bytes" not in region:
                raise TraceError(f"{at}.from: the stored pointer is not in recorded bytes")
            key = ("region", source["region"], start)
            recorded = int.from_bytes(
                bytes.fromhex(region["bytes"])[start:start + POINTER_SIZE], "little")
            absolute = int(region["base"], 16) + start
            windows.append((absolute, absolute + POINTER_SIZE))
        if key in sources:
            raise TraceError(f"{at}.from: a second edge from the same source")
        sources.add(key)
        if recorded != expected:
            raise TraceError(f"{at}: recorded value is {hex(recorded)}, not {hex(expected)}")
    for index, (start, end) in enumerate(windows):
        for other, limit in windows[index + 1:]:
            if start < limit and other < end:
                raise TraceError(f"{where}: pointer sources overlap at {hex(max(start, other))}")


def _memory_event(event: dict, where: str, complete: bool, kind: str | None = None) -> None:
    _keys(event, MEMORY_KEYS, {"kind", "pc", "address", "size"}, where)
    if kind is not None and event["kind"] != kind:
        raise TraceError(f"{where}.kind: must be {kind!r}")
    address(event["pc"], f"{where}.pc")
    start = address(event["address"], f"{where}.address")
    length = size(event["size"], f"{where}.size")
    _span(start, length, where)
    if "value" in event:
        byte_string(event["value"], length, f"{where}.value")
    elif complete:
        raise TraceError(f"{where}: a complete trace has no unknown value")
    source = event.get("value_source")
    if source is None:
        if "value" in event and complete:
            raise TraceError(f"{where}: a present value needs value_source")
    elif "value" not in event:
        raise TraceError(f"{where}: value_source without a value")
    elif source not in VALUE_SOURCES:
        raise TraceError(f"{where}.value_source: must be one of {VALUE_SOURCES}")
    if source == "derived-scenario":
        _text(event.get("provenance"), f"{where}.provenance")
    elif "provenance" in event:
        raise TraceError(f"{where}: provenance belongs to a derived-scenario value")


def _events(events: Any, complete: bool) -> None:
    if not isinstance(events, list):
        raise TraceError("events: expected a list")
    frames: list[dict] = []
    for index, event in enumerate(events):
        where = f"events[{index}]"
        kind = event.get("kind") if isinstance(event, dict) else None
        if kind in ("read", "write"):
            _memory_event(event, where, complete)
            if frames:
                frames[-1]["nested"] = True
        elif kind == "call_enter":
            _keys(event, CALL_ENTER_KEYS, set(CALL_ENTER_KEYS), where)
            address(event["pc"], f"{where}.pc")
            if frames:
                frames[-1]["nested"] = True
            frames.append({"target": address(event["target"], f"{where}.target"), "nested": False})
        elif kind == "call_exit":
            _keys(event, CALL_EXIT_KEYS, {"kind", "target"}, where)
            target = address(event["target"], f"{where}.target")
            if not frames:
                raise TraceError(f"{where}: call_exit without an open frame")
            frame = frames.pop()
            if frame["target"] != target:
                raise TraceError(f"{where}: call_exit target differs from the open frame")
            effects_complete = event.get("effects_complete", False)
            if not isinstance(effects_complete, bool):
                raise TraceError(f"{where}.effects_complete: must be a boolean")
            if "effects" in event:
                if not effects_complete:
                    raise TraceError(f"{where}: effects need effects_complete: true")
                if not isinstance(event["effects"], list):
                    raise TraceError(f"{where}.effects: expected a list")
                for number, effect in enumerate(event["effects"]):
                    _memory_event(effect, f"{where}.effects[{number}]", complete, "write")
            elif complete and not frame["nested"]:
                raise TraceError(f"{where}: a frame with no nested events needs "
                                 "effects_complete: true and an effects list")
        else:
            raise TraceError(f"{where}: unknown event kind {kind!r}")
    if frames and complete:
        raise TraceError("events: a complete trace ends with an open call frame")


def region_conflicts(regions: list[dict]) -> list[str]:
    """Overlapping regions whose recorded bytes disagree (TRACE_V1.md, Encoding)."""
    known = [(int(r["base"], 16), bytes.fromhex(r["bytes"]), r["id"])
             for r in regions if "bytes" in r]
    conflicts = []
    for i, (base_a, data_a, id_a) in enumerate(known):
        for base_b, data_b, id_b in known[i + 1:]:
            low = max(base_a, base_b)
            high = min(base_a + len(data_a), base_b + len(data_b))
            for at in range(low, high):
                if data_a[at - base_a] != data_b[at - base_b]:
                    conflicts.append(f"regions {id_a} and {id_b} disagree at {hex(at)}")
                    break
    return conflicts


def validate(trace: Any) -> dict:
    """Return ``trace`` when ``execution-observation/v1`` admits it, else raise ``TraceError``."""
    _keys(trace, TRACE_KEYS, set(TRACE_KEYS), "trace")
    if trace["schema"] != SCHEMA:
        raise TraceError(f"schema: must be {SCHEMA}")
    _text(trace["function_id"], "function_id")
    if trace["binary"] is not None:
        binary = _keys(trace["binary"], BINARY_KEYS, set(BINARY_KEYS), "binary")
        if not isinstance(binary["sha256"], str) or not _SHA256.match(binary["sha256"]):
            raise TraceError("binary.sha256: expected 64 lowercase hex digits")
        address(binary["image_base"], "binary.image_base")
        address(binary["entry_rva"], "binary.entry_rva")
    if trace["evidence"] not in EVIDENCE:
        raise TraceError(f"evidence: must be one of {EVIDENCE}")
    instrumentation = _keys(trace["instrumentation"], INSTRUMENTATION_KEYS,
                            set(INSTRUMENTATION_KEYS), "instrumentation")
    for key in INSTRUMENTATION_KEYS:
        _text(instrumentation[key], f"instrumentation.{key}")

    if trace["completeness"] not in ("complete", "incomplete"):
        raise TraceError("completeness: must be complete or incomplete")
    complete = trace["completeness"] == "complete"

    entry = _keys(trace["entry_state"], ENTRY_KEYS, {"registers", "regions"}, "entry_state")
    registers = _registers(entry["registers"], "entry_state.registers")
    if not isinstance(entry["regions"], list):
        raise TraceError("entry_state.regions: expected a list")
    regions: dict[str, dict] = {}
    for index, region in enumerate(entry["regions"]):
        _region(region, f"entry_state.regions[{index}]")
        if region["id"] in regions:
            raise TraceError(f"entry_state.regions[{index}]: duplicate id {region['id']!r}")
        regions[region["id"]] = region
    _pointers(entry.get("pointers", []), regions, registers, "entry_state.pointers")

    _events(trace["events"], complete)

    exit_state = _keys(trace["exit_state"], EXIT_KEYS, {"registers"}, "exit_state")
    _registers(exit_state["registers"], "exit_state.registers")
    exit_regions = exit_state.get("regions", [])
    if not isinstance(exit_regions, list):
        raise TraceError("exit_state.regions: expected a list")
    seen = set()
    for index, region in enumerate(exit_regions):
        where = f"exit_state.regions[{index}]"
        _keys(region, EXIT_REGION_KEYS, set(EXIT_REGION_KEYS), where)
        entry_region = regions.get(region["id"])
        if entry_region is None or region["id"] in seen:
            raise TraceError(f"{where}: must name one entry region once")
        seen.add(region["id"])
        if region["base"] != entry_region["base"] or region["size"] != entry_region["size"]:
            raise TraceError(f"{where}: base and size must match the entry region")
        byte_string(region["bytes"], entry_region["size"], f"{where}.bytes")

    reasons = trace["incomplete_reasons"]
    if not isinstance(reasons, list):
        raise TraceError("incomplete_reasons: expected a list")
    for index, reason in enumerate(reasons):
        _text(reason, f"incomplete_reasons[{index}]")
    unresolved = trace["unresolved_dependencies"]
    if not isinstance(unresolved, list):
        raise TraceError("unresolved_dependencies: expected a list")
    for index, dependency in enumerate(unresolved):
        where = f"unresolved_dependencies[{index}]"
        _keys(dependency, DEPENDENCY_KEYS, set(DEPENDENCY_KEYS), where)
        address(dependency["address"], f"{where}.address")
        size(dependency["size"], f"{where}.size")
        _text(dependency["reason"], f"{where}.reason")

    if complete:
        if reasons or unresolved:
            raise TraceError("a complete trace has no incomplete_reasons or "
                             "unresolved_dependencies")
        conflicts = region_conflicts(entry["regions"])
        if conflicts:
            raise TraceError(f"a complete trace has disagreeing regions: {conflicts[0]}")
    elif not reasons:
        raise TraceError("an incomplete trace names its incomplete_reasons")
    return trace
