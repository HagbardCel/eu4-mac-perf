"""RE2 differential comparison: the RE1 reconstruction against the P-code reference.

`_GfxSetConstantBuffer` exists twice here. RE1 reconstructs it in C++ from the documented
contract (`analysis/reconstruction/_GfxSetConstantBuffer/reconstruction/`); RE0 executes the
original pinned instructions in Ghidra's P-code emulator (`reverse_engineering/
reconstruction_pilot.py`). This module reconciles the two on the differential corpus in
`analysis/reconstruction/_GfxSetConstantBuffer/differential/`:

* it drives the compiled reconstruction through its `scenario` harness mode, one corpus row at a
  time, and
* it compares the result with the emulator's recorded observation of the original for that row,

for the context bytes both sides describe, and as a *role* where the value cannot be one: the
emulator stores the constructed buffer address, the reconstruction a host pointer. Each side's role
is validated independently, so a nonzero-but-foreign pointer is refused on either side.

The corpus freezes **inputs, not reference outputs**. Nothing here compares the reconstruction
with a hand-written expectation, and nothing derives an expectation from the reconstruction: the
reference is `differential/reference.json`, the emulator's own record, bound to the corpus digest
by `canonical.scenarios_sha256`. That is why this qualification mode differs from the RE0 pilot's:
there, expectations were hand-derived and frozen before execution; here, the reference is the
original's executed behaviour and the comparison itself is the evidence.

Because the comparison is the evidence, the reference is qualified first and structurally: that it
came from this corpus, the pinned binary and the pinned executable, that it is the same on a repeat
run, that every row began from the declared constructed state (canaries included), executed the
frozen instruction path, touched only the declared memory, and wrote exactly the declared values -
with the word count read from the execution rather than from a second arithmetic model - and that the
instruction bytes it records are the pinned binary's own bytes. `compare_corpus` refuses to compare
anything until that holds, so a verdict cannot come from a record this corpus cannot place.

Strictly offline: nothing launches, attaches to or reads the game.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
from pathlib import Path
from typing import Any, Callable

from reverse_engineering import paths
from reverse_engineering import reconstruction_pilot as pilot

FUNCTION_DIR = pilot.FUNCTION_DIR
DIFFERENTIAL_DIR = FUNCTION_DIR / "differential"
SCENARIOS_PATH = DIFFERENTIAL_DIR / "scenarios.json"
REFERENCE_PATH = DIFFERENTIAL_DIR / "reference.json"

CORPUS_SCHEMA = "eu4-re2-differential-scenarios/v1"
REFERENCE_SCHEMA = "eu4-re2-differential-receipt/v1"

# What the reference is, so it cannot be read as more than it is. It is part of the artifact.
STATEMENT = (
    "Ghidra P-code execution of the original pinned x86-64 instructions under constructed state, "
    "reconciled with the RE1 C++ reconstruction on the same frozen inputs - not hardware-level "
    "execution of x86 machine code, and not runtime behaviour in the game."
)

#: `R9D == -1` selects the path that reads the byte length from the buffer itself.
BUFFER_MARKER = 0xFFFFFFFF

#: The reconstruction's context object. The emulator constructs 0x140 bytes; only the shared
#: prefix is comparable, and only from the corpus's observation window onward.
CONTEXT_SIZE = 0x130
HARNESS_TIMEOUT = 60


def sanitizer_env(environ: dict[str, str] | None = None) -> dict[str, str]:
    """The reconstruction subprocess environment: ASan's exit code made authoritative.

    The fatal compile flag covers UBSan; an ambient `ASAN_OPTIONS` could still soften ASan's
    default, and the reconstruction is built with sanitizers, so its runs are given the same
    environment the RE1 gate enforces for its own harness runs: the ambient options with
    `halt_on_error=1` appended, so unrelated settings survive.
    """
    env = dict(os.environ if environ is None else environ)
    previous = env.get("ASAN_OPTIONS", "")
    env["ASAN_OPTIONS"] = (f"{previous}:" if previous else "") + "halt_on_error=1"
    return env


def frozen_structure() -> dict[str, Any]:
    """RE0's frozen corpus, read for the execution structure it owns.

    The path walks, the load shapes and the store list are properties of the pinned binary and the
    constructed state, and RE0 qualified them; this corpus declares the same constructed state (a
    test asserts it), so the addresses RE0 derives are the addresses this corpus constructs. What is
    *not* reused is anything per-input that this corpus deliberately does not freeze: the word count
    and the non-RAX caller-saved register outcomes.
    """
    return pilot.load_scenarios()


def inventory_by_rva() -> dict[int, tuple[int, str]]:
    """The reviewed listing: `rva -> (length, decoded text)`.

    RE0's inventory, which the repository checks against the pinned binary's own bytes where the
    local corpus is present. Used here as the independent statement of where each instruction
    starts and how long it is.
    """
    inventory = json.loads(pilot.INVENTORY_PATH.read_text())
    return {pilot.hexint(item["rva"]): (item["length"], item["operands"])
            for item in inventory["instructions"]}


def _declared_stores(row: dict[str, Any], corpus: dict[str, Any],
                     reference: dict[str, Any]) -> list[tuple[str, int, int, str]]:
    """`(space, address, width, value role)` of the original's stores, in its frozen order."""
    layout = corpus["layout"]
    stride = layout["slot_stride"]
    slot = row["slot"] * stride
    ctx = pilot.hexint(corpus["memory"]["ctx"])
    rsp0 = pilot.hexint(corpus["memory"]["rsp0"])
    addresses = {
        "stack_minus_8": rsp0 - 8,
        "ctx_words": ctx + pilot.hexint(layout["word_count_base"]) + slot,
        "ctx_pointer": ctx + pilot.hexint(layout["pointer_base"]) + slot,
        "ctx_dirty": ctx + pilot.hexint(layout["dirty"]),
    }
    return [(pilot.DATA_SPACE, addresses[item["base"]], item["width"], item["value"])
            for item in reference["expected_stores"]]


