"""PR C: replay one A2 function fixture through the RE1 reconstruction.

This is an offline bridge, not a second capture path. A ``FUNCTION-CAPTURE v1``
transcript is first materialized and closed by A2; only a qualified observation is
replayed. The captured pre-state is then fed to the existing RE1 ``scenario``
harness and the result is compared with the captured post-state, canaries and
outcome. Absolute addresses remain roles: the captured buffer pointer is checked
against RSI, and RAX against the context base, never across processes.

The candidate is observed over ``[ctx+0xb8, ctx+0x130)``. A2's reference extends to
``ctx+0x139``; the final nine bytes are outside the reconstruction's object and are
not candidate-side evidence. The uncaptured prefix ``[ctx, ctx+0xb8)`` is a fixed
sentinel used only to detect a write, not captured original state, so candidate
reads from it are not observable here.

Verdicts are closed: ``refused`` means the comparison could not legitimately run;
``mismatch`` means a qualified reference was available and the candidate failed;
``match`` means the candidate agreed on the declared candidate-side domain.
"""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path
from typing import Any

from oracle.function import fixture
from reverse_engineering import reconstruction_diff as diff

SUPPORTED_FUNCTION = "gfx:set-constant-buffer"
SUPPORTED_SYMBOL = "_GfxSetConstantBuffer"

#: The RE1 harness's pre-invocation refusals. A nonzero exit carrying one of these is
#: an infrastructure refusal; any other nonzero exit is the candidate failing.
SETUP_ERRORS = frozenset({
    "usage", "slot-out-of-domain", "missing-buffer-input", "bad-hex-input",
    "prestate-size", "bad-buffer-dword", "absent-buffer-unavailable",
})

REQUIRED_FIELDS = frozenset({
    "size", "post", "rax", "word_count", "buffer", "dirty",
    "handle_preserved", "buffer_preserved",
})

CONTEXT_SIZE = diff.CONTEXT_SIZE
_HEX8 = re.compile(r"\A[0-9a-f]{8}\Z")
_HEX16 = re.compile(r"\A[0-9a-f]{16}\Z")
_HEX_POST = re.compile(rf"\A[0-9a-f]{{{2 * CONTEXT_SIZE}}}\Z")


class ReplayError(ValueError):
    """The captured evidence cannot be turned into a replay input."""


def _result(descriptor: dict[str, Any], verdict: str, *,
            record_digest: str | None = None, domain_digest: str | None = None,
            synthetic: bool | None = None,
            failures: list[str] | None = None) -> dict[str, Any]:
    layout = descriptor["layout"]
    return {
        "verdict": verdict,
        "record_digest": record_digest,
        "domain_digest": domain_digest,
        "synthetic": synthetic,
        "failures": list(failures or []),
        "candidate_domain": {"start": layout["canary_before_start"],
                             "end": CONTEXT_SIZE},
        "reference_domain": {"start": layout["canary_before_start"],
                             "end": layout["canary_after_end"]},
    }


def _span(record: dict[str, Any], phase: str, name: str) -> bytes:
    for span in record["state"][phase]:
        if span["space"] == f"heap:{name}":
            return bytes.fromhex(span["value_hex"])
    raise ReplayError(f"captured {phase} region {name!r} is missing")


def _carrier(record: dict[str, Any], name: str) -> int:
    for carrier in record["abi"]["carriers"]:
        if carrier["name"] == name and carrier["confidence"] == "observed":
            return int(carrier["value_hex"], 16)
    raise ReplayError(f"captured carrier {name!r} is missing or unobserved")


