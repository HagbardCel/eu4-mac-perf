"""Sealed frame captures and the independent replay engine (P4b).

A frame capture is the *whole* input a replay is allowed to need: an ordered
timeline of resource creations, destructions and draw operations, together with the
initial contents of each created resource and the frame the subject presented. It
contains no pointer, no address, no clock and no process identity, so a frame can be
reconstructed elsewhere - in a fresh process, from the sealed artifact alone.

The replay reimplements the declared byte-level semantics of each operation rather
than calling the subject: that is what makes it an *independent* reconstruction
instead of a re-run. References are resolved by the closure rule P4.6 declares -
``oracle/recorder/registry.py`` names the lifetimes, and this module uses that one
naming - so a reference to a lifetime the capture does not hold fails **closed** with
``INCOMPLETE_ORACLE`` rather than reconstructing a plausible wrong frame.

Sealing reuses the recorder's canonical serialization (one canonicalization, not
two) and the create-only publication discipline the evidence store adapted, so a
sealed capture is tamper-evident at load.

Two properties keep an incomplete capture from reading as a complete one. The
transcript is self-accounting: its terminal counters are recomputed from the parsed
timeline and compared, so losing records without losing the ``END`` is an error
rather than a shorter, plausible frame. And every presentation is verified where it
happens - digest and bytes - instead of only the last one, so an inconsistent earlier
frame cannot hide behind a correct final one.
"""
from __future__ import annotations

import json
import re
from hashlib import sha256
from pathlib import Path
from typing import Any, Sequence

from oracle.recorder import registry
from oracle.recorder.schema import canonical_bytes
from oracle.recorder.store import write_create_only

FRAME_CAPTURE_VERSION = "eu4-oracle-frame-capture/v1"
FRAME_SEAL_VERSION = "eu4-oracle-frame-capture-seal/v1"

KIND_NAMES = {1: "texture", 2: "vertex-buffer"}
TEXTURE = "texture"
VERTEX_BUFFER = "vertex-buffer"

OPS = ("create", "destroy", "fill", "blit", "mix", "present")

FNV_OFFSET_BASIS = 2166136261
FNV_PRIME = 16777619
_MASK32 = 0xFFFFFFFF


class ReplayError(RuntimeError):
    """A frame could not be reconstructed from its capture."""


class CaptureError(ReplayError):
    """The capture is malformed, or its reconstruction does not match it."""


class IncompleteOracle(ReplayError):
    """The capture does not hold a resource the frame depends on."""


def fnv1a32(data: bytes | bytearray) -> int:
    """The 32-bit FNV-1a the subject uses; the one hash both sides must agree on."""
    value = FNV_OFFSET_BASIS
    for byte in data:
        value = ((value ^ byte) * FNV_PRIME) & _MASK32
    return value


# --- parsing the subject's transcript -------------------------------------------

#: The graphics subject's header. One subject, one version: an unknown header is
#: rejected rather than parsed as if it were this one.
GFX_HEADER = "GFX"
GFX_SUBJECT = "gfx"
GFX_VERSIONS = ("v1",)

#: The terminal accounting record's exact fields - no omissions, no extras.
END_RECORD = "END"
END_FIELDS = ("creates", "destroys", "ops", "effects")

_CANONICAL_DECIMAL = re.compile(r"\A(?:0|[1-9][0-9]*)\Z")


def _hex(value: str, where: str = "value") -> str:
    if len(value) % 2 or any(character not in "0123456789abcdef" for character in value):
        raise CaptureError(f"{where} must be lowercase, even-length hex, got {value!r}")
    return value


