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
emulator stores the constructed buffer address, the reconstruction a host pointer.

The corpus freezes **inputs, not reference outputs**. Nothing here compares the reconstruction
with a hand-written expectation, and nothing derives an expectation from the reconstruction: the
reference is `differential/reference.json`, the emulator's own record, bound to the corpus digest
by `canonical.scenarios_sha256`. That is why this qualification mode differs from the RE0 pilot's:
there, expectations were hand-derived and frozen before execution; here, the reference is the
original's executed behaviour and the comparison itself is the evidence.

Strictly offline: nothing launches, attaches to or reads the game.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path
from typing import Any

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


def run_reconstruction(harness: Path, row: dict[str, Any], corpus: dict[str, Any]) -> dict[str, str]:
    """One corpus row through the compiled reconstruction's `scenario` harness mode."""
    dword = row["buffer_first_dword"]
    argument = "absent" if dword is None else f"{pilot.hexint(dword):08x}"
    command = [str(harness), "scenario", str(row["slot"]), f"{pilot.hexint(row['r9d']):x}",
               argument]
    result = subprocess.run(command, input=prestate_bytes(corpus).hex() + "\n",
                            capture_output=True, text=True, timeout=HARNESS_TIMEOUT)
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

    # Byte-exact reconciliation of the declared fields; the pointer is compared as a role.
    for name, (offset, width) in fields.items():
        window = slice(offset - start, offset - start + width)
        if name == "buffer":
            check(any(oracle_after[window]), "the original did not store a buffer pointer")
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
    """Reconcile every frozen corpus row; returns one message per disagreement."""
    corpus = corpus or load_corpus()
    reference = reference or load_reference()
    recorded = {result.get("id"): result for result in reference["canonical"]["results"]}
    failures: list[str] = []
    for row in corpus["scenarios"]:
        oracle = recorded.get(row["id"])
        if oracle is None:
            failures.append(f"{row['id']}: the reference has no row for this corpus input")
            continue
        failures += compare_row(row, oracle, run_reconstruction(harness, row, corpus), corpus)
    return failures


def validate_reference(reference: dict[str, Any],
                       corpus: dict[str, Any] | None = None) -> list[str]:
    """Validate the committed reference record against the frozen corpus.

    This is RE2's qualification mode, and it is deliberately narrower than the RE0 pilot's
    receipt validation: the corpus freezes no expected words, so there is nothing here to compare
    an outcome *value* against. What is checked is that the record was produced from this corpus,
    from the pinned binary, that it is the same on a repeat run, and that every row is a
    well-formed execution of the original that wrote the three declared fields.
    """
    corpus = corpus or load_corpus()
    failures: list[str] = []

    def check(condition: bool, message: str) -> None:
        if not condition:
            failures.append(message)

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
    check(canonical.get("binary_sha256") == paths.binary_sha256(),
          "the reference was not produced from the pinned target binary")
    check(canonical.get("scenarios_sha256") == corpus_sha256(),
          "the reference did not consume the frozen corpus byte-for-byte")

    results = canonical.get("results", [])
    check([result.get("id") for result in results] == [row["id"] for row in corpus["scenarios"]],
          "the reference's scenario ids or their order differ from the frozen corpus")
    ctx = pilot.hexint(corpus["memory"]["ctx"])
    for row, result in zip(corpus["scenarios"], results):
        tag = row["id"]
        check(result.get("slot") == row["slot"], f"{tag}: the recorded slot is not the input's")
        check(pilot.hexint(result.get("r9d", "0xdeadbeef")) == pilot.hexint(row["r9d"]),
              f"{tag}: the recorded r9d is not the input's")
        check(result.get("path") == path_label(pilot.hexint(row["r9d"])),
              f"{tag}: the recorded path label is not what the input selects")
        check(result.get("qualified") is True,
              f"{tag}: the recorded run is not qualified: {result.get('reasons')}")
        check(result.get("reasons") == [], f"{tag}: the recorded run carried reasons")
        check(result.get("uninitialized_reads") == [],
              f"{tag}: the recorded run read uninitialized constructed state")
        stored = _store_set(result)
        for name, (offset, width) in declared_fields(corpus, row["slot"]).items():
            check((ctx + offset, width) in stored,
                  f"{tag}: the recorded run has no {name} store at ctx+{offset:#x}")
    return failures


def run_twice(work_dir: Path) -> dict[str, Any]:
    """Capture the reference: two emulator runs of the frozen corpus, receipt-shaped.

    This is how `differential/reference.json` was produced; the tests call it to re-derive the
    committed block. It needs Ghidra, the analysed project and the pinned binary.
    """
    return pilot.run_twice(work_dir, scenarios_path=SCENARIOS_PATH, schema=REFERENCE_SCHEMA,
                           statement=STATEMENT)