def _build_prestate(record: dict[str, Any], descriptor: dict[str, Any]) -> tuple[
        bytes, int, int, int | None, int]:
    """The RE1 0x130 context, plus the inputs that select the scenario path."""
    layout = descriptor["layout"]
    rdi = _carrier(record, "RDI")
    rsi = _carrier(record, "RSI")
    rcx = _carrier(record, "RCX")
    r9 = _carrier(record, "R9")

    slot = rcx & 0xFFFFFFFF
    selector = r9 & 0xFFFFFFFF
    if not (layout["slot_min"] <= slot < layout["slot_max"]):
        raise ReplayError(f"captured slot {slot} is outside the declared domain")

    addresses = record["raw"]["addresses"]
    handle = _span(record, "pre", "handle")
    handle_width = descriptor["input_regions"]["handle"]["width"]
    if len(handle) < handle_width:
        raise ReplayError("captured handle region is too short")
    ctx_base = int.from_bytes(handle[:handle_width], "little")
    if ctx_base != int(addresses["ctx"], 16):
        raise ReplayError("the captured handle does not point at the captured context")
    if rdi != int(addresses["handle"], 16):
        raise ReplayError("captured RDI does not name the captured handle region")
    if addresses.get("buffer") is not None and rsi != int(addresses["buffer"], 16):
        raise ReplayError("captured RSI does not name the captured buffer region")

    pre = bytearray(b"\xcc" * CONTEXT_SIZE)
    canary_before_start = layout["canary_before_start"]
    canary_before_end = layout["canary_before_end"]
    observation_start = layout["observation_start"]
    observation_end = layout["observation_end"]
    canary_after_end = layout["canary_after_end"]

    canary_before = _span(record, "pre", "canary-before")
    observation = _span(record, "pre", "ctx")
    canary_after = _span(record, "pre", "canary-after")
    if len(canary_before) != canary_before_end - canary_before_start:
        raise ReplayError("captured canary-before has the wrong length")
    if len(observation) != observation_end - observation_start:
        raise ReplayError("captured context observation has the wrong length")
    if len(canary_after) != canary_after_end - observation_end:
        raise ReplayError("captured canary-after has the wrong length")

    pre[canary_before_start:canary_before_end] = canary_before
    pre[observation_start:observation_end] = observation
    visible_after = min(canary_after_end, CONTEXT_SIZE) - observation_end
    if visible_after < 0:
        raise ReplayError("the captured canary-after starts beyond the Replay context")
    pre[observation_end:observation_end + visible_after] = canary_after[:visible_after]

    buffer_dword = None
    if selector == descriptor["input_regions"]["buffer"]["selector_value"]:
        buffer = _span(record, "pre", "buffer")
        width = descriptor["input_regions"]["buffer"]["width"]
        if len(buffer) < width:
            raise ReplayError("captured buffer region is too short")
        buffer_dword = int.from_bytes(buffer[:width], "little")

    return bytes(pre), slot, selector, buffer_dword, ctx_base


def _parse_field(fields: dict[str, str], name: str, pattern: re.Pattern[str],
                 failures: list[str]) -> None:
    value = fields[name]
    if not pattern.match(value):
        failures.append(f"candidate:malformed-output: {name} has invalid form {value!r}")


def _parse_candidate(stdout: str, descriptor: dict[str, Any], slot: int) -> tuple[
        dict[str, str] | None, list[str]]:
    """Strictly parse the candidate report, including its internal consistency."""
    failures: list[str] = []
    fields: dict[str, str] = {}
    for line in stdout.splitlines():
        if not line:
            continue
        key, separator, value = line.partition("=")
        if not separator or not key:
            failures.append(f"candidate:malformed-output: not key=value: {line!r}")
            continue
        if key in fields:
            failures.append(f"candidate:malformed-output: duplicate field {key!r}")
            continue
        fields[key] = value

    missing = REQUIRED_FIELDS - set(fields)
    unexpected = set(fields) - REQUIRED_FIELDS
    if missing:
        failures.append(f"candidate:malformed-output: missing {sorted(missing)}")
    if unexpected:
        failures.append(f"candidate:malformed-output: unexpected {sorted(unexpected)}")
    if failures:
        return None, failures

    _parse_field(fields, "size", re.compile(r"\A[0-9a-f]{16}\Z"), failures)
    _parse_field(fields, "post", _HEX_POST, failures)
    _parse_field(fields, "word_count", _HEX8, failures)
    _parse_field(fields, "dirty", _HEX8, failures)
    if fields["rax"] != "ctx":
        _parse_field(fields, "rax", _HEX16, failures)
    if fields["buffer"] != "ARG":
        _parse_field(fields, "buffer", _HEX16, failures)
    if fields["handle_preserved"] not in ("unchanged", "changed"):
        failures.append("candidate:malformed-output: handle_preserved has an invalid role")
    if fields["buffer_preserved"] not in ("unchanged", "changed", "absent"):
        failures.append("candidate:malformed-output: buffer_preserved has an invalid role")
    if failures:
        return None, failures

    if int(fields["size"], 16) != CONTEXT_SIZE:
        failures.append(f"candidate:malformed-output: size is {fields['size']}, "
                        f"expected {CONTEXT_SIZE:016x}")
    post = bytes.fromhex(fields["post"])
    if len(post) != CONTEXT_SIZE:
        failures.append("candidate:malformed-output: post is not the declared context size")
        return None, failures

    layout = descriptor["layout"]
    word_offset = layout["word_count_base"] + slot * layout["slot_stride"]
    word_count = int(fields["word_count"], 16)
    if word_count != int.from_bytes(post[word_offset:word_offset + 4], "little"):
        failures.append("candidate:malformed-output: word_count disagrees with post")
    dirty = int(fields["dirty"], 16)
    if dirty != post[layout["dirty"]]:
        failures.append("candidate:malformed-output: dirty disagrees with post")
    return (None, failures) if failures else (fields, [])