def load_corpus(path: Path | None = None) -> dict[str, Any]:
    return json.loads((path or SCENARIOS_PATH).read_text())


def corpus_sha256(path: Path | None = None) -> str:
    return hashlib.sha256((path or SCENARIOS_PATH).read_bytes()).hexdigest()


def load_reference(path: Path | None = None) -> dict[str, Any]:
    return json.loads((path or REFERENCE_PATH).read_text())


def path_label(r9d: int) -> str:
    """The path an input selects, derived from the input - never read from the corpus row."""
    return "length_from_buffer" if r9d == BUFFER_MARKER else "explicit_length"


def declared_fields(corpus: dict[str, Any], slot: int) -> dict[str, tuple[int, int]]:
    """`(offset from the context base, width)` of each write the contract declares."""
    layout = corpus["layout"]
    stride = layout["slot_stride"]
    return {
        "word_count": (pilot.hexint(layout["word_count_base"]) + slot * stride, 4),
        "buffer": (pilot.hexint(layout["pointer_base"]) + slot * stride, 8),
        "dirty": (pilot.hexint(layout["dirty"]), 1),
    }


def common_window(corpus: dict[str, Any]) -> tuple[int, int]:
    """The context offsets both sides describe: the observation window, clipped to 0x130."""
    layout = corpus["layout"]
    start = pilot.hexint(layout["canary_before_start"])
    return start, min(pilot.hexint(layout["canary_after_end"]), CONTEXT_SIZE)


def prestate_bytes(corpus: dict[str, Any]) -> bytes:
    """The reconstruction's initial context: the corpus's own declared fill pattern.

    The emulator constructs the same bytes from the same declaration, so the two sides start
    from an identical pre-state - which is what makes a byte-level reconciliation meaningful.
    """
    return pilot.fill_pattern(corpus["memory"], CONTEXT_SIZE)


