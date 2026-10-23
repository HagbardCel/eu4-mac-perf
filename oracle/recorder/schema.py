"""Versioned observation records for the P4 recorder: schema, canonicalization, digests.

This module is the *schema contract* and nothing else. It captures no events,
touches no process, and never opens EU IV. It defines what a recorded observation
looks like, how it serializes deterministically, and the two distinct digests a
consumer may rely on, so that the native recorder (P4.2+) and the offline analysis
share one definition instead of two.

Two digests, deliberately different:

* **record digest** - integrity over the record's canonical bytes. Defined for
  *any* well-formed record, including an incomplete one. It answers "are these the
  same bytes?" and never claims observable equivalence.
* **semantic digest** - equality over the canonical *semantic projection* of a
  **fully qualified** observation. Defined only when the observation is complete
  and canonical. It answers "are these the same observed behavior?" and is the only
  digest eligible for semantic deduplication.

The three fidelity modes are declared here as an opaque, versioned policy tag.
They are owned by the reconstruction contract (PR #44); the recorder records which
policy a fixture is qualified under and does not interpret it.
"""
from __future__ import annotations

import json
import re
from hashlib import sha256
from typing import Any

from oracle.core import canonical_bytes as _canonical_json

RECORD_SCHEMA_VERSION = "eu4-oracle-record/v1"
CANONICALIZATION_VERSION = "eu4-oracle-canon/v1"

#: The declared comparison contract (PR #44). Opaque to this module: it is a tag.
FIDELITY_MODES = ("faithful", "gl-optimization", "backend-redesign")

#: How completely an invocation was observed. ``counted-only`` is an intercepted
#: counter with no deep observation; it is never eligible for semantic comparison.
CAPTURE_STATES = ("captured", "incomplete", "counted-only")

#: Confidence in a value recovered from ABI/layout reconstruction. An ``inferred``
#: or ``unknown`` field is not the same evidence as an ``observed`` one.
FIELD_CONFIDENCE = ("observed", "inferred", "unknown")

#: ABI carrier kinds. ``this`` and ``sret`` are the hidden pointer carriers.
CARRIER_KINDS = ("int", "xmm", "stack", "sret", "this")

_HEX = re.compile(r"\A(?:[0-9a-fA-F]{2})*\Z")
_SHA256 = re.compile(r"\A[0-9a-f]{64}\Z")
_TOKEN = re.compile(r"\A[A-Za-z0-9][A-Za-z0-9._:+/@#-]*\Z")


class SchemaError(ValueError):
    """A record is malformed, non-canonical, or not eligible for an operation."""


# --- canonical serialization ---------------------------------------------------

def canonical_bytes(value: Any) -> bytes:
    """Deterministic canonical serialization; see ``CANONICALIZATION_VERSION``.

    Canonical form is key-sorted JSON with no insignificant whitespace and no NaN
    or Infinity. Raw bytes are carried as lowercase hex strings, never as opaque
    blobs, so the form is stable across the native encoder and this reader.
    """
    try:
        return _canonical_json(value)
    except (ValueError, TypeError) as exc:
        raise SchemaError(f"value is not canonically serializable: {exc}") from exc


# --- validation helpers --------------------------------------------------------

def _check(condition: bool, where: str, message: str) -> None:
    if not condition:
        raise SchemaError(f"{where}: {message}")


def _obj(value: Any, where: str) -> dict[str, Any]:
    _check(isinstance(value, dict), where, f"expected an object, got {value!r}")
    return value


def _token(value: Any, where: str) -> None:
    _check(isinstance(value, str) and bool(_TOKEN.match(value)), where,
           f"expected a token string, got {value!r}")


def _sha(value: Any, where: str) -> None:
    _check(isinstance(value, str) and bool(_SHA256.match(value)), where,
           f"expected a 64-hex sha256, got {value!r}")


def _hex(value: Any, where: str) -> None:
    _check(isinstance(value, str) and bool(_HEX.match(value)), where,
           f"expected an even-length hex string, got {value!r}")


def _int(value: Any, where: str, *, minimum: int = 0) -> None:
    _check(isinstance(value, int) and not isinstance(value, bool) and value >= minimum, where,
           f"expected an integer >= {minimum}, got {value!r}")


def _enum(value: Any, allowed: tuple[str, ...], where: str) -> None:
    _check(value in allowed, where, f"expected one of {allowed}, got {value!r}")


def _bool(value: Any, where: str) -> None:
    _check(isinstance(value, bool), where, f"expected a boolean, got {value!r}")