def _reference_context(record: dict[str, Any], descriptor: dict[str, Any],
                       prestate: bytes) -> bytes:
    """The captured post-state overlaid on the replay pre-state.

    The uncaptured prefix keeps the sentinel. Every captured byte is replaced by
    the A2 post-state, including the whole canary-before and the visible part of
    canary-after.
    """
    layout = descriptor["layout"]
    reference = bytearray(prestate)
    post_canary_before = _span(record, "post", "canary-before")
    post_observation = _span(record, "post", "ctx")
    post_canary_after = _span(record, "post", "canary-after")
    observation_start = layout["observation_start"]
    observation_end = layout["observation_end"]
    canary_before_start = layout["canary_before_start"]
    canary_before_end = layout["canary_before_end"]
    visible_after = min(layout["canary_after_end"], CONTEXT_SIZE) - observation_end

    if len(post_canary_before) != canary_before_end - canary_before_start:
        raise ReplayError("captured post canary-before has the wrong length")
    if len(post_observation) != observation_end - observation_start:
        raise ReplayError("captured post context observation has the wrong length")
    if len(post_canary_after) < visible_after:
        raise ReplayError("captured post canary-after is too short")

    reference[canary_before_start:canary_before_end] = post_canary_before
    reference[observation_start:observation_end] = post_observation
    reference[observation_end:observation_end + visible_after] = \
        post_canary_after[:visible_after]
    return bytes(reference)


def compare_replay(record: dict[str, Any], descriptor: dict[str, Any],
                   prestate: bytes, fields: dict[str, str], slot: int,
                   selector: int, ctx_base: int) -> list[str]:
    """Compare one strict candidate report with the A2 reference."""
    failures: list[str] = []
    layout = descriptor["layout"]
    post = bytes.fromhex(fields["post"])
    reference = _reference_context(record, descriptor, prestate)
    word_offset = layout["word_count_base"] + slot * layout["slot_stride"]
    word_end = word_offset + descriptor["post_state"]["word_count"]["width"]
    pointer_offset = layout["pointer_base"] + slot * layout["slot_stride"]
    pointer_end = pointer_offset + descriptor["post_state"]["buffer"]["width"]

    # Every observable byte is checked, not only the three declared fields. The stored
    # pointer is the one exception: it is a host address and is validated by its role.
    for offset in range(CONTEXT_SIZE):
        if pointer_offset <= offset < pointer_end:
            continue
        if post[offset] == reference[offset]:
            continue
        if word_offset <= offset < word_end:
            name = "word_count"
        elif offset == layout["dirty"]:
            name = "dirty"
        else:
            name = f"ctx+{offset:#x}"
        failures.append(
            f"candidate:context-mismatch: {name} is "
            f"{post[offset]:#04x}, reference {reference[offset]:#04x}")

    captured_pointer = int.from_bytes(
        reference[pointer_offset:pointer_end], "little")
    if captured_pointer != _carrier(record, descriptor["input_regions"]["buffer"]["carrier"]):
        failures.append("reference:buffer-pointer-role: captured pointer is not RSI")
    if fields["buffer"] != "ARG":
        failures.append("candidate:buffer-role: the reconstruction did not store its buffer "
                        "argument")

    expected_buffer = "unchanged" if selector == descriptor[
        "input_regions"]["buffer"]["selector_value"] else "absent"
    if fields["buffer_preserved"] != expected_buffer:
        failures.append(
            f"candidate:buffer-preservation: expected {expected_buffer}, "
            f"got {fields['buffer_preserved']}")
    if fields["handle_preserved"] != "unchanged":
        failures.append("candidate:handle-preservation: the reconstruction changed *RDI")

    outcome = record["raw"].get("outcome")
    if not isinstance(outcome, dict) or outcome.get("register") != "RAX" \
            or outcome.get("confidence") != "observed" \
            or int(outcome["value_hex"], 16) != ctx_base:
        failures.append("reference:outcome-role: captured RAX is not the context base")
    if fields["rax"] != "ctx":
        failures.append("candidate:rax-role: the reconstruction did not return the context")
    return failures