def run_reconstruction(harness: Path, row: dict[str, Any], corpus: dict[str, Any],
                       env: dict[str, str] | None = None) -> dict[str, str]:
    """One corpus row through the compiled reconstruction's `scenario` harness mode."""
    dword = row["buffer_first_dword"]
    argument = "absent" if dword is None else f"{pilot.hexint(dword):08x}"
    command = [str(harness), "scenario", str(row["slot"]), f"{pilot.hexint(row['r9d']):x}",
               argument]
    result = subprocess.run(command, input=prestate_bytes(corpus).hex() + "\n",
                            capture_output=True, text=True, timeout=HARNESS_TIMEOUT,
                            env=sanitizer_env() if env is None else env)
    fields: dict[str, str] = {}
    for line in result.stdout.splitlines():
        key, _, value = line.partition("=")
        fields[key] = value
    fields["returncode"] = str(result.returncode)
    fields["stderr"] = result.stderr.strip()
    return fields


def _store_set(oracle: dict[str, Any]) -> set[tuple[int, int]]:
    """`(address, width)` of every byte range the recorded original wrote to the data space."""
    return {(pilot.hexint(row[1]), int(row[2])) for row in oracle.get("stores", [])
            if row[0] == pilot.DATA_SPACE}


def compare_row(row: dict[str, Any], oracle: dict[str, Any], recon: dict[str, str],
                corpus: dict[str, Any]) -> list[str]:
    """Reconcile one row's recorded original with the reconstruction's own report.

    A per-row unit, not the gate: it is given a single recorded row and cannot qualify the record it
    came from (the instruction bytes, the corpus binding and the determinism of the whole record are
    corpus-level facts). `compare_corpus` qualifies the reference first and is the entry point that
    produces a verdict.

    Fails closed: a row whose recorded original is not well formed is never compared, because
    agreement with a broken reference would be evidence of nothing.
    """
    tag = row["id"]
    failures: list[str] = []

    def check(condition: bool, message: str) -> None:
        if not condition:
            failures.append(f"{tag}: {message}")

    check(oracle.get("qualified") is True,
          f"the recorded original is not qualified: {oracle.get('reasons')}")
    check(oracle.get("uninitialized_reads") == [],
          f"the recorded original read uninitialized constructed state: "
          f"{oracle.get('uninitialized_reads')}")
    check(recon.get("returncode") == "0",
          f"the reconstruction exited {recon.get('returncode')}: {recon.get('stderr')}")
    check("error" not in recon, f"the reconstruction refused the row: {recon.get('error')}")
    if failures:
        return failures

    check(recon.get("size") == f"{CONTEXT_SIZE:016x}",
          f"the reconstruction reported context size {recon.get('size')}")

    start, end = common_window(corpus)
    length = end - start
    ctx = pilot.hexint(corpus["memory"]["ctx"])
    window_base = pilot.hexint(oracle.get("ctx_window_base", "0x0"))
    check(window_base == ctx + start,
          f"the recorded original's window starts at {window_base:#x}, the corpus declares "
          f"{ctx + start:#x}")
    oracle_window_before = bytes.fromhex(oracle.get("ctx_window_before", ""))
    oracle_window_after = bytes.fromhex(oracle.get("ctx_window_after", ""))
    post = bytes.fromhex(recon.get("post", ""))
    # The window covers the canary bands too, so it may be longer than the compared region; it may
    # not be shorter, which would silently shrink the comparison.
    if (len(oracle_window_before) < length or len(oracle_window_after) < length
            or len(post) != CONTEXT_SIZE):
        check(False, "one side's context window is the wrong length")
        return failures
    oracle_before = oracle_window_before[:length]
    oracle_after = oracle_window_after[:length]
    recon_before = prestate_bytes(corpus)[start:end]
    recon_after = post[start:end]

    # The initial context is a frozen input, not an observation, so it is qualified rather than
    # compared: a record of a run that began from anything else is not a record of this corpus, and
    # agreement after it would be agreement about different memory.
    check(oracle_before == recon_before,
          f"the recorded original began from a different pre-state than the declared constructed "
          f"state ({sum(a != b for a, b in zip(oracle_before, recon_before))} of {length} bytes "
          f"differ)")

    fields = declared_fields(corpus, row["slot"])
    declared: set[int] = set()
    for offset, width in fields.values():
        declared.update(range(offset - start, offset - start + width))
    pointer = set(range(fields["buffer"][0] - start, fields["buffer"][0] - start + 8))

    # Containment: neither side may write outside the three declared fields.
    changed: dict[str, set[int]] = {}
    for label, before, after in (("original", oracle_before, oracle_after),
                                 ("reconstruction", recon_before, recon_after)):
        changed[label] = {index for index in range(length) if before[index] != after[index]}
        check(changed[label] <= declared,
              f"the {label} changed bytes outside the declared fields: "
              f"{sorted(changed[label] - declared)}")

    # Beyond the pointer field the two sides start from the same pre-state and must write the
    # same bytes, so their traces have to agree exactly - a coincidental no-op (a written byte
    # equal to its seed) hides from both in the same way.
    check(changed["original"] - pointer == changed["reconstruction"] - pointer,
          "the two sides changed different bytes outside the pointer field: "
          f"original {sorted(changed['original'] - pointer)}, "
          f"reconstruction {sorted(changed['reconstruction'] - pointer)}")

    # Byte-exact reconciliation of the declared fields; the pointer is compared as a role, but each
    # side's role is checked independently against the value that role means.
    for name, (offset, width) in fields.items():
        window = slice(offset - start, offset - start + width)
        if name == "buffer":
            recorded = int.from_bytes(oracle_after[window], "little")
            declared_pointer = pilot.hexint(corpus["memory"]["buffer"])
            check(recorded == declared_pointer,
                  f"the original stored {recorded:#x}, not the constructed buffer address "
                  f"{declared_pointer:#x}")
            check(recon.get("buffer") == "ARG",
                  f"the reconstruction stored {recon.get('buffer')!r}, not its buffer argument")
            continue
        check(oracle_after[window] == recon_after[window],
              f"the {name} differs: original "
              f"{int.from_bytes(oracle_after[window], 'little'):#x}, reconstruction "
              f"{int.from_bytes(recon_after[window], 'little'):#x}")

    # The reconstruction's own role fields must describe the bytes it actually reported.
    words = slice(fields["word_count"][0] - start, fields["word_count"][0] - start + 4)
    dirty = fields["dirty"][0] - start
    check(int(recon.get("word_count", "0"), 16) == int.from_bytes(recon_after[words], "little"),
          f"the reconstruction reports word_count={recon.get('word_count')} but wrote "
          f"{int.from_bytes(recon_after[words], 'little'):#x}")
    check(int(recon.get("dirty", "0"), 16) == recon_after[dirty],
          f"the reconstruction reports dirty={recon.get('dirty')} but wrote "
          f"{recon_after[dirty]:#04x}")

    # The recorded original's store trace must contain the three declared writes.
    stored = _store_set(oracle)
    for name, (offset, width) in fields.items():
        check((ctx + offset, width) in stored,
              f"the recorded original has no {name} store at ctx+{offset:#x}")

    # RAX: the context base, on both sides (a recorded outcome, not a declared return type).
    check(recon.get("rax") == "ctx", "the reconstruction did not leave the context base in RAX")
    check(pilot.hexint(oracle.get("final_registers", {}).get("RAX", "0xdeadbeef")) == ctx,
          "the recorded original did not leave the context base in RAX")
    return failures


