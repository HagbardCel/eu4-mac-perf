"""E1 coverage policy and the normalized function-behavior key.

`certified` is not unrestricted equivalence. A registered policy declares the
claim scope (slots, selector paths, input classes), the required and optional
coverage cells, and a quantitative behavioral-diversity floor. Missing required
cells or a missed floor yield ``scoped``, not a pass.

The behavior key is function-specific and deliberately excludes raw addresses,
canary contents and irrelevant allocation bytes. It preserves the slot, selector
path, buffer input class, word count, dirty and preservation outcomes, pointer
roles and alias relationships.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from typing import Any, Iterable

from oracle.certify.errors import CertifyError

POLICY_SCHEMA = "eu4-oracle-coverage-policy/v1"
SELECTORS = ("fixed-length", "length-from-buffer")
INPUT_CLASSES = ("absent", "nonzero", "zero")
KNOWN_DIMENSIONS = frozenset({
    "slot", "selector", "input", "alias", "handle", "buffer", "rax", "pointer",
    "canary",
})
_TOKEN = re.compile(r"\A[A-Za-z0-9][A-Za-z0-9._-]*\Z")
_CELL = re.compile(r"\A[A-Za-z][A-Za-z0-9._-]*:[A-Za-z0-9][A-Za-z0-9._-]*\Z")
_INT = re.compile(r"\A(?:0|[1-9][0-9]*)\Z")


@dataclass(frozen=True)
class Policy:
    function_id: str
    claim: str
    slots: tuple[int, ...]
    selectors: tuple[str, ...]
    input_classes: tuple[str, ...]
    required_cells: tuple[str, ...]
    optional_cells: tuple[str, ...]
    min_distinct_behaviors: int
    sha256: str


@dataclass(frozen=True)
class Behavior:
    key: tuple[str, ...]
    cells: frozenset[str]
    slot: int
    selector: str
    input_class: str


def _reject_unknown(raw: dict[str, Any], allowed: set[str], where: str) -> None:
    unknown = sorted(set(raw) - allowed)
    if unknown:
        raise CertifyError(f"{where} has unknown fields: {unknown}")


def _string_list(value: Any, where: str, *, allow_empty: bool = False
                 ) -> tuple[str, ...]:
    if not isinstance(value, list) or (not value and not allow_empty):
        raise CertifyError(f"{where} must be a "
                           f"{'possibly empty' if allow_empty else 'non-empty'} list")
    result: list[str] = []
    for item in value:
        if not isinstance(item, str) or not _TOKEN.match(item):
            raise CertifyError(f"{where} contains a non-token value: {item!r}")
        if item in result:
            raise CertifyError(f"{where} contains a duplicate: {item!r}")
        result.append(item)
    return tuple(result)


def _cell_list(value: Any, where: str, *, allow_empty: bool = False
               ) -> tuple[str, ...]:
    if not isinstance(value, list) or (not value and not allow_empty):
        raise CertifyError(f"{where} must be a "
                           f"{'possibly empty' if allow_empty else 'non-empty'} list")
    result: list[str] = []
    for item in value:
        if not isinstance(item, str) or not _CELL.match(item):
            raise CertifyError(f"{where} contains a non-cell value: {item!r}")
        if item in result:
            raise CertifyError(f"{where} contains a duplicate: {item!r}")
        result.append(item)
    return tuple(result)


def _int_list(value: Any, where: str) -> tuple[int, ...]:
    if not isinstance(value, list) or not value:
        raise CertifyError(f"{where} must be a non-empty list")
    result: list[int] = []
    for item in value:
        if type(item) is not int or item < 0:
            raise CertifyError(f"{where} contains a value that is not a "
                               f"non-negative integer: {item!r}")
        if item in result:
            raise CertifyError(f"{where} contains a duplicate: {item!r}")
        result.append(item)
    return tuple(sorted(result))


def _cell_parts(cell: str) -> tuple[str, str]:
    dimension, separator, value = cell.partition(":")
    if not separator or dimension not in KNOWN_DIMENSIONS or not value:
        raise CertifyError(f"not a coverage cell: {cell!r}")
    if not _TOKEN.match(value):
        raise CertifyError(f"coverage cell has a non-token value: {cell!r}")
    return dimension, value


def load_policy(path: Path, descriptor: dict[str, Any],
                descriptor_sha256: str) -> Policy:
    path = path.expanduser().resolve()
    try:
        data = path.read_bytes()
    except OSError as error:
        raise CertifyError(f"cannot read the coverage policy: {error}") from None
    try:
        text = data.decode("utf-8")
    except UnicodeError:
        raise CertifyError("the coverage policy is not UTF-8") from None
    try:
        raw = json.loads(text)
    except json.JSONDecodeError as error:
        raise CertifyError(f"the coverage policy is not JSON: {error}") from None
    if not isinstance(raw, dict):
        raise CertifyError("the coverage policy must be a JSON object")
    _reject_unknown(raw, {
        "schema", "function_id", "descriptor_sha256", "claim", "scope",
        "required_cells", "optional_cells", "diversity",
    }, "coverage policy")
    if raw.get("schema") != POLICY_SCHEMA:
        raise CertifyError(f"coverage policy schema must be {POLICY_SCHEMA!r}")
    if raw.get("function_id") != descriptor["function_id"]:
        raise CertifyError("coverage policy function does not match the descriptor")
    if raw.get("descriptor_sha256") != descriptor_sha256:
        raise CertifyError("coverage policy descriptor digest does not match")
    claim = raw.get("claim")
    if not isinstance(claim, str) or not _TOKEN.match(claim):
        raise CertifyError("coverage policy claim must be a non-empty token")

    scope = raw.get("scope")
    if not isinstance(scope, dict):
        raise CertifyError("coverage policy scope must be an object")
    _reject_unknown(scope, {"slots", "selectors", "input_classes"},
                    "coverage policy scope")
    slots = _int_list(scope.get("slots"), "coverage policy scope.slots")
    layout = descriptor["layout"]
    if any(not (layout["slot_min"] <= slot < layout["slot_max"])
           for slot in slots):
        raise CertifyError("coverage policy slot is outside the descriptor domain")
    selectors = _string_list(scope.get("selectors"),
                             "coverage policy scope.selectors")
    if any(item not in SELECTORS for item in selectors):
        raise CertifyError("coverage policy selector is not a known path")
    inputs = _string_list(scope.get("input_classes"),
                          "coverage policy scope.input_classes")
    if any(item not in INPUT_CLASSES for item in inputs):
        raise CertifyError("coverage policy input class is not known")
    if "absent" in inputs and "fixed-length" not in selectors:
        raise CertifyError(
            "the absent input class requires the fixed-length selector in scope")
    if ({"zero", "nonzero"} & set(inputs)) and "length-from-buffer" not in selectors:
        raise CertifyError(
            "a buffer input class requires the length-from-buffer selector in scope")

    required = _cell_list(raw.get("required_cells"),
                          "coverage policy required_cells")
    optional = _cell_list(raw.get("optional_cells", []),
                          "coverage policy optional_cells", allow_empty=True)
    required_set, optional_set = set(required), set(optional)
    if required_set & optional_set:
        raise CertifyError("a coverage cell is both required and optional")
    for cell in required:
        dimension, value = _cell_parts(cell)
        if dimension == "slot":
            if not _INT.match(value) or int(value) not in slots:
                raise CertifyError(f"required slot cell is outside scope: {cell!r}")
        elif dimension == "selector":
            if value not in selectors:
                raise CertifyError(f"required selector cell is outside scope: {cell!r}")
        elif dimension == "input":
            if value not in inputs:
                raise CertifyError(f"required input cell is outside scope: {cell!r}")
    for cell in optional:
        dimension, value = _cell_parts(cell)
        if dimension == "slot" and (not _INT.match(value) or int(value) not in slots):
            raise CertifyError(f"optional slot cell is outside scope: {cell!r}")
        if dimension == "selector" and value not in selectors:
            raise CertifyError(f"optional selector cell is outside scope: {cell!r}")
        if dimension == "input" and value not in inputs:
            raise CertifyError(f"optional input cell is outside scope: {cell!r}")
    for slot in slots:
        if f"slot:{slot}" not in required_set:
            raise CertifyError(
                f"policy scope slot {slot} is not required by required_cells")
    for selector in selectors:
        if f"selector:{selector}" not in required_set:
            raise CertifyError(
                f"policy scope selector {selector} is not required by required_cells")
    for input_class in inputs:
        if f"input:{input_class}" not in required_set:
            raise CertifyError(
                f"policy scope input {input_class} is not required by required_cells")

    diversity = raw.get("diversity")
    if not isinstance(diversity, dict):
        raise CertifyError("coverage policy diversity must be an object")
    _reject_unknown(diversity, {"min_distinct_behaviors"},
                    "coverage policy diversity")
    floor = diversity.get("min_distinct_behaviors")
    if type(floor) is not int or floor < 1:
        raise CertifyError(
            "coverage policy diversity.min_distinct_behaviors must be a positive "
            "integer; the live policy must be pre-registered")

    return Policy(
        function_id=descriptor["function_id"],
        claim=claim,
        slots=slots,
        selectors=selectors,
        input_classes=inputs,
        required_cells=tuple(sorted(required)),
        optional_cells=tuple(sorted(optional)),
        min_distinct_behaviors=floor,
        sha256=sha256(data).hexdigest(),
    )


def _span(record: dict[str, Any], phase: str, name: str) -> dict[str, Any] | None:
    for span in record["state"][phase]:
        if span["space"] == f"heap:{name}":
            return span
    return None


def _span_bytes(span: dict[str, Any]) -> bytes:
    return bytes.fromhex(span["value_hex"])


def _bytes_at(span: dict[str, Any], start: int, width: int) -> bytes:
    offset = int(span["offset"])
    begin = start - offset
    end = begin + width
    data = _span_bytes(span)
    if begin < 0 or end > len(data):
        raise CertifyError(f"region does not cover [{start:#x}, {start + width:#x})")
    return data[begin:end]


def _carrier(record: dict[str, Any], name: str) -> int:
    for carrier in record["abi"]["carriers"]:
        if carrier["name"] == name and carrier["confidence"] == "observed":
            return int(carrier["value_hex"], 16)
    raise CertifyError(f"the qualified record has no observed {name} carrier")


def behavior(record: dict[str, Any], descriptor: dict[str, Any]) -> Behavior:
    """The normalized behavior of one qualified capture."""
    layout = descriptor["layout"]
    slot = _carrier(record, "RCX") & 0xFFFFFFFF
    if not (layout["slot_min"] <= slot < layout["slot_max"]):
        raise CertifyError(f"captured slot {slot} is outside the declared domain")
    selector_value = _carrier(record, "R9") & 0xFFFFFFFF
    selector = ("length-from-buffer"
                if selector_value == descriptor["input_regions"]["buffer"]["selector_value"]
                else "fixed-length")

    pre_ctx = _span(record, "pre", "ctx")
    post_ctx = _span(record, "post", "ctx")
    if pre_ctx is None or post_ctx is None:
        raise CertifyError("the qualified record is missing its context observation")
    word_offset = layout["word_count_base"] + slot * layout["slot_stride"]
    word_count = int.from_bytes(_bytes_at(post_ctx, word_offset, 4), "little")
    dirty = _bytes_at(post_ctx, layout["dirty"], 1)[0]

    pre_handle = _span(record, "pre", "handle")
    post_handle = _span(record, "post", "handle")
    handle = ("unchanged"
              if pre_handle is not None and post_handle is not None
              and _span_bytes(pre_handle) == _span_bytes(post_handle)
              else "changed")

    pre_buffer = _span(record, "pre", "buffer")
    post_buffer = _span(record, "post", "buffer")
    if pre_buffer is None:
        buffer_state = "absent"
        input_class = "absent"
    else:
        input_class = "absent" if selector == "fixed-length" else (
            "zero" if int.from_bytes(_bytes_at(
                pre_buffer, 0, descriptor["input_regions"]["buffer"]["width"]),
                "little") == 0 else "nonzero")
        buffer_state = ("unchanged"
                        if post_buffer is not None
                        and _span_bytes(pre_buffer) == _span_bytes(post_buffer)
                        else "changed")

    addresses = record["raw"]["addresses"]
    buffer_address = addresses.get("buffer")
    if buffer_address is None:
        alias = "absent"
    elif buffer_address == addresses.get("handle"):
        alias = "handle"
    elif buffer_address == addresses.get("ctx"):
        alias = "ctx"
    else:
        alias = "distinct"

    pointer_offset = layout["pointer_base"] + slot * layout["slot_stride"]
    written = int.from_bytes(_bytes_at(post_ctx, pointer_offset, 8), "little")
    pointer = "rsi" if written == _carrier(record, "RSI") else "other"

    outcome = record["raw"].get("outcome")
    if not isinstance(outcome, dict) or outcome.get("confidence") != "observed":
        raise CertifyError("the qualified record is missing its observed outcome")
    rax = ("ctx" if int(outcome["value_hex"], 16)
           == int(addresses["ctx"], 16) else "other")

    canary = "preserved"
    for name in ("canary-before", "canary-after"):
        before = _span(record, "pre", name)
        after = _span(record, "post", name)
        if before is None or after is None or _span_bytes(before) != _span_bytes(after):
            canary = "changed"
            break

    key = (
        f"slot={slot}",
        f"selector={selector}",
        f"input={input_class}",
        f"word_count={word_count}",
        f"dirty={dirty}",
        f"buffer={buffer_state}",
        f"handle={handle}",
        f"alias={alias}",
        f"pointer={pointer}",
        f"rax={rax}",
        f"canary={canary}",
    )
    cells = frozenset({
        f"slot:{slot}",
        f"selector:{selector}",
        f"input:{input_class}",
        f"buffer:{buffer_state}",
        f"handle:{handle}",
        f"alias:{alias}",
        f"pointer:{pointer}",
        f"rax:{rax}",
        f"canary:{canary}",
    })
    return Behavior(key=key, cells=cells, slot=slot, selector=selector,
                    input_class=input_class)


def coverage(records: Iterable[dict[str, Any]], descriptor: dict[str, Any],
             policy: Policy) -> dict[str, Any]:
    behaviors = [behavior(record, descriptor) for record in records]
    distinct = sorted({item.key for item in behaviors})
    observed = frozenset().union(*(item.cells for item in behaviors)) \
        if behaviors else frozenset()
    required = set(policy.required_cells)
    optional = set(policy.optional_cells)
    missing_required = sorted(required - observed)
    missing_optional = sorted(optional - observed)
    diversity_met = len(distinct) >= policy.min_distinct_behaviors

    layout = descriptor["layout"]
    domain_slots = set(range(layout["slot_min"], layout["slot_max"]))
    limitations: list[str] = []
    for slot in sorted(domain_slots - set(policy.slots)):
        limitations.append(
            f"scope: slot {slot} is outside the declared claim scope")
    for selector in sorted(set(SELECTORS) - set(policy.selectors)):
        limitations.append(
            f"scope: selector {selector} is outside the declared claim scope")
    for input_class in sorted(set(INPUT_CLASSES) - set(policy.input_classes)):
        limitations.append(
            f"scope: input {input_class} is outside the declared claim scope")
    for cell in missing_optional:
        limitations.append(f"optional-cell-missing: {cell}")
    unrestricted = (set(policy.slots) == domain_slots
                    and set(policy.selectors) == set(SELECTORS)
                    and set(policy.input_classes) == set(INPUT_CLASSES))
    if not unrestricted:
        limitations.append(
            "claim: the result is limited to the declared coverage scope, not the "
            "full A2 descriptor")

    return {
        "policy_sha256": policy.sha256,
        "claim": policy.claim,
        "scope": {
            "slots": list(policy.slots),
            "selectors": list(policy.selectors),
            "input_classes": list(policy.input_classes),
        },
        "unrestricted": unrestricted,
        "required_cells": sorted(required),
        "observed_required": sorted(required & observed),
        "missing_required": missing_required,
        "optional_cells": sorted(optional),
        "observed_optional": sorted(optional & observed),
        "missing_optional": missing_optional,
        "distinct_behaviors": len(distinct),
        "duplicate_count": len(behaviors) - len(distinct),
        "diversity_floor": policy.min_distinct_behaviors,
        "diversity_met": diversity_met,
        "_limitations": limitations,
    }