def _setup_error(recon: dict[str, str]) -> str | None:
    """Recognise the harness's closed setup refusal, not candidate mimicry.

    The harness prints exactly one ``error=...`` line and exits 2 before invoking the
    candidate. Anything else on stdout/stderr, or any other status, is candidate output.
    """
    if recon.get("returncode") != "2" or recon.get("stderr", "").strip():
        return None
    lines = recon.get("stdout", "").splitlines()
    if len(lines) != 1:
        return None
    key, separator, value = lines[0].partition("=")
    if separator and key == "error" and value in SETUP_ERRORS:
        return value
    return None


def _timeout_output(error: subprocess.TimeoutExpired) -> str:
    for attribute in ("stdout", "output", "stderr"):
        value = getattr(error, attribute, None)
        if value:
            return value.strip() if isinstance(value, str) else str(value)
    return ""


def replay_capture(text: str, harness: str | Path,
                   env: dict[str, str] | None = None) -> dict[str, Any]:
    """Replay one A2 transcript through the RE1 `scenario` harness."""
    descriptor = fixture.load_descriptor()
    try:
        materialized = fixture.materialize_capture(text, descriptor)
    except fixture.FixtureError as error:
        return _result(descriptor, "refused", failures=[f"reference:unqualified: {error}"])

    record = materialized["record"]
    manifest = materialized["manifest"]
    synthetic = record["raw"]["synthetic"]
    common = {"record_digest": manifest["record_digest"],
              "domain_digest": manifest["domain_digest"], "synthetic": synthetic}

    if descriptor["function_id"] != SUPPORTED_FUNCTION \
            or descriptor["symbol"] != SUPPORTED_SYMBOL:
        return _result(descriptor, "refused", **common,
                       failures=["reference:unsupported-function: PR C has no replay "
                                 "adapter for this descriptor"])
    if manifest["verdict"] != "qualified":
        missing = record["completeness"]["unavailable_dependencies"]
        return _result(descriptor, "refused", **common,
                       failures=[f"reference:unqualified: missing {missing}"])

    try:
        prestate, slot, selector, buffer_dword, ctx_base = _build_prestate(
            record, descriptor)
    except ReplayError as error:
        return _result(descriptor, "refused", **common,
                       failures=[f"reference:unqualified: {error}"])

    try:
        recon = diff.run_harness(Path(harness), slot, selector, buffer_dword,
                                 prestate, env)
    except subprocess.TimeoutExpired as error:
        details = _timeout_output(error)
        return _result(descriptor, "mismatch", **common,
                       failures=["candidate:timeout: the reconstruction did not finish"
                                 + (f" ({details})" if details else "")])
    except OSError as error:
        return _result(descriptor, "refused", **common,
                       failures=[f"infrastructure:harness-unavailable: {error}"])
    except UnicodeError as error:
        return _result(descriptor, "mismatch", **common,
                       failures=[f"candidate:malformed-output: {error}"])

    if recon["returncode"] != "0":
        setup = _setup_error(recon)
        if setup is not None:
            return _result(descriptor, "refused", **common,
                           failures=[f"infrastructure:{setup}"])
        detail = recon.get("stderr", "")
        return _result(descriptor, "mismatch", **common,
                       failures=[f"candidate:nonzero-exit: status {recon['returncode']}"
                                 + (f": {detail}" if detail else "")])

    fields, protocol_failures = _parse_candidate(
        recon.get("stdout", ""), descriptor, slot)
    if fields is None:
        return _result(descriptor, "mismatch", **common, failures=protocol_failures)

    try:
        failures = compare_replay(record, descriptor, prestate, fields, slot, selector,
                                  ctx_base)
    except ReplayError as error:
        return _result(descriptor, "refused", **common,
                       failures=[f"reference:unqualified: {error}"])
    if failures:
        return _result(descriptor, "mismatch", **common, failures=failures)
    return _result(descriptor, "match", **common)


def main(argv: list[str] | None = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    if len(arguments) != 2:
        print("usage: python -m oracle.function.replay <capture> <re1-harness>",
              file=sys.stderr)
        return 2
    try:
        text = Path(arguments[0]).read_text(encoding="utf-8")
    except (OSError, UnicodeError) as error:
        print(f"error={error}", file=sys.stderr)
        return 2
    result = replay_capture(text, arguments[1])
    synthetic = result["synthetic"]
    label = "unknown" if synthetic is None else str(int(synthetic))
    domain = result["candidate_domain"]
    print(f"verdict={result['verdict']} synthetic={label} "
          f"candidate_domain=ctx+{domain['start']:#x}..ctx+{domain['end']:#x}")
    for failure in result["failures"]:
        print(f"failure={failure}")
    return {"match": 0, "mismatch": 1, "refused": 2}[result["verdict"]]


if __name__ == "__main__":
    raise SystemExit(main())