def compare_corpus(harness: Path, corpus: dict[str, Any] | None = None,
                   reference: dict[str, Any] | None = None) -> list[str]:
    """Reconcile every frozen corpus row; returns one message per disagreement.

    The reference is qualified first, and a reference that fails qualification is returned as the
    result without a single reconstruction subprocess being launched: comparing a reconstruction
    against a record this corpus cannot place is agreement about nothing, so the comparison is
    not allowed to produce a verdict from one.
    """
    corpus = corpus or load_corpus()
    reference = reference or load_reference()
    failures = validate_reference(reference, corpus)
    if failures:
        return failures
    recorded = {result.get("id"): result for result in reference["canonical"]["results"]}
    for row in corpus["scenarios"]:
        oracle = recorded.get(row["id"])
        if oracle is None:
            failures.append(f"{row['id']}: the reference has no row for this corpus input")
            continue
        failures += compare_row(row, oracle, run_reconstruction(harness, row, corpus), corpus)
    return failures


def _validate_row(check: Callable[[bool, str], None], row: dict[str, Any], result: dict[str, Any],
                  corpus: dict[str, Any], reference: dict[str, Any],
                  inventory: dict[int, tuple[int, str]]) -> None:
    """Qualify one recorded original against the frozen corpus, structurally.

    This is RE0's per-scenario qualification with the two things this corpus deliberately does not
    freeze left out: the word count, which must come from the execution and is compared with the
    reconstruction rather than with a model, and the non-RAX caller-saved register outcomes, which
    are diagnostic only here. Everything else is required exactly: the declared initial registers,
    the ordered instruction path, the decoded text and width, the load sequence, the store sequence
    and its values, both independent write traces, the constructed pre-state and the canaries.
    """
    tag = row["id"]
    layout = corpus["layout"]
    memory = corpus["memory"]
    constants = corpus["constants"]
    ctx = pilot.hexint(memory["ctx"])
    rsp0 = pilot.hexint(memory["rsp0"])
    r9d = pilot.hexint(row["r9d"])

    check(result.get("slot") == row["slot"], f"{tag}: the recorded slot is not the input's")
    check(pilot.hexint(result.get("r9d", "0xdeadbeef")) == r9d,
          f"{tag}: the recorded r9d is not the input's")
    check(result.get("path") == path_label(r9d),
          f"{tag}: the recorded path label is not what the input selects")
    check(result.get("qualified") is True,
          f"{tag}: the recorded run is not qualified: {result.get('reasons')}")
    check(result.get("reasons") == [], f"{tag}: the recorded run carried reasons")
    check(result.get("uninitialized_reads") == [],
          f"{tag}: the recorded run read uninitialized constructed state")

    # The declared initial register file, register by register.
    initial = pilot.initial_registers(row, corpus)
    recorded_initial = result.get("initial_registers", {})
    for name, value in initial.items():
        check(pilot.hexint(recorded_initial.get(name, "0xdeadbeef")) == value,
              f"{tag}: initial {name} is {recorded_initial.get(name)}, the corpus declares "
              f"{value:#x}")
    check(set(recorded_initial) == set(pilot.GENERAL_PURPOSE),
          f"{tag}: the record does not carry every general-purpose initial register")

    # The exact ordered instruction path, from RE0's frozen listing walk, every instruction decoded
    # and its bytes, length and text cross-checked against the reviewed listing.
    path = reference["paths"][row["path"]]
    executed = result.get("executed", [])
    check([pilot.hexint(item[0]) for item in executed]
          == [pilot.hexint(rva) for rva in path["rvas"]],
          f"{tag}: the executed instruction sequence is not the frozen {row['path']} path")
    check(len(executed) == path["length"],
          f"{tag}: executed {len(executed)} instructions, the path is {path['length']}")
    total_bytes = 0
    for item in executed:
        rva = pilot.hexint(item[0])
        raw = bytes.fromhex(item[1])
        total_bytes += len(raw)
        length, text = inventory.get(rva, (None, None))
        check(text == item[2],
              f"{tag}: the decoded text at 0x{rva:x} is {item[2]!r}, the reviewed listing says "
              f"{text!r}")
        check(len(raw) == length,
              f"{tag}: the instruction at 0x{rva:x} is {len(raw)} bytes, the reviewed listing says "
              f"{length}")
    check(total_bytes == path["instruction_bytes"],
          f"{tag}: executed {total_bytes} instruction bytes, the path is {path['instruction_bytes']}")

    # The frozen structural load sequence for this path: nothing extra, nothing missing, no
    # reordering. The loaded values are the declared inputs, so they are not frozen here either.
    check(pilot._rows(result.get("loads", []), False) == pilot.expected_loads(row, reference),
          f"{tag}: the data load sequence differs from the frozen structure: {result.get('loads')}")

    # The two memory windows, pre-state first: the recorded initial bytes must be the state this
    # corpus constructs, canary bands included, or the record is not of this corpus.
    window_base = ctx + pilot.hexint(layout["canary_before_start"])
    window_length = (pilot.hexint(layout["canary_after_end"])
                     - pilot.hexint(layout["canary_before_start"]))
    before = bytes.fromhex(result.get("ctx_window_before", ""))
    after = bytes.fromhex(result.get("ctx_window_after", ""))
    frozen_ctx, frozen_stack = pilot.expected_windows(corpus)
    check(pilot.hexint(result.get("ctx_window_base", "0x0")) == window_base,
          f"{tag}: the recorded context window is not the declared observation window")
    if len(before) != window_length or len(after) != window_length:
        check(False, f"{tag}: the recorded context window has the wrong length")
        return
    check(before == frozen_ctx,
          f"{tag}: the recorded context pre-state is not the declared constructed state "
          f"({sum(a != b for a, b in zip(before, frozen_ctx))} of {window_length} bytes differ)")

    # The stack window: the same constructed state, only the saved frame word may change, RBP is
    # restored and the return sentinel slot is untouched.
    stack_base = rsp0 - pilot.hexint(layout["stack_window_before"])
    stack_length = (pilot.hexint(layout["stack_window_before"])
                    + pilot.hexint(layout["stack_window_after"]))
    stack_before = bytes.fromhex(result.get("stack_window_before", ""))
    stack_after = bytes.fromhex(result.get("stack_window_after", ""))
    check(pilot.hexint(result.get("stack_window_base", "0x0")) == stack_base,
          f"{tag}: the recorded stack window is not the declared frame window")
    if len(stack_before) != stack_length or len(stack_after) != stack_length:
        check(False, f"{tag}: the recorded stack window has the wrong length")
        return
    check(stack_before == frozen_stack,
          f"{tag}: the recorded stack pre-state is not the declared constructed state "
          f"({sum(a != b for a, b in zip(stack_before, frozen_stack))} of {stack_length} bytes "
          f"differ)")
    saved = rsp0 - stack_base - 8
    stack_changed = {index for index in range(stack_length)
                     if stack_before[index] != stack_after[index]}
    check(stack_changed == set(range(saved, saved + 8)),
          f"{tag}: the stack changed outside the saved frame word: "
          f"{sorted(hex(stack_base + index) for index in stack_changed ^ set(range(saved, saved + 8)))}")

    # The stores: exactly the stack save and the three declared context stores, in the original's
    # order. The word count is read back from this record's own post-state - it is the original's
    # arithmetic result, frozen nowhere - and the other values are the declared input roles.
    roles = {
        "rbp_initial": pilot.little_endian(pilot.hexint(constants["rbp_initial"]), 8),
        "buffer_pointer": pilot.little_endian(pilot.hexint(memory["buffer"]), 8),
        "0x01": pilot.little_endian(1, 1),
    }
    declared = []
    for space, address, width, role in _declared_stores(row, corpus, reference):
        if role == "words":
            offset = address - window_base
            value = after[offset:offset + width].hex()
        else:
            value = roles[role]
        declared.append((space, address, width, value))
    stores = pilot._rows(result.get("stores", []), True)
    check(stores == declared,
          f"{tag}: the executed store sequence differs from the declared operations: "
          f"{result.get('stores')}")
    check(pilot._rows(result.get("data_written", []), True) == stores,
          f"{tag}: the state-level write trace differs from the executed store sequence: "
          f"{result.get('data_written')}")

    # ... and every declared value must agree with the reported post-state, wherever that state is
    # recorded: the store trace and the memory windows are independent emulator outputs, and a
    # record whose two traces disagree is not a record of one execution.
    for _, address, width, value in declared:
        for label, base, window in (("context", window_base, after),
                                    ("stack", stack_base, stack_after)):
            if base <= address and address + width <= base + len(window):
                offset = address - base
                check(window[offset:offset + width] == bytes.fromhex(value),
                      f"{tag}: the recorded {label} window does not show the stored value at "
                      f"0x{address:x}")

    # Only the declared writes may have changed anything, canaries included.
    declared_changes: set[int] = set()
    for _, address, width, _ in declared:
        if window_base <= address and address + width <= window_base + window_length:
            start = address - window_base
            declared_changes.update(range(start, start + width))
    changed = {index for index in range(window_length) if before[index] != after[index]}
    check(changed <= declared_changes,
          f"{tag}: context bytes changed outside the declared writes: "
          f"{sorted(hex(window_base + index) for index in changed - declared_changes)}")

    # The saved frame word is the caller's RBP, and the sentinel above the frame is intact.
    check(stack_after[saved:saved + 8] == bytes.fromhex(roles["rbp_initial"]),
          f"{tag}: the saved frame word is not the caller's RBP")
    sentinel = pilot.hexint(constants["return_sentinel"])
    check(stack_after[rsp0 - stack_base:rsp0 - stack_base + 8]
          == bytes.fromhex(pilot.little_endian(sentinel, 8)),
          f"{tag}: the return sentinel slot was disturbed")