def _validate_span(span: Any, where: str) -> None:
    _obj(span, where)
    _token(span.get("space"), f"{where}.space")
    _int(span.get("offset"), f"{where}.offset")
    value = span.get("value_hex")
    mask = span.get("valid_mask")
    _hex(value, f"{where}.value_hex")
    _hex(mask, f"{where}.valid_mask")
    # An unknown byte is *masked*, never zero-filled: ``value_hex`` and
    # ``valid_mask`` describe the same span length, and the mask says which bytes
    # are known. Collapsing an unknown byte to a known zero is the defect this
    # pairing exists to prevent.
    _check(len(value) == len(mask), where,
           "value_hex and valid_mask must describe the same byte length")
    _enum(span.get("confidence"), FIELD_CONFIDENCE, f"{where}.confidence")


def _validate_carrier(carrier: Any, where: str) -> None:
    _obj(carrier, where)
    _token(carrier.get("name"), f"{where}.name")
    _enum(carrier.get("kind"), CARRIER_KINDS, f"{where}.kind")
    _int(carrier.get("bits"), f"{where}.bits", minimum=1)
    _hex(carrier.get("value_hex"), f"{where}.value_hex")
    _enum(carrier.get("confidence"), FIELD_CONFIDENCE, f"{where}.confidence")


def _validate_resource_ref(ref: Any, where: str) -> None:
    _obj(ref, where)
    _token(ref.get("resource_id"), f"{where}.resource_id")
    _int(ref.get("generation"), f"{where}.generation")
    if ref.get("version") is not None:
        _int(ref["version"], f"{where}.version")


def _validate_resource(res: Any, where: str) -> None:
    _obj(res, where)
    # Lifetime identity (``resource_id@generation``) is distinct from the content
    # version: the same live resource can be observed in several states.
    _token(res.get("resource_id"), f"{where}.resource_id")
    _int(res.get("generation"), f"{where}.generation")
    _int(res.get("version"), f"{where}.version")
    _sha(res.get("contents_sha256"), f"{where}.contents_sha256")
    aliases = res.get("aliases")
    _check(isinstance(aliases, list), where, "aliases must be a list")
    for i, alias in enumerate(aliases):
        _validate_resource_ref(alias, f"{where}.aliases[{i}]")


def _validate_effect(effect: Any, where: str) -> None:
    _obj(effect, where)
    _token(effect.get("op"), f"{where}.op")
    args = effect.get("args")
    _check(isinstance(args, list), where, "args must be a list")
    for i, arg in enumerate(args):
        _hex(arg, f"{where}.args[{i}]")
    if effect.get("target") is not None:
        _validate_resource_ref(effect["target"], f"{where}.target")


def _validate_provenance(prov: Any, where: str) -> None:
    _obj(prov, where)
    _sha(prov.get("module_sha256"), f"{where}.module_sha256")
    _token(prov.get("function_id"), f"{where}.function_id")
    _token(prov.get("callsite"), f"{where}.callsite")
    _sha(prov.get("capture_policy_digest"), f"{where}.capture_policy_digest")
    _token(prov.get("scenario"), f"{where}.scenario")
    _token(prov.get("epoch"), f"{where}.epoch")
    _int(prov.get("thread"), f"{where}.thread")


def _validate_invocation(inv: Any, where: str) -> None:
    _obj(inv, where)
    _token(inv.get("hook_id"), f"{where}.hook_id")
    _token(inv.get("call_id"), f"{where}.call_id")
    if inv.get("parent_span") is not None:
        _token(inv["parent_span"], f"{where}.parent_span")
    _int(inv.get("sequence"), f"{where}.sequence")


def _validate_abi(abi: Any, where: str) -> None:
    _obj(abi, where)
    _token(abi.get("calling_convention"), f"{where}.calling_convention")
    carriers = abi.get("carriers")
    _check(isinstance(carriers, list), where, "carriers must be a list")
    for i, carrier in enumerate(carriers):
        _validate_carrier(carrier, f"{where}.carriers[{i}]")
    if abi.get("return_carrier") is not None:
        _validate_carrier(abi["return_carrier"], f"{where}.return_carrier")
    _enum(abi.get("confidence"), FIELD_CONFIDENCE, f"{where}.confidence")


def _validate_state(state: Any, where: str) -> None:
    _obj(state, where)
    for key in ("pre", "post"):
        spans = state.get(key)
        _check(isinstance(spans, list), where, f"{key} must be a list")
        for i, span in enumerate(spans):
            _validate_span(span, f"{where}.{key}[{i}]")
    deps = state.get("unknown_dependencies")
    _check(isinstance(deps, list), where, "unknown_dependencies must be a list")
    for i, dep in enumerate(deps):
        _token(dep, f"{where}.unknown_dependencies[{i}]")


def _validate_effects(effects: Any, where: str) -> None:
    _obj(effects, where)
    ordered = effects.get("ordered")
    _check(isinstance(ordered, list), where, "ordered must be a list")
    for i, effect in enumerate(ordered):
        _validate_effect(effect, f"{where}.ordered[{i}]")
    _bool(effects.get("complete"), f"{where}.complete")