class _Record:
    """One transcript record: its tag and its fields, read strictly.

    Fields must be unique ``key=value`` pairs, integers must be canonical non-negative
    decimals (no sign, no leading zeros, no surrounding space) and hex must be
    lowercase and even-length. A transcript is a wire format, so every ambiguity is a
    way for two different captures to look alike; there is no reason to accept one.
    """

    def __init__(self, tag: str, parts: Sequence[str]):
        self.tag = tag
        self.fields: dict[str, str] = {}
        for part in parts:
            key, separator, value = part.partition("=")
            if not separator:
                raise CaptureError(f"{tag}: field is not key=value: {part!r}")
            if key in self.fields:
                raise CaptureError(f"{tag}: duplicate field {key!r}")
            self.fields[key] = value

    def text(self, key: str) -> str:
        if key not in self.fields:
            raise CaptureError(f"{self.tag}: missing the {key!r} field")
        return self.fields[key]

    def integer(self, key: str) -> int:
        value = self.text(key)
        if not _CANONICAL_DECIMAL.match(value):
            raise CaptureError(
                f"{self.tag}: {key} must be a canonical non-negative decimal integer, "
                f"got {value!r}")
        return int(value)

    def hex(self, key: str) -> str:
        return _hex(self.text(key), f"{self.tag}.{key}")

    def reference(self, slot_key: str = "slot",
                  generation_key: str = "gen") -> dict[str, int]:
        return {"slot": self.integer(slot_key), "generation": self.integer(generation_key)}


def _terminal_counts(timeline: Sequence[dict[str, Any]]) -> dict[str, int]:
    """The subject's terminal counters, recomputed from the parsed timeline.

    The subject emits exactly one effect per successful create, destroy, draw or
    present, so its declared counters are a function of the timeline and can be
    checked against it rather than trusted.
    """
    creates = sum(1 for step in timeline if step["op"] == "create")
    destroys = sum(1 for step in timeline if step["op"] == "destroy")
    draws = sum(1 for step in timeline if step["op"] in ("fill", "blit", "mix"))
    presents = sum(1 for step in timeline if step["op"] == "present")
    return {"creates": creates, "destroys": destroys, "ops": draws,
            "effects": creates + destroys + draws + presents}


def parse_frame_transcript(text: str) -> dict[str, Any]:
    """Parse the graphics subject's transcript into a frame capture document.

    The parser is a state machine over the transcript: one header, then records, then
    exactly one terminal accounting record, which must be last. The terminal counts are
    recomputed from the parsed timeline and compared against the declared ones, so a
    transcript that lost records - but kept its ``END`` - is rejected instead of
    replaying as a plausible, shorter frame.

    The accounting is a check, not evidence: it is consumed here and never enters the
    capture document, whose bytes - and therefore its seal - are unaffected. A capture
    of a frame that did not run cleanly is rejected rather than replayed.
    """
    version: str | None = None
    timeline: list[dict[str, Any]] = []
    end: _Record | None = None
    finished = False

    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        parts = line.split()
        tag = parts[0]

        if finished:
            raise CaptureError(
                f"the transcript continues after its {END_RECORD} record: {raw!r}")
        if version is None and tag != GFX_HEADER:
            raise CaptureError(
                f"the transcript must begin with its {GFX_HEADER} header, got {raw!r}")

        if tag == GFX_HEADER:
            if version is not None:
                raise CaptureError("the transcript declares its header more than once")
            if len(parts) != 2 or parts[1] not in GFX_VERSIONS:
                raise CaptureError(f"unsupported graphics subject header: {raw!r}")
            version = parts[1]
        elif tag == "CREATE":
            record = _Record(tag, parts[1:])
            kind = record.integer("kind")
            if kind not in KIND_NAMES:
                raise CaptureError(f"unknown resource kind {kind}")
            timeline.append({"op": "create", **record.reference(), "kind": KIND_NAMES[kind],
                             "size": record.integer("size"),
                             "contents_hex": record.hex("contents"),
                             "result": record.integer("result")})
        elif tag == "DESTROY":
            record = _Record(tag, parts[1:])
            timeline.append({"op": "destroy", "target": record.reference(),
                             "result": record.integer("result")})
        elif tag == "FILL":
            record = _Record(tag, parts[1:])
            timeline.append({"op": "fill", "target": record.reference(),
                             "offset": record.integer("offset"),
                             "span": record.integer("span"),
                             "pattern": record.integer("pattern"),
                             "result": record.integer("result")})
        elif tag == "BLIT":
            record = _Record(tag, parts[1:])
            timeline.append({"op": "blit", "target": record.reference(),
                             "offset": record.integer("offset"),
                             "source": record.reference("source_slot", "source_gen"),
                             "result": record.integer("result")})
        elif tag == "MIX":
            record = _Record(tag, parts[1:])
            timeline.append({"op": "mix", "target": record.reference(),
                             "offset": record.integer("offset"),
                             "source": record.reference("source_slot", "source_gen"),
                             "rounds": record.integer("rounds"),
                             "result": record.integer("result")})
        elif tag == "PRESENT":
            record = _Record(tag, parts[1:])
            timeline.append({"op": "present", "target": record.reference(),
                             "digest": record.hex("digest"), "bytes_hex": record.hex("bytes"),
                             "result": record.integer("result")})
        elif tag == END_RECORD:
            end = _Record(tag, parts[1:])
            finished = True
        else:
            raise CaptureError(f"unrecognized graphics transcript line: {raw!r}")

    if version is None:
        raise CaptureError(f"the transcript has no {GFX_HEADER} header")
    if end is None:
        raise CaptureError(f"the transcript has no {END_RECORD} record")

    if set(end.fields) != set(END_FIELDS):
        raise CaptureError(
            f"{END_RECORD} must declare exactly {list(END_FIELDS)}, "
            f"got {sorted(end.fields)}")
    declared = {key: end.integer(key) for key in END_FIELDS}
    computed = _terminal_counts(timeline)
    if declared != computed:
        raise CaptureError(
            f"the {END_RECORD} accounting does not match the timeline: declared "
            f"{declared}, recomputed {computed}")

    capture = {"record_type": "frame-capture", "capture_version": FRAME_CAPTURE_VERSION,
               "subject": f"{GFX_SUBJECT}-{version}", "timeline": timeline}
    _validate_capture(capture)
    return capture