def _validate_code_window(check: Callable[[bool, str], None], results: list[dict[str, Any]],
                          corpus: dict[str, Any]) -> None:
    """Authenticate every recorded instruction's bytes against the pinned code window.

    The anchor is `reverse_engineering/target.json`'s `code_windows` entry, derived from the pinned
    binary by the repository's own Mach-O mapping - not the receipt's own `code_window_sha256` (a
    digest a record carries cannot authenticate that record) and not the loaded program's executable
    digest (import metadata, not the bytes being emulated). A recorded byte no execution receipt can
    contradict is a byte no evidence rests on; this is the check that contradicts one.

    Cross-row agreement comes first, so a single row cannot disagree with the rest about what the
    original's instructions are, and the recovered instructions must tile the pinned range exactly.
    The arithmetic result is untouched by any of this: the word count still comes from the execution.
    """
    window = paths.code_window(corpus["function"])
    entry = pilot.hexint(window["entry_rva"])
    end = pilot.hexint(window["end_rva"])
    check(pilot.hexint(corpus["entry_rva"]) == entry and pilot.hexint(corpus["end_rva"]) == end,
          "the pinned code window is not this corpus's function range")

    inventory = inventory_by_rva()
    recorded: dict[int, str] = {}
    for result in results:
        tag = result.get("id")
        for item in result.get("executed", []):
            rva = pilot.hexint(item[0])
            if rva in recorded:
                check(recorded[rva] == item[1],
                      f"{tag}: the recorded bytes at 0x{rva:x} differ from the other rows'")
                continue
            recorded[rva] = item[1]

    body = bytearray()
    cursor = entry
    for rva in sorted(recorded):
        raw = recorded[rva]
        length, _ = inventory.get(rva, (None, None))
        check(len(raw) // 2 == length,
              f"the recorded instruction at 0x{rva:x} is {len(raw) // 2} bytes, the reviewed "
              f"listing says {length}")
        check(rva == cursor,
              f"the recorded instructions do not cover the pinned window: 0x{rva:x} follows "
              f"0x{cursor:x}")
        body += bytes.fromhex(raw)
        cursor = rva + len(raw) // 2
    check(cursor == end,
          f"the recorded instructions end at 0x{cursor:x}, the pinned window ends at 0x{end:x}")
    check(hashlib.sha256(bytes(body)).hexdigest() == window["sha256"],
          "the recorded instruction bytes are not the pinned code window")


