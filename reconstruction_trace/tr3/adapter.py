"""Translate one qualified `_GfxSetConstantBuffer` fixture into an RE1 scenario request.

The request is `scenario <slot> <selector-hex> <first-dword-hex|absent>` plus
the 0x130-byte prestate. This module does not run the harness. A
`handwritten-synthetic` trace with `binary: null` is a test double.
`fixture.qualify` does not authenticate the pinned function, the stored
pointer's role, or the observed `RAX`.
"""
from __future__ import annotations

import json
from pathlib import Path

from reconstruction_trace import fixture

ROOT = Path(__file__).resolve().parents[2]
TARGET = ROOT / "reverse_engineering" / "target.json"
CONTEXT_SIZE = 0x130
WORD_BASE = 0xc8
POINTER_BASE = 0xd0
DIRTY_OFFSET = 0x128
SLOT_STRIDE = 0x10
SELECTOR_READ = 0xffffffff
FUNCTION_ID = "_GfxSetConstantBuffer"


def _pin() -> tuple[dict, dict]:
    target = json.loads(TARGET.read_text())
    return target, target["code_windows"][FUNCTION_ID]


def _no(reason: str) -> dict:
    return {"refusal": reason, "scenario": None}


def _identity(trace: dict) -> str | None:
    if trace["evidence"] == "handwritten-synthetic":
        if trace["binary"] is not None:
            return "unauthenticated-target"
        return None
    if trace["evidence"] != "qbdi-rosetta" or not isinstance(trace["binary"], dict):
        return "unauthenticated-target"
    target, window = _pin()
    binary = trace["binary"]
    if (trace["function_id"] != FUNCTION_ID
            or binary["sha256"] != target["sha256"]
            or binary["entry_rva"] != window["entry_rva"]
            or int(binary["image_base"], 16) != target["image_base"]):
        return "unauthenticated-target"
    return None


def _region(regions: list[dict], region_id: str) -> dict:
    return next(region for region in regions if region["id"] == region_id)


def _register_edge(pointers: list[dict], name: str) -> dict | None:
    return next((edge for edge in pointers if edge["from"].get("register") == name), None)


def _stored_edge(pointers: list[dict], region_id: str, offset: int) -> dict | None:
    return next((edge for edge in pointers
                 if edge["from"].get("region") == region_id
                 and edge["from"].get("offset") == offset), None)


def _span(region: dict) -> tuple[int, int]:
    base = int(region["base"], 16)
    return base, base + region["size"]


def _overlaps(left: tuple[int, int], right: tuple[int, int]) -> bool:
    return left[0] < right[1] and right[0] < left[1]


def _covered(rows: list[dict]) -> dict[tuple[str, int], int] | None:
    covered: dict[tuple[str, int], int] = {}
    for row in rows:
        data = bytes.fromhex(row["bytes"])
        if len(data) != row["size"]:
            return None
        for index, byte in enumerate(data):
            key = (row["region"], row["offset"] + index)
            if key in covered and covered[key] != byte:
                return None
            covered[key] = byte
    return covered


def _slice(covered: dict[tuple[str, int], int], region_id: str, offset: int, size: int) -> bytes:
    return bytes(covered[(region_id, offset + index)] for index in range(size))


def words_from_selector(selector: int) -> int:
    """Truncating signed division of the wrapped `(selector + 3)` by four."""
    signed = int.from_bytes(((selector + 3) & 0xffffffff).to_bytes(4, "little"),
                            "little", signed=True)
    magnitude = abs(signed) // 4
    return (-magnitude if signed < 0 else magnitude) & 0xffffffff


def words_from_buffer(first_dword: int) -> int:
    return ((first_dword + 3) & 0xffffffff) >> 2


