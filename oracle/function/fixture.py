"""P4.10 A2: one offline paired-state capture, one closed function fixture.

A2 consumes a strict ``FUNCTION-CAPTURE v1`` transcript for one invocation of
``_GfxSetConstantBuffer`` and converts it into the existing observation schema. It
then applies a static function descriptor and returns a digest manifest. Nothing
here is live evidence: the committed fixture is produced by
``oracle.function.synthetic``, and ``raw.synthetic`` keeps that visible even though
every captured field is marked ``observed``.

The descriptor declares pins, carriers, layout, input regions, post-state and the
three write extents. It is not a descriptor interpreter. One explicit rule handles
the only branch: ``R9D == 0xffffffff`` reads the first four bytes at ``RSI``.

Memory writes are not an ordered effect stream. The reviewed contract has no calls
or externally ordered effects, so ``effects.ordered`` is empty and ``effects.complete``
is true; the three stores are checked as final state plus preservation. Closure runs
before sealing and writes every missing required component into
``completeness.unavailable_dependencies``. A qualified fixture is therefore both
schema-qualified and descriptor-closed. Missing evidence is stored as unqualified
evidence; malformed input or a foreign descriptor is rejected.
"""
from __future__ import annotations

import json
import re
from hashlib import sha256
from pathlib import Path
from typing import Any

from oracle.recorder import schema

DESCRIPTOR_SCHEMA_VERSION = "eu4-oracle-function-descriptor/v1"
DESCRIPTOR_PATH = (Path(__file__).resolve().parent / "descriptors"
                   / "gfx_set_constant_buffer.json")

CAPTURE_HEADER = "FUNCTION-CAPTURE"
CAPTURE_VERSION = "v1"
CAPTURE_FIELDS = ("capture", "scenario", "epoch", "thread", "sequence", "function",
                  "callsite", "module", "policy", "fidelity", "policy_version",
                  "synthetic")
REGION_NAMES = ("handle", "ctx", "canary-before", "canary-after", "buffer")
REGION_ORDER = REGION_NAMES
LOSS_KINDS = ("region", "carrier", "outcome")

_DECIMAL = re.compile(r"\A(?:0|[1-9][0-9]*)\Z")
_ADDRESS = re.compile(r"\A0x[0-9a-f]{1,16}\Z")
_BYTES = re.compile(r"\A(?:[0-9a-f]{2})+\Z")
_SHA256 = re.compile(r"\A[0-9a-f]{64}\Z")
_TOKEN = re.compile(r"\A[A-Za-z0-9][A-Za-z0-9._:+/@#-]*\Z")

#: Missing evidence that makes the *capture* incomplete rather than merely
#: out-of-domain. A preservation violation or a slot/aliasing failure is a
#: complete capture that does not close; it stays ``captured`` and unqualified.
_EVIDENCE_PREFIXES = ("carrier", "read", "post", "outcome")


class FixtureError(ValueError):
    """A transcript, descriptor or fixture cannot be used as declared evidence."""


# --- strict transcript parsing --------------------------------------------------

class _Record:
    """One transcript record read as unique ``key=value`` pairs."""

    def __init__(self, tag: str, parts: list[str]):
        self.tag = tag
        self.fields: dict[str, str] = {}
        for part in parts:
            key, separator, value = part.partition("=")
            if not separator or not key:
                raise FixtureError(f"{tag}: field is not key=value: {part!r}")
            if key in self.fields:
                raise FixtureError(f"{tag}: duplicate field {key!r}")
            self.fields[key] = value

    def exact(self, *keys: str) -> None:
        if set(self.fields) != set(keys):
            raise FixtureError(
                f"{self.tag}: expected exactly {list(keys)}, got {sorted(self.fields)}")

    def text(self, key: str) -> str:
        if key not in self.fields:
            raise FixtureError(f"{self.tag}: missing the {key!r} field")
        return self.fields[key]

    def token(self, key: str) -> str:
        value = self.text(key)
        if not _TOKEN.match(value):
            raise FixtureError(f"{self.tag}: {key} is not a token: {value!r}")
        return value

    def integer(self, key: str) -> int:
        value = self.text(key)
        if not _DECIMAL.match(value):
            raise FixtureError(
                f"{self.tag}: {key} is not a canonical non-negative decimal: {value!r}")
        return int(value)

    def boolean(self, key: str) -> bool:
        value = self.text(key)
        if value not in ("0", "1"):
            raise FixtureError(f"{self.tag}: {key} must be 0 or 1, got {value!r}")
        return value == "1"

    def sha(self, key: str) -> str:
        value = self.text(key)
        if not _SHA256.match(value):
            raise FixtureError(f"{self.tag}: {key} is not a lowercase sha256: {value!r}")
        return value

    def address(self, key: str) -> int:
        value = self.text(key)
        if not _ADDRESS.match(value):
            raise FixtureError(
                f"{self.tag}: {key} is not a lowercase 0x address: {value!r}")
        return int(value, 16)

    def byte_string(self, key: str) -> bytes:
        value = self.text(key)
        if not _BYTES.match(value):
            raise FixtureError(
                f"{self.tag}: {key} is not non-empty lowercase hex bytes: {value!r}")
        return bytes.fromhex(value)