def validate_reference(reference: dict[str, Any],
                       corpus: dict[str, Any] | None = None) -> list[str]:
    """Validate the committed reference record against the frozen corpus.

    This is RE2's qualification mode, and it is deliberately narrower than the RE0 pilot's receipt
    validation in exactly one respect: the corpus freezes no expected word counts, so there is
    nothing here to compare an arithmetic *result* against. A record is qualified when it was
    produced from this corpus and from the pinned binary, is the same on a repeat run, and every row
    is a well-formed execution of the original that began from the declared constructed state,
    followed the frozen path, touched only the declared memory and wrote exactly the declared
    values; and when the instruction bytes every row records are the pinned binary's own bytes.

    RE2 is restricted to that one frozen corpus, so a *supplied* corpus has to be it: the record's
    `scenarios_sha256` is the digest of the committed file's bytes, which says nothing about a
    dictionary a caller assembled, and a row changed in memory can leave the digest check intact
    while the two sides are no longer running the same input. Supplying anything else is refused
    rather than qualified against a digest that does not describe it.
    """
    frozen = load_corpus()
    corpus = corpus or frozen
    failures: list[str] = []

    def check(condition: bool, message: str) -> None:
        if not condition:
            failures.append(message)

    check(corpus == frozen,
          "the supplied corpus is not the committed frozen corpus this reference was produced from")

    check(reference.get("schema") == REFERENCE_SCHEMA,
          f"reference schema is {reference.get('schema')!r}")
    check(reference.get("statement") == STATEMENT,
          "the reference does not carry the differential label")
    check(reference.get("limits") == corpus["limits"],
          "the reference does not carry the frozen limits verbatim")

    determinism = reference.get("determinism", {})
    digests = determinism.get("canonical_sha256", [])
    check(determinism.get("runs") == 2, "the reference does not record two runs")
    check(len(digests) == 2, "the reference does not record two canonical digests")
    check(digests[:1] == digests[1:2], "the two runs produced different canonical blocks")
    check(determinism.get("identical") is True, "the reference does not assert determinism")

    canonical = reference.get("canonical")
    if canonical is None:
        failures.append("the reference has no canonical block")
        return failures
    check(pilot.canonical_digest(canonical) == (digests[0] if digests else None),
          "the canonical block does not hash to its recorded digest")
    for key in ("function", "entry_rva", "end_rva", "binary_sha256"):
        check(canonical.get(key) == corpus[key],
              f"the reference was not produced from this corpus's {key}")
    pinned = paths.binary_sha256()
    check(canonical.get("binary_sha256") == pinned,
          "the reference was not produced from the pinned target binary")
    # The declaration is copied from the corpus; this is the digest of the program the emulator
    # actually loaded, and it is what makes the record evidence about the pinned binary rather than
    # about whatever was installed next to it.
    check(canonical.get("executable_sha256") == pinned,
          "the reference does not identify the pinned executable that was emulated")
    check(canonical.get("scenarios_sha256") == corpus_sha256(),
          "the reference did not consume the frozen corpus byte-for-byte")

    results = canonical.get("results", [])
    check([result.get("id") for result in results] == [row["id"] for row in corpus["scenarios"]],
          "the reference's scenario ids or their order differ from the frozen corpus")

    reference_structure = frozen_structure()
    _validate_code_window(check, results, corpus)
    inventory = inventory_by_rva()
    for row, result in zip(corpus["scenarios"], results):
        _validate_row(check, row, result, corpus, reference_structure, inventory)
    return failures


def run_twice(work_dir: Path) -> dict[str, Any]:
    """Capture the reference: two emulator runs of the frozen corpus, receipt-shaped.

    This is how `differential/reference.json` was produced; the tests call it to re-derive the
    committed block. It needs Ghidra, the analysed project and the pinned binary.
    """
    return pilot.run_twice(work_dir, scenarios_path=SCENARIOS_PATH, schema=REFERENCE_SCHEMA,
                           statement=STATEMENT)