def prepare_scenario(trace: dict) -> dict:
    """Return an RE1 scenario request, or a refusal. Does not launch the harness."""
    reason = _identity(trace)
    if reason:
        return _no(reason)
    qualified = fixture.qualify(trace)
    if not qualified["fixture_qualified"]:
        return _no("unqualified")
    prepared = qualified["fixture"]
    pointers = prepared["pointers"]
    rdi = _register_edge(pointers, "rdi")
    if rdi is None:
        return _no("unauthenticated-context")
    stored = _stored_edge(pointers, rdi["to"]["region"], rdi["to"]["offset"])
    if stored is None:
        return _no("unauthenticated-context")
    context = _region(prepared["regions"], stored["to"]["region"])
    if stored["to"]["offset"] != 0:
        return _no("incomplete-prestate")
    if (context["size"] != CONTEXT_SIZE
            or context.get("preservation", "required") != "required"
            or "bytes" not in context):
        return _no("incomplete-prestate")
    context_base = int(context["base"], 16) + stored["to"]["offset"]
    observed_rax = trace["exit_state"]["registers"].get("rax")
    if observed_rax is None or int(observed_rax, 16) != context_base:
        return _no("unauthenticated-rax")
    # The frozen fixture drops an RSI edge the explicit-length path never reads.
    rsi = _register_edge(pointers, "rsi")
    if rsi is None:
        return _no("unauthenticated-buffer-pointer")
    buffer = _region(prepared["regions"], rsi["to"]["region"])
    if _overlaps(_span(context), _span(buffer)):
        return _no("unsupported-alias")
    registers = trace["entry_state"]["registers"]
    if "rcx" not in registers or "r9" not in registers:
        return _no("unsupported-slot")
    slot = int(registers["rcx"], 16) & 0xffffffff
    if slot >= 6:
        return _no("unsupported-slot")
    selector = int(registers["r9"], 16) & 0xffffffff
    covered = _covered(prepared["effects"])
    dependencies = _covered(prepared["dependencies"])
    if covered is None or dependencies is None:
        return _no("unsupported-effect-domain")
    word_at = WORD_BASE + SLOT_STRIDE * slot
    pointer_at = POINTER_BASE + SLOT_STRIDE * slot
    permitted = {(context["id"], word_at + index) for index in range(4)}
    permitted |= {(context["id"], pointer_at + index) for index in range(8)}
    permitted.add((context["id"], DIRTY_OFFSET))
    if set(covered) != permitted or _slice(covered, context["id"], DIRTY_OFFSET, 1) != b"\x01":
        return _no("unsupported-effect-domain")
    pointer = int.from_bytes(_slice(covered, context["id"], pointer_at, 8), "little")
    buffer_address = int(buffer["base"], 16) + rsi["to"]["offset"]
    if pointer != buffer_address:
        return _no("unsupported-effect-domain")
    if selector == SELECTOR_READ:
        if any((buffer["id"], rsi["to"]["offset"] + index) not in dependencies
               for index in range(4)):
            return _no("unauthenticated-buffer-read")
        first = int.from_bytes(
            _slice(dependencies, buffer["id"], rsi["to"]["offset"], 4), "little")
        expected_words = words_from_buffer(first)
        buffer_arg = format(first, "x")
    else:
        expected_words = words_from_selector(selector)
        buffer_arg = "absent"
    word = int.from_bytes(_slice(covered, context["id"], word_at, 4), "little")
    if word != expected_words:
        return _no("unsupported-effect-domain")
    prestate = bytearray.fromhex(context["bytes"])
    post = bytearray(prestate)
    for (_region_id, offset), byte in covered.items():
        post[offset] = byte
    return {
        "refusal": None,
        "scenario": {
            "command": f"scenario {slot} {format(selector, 'x')} {buffer_arg}",
            "prestate": prestate.hex(),
        },
        "expected_post": post.hex(),
        "pointer_offset": pointer_at,
    }


def compare_scenario(prepared: dict, candidate: dict) -> str | None:
    """Compare one candidate report with the observed poststate.

    The stored pointer is the ``ARG`` role. Its bytes are not compared across
    processes. ``rax`` must be the ``ctx`` role. Every other context byte,
    written or preserved, must match.
    """
    if prepared.get("refusal") or prepared.get("scenario") is None:
        return "refused"
    if candidate.get("rax") != "ctx":
        return "rax"
    if candidate.get("buffer") != "ARG":
        return "buffer"
    expected = bytes.fromhex(prepared["expected_post"])
    observed = bytes.fromhex(candidate["post"])
    if len(expected) != CONTEXT_SIZE or len(observed) != CONTEXT_SIZE:
        return "post-size"
    start = prepared["pointer_offset"]
    for index, (want, got) in enumerate(zip(expected, observed)):
        if start <= index < start + 8:
            continue
        if want != got:
            return f"post-byte {hex(index)}"
    return None