# --- capture validation ---------------------------------------------------------

def _validate_reference(reference: Any, where: str) -> None:
    if not isinstance(reference, dict):
        raise CaptureError(f"{where} must be an object")
    for key in ("slot", "generation"):
        value = reference.get(key)
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            raise CaptureError(f"{where}.{key} must be a non-negative integer")


def _validate_capture(capture: Any) -> None:
    if not isinstance(capture, dict):
        raise CaptureError("a capture must be an object")
    if capture.get("record_type") != "frame-capture":
        raise CaptureError(f"unexpected record type {capture.get('record_type')!r}")
    if capture.get("capture_version") != FRAME_CAPTURE_VERSION:
        raise CaptureError(f"unsupported capture version {capture.get('capture_version')!r}")
    if not isinstance(capture.get("subject"), str) or not capture["subject"]:
        raise CaptureError("a capture must name its subject")
    timeline = capture.get("timeline")
    if not isinstance(timeline, list) or not timeline:
        raise CaptureError("a capture must have a non-empty timeline")
    for index, step in enumerate(timeline):
        where = f"timeline[{index}]"
        if not isinstance(step, dict) or step.get("op") not in OPS:
            raise CaptureError(f"{where} is not a known operation: {step!r}")
        if step.get("result") != 0:
            raise CaptureError(f"{where} recorded a failed operation")
        if step["op"] == "create":
            if step.get("kind") not in (TEXTURE, VERTEX_BUFFER):
                raise CaptureError(f"{where} has an unknown resource kind")
            _validate_reference(step, where)
            if not isinstance(step.get("size"), int) or step["size"] <= 0:
                raise CaptureError(f"{where}.size must be a positive integer")
            contents = step.get("contents_hex")
            if not isinstance(contents, str) or len(contents) != 2 * step["size"]:
                raise CaptureError(f"{where}.contents_hex must describe exactly {step['size']} bytes")
            _hex(contents, f"{where}.contents_hex")
        elif step["op"] == "present":
            _validate_reference(step.get("target"), f"{where}.target")
            digest = step.get("digest")
            if not isinstance(digest, str) or len(digest) != 8:
                raise CaptureError(f"{where}.digest must be 8 hex digits")
            _hex(digest, f"{where}.digest")
            if not isinstance(step.get("bytes_hex"), str):
                raise CaptureError(f"{where}.bytes_hex must be a string")
            _hex(step["bytes_hex"], f"{where}.bytes_hex")
        else:
            _validate_reference(step.get("target"), f"{where}.target")
            if step["op"] == "destroy":
                continue
            if not isinstance(step.get("offset"), int) or step["offset"] < 0:
                raise CaptureError(f"{where}.offset must be a non-negative integer")
            if step["op"] == "fill":
                for key in ("span", "pattern"):
                    if not isinstance(step.get(key), int) or step[key] < 0:
                        raise CaptureError(f"{where}.{key} must be a non-negative integer")
            if step["op"] in ("blit", "mix"):
                _validate_reference(step.get("source"), f"{where}.source")
            if step["op"] == "mix" and (not isinstance(step.get("rounds"), int)
                                        or step["rounds"] < 0):
                raise CaptureError(f"{where}.rounds must be a non-negative integer")