def _checked_number(record: _Record, key: str, bits: int) -> int:
    value = record.address(key)
    if value >= 1 << bits:
        raise FixtureError(f"{record.tag}: {key} does not fit in {bits} bits")
    return value


def parse_capture(text: str) -> dict[str, Any]:
    """Parse and account one ``FUNCTION-CAPTURE v1`` transcript.

    The grammar is closed: one header, one ``CAPTURE`` record, optional ``ENTRY`` /
    ``EXIT`` region snapshots, ``CARRIER`` records, one optional ``OUTCOME``, ``LOSS``
    records and one terminal ``END``. Every region pair must describe the same
    address and length; a changed address or length is a contradictory capture, not
    a preservation failure. ``END`` counters are recomputed and compared, so losing
    lines without losing ``END`` is an error rather than a shorter, plausible
    capture.
    """
    version: str | None = None
    capture: dict[str, Any] | None = None
    regions: list[dict[str, Any]] = []
    carriers: dict[str, dict[str, Any]] = {}
    outcome: dict[str, Any] | None = None
    losses: list[dict[str, Any]] = []
    end: dict[str, int] | None = None
    finished = False

    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        if finished:
            raise FixtureError(f"the transcript continues after END: {raw!r}")
        parts = line.split()
        tag = parts[0]

        if version is None:
            if parts != [CAPTURE_HEADER, CAPTURE_VERSION]:
                raise FixtureError(
                    f"the transcript must begin with '{CAPTURE_HEADER} "
                    f"{CAPTURE_VERSION}', got {raw!r}")
            version = CAPTURE_VERSION
            continue

        if tag == "CAPTURE":
            if capture is not None:
                raise FixtureError("the transcript declares CAPTURE more than once")
            record = _Record(tag, parts[1:])
            record.exact(*CAPTURE_FIELDS)
            values: dict[str, Any] = {}
            for key in ("capture", "scenario", "epoch", "function", "callsite",
                        "fidelity", "policy_version"):
                values[key] = record.token(key)
            values["thread"] = record.integer("thread")
            values["sequence"] = record.integer("sequence")
            values["module"] = record.sha("module")
            values["policy"] = record.sha("policy")
            values["synthetic"] = record.boolean("synthetic")
            capture = values
        elif tag in ("ENTRY", "EXIT"):
            if capture is None:
                raise FixtureError(f"{tag} appears before the CAPTURE record")
            record = _Record(tag, parts[1:])
            record.exact("name", "address", "bytes")
            name = record.token("name")
            if name not in REGION_NAMES:
                raise FixtureError(f"{tag}: unknown region {name!r}")
            phase = "pre" if tag == "ENTRY" else "post"
            if any(r["name"] == name and r["phase"] == phase for r in regions):
                raise FixtureError(f"{tag}: region {name!r} appears twice")
            regions.append({"name": name, "phase": phase,
                            "address": record.address("address"),
                            "bytes": record.byte_string("bytes")})
        elif tag == "CARRIER":
            if capture is None:
                raise FixtureError("CARRIER appears before the CAPTURE record")
            record = _Record(tag, parts[1:])
            record.exact("name", "kind", "bits", "value", "confidence")
            name = record.token("name")
            if name in carriers:
                raise FixtureError(f"CARRIER: {name!r} appears twice")
            kind = record.token("kind")
            if kind not in schema.CARRIER_KINDS:
                raise FixtureError(f"CARRIER: unknown kind {kind!r}")
            bits = record.integer("bits")
            if bits not in (8, 16, 32, 64):
                raise FixtureError(f"CARRIER: unsupported width {bits}")
            confidence = record.token("confidence")
            if confidence not in schema.FIELD_CONFIDENCE:
                raise FixtureError(f"CARRIER: unknown confidence {confidence!r}")
            carriers[name] = {"name": name, "kind": kind, "bits": bits,
                              "value": _checked_number(record, "value", bits),
                              "confidence": confidence}
        elif tag == "OUTCOME":
            if capture is None:
                raise FixtureError("OUTCOME appears before the CAPTURE record")
            if outcome is not None:
                raise FixtureError("the transcript declares OUTCOME more than once")
            record = _Record(tag, parts[1:])
            record.exact("register", "value", "confidence")
            confidence = record.token("confidence")
            if confidence not in schema.FIELD_CONFIDENCE:
                raise FixtureError(f"OUTCOME: unknown confidence {confidence!r}")
            outcome = {"register": record.token("register"),
                       "value": _checked_number(record, "value", 64),
                       "confidence": confidence}
        elif tag == "LOSS":
            if capture is None:
                raise FixtureError("LOSS appears before the CAPTURE record")
            record = _Record(tag, parts[1:])
            record.exact("kind", "name", "reason")
            kind = record.token("kind")
            if kind not in LOSS_KINDS:
                raise FixtureError(f"LOSS: unknown kind {kind!r}")
            loss = {"kind": kind, "name": record.token("name"),
                    "reason": record.token("reason")}
            if any(item["kind"] == kind and item["name"] == loss["name"]
                   for item in losses):
                raise FixtureError(f"LOSS: {kind} {loss['name']!r} appears twice")
            losses.append(loss)
        elif tag == "END":
            if end is not None:
                raise FixtureError("the transcript declares END more than once")
            record = _Record(tag, parts[1:])
            record.exact("regions", "carriers", "outcomes", "losses")
            end = {key: record.integer(key)
                   for key in ("regions", "carriers", "outcomes", "losses")}
            finished = True
        else:
            raise FixtureError(f"unrecognized transcript line: {raw!r}")

    if version is None:
        raise FixtureError("the transcript has no FUNCTION-CAPTURE header")
    if capture is None:
        raise FixtureError("the transcript has no CAPTURE record")
    if end is None:
        raise FixtureError("the transcript has no END record")

    counted = {"regions": len(regions), "carriers": len(carriers),
               "outcomes": 0 if outcome is None else 1, "losses": len(losses)}
    if end != counted:
        raise FixtureError(
            f"END accounting does not match the transcript: declared {end}, "
            f"recomputed {counted}")

    paired: dict[str, dict[str, dict[str, Any]]] = {}
    for region in regions:
        paired.setdefault(region["name"], {})[region["phase"]] = region
    for name, pair in paired.items():
        if "pre" in pair and "post" in pair:
            if pair["pre"]["address"] != pair["post"]["address"]:
                raise FixtureError(f"region {name!r} changes address between entry and exit")
            if len(pair["pre"]["bytes"]) != len(pair["post"]["bytes"]):
                raise FixtureError(f"region {name!r} changes length between entry and exit")

    return {"capture_version": CAPTURE_VERSION, "capture": capture,
            "regions": regions, "carriers": carriers, "outcome": outcome,
            "losses": losses}