def _validate_completeness(comp: Any, where: str) -> None:
    _obj(comp, where)
    _enum(comp.get("state"), CAPTURE_STATES, f"{where}.state")
    _int(comp.get("losses"), f"{where}.losses")
    deps = comp.get("unavailable_dependencies")
    _check(isinstance(deps, list), where, "unavailable_dependencies must be a list")
    for i, dep in enumerate(deps):
        _token(dep, f"{where}.unavailable_dependencies[{i}]")


def _validate_fidelity(fid: Any, where: str) -> None:
    _obj(fid, where)
    _enum(fid.get("mode"), FIDELITY_MODES, f"{where}.mode")
    _token(fid.get("policy_version"), f"{where}.policy_version")


def validate_payload(payload: Any) -> None:
    """Raise ``SchemaError`` unless ``payload`` is a well-formed observation."""
    _obj(payload, "record")
    _check(payload.get("record_type") == "observation", "record.record_type",
           f"expected 'observation', got {payload.get('record_type')!r}")
    _validate_provenance(payload.get("provenance"), "record.provenance")
    _validate_invocation(payload.get("invocation"), "record.invocation")
    _validate_abi(payload.get("abi"), "record.abi")
    _validate_state(payload.get("state"), "record.state")
    resources = payload.get("resources")
    _check(isinstance(resources, list), "record.resources", "resources must be a list")
    for i, res in enumerate(resources):
        _validate_resource(res, f"record.resources[{i}]")
    _validate_effects(payload.get("effects"), "record.effects")
    _validate_completeness(payload.get("completeness"), "record.completeness")
    _validate_fidelity(payload.get("fidelity"), "record.fidelity")
    _obj(payload.get("raw"), "record.raw")
    if payload.get("private") is not None:
        _obj(payload.get("private"), "record.private")


# --- digests -------------------------------------------------------------------

def record_digest(payload: dict[str, Any]) -> str:
    """Integrity digest over the record's canonical bytes; defined for any valid record."""
    validate_payload(payload)
    return sha256(canonical_bytes(payload)).hexdigest()


def qualification_gaps(payload: dict[str, Any]) -> tuple[str, ...]:
    """Why a well-formed observation is *not* semantically comparable, in a fixed order.

    A gap is a category, optionally followed by the named dependency it is about, so a
    ledger can account for an observation without inventing a finer model. Native
    capture completeness (``completeness.state``) is deliberately not folded into a
    single boolean here: an observation may be completely captured yet lie outside the
    declared semantic domain (for example, its external effects were not recorded).
    """
    gaps: list[str] = []
    comp = payload["completeness"]
    if comp["state"] != "captured":
        gaps.append("not-captured")
    if comp["losses"] != 0:
        gaps.append("transport-loss")
    gaps.extend(f"unavailable-dependency:{dep}" for dep in comp["unavailable_dependencies"])
    gaps.extend(f"unknown-input:{dep}" for dep in payload["state"]["unknown_dependencies"])
    if not payload["effects"]["complete"]:
        gaps.append("effects-incomplete")
    return tuple(gaps)


def qualifies_for_semantic(payload: dict[str, Any]) -> bool:
    """Whether an observation is complete and canonical enough to compare semantically.

    A record qualifies only when it is a complete deep capture with no transport
    loss, no unavailable dependency, no declared unknown input, and a complete
    effect log. A ``counted-only`` or ``incomplete`` observation never qualifies: it
    may be counted and integrity-checked, but it cannot prove or disprove
    observable equality, so it must never enter semantic deduplication. A *captured*
    observation that fails only a domain-specific requirement is still fully stored
    evidence; ``qualification_gaps`` says why it is not comparable. The caller is
    responsible for ``validate_payload``; this function assumes a valid payload.
    """
    return not qualification_gaps(payload)