# --- sealing --------------------------------------------------------------------

def seal(capture: dict[str, Any]) -> dict[str, Any]:
    """Wrap a validated capture with its content digest."""
    _validate_capture(capture)
    return {"schema_version": FRAME_SEAL_VERSION,
            "capture_sha256": sha256(canonical_bytes(capture)).hexdigest(),
            "payload": capture}


def write_sealed(path: Path, sealed: dict[str, Any]) -> str:
    """Publish a sealed capture, refusing to overwrite a different one."""
    _validate_seal(sealed)
    return write_create_only(Path(path), canonical_bytes(sealed))


def _validate_seal(sealed: Any) -> None:
    if not isinstance(sealed, dict):
        raise CaptureError("a sealed capture must be an object")
    if sealed.get("schema_version") != FRAME_SEAL_VERSION:
        raise CaptureError(f"unsupported seal schema {sealed.get('schema_version')!r}")
    digest = sealed.get("capture_sha256")
    if not isinstance(digest, str) or len(digest) != 64:
        raise CaptureError("a sealed capture must carry a content digest")
    payload = sealed.get("payload")
    _validate_capture(payload)
    if sha256(canonical_bytes(payload)).hexdigest() != digest:
        raise CaptureError("the sealed capture does not match its digest")


def read_sealed(path: Path) -> dict[str, Any]:
    """Load a sealed capture, verifying its canonical form and its digest."""
    data = Path(path).read_bytes()
    try:
        sealed = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise CaptureError(f"cannot read {path}: {exc}") from exc
    _validate_seal(sealed)
    if data != canonical_bytes(sealed):
        raise CaptureError("the sealed capture is not in canonical form")
    return sealed


# --- the replay -----------------------------------------------------------------

class _Slot:
    """A reconstructed resource: its lifetime, its kind, and its live bytes."""

    def __init__(self, kind: str, size: int, contents: bytes):
        self.kind = kind
        self.size = size
        self.data = bytearray(contents)
        self.live = True


def _resolve(slots: dict[int, dict[str, Any]], reference: dict[str, int],
             index: int, what: str) -> dict[str, Any]:
    slot = reference["slot"]
    generation = reference["generation"]
    entry = slots.get(slot)
    if entry is None or not entry["live"] or entry["generation"] != generation:
        raise IncompleteOracle(
            f"the {what} at timeline index {index} references "
            f"{registry.lifetime_name(slot, generation)}, which the capture does not hold")
    return entry


def _require_kind(entry: dict[str, Any], kind: str, index: int, what: str) -> None:
    if entry["kind"] != kind:
        raise CaptureError(
            f"the {what} at timeline index {index} needs a {kind}, "
            f"but the reference is a {entry['kind']}")