# --- the static descriptor ------------------------------------------------------

def _is_int(value: Any, minimum: int = 0) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value >= minimum


def validate_descriptor(descriptor: Any) -> None:
    """Reject a descriptor that is missing the facts closure reads.

    Static values are pinned by tests against the reviewed contract; this is not a
    general descriptor schema.
    """
    if not isinstance(descriptor, dict):
        raise FixtureError("function descriptor is not an object")
    if descriptor.get("schema_version") != DESCRIPTOR_SCHEMA_VERSION:
        raise FixtureError(
            f"unsupported function descriptor schema: "
            f"{descriptor.get('schema_version')!r}")
    for key in ("function_id", "hook_id", "symbol", "binary", "abi", "layout",
                "input_regions", "post_state", "preservation", "effects",
                "fidelity", "capture_policy"):
        if key not in descriptor:
            raise FixtureError(f"descriptor is missing {key!r}")
    binary = descriptor["binary"]
    if not isinstance(binary.get("sha256"), str) or not _SHA256.match(binary["sha256"]):
        raise FixtureError("descriptor.binary.sha256 must be lowercase sha256")
    if not isinstance(binary.get("code_sha256"), str) or not _SHA256.match(
            binary["code_sha256"]):
        raise FixtureError("descriptor.binary.code_sha256 must be lowercase sha256")
    layout = descriptor["layout"]
    if not all(_is_int(layout.get(key)) for key in (
            "observation_start", "observation_end", "slot_min", "slot_max")):
        raise FixtureError("descriptor layout is incomplete")
    if layout["observation_start"] >= layout["observation_end"] \
            or layout["slot_min"] >= layout["slot_max"]:
        raise FixtureError("descriptor ranges are empty")
    if descriptor["effects"].get("external") != [] \
            or descriptor["effects"].get("complete") is not True:
        raise FixtureError("descriptor effects must be empty and complete")
    if descriptor["fidelity"].get("mode") not in schema.FIDELITY_MODES:
        raise FixtureError("descriptor fidelity mode is unknown")