def _project_span(span: dict[str, Any]) -> dict[str, Any]:
    known = bytearray()
    value, mask = span["value_hex"], span["valid_mask"]
    for i in range(len(mask) // 2):
        if mask[2 * i:2 * i + 2].lower() == "ff":
            known += bytes.fromhex(value[2 * i:2 * i + 2])
    return {
        "space": span["space"],
        "offset": span["offset"],
        "valid_mask": mask.lower(),
        "known_hex": known.hex(),
        "confidence": span["confidence"],
    }


def _project_carrier(carrier: dict[str, Any]) -> dict[str, Any]:
    return {
        "name": carrier["name"],
        "kind": carrier["kind"],
        "bits": carrier["bits"],
        "value_hex": carrier["value_hex"].lower(),
        "confidence": carrier["confidence"],
    }


def _project_resource(res: dict[str, Any]) -> dict[str, Any]:
    return {
        "resource_id": res["resource_id"],
        "generation": res["generation"],
        "version": res["version"],
        "contents_sha256": res["contents_sha256"],
        "aliases": [{"resource_id": a["resource_id"], "generation": a["generation"]}
                    for a in res["aliases"]],
    }


def _project_effect(effect: dict[str, Any]) -> dict[str, Any]:
    projected: dict[str, Any] = {
        "op": effect["op"],
        "args": [arg.lower() for arg in effect["args"]],
    }
    if effect.get("target") is not None:
        target = effect["target"]
        projected["target"] = {
            "resource_id": target["resource_id"],
            "generation": target["generation"],
            "version": target.get("version"),
        }
    return projected


def semantic_projection(payload: dict[str, Any]) -> dict[str, Any]:
    """The canonical observable view used for semantic equality.

    Included: the binary/module and capture-policy identity (behavior is only
    comparable within a fixed observation scope), the function identity, the ABI
    carriers, the pre/post state, the declared unknown inputs, the resources and
    the ordered effects, and the declared fidelity policy.

    Excluded as occurrence metadata (not observable behavior): the callsite,
    scenario, epoch and thread, the invocation identity and per-thread sequence,
    the raw blob digest, and ``private`` diagnostics such as raw addresses. That
    exclusion is what lets the *same* behavior observed twice - at a different
    time, on a different thread, under a different call id - deduplicate. Unknown
    bytes contribute only their mask position; they are never coerced to zero.
    """
    prov, inv, abi = payload["provenance"], payload["invocation"], payload["abi"]
    return {
        "canonicalization_version": CANONICALIZATION_VERSION,
        "provenance": {
            "module_sha256": prov["module_sha256"],
            "function_id": prov["function_id"],
            "capture_policy_digest": prov["capture_policy_digest"],
        },
        "abi": {
            "calling_convention": abi["calling_convention"],
            "carriers": [_project_carrier(c) for c in abi["carriers"]],
            "return_carrier": (_project_carrier(abi["return_carrier"])
                               if abi.get("return_carrier") is not None else None),
            "confidence": abi["confidence"],
        },
        "state": {
            "pre": [_project_span(s) for s in payload["state"]["pre"]],
            "post": [_project_span(s) for s in payload["state"]["post"]],
            "unknown_dependencies": sorted(payload["state"]["unknown_dependencies"]),
        },
        "resources": [_project_resource(r) for r in payload["resources"]],
        "effects": {
            "ordered": [_project_effect(e) for e in payload["effects"]["ordered"]],
            "complete": payload["effects"]["complete"],
        },
        "fidelity": {
            "mode": payload["fidelity"]["mode"],
            "policy_version": payload["fidelity"]["policy_version"],
        },
    }


def semantic_digest(payload: dict[str, Any]) -> str:
    """Equality digest over the semantic projection; only for a qualified record.

    Raises ``SchemaError`` for an incomplete, lossy or dependency-unavailable
    observation - the record may still be integrity-checked with ``record_digest``,
    but it is not eligible to prove or disprove observable equality.
    """
    validate_payload(payload)
    if not qualifies_for_semantic(payload):
        raise SchemaError(
            "record does not qualify for semantic comparison "
            "(incomplete capture, transport loss, or an unavailable dependency)")
    return sha256(canonical_bytes(semantic_projection(payload))).hexdigest()


# --- sealed envelope -----------------------------------------------------------

def seal(payload: dict[str, Any]) -> dict[str, Any]:
    """Wrap a validated payload into a content-addressed sealed record."""
    validate_payload(payload)
    return {
        "schema_version": RECORD_SCHEMA_VERSION,
        "canonicalization_version": CANONICALIZATION_VERSION,
        "record_sha256": record_digest(payload),
        "payload": payload,
    }


def verify(sealed: Any) -> None:
    """Raise ``SchemaError`` unless the sealed record is well-formed and intact.

    An unsupported ``schema_version`` is rejected outright. No earlier record
    version exists yet, so there is no migration path to exercise; the mechanism
    is reserved for the first genuine version transition rather than faked with a
    dummy legacy schema.
    """
    _obj(sealed, "sealed")
    _check(sealed.get("schema_version") == RECORD_SCHEMA_VERSION, "sealed.schema_version",
           f"unsupported schema version {sealed.get('schema_version')!r}")
    _check(sealed.get("canonicalization_version") == CANONICALIZATION_VERSION,
           "sealed.canonicalization_version",
           f"unsupported canonicalization version {sealed.get('canonicalization_version')!r}")
    _sha(sealed.get("record_sha256"), "sealed.record_sha256")
    payload = sealed.get("payload")
    _obj(payload, "sealed.payload")
    validate_payload(payload)
    recomputed = record_digest(payload)
    _check(recomputed == sealed["record_sha256"], "sealed.record_sha256",
           f"record digest mismatch: {sealed['record_sha256']} != {recomputed}")