def replay(capture: dict[str, Any]) -> dict[str, Any]:
    """Reconstruct the frame a capture describes, or fail closed.

    Accepts a raw capture or a sealed wrapper. A wrapper's digest is verified here, so
    a caller that bypasses :func:`read_sealed` does not bypass the seal either.

    Returns ``{"frame": {"digest", "bytes_hex"}, "lifetimes": [...], "ops": n}``. Every
    presentation is checked against the capture as it happens - bytes and digest alike,
    not just the last one - because a plausible but different frame is a failure, not
    a pass.
    """
    if not isinstance(capture, dict):
        raise CaptureError("a capture must be an object")
    if "payload" in capture:
        _validate_seal(capture)
        payload = capture["payload"]
    else:
        payload = capture
    _validate_capture(payload)

    slots: dict[int, dict[str, Any]] = {}
    created: set[str] = set()
    operations = 0
    presented: dict[str, str] | None = None

    for index, step in enumerate(payload["timeline"]):
        op = step["op"]
        if op == "create":
            slot = step["slot"]
            generation = step["generation"]
            contents = bytes.fromhex(step["contents_hex"])
            previous = slots.get(slot)
            if previous is not None and previous["live"]:
                raise CaptureError(f"timeline index {index} recreates a live slot {slot}")
            if previous is not None and generation <= previous["generation"]:
                raise CaptureError(
                    f"timeline index {index} reuses slot {slot} without a new generation")
            slots[slot] = {"live": True, "generation": generation, "kind": step["kind"],
                           "size": step["size"], "data": bytearray(contents)}
            created.add(registry.lifetime_name(slot, generation))
        elif op == "destroy":
            entry = _resolve(slots, step["target"], index, "destroy")
            entry["live"] = False
        elif op == "fill":
            entry = _resolve(slots, step["target"], index, "fill")
            _require_kind(entry, TEXTURE, index, "fill")
            span = step["span"]
            offset = step["offset"]
            if span == 0 or offset + span > entry["size"]:
                raise CaptureError(f"the fill at timeline index {index} writes outside the target")
            for i in range(span):
                entry["data"][offset + i] = (step["pattern"] + i) & 0xFF
            operations += 1
        elif op == "blit":
            target = _resolve(slots, step["target"], index, "blit")
            source = _resolve(slots, step["source"], index, "blit")
            _require_kind(target, TEXTURE, index, "blit target")
            _require_kind(source, VERTEX_BUFFER, index, "blit source")
            offset = step["offset"]
            if offset > target["size"]:
                raise CaptureError(f"the blit at timeline index {index} starts outside the target")
            count = min(source["size"], target["size"] - offset)
            for i in range(count):
                target["data"][offset + i] ^= source["data"][i]
            operations += 1
        elif op == "mix":
            target = _resolve(slots, step["target"], index, "mix")
            source = _resolve(slots, step["source"], index, "mix")
            _require_kind(target, TEXTURE, index, "mix target")
            _require_kind(source, TEXTURE, index, "mix source")
            offset = step["offset"]
            if offset > target["size"]:
                raise CaptureError(f"the mix at timeline index {index} starts outside the target")
            count = min(source["size"], target["size"] - offset)
            for round_index in range(step["rounds"]):
                for i in range(count):
                    target["data"][offset + i] = (target["data"][offset + i]
                                                  + source["data"][i]
                                                  + round_index) & 0xFF
            operations += 1
        else:  # present
            target = _resolve(slots, step["target"], index, "present")
            _require_kind(target, TEXTURE, index, "present")
            frame = bytes(target["data"][:target["size"]])
            if frame.hex() != step["bytes_hex"]:
                raise CaptureError(
                    f"the frame presented at timeline index {index} differs from the capture")
            digest = f"{fnv1a32(frame):08x}"
            if digest != step["digest"]:
                raise CaptureError(
                    f"the frame presented at timeline index {index} digests to {digest}, "
                    f"but the capture recorded {step['digest']}")
            presented = {"digest": digest, "bytes_hex": frame.hex()}
            operations += 1

    if presented is None:
        raise CaptureError("the capture presents no frame")

    return {"frame": presented, "lifetimes": sorted(created), "ops": operations}