def load_descriptor(path: str | Path = DESCRIPTOR_PATH) -> dict[str, Any]:
    """Read and validate the static function descriptor."""
    try:
        descriptor = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise FixtureError(f"cannot read function descriptor {path}: {exc}") from exc
    validate_descriptor(descriptor)
    return descriptor


def descriptor_digest(descriptor: dict[str, Any]) -> str:
    """Identity of the static domain under which a fixture was closed."""
    validate_descriptor(descriptor)
    return sha256(schema.canonical_bytes(descriptor)).hexdigest()


def capture_policy_digest(descriptor: dict[str, Any]) -> str:
    """Identity of the effective paired-state capture policy."""
    validate_descriptor(descriptor)
    return sha256(schema.canonical_bytes(descriptor["capture_policy"])).hexdigest()


# --- materialization ------------------------------------------------------------

def _value_hex(value: int, bits: int) -> str:
    return value.to_bytes(bits // 8, "big").hex()


def _region_offset(descriptor: dict[str, Any], name: str) -> int:
    layout = descriptor["layout"]
    return {"ctx": layout["observation_start"],
            "canary-before": layout["canary_before_start"],
            "canary-after": layout["canary_after_start"]}.get(name, 0)


def _span(descriptor: dict[str, Any], name: str, data: bytes) -> dict[str, Any]:
    return {"space": f"heap:{name}", "offset": _region_offset(descriptor, name),
            "value_hex": data.hex(), "valid_mask": "ff" * len(data),
            "confidence": "observed"}


def _region_map(capture: dict[str, Any]) -> dict[tuple[str, str], dict[str, Any]]:
    return {(region["phase"], region["name"]): region
            for region in capture["regions"]}


def _check_capture_matches_descriptor(capture: dict[str, Any],
                                      descriptor: dict[str, Any]) -> None:
    meta = capture["capture"]
    if meta["function"] != descriptor["function_id"]:
        raise FixtureError(
            f"capture is for {meta['function']!r}, descriptor is for "
            f"{descriptor['function_id']!r}")
    if meta["module"] != descriptor["binary"]["sha256"]:
        raise FixtureError("capture module does not match the pinned binary")
    if meta["policy"] != capture_policy_digest(descriptor):
        raise FixtureError("capture policy does not match the descriptor")
    if meta["fidelity"] != descriptor["fidelity"]["mode"]:
        raise FixtureError("capture fidelity does not match the descriptor")
    if meta["policy_version"] != descriptor["fidelity"]["policy_version"]:
        raise FixtureError("capture policy version does not match the descriptor")

    required = {carrier["name"]: carrier
                for carrier in descriptor["abi"]["required_carriers"]}
    ignored = set(descriptor["abi"]["ignored_carriers"])
    for name, carrier in capture["carriers"].items():
        if name in ignored:
            continue
        if name not in required:
            raise FixtureError(f"capture carries undeclared register {name!r}")
        spec = required[name]
        if carrier["kind"] != spec["kind"] or carrier["bits"] != spec["bits"]:
            raise FixtureError(
                f"capture carrier {name!r} does not match the descriptor shape")
    if capture["outcome"] is not None:
        outcome = capture["outcome"]
        expected = descriptor["abi"]["outcome"]["register"]
        if outcome["register"] != expected:
            raise FixtureError(
                f"capture outcome is {outcome['register']!r}, descriptor expects "
                f"{expected!r}")
    if capture["capture_version"] != CAPTURE_VERSION:
        raise FixtureError("unsupported capture version")


def _build_record(capture: dict[str, Any], descriptor: dict[str, Any]) -> dict[str, Any]:
    _check_capture_matches_descriptor(capture, descriptor)
    meta = capture["capture"]
    regions = _region_map(capture)
    layout = descriptor["layout"]

    pre = [_span(descriptor, name, regions[("pre", name)]["bytes"])
           for name in REGION_ORDER if ("pre", name) in regions]
    post = [_span(descriptor, name, regions[("post", name)]["bytes"])
            for name in REGION_ORDER if ("post", name) in regions]

    carriers = []
    required = descriptor["abi"]["required_carriers"]
    for spec in required:
        observed = capture["carriers"].get(spec["name"])
        if observed is None:
            continue
        carriers.append({
            "name": spec["name"], "kind": spec["kind"], "bits": spec["bits"],
            "value_hex": _value_hex(observed["value"], spec["bits"]),
            "confidence": observed["confidence"],
        })
    all_observed = all(c["confidence"] == "observed" for c in carriers) \
        and len(carriers) == len(required)

    raw_regions: dict[str, dict[str, str]] = {}
    for (phase, name), region in regions.items():
        raw_regions.setdefault(name, {})[phase] = hex(region["address"])
    handle_pre = regions.get(("pre", "handle"))
    ctx_base = (int.from_bytes(handle_pre["bytes"][:8], "little")
                if handle_pre is not None and len(handle_pre["bytes"]) >= 8 else None)
    handle_region = regions.get(("pre", "handle")) or regions.get(("post", "handle"))
    buffer_region = regions.get(("pre", "buffer")) or regions.get(("post", "buffer"))

    outcome = capture["outcome"]
    domain_digest = descriptor_digest(descriptor)
    raw = {
        "synthetic": meta["synthetic"],
        "symbol": descriptor["symbol"],
        "descriptor_sha256": domain_digest,
        "regions": raw_regions,
        "addresses": {
            "handle": hex(handle_region["address"]) if handle_region is not None else None,
            "ctx": hex(ctx_base) if ctx_base is not None else None,
            "buffer": hex(buffer_region["address"]) if buffer_region is not None else None,
        },
        "outcome": ({"register": outcome["register"],
                     "value_hex": _value_hex(outcome["value"], 64),
                     "confidence": outcome["confidence"]}
                    if outcome is not None else None),
    }

    record = {
        "record_type": "observation",
        "provenance": {
            "module_sha256": descriptor["binary"]["sha256"],
            "function_id": descriptor["function_id"],
            "callsite": meta["callsite"],
            # semantic_projection includes this, so bind equality to the whole domain.
            "capture_policy_digest": domain_digest,
            "scenario": meta["scenario"],
            "epoch": meta["epoch"],
            "thread": meta["thread"],
        },
        "invocation": {
            "hook_id": descriptor["hook_id"],
            "call_id": f"{meta['capture']}:{meta['scenario']}:{meta['thread']}:"
                       f"{meta['sequence']}",
            "parent_span": None,
            "sequence": meta["sequence"],
        },
        "abi": {
            "calling_convention": descriptor["abi"]["calling_convention"],
            "carriers": carriers,
            "return_carrier": None,
            "confidence": "observed" if all_observed else "unknown",
        },
        "state": {"pre": pre, "post": post, "unknown_dependencies": []},
        "resources": [],
        "effects": {"ordered": [], "complete": True},
        "completeness": {"state": "captured", "losses": len(capture["losses"]),
                         "unavailable_dependencies": []},
        "fidelity": {"mode": descriptor["fidelity"]["mode"],
                     "policy_version": descriptor["fidelity"]["policy_version"]},
        "raw": raw,
    }

    schema.validate_payload(record)
    assessment = assess(record, descriptor)
    record["completeness"]["unavailable_dependencies"] = assessment["missing"]
    record["completeness"]["state"] = _state_for(assessment["missing"], capture["losses"])
    schema.validate_payload(record)
    return record


# --- closure --------------------------------------------------------------------

def _span_data(record: dict[str, Any], phase: str, name: str,
               descriptor: dict[str, Any]) -> bytes | None:
    space = f"heap:{name}"
    if name == "ctx":
        offset = _region_offset(descriptor, name)
        width = descriptor["layout"]["observation_end"] - offset
    elif name in ("canary-before", "canary-after"):
        offset, width = _band(descriptor, name)
    else:
        offset = _region_offset(descriptor, name)
        width = descriptor["input_regions"][name]["width"]
    for span in record["state"][phase]:
        if span["space"] != space:
            continue
        if (span["offset"] != offset or len(span["value_hex"]) != 2 * width
                or len(span["valid_mask"]) != 2 * width):
            return None
        if any(span["valid_mask"][i:i + 2] != "ff"
               for i in range(0, len(span["valid_mask"]), 2)):
            return None
        return bytes.fromhex(span["value_hex"])
    return None


def _region_address(record: dict[str, Any], phase: str, name: str) -> int | None:
    value = record["raw"].get("regions", {}).get(name, {}).get(phase)
    if not isinstance(value, str) or not _ADDRESS.match(value):
        return None
    return int(value, 16)


def _carrier(record: dict[str, Any], name: str) -> int | None:
    for carrier in record["abi"]["carriers"]:
        if carrier["name"] == name and carrier["confidence"] == "observed":
            return int(carrier["value_hex"], 16)
    return None


def _field_offset(descriptor: dict[str, Any], field: str, slot: int) -> int:
    spec = descriptor["post_state"][field]
    if "offset" in spec:
        return spec["offset"]
    return spec["base"] + slot * spec["stride"]


def _write_indices(descriptor: dict[str, Any], slot: int | None) -> set[int]:
    """Offsets *relative to the observation start* the contract permits changing."""
    layout = descriptor["layout"]
    if slot is None or not (layout["slot_min"] <= slot < layout["slot_max"]):
        return set()
    allowed: set[int] = set()
    for field in ("word_count", "buffer", "dirty"):
        offset = _field_offset(descriptor, field, slot)
        width = descriptor["post_state"][field]["width"]
        for absolute in range(offset, offset + width):
            if layout["observation_start"] <= absolute < layout["observation_end"]:
                allowed.add(absolute - layout["observation_start"])
    return allowed


def _regions_disjoint(record: dict[str, Any], descriptor: dict[str, Any]) -> bool:
    intervals: list[tuple[int, int]] = []
    for name in REGION_ORDER:
        data = _span_data(record, "pre", name, descriptor)
        address = _region_address(record, "pre", name)
        if data is None:
            data = _span_data(record, "post", name, descriptor)
            address = _region_address(record, "post", name)
        if data is not None and address is not None:
            intervals.append((address, address + len(data)))
    intervals.sort()
    return all(intervals[index][1] <= intervals[index + 1][0]
               for index in range(len(intervals) - 1))


def _band(descriptor: dict[str, Any], name: str) -> tuple[int, int]:
    layout = descriptor["layout"]
    start = layout[f"{name.replace('-', '_')}_start"]
    end = layout[f"{name.replace('-', '_')}_end"]
    return start, end - start


def _state_for(missing: list[str], losses: int) -> str:
    if losses or any(item.split(":", 1)[0] in _EVIDENCE_PREFIXES for item in missing):
        return "incomplete"
    return "captured"


def assess(record: dict[str, Any], descriptor: dict[str, Any]) -> dict[str, Any]:
    """Recompute closure from the record, never from its qualification fields.

    Returns the required components satisfied, the ones missing, and which branch
    the selector takes. The verdict is derived from this result; there is no second
    rules engine over coverage labels.
    """
    layout = descriptor["layout"]
    observation_start = layout["observation_start"]
    observation_end = layout["observation_end"]
    observation_length = observation_end - observation_start
    handle_width = descriptor["input_regions"]["handle"]["width"]
    buffer_width = descriptor["input_regions"]["buffer"]["width"]
    selector_value = descriptor["input_regions"]["buffer"]["selector_value"]
    outcome_register = descriptor["abi"]["outcome"]["register"]

    rdi = _carrier(record, descriptor["input_regions"]["handle"]["carrier"])
    rsi = _carrier(record, descriptor["input_regions"]["buffer"]["carrier"])
    rcx = _carrier(record, "RCX")
    r9 = _carrier(record, descriptor["input_regions"]["buffer"]["selector_carrier"])
    slot = rcx & 0xFFFFFFFF if rcx is not None else None
    selector = r9 & 0xFFFFFFFF if r9 is not None else None
    buffer_path = selector == selector_value

    required = {f"carrier:{carrier['name']}"
                for carrier in descriptor["abi"]["required_carriers"]}
    required.update(f"read:{name}" for name in descriptor["required_reads"])
    required.update(f"post:{name}" for name in descriptor["post_state"])
    required.update(f"preserve:{name}"
                    for name in descriptor["preservation"]["canary_regions"])
    required.add(f"preserve:{descriptor['preservation']['observation_region']}")
    for name in descriptor["preservation"]["input_regions"]:
        if name == "buffer" and not buffer_path:
            continue
        required.add(f"preserve:{name}")
    if buffer_path:
        required.add("read:buffer")
    required.add(f"outcome:{outcome_register}")
    required.update(("domain:slot", "domain:disjoint"))

    satisfied: set[str] = set()

    def check(identifier: str, condition: bool) -> None:
        if condition:
            satisfied.add(identifier)

    for carrier in descriptor["abi"]["required_carriers"]:
        check(f"carrier:{carrier['name']}", _carrier(record, carrier["name"]) is not None)

    handle_pre = _span_data(record, "pre", "handle", descriptor)
    handle_post = _span_data(record, "post", "handle", descriptor)
    handle_pre_address = _region_address(record, "pre", "handle")
    handle_post_address = _region_address(record, "post", "handle")
    ctx_base = (int.from_bytes(handle_pre[:handle_width], "little")
                if handle_pre is not None and len(handle_pre) >= handle_width else None)

    check("read:handle",
          handle_pre is not None and len(handle_pre) >= handle_width
          and rdi is not None and handle_pre_address == rdi)

    ctx_pre = _span_data(record, "pre", "ctx", descriptor)
    ctx_post = _span_data(record, "post", "ctx", descriptor)
    ctx_pre_address = _region_address(record, "pre", "ctx")
    ctx_post_address = _region_address(record, "post", "ctx")
    check("read:ctx",
          ctx_pre is not None and len(ctx_pre) >= observation_length
          and ctx_base is not None and ctx_pre_address == ctx_base + observation_start)

    for name in descriptor["preservation"]["canary_regions"]:
        start, width = _band(descriptor, name)
        data = _span_data(record, "pre", name, descriptor)
        address = _region_address(record, "pre", name)
        check(f"read:{name}",
              data is not None and len(data) >= width
              and ctx_base is not None and address == ctx_base + start)

    if buffer_path:
        buffer_pre = _span_data(record, "pre", "buffer", descriptor)
        buffer_pre_address = _region_address(record, "pre", "buffer")
        check("read:buffer",
              buffer_pre is not None and len(buffer_pre) >= buffer_width
              and rsi is not None and buffer_pre_address == rsi)

    post_ok = (ctx_post is not None and len(ctx_post) >= observation_length
               and ctx_base is not None and ctx_post_address == ctx_base + observation_start)
    for field in descriptor["post_state"]:
        ok = False
        if post_ok and slot is not None:
            offset = _field_offset(descriptor, field, slot)
            width = descriptor["post_state"][field]["width"]
            start = offset - observation_start
            if (observation_start <= offset < observation_end
                    and offset + width <= observation_end
                    and start + width <= len(ctx_post)):
                value = int.from_bytes(ctx_post[start:start + width], "little")
                if field == "word_count":
                    ok = True
                elif field == "buffer":
                    ok = value == rsi
                elif field == "dirty":
                    ok = value == descriptor["post_state"][field]["equals"]
        check(f"post:{field}", ok)

    check("preserve:handle",
          handle_pre is not None and handle_post is not None
          and len(handle_pre) >= handle_width and len(handle_post) >= handle_width
          and handle_pre_address == handle_post_address
          and handle_pre[:handle_width] == handle_post[:handle_width])

    for name in descriptor["preservation"]["canary_regions"]:
        _, width = _band(descriptor, name)
        pre = _span_data(record, "pre", name, descriptor)
        post = _span_data(record, "post", name, descriptor)
        check(f"preserve:{name}",
              pre is not None and post is not None
              and len(pre) >= width and len(post) >= width
              and _region_address(record, "pre", name)
              == _region_address(record, "post", name)
              and pre[:width] == post[:width])

    if ctx_pre is not None and ctx_post is not None \
            and len(ctx_pre) >= observation_length \
            and len(ctx_post) >= observation_length \
            and ctx_pre_address == ctx_post_address:
        allowed = _write_indices(descriptor, slot)
        changed = {index for index in range(observation_length)
                   if ctx_pre[index] != ctx_post[index]}
        check(f"preserve:{descriptor['preservation']['observation_region']}",
              changed <= allowed)

    if buffer_path:
        buffer_pre = _span_data(record, "pre", "buffer", descriptor)
        buffer_post = _span_data(record, "post", "buffer", descriptor)
        check("preserve:buffer",
              buffer_pre is not None and buffer_post is not None
              and len(buffer_pre) >= buffer_width and len(buffer_post) >= buffer_width
              and _region_address(record, "pre", "buffer")
              == _region_address(record, "post", "buffer")
              and buffer_pre[:buffer_width] == buffer_post[:buffer_width])

    outcome = record["raw"].get("outcome")
    if isinstance(outcome, dict) and outcome.get("register") == outcome_register \
            and outcome.get("confidence") == "observed":
        value = int(outcome["value_hex"], 16)
        check(f"outcome:{outcome_register}", ctx_base is not None and value == ctx_base)

    check("domain:slot",
          slot is not None and layout["slot_min"] <= slot < layout["slot_max"])
    check("domain:disjoint", _regions_disjoint(record, descriptor))

    return {"coverage": sorted(satisfied & required),
            "missing": sorted(required - satisfied),
            "buffer_path": buffer_path}


# --- manifest and deterministic verification ------------------------------------

def close(record: dict[str, Any], descriptor: dict[str, Any]) -> dict[str, Any]:
    """Close a materialized record under the descriptor and return its manifest.

    The record's qualification fields are not trusted: closure is recomputed, and a
    record that disagrees with it is refused rather than relabelled. A qualified
    verdict requires both descriptor closure and ``schema.qualifies_for_semantic``.
    """
    schema.validate_payload(record)
    expected_descriptor = descriptor_digest(descriptor)
    if record["raw"].get("descriptor_sha256") != expected_descriptor:
        raise FixtureError("record was materialized under a different descriptor")
    assessment = assess(record, descriptor)
    missing = assessment["missing"]
    completeness = record["completeness"]
    if sorted(completeness["unavailable_dependencies"]) != missing:
        raise FixtureError(
            "record unavailable_dependencies do not match descriptor closure")
    if completeness["state"] != _state_for(missing, completeness["losses"]):
        raise FixtureError("record completeness state does not match descriptor closure")
    if record["effects"] != {"ordered": [], "complete": True}:
        raise FixtureError("A2 records must carry an empty, complete effect log")

    qualified = (not missing and completeness["losses"] == 0
                 and schema.qualifies_for_semantic(record))
    return {
        "record_digest": schema.record_digest(record),
        "domain_digest": expected_descriptor,
        "verdict": "qualified" if qualified else "unqualified",
        "coverage": assessment["coverage"],
    }


def materialize_capture(text: str, descriptor: dict[str, Any]) -> dict[str, Any]:
    """Parse, materialize and close one transcript; return record and manifest."""
    capture = parse_capture(text)
    record = _build_record(capture, descriptor)
    return {"record": record, "manifest": close(record, descriptor)}


def verify_fixture(text: str, descriptor: dict[str, Any],
                   manifest: dict[str, Any]) -> dict[str, Any]:
    """Recompute the record and manifest from their sources and compare four fields.

    A manifest is a projection, not a second source of truth. This function does not
    consult a store or a second closure implementation: it parses the transcript,
    rebuilds the record, reruns closure and requires exact equality.
    """
    if not isinstance(manifest, dict):
        raise FixtureError("fixture manifest is not an object")
    expected = materialize_capture(text, descriptor)["manifest"]
    if set(manifest) != set(expected):
        raise FixtureError(
            f"fixture manifest fields are {sorted(manifest)}, expected "
            f"{sorted(expected)}")
    for key in expected:
        if manifest[key] != expected[key]:
            raise FixtureError(
                f"fixture manifest {key} mismatch: {manifest[key]!r} != {expected[key]!r}")
    return expected
