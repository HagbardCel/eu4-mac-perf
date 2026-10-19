"""RE0 offline execution pilot for `_GfxSetConstantBuffer` (entry RVA 0x15ec36a).

Runs the frozen scenarios in `analysis/reconstruction/_GfxSetConstantBuffer/pilot/scenarios.json`
through the Ghidra P-code emulator and validates the resulting observation record against the
frozen expectations. Strictly offline: no game launch, no process attach, no game process. The
emulator interprets the decoded instructions of the pinned binary under a constructed state, so
the evidence is about those instructions in that emulator - see `pilot/pilot.md` for the limits.

This module never derives an expectation: expectations are literals in the frozen scenarios, and
everything here either runs the pilot or compares a record against them.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Callable

from reverse_engineering import corpus, paths

ROOT = Path(__file__).resolve().parents[1]
FUNCTION_DIR = ROOT / "analysis" / "reconstruction" / "_GfxSetConstantBuffer"
PILOT_DIR = FUNCTION_DIR / "pilot"
SCENARIOS_PATH = PILOT_DIR / "scenarios.json"
INVENTORY_PATH = FUNCTION_DIR / "instructions.json"

GHIDRA_SCRIPT = "EmulateGfxSetConstantBuffer.java"
OBSERVATIONS_SCHEMA = "eu4-re0-pilot-observations/v1"
RECEIPT_SCHEMA = "eu4-re0-pilot-receipt/v1"
PROCESS_TIMEOUT = 1800

# What the receipt says about itself, so the evidence cannot be read as more than it is.
STATEMENT = (
    "Ghidra P-code execution of the original pinned x86-64 instructions under constructed "
    "state - not hardware-level execution of x86 machine code and not runtime behaviour in "
    "the game."
)

# The emulator's default data space; every declared region lives here.
DATA_SPACE = "ram"

Failure = Callable[[bool, str], None]


def hexint(value: str | int) -> int:
    return int(value, 16) if isinstance(value, str) else int(value)


def little_endian(value: int, size: int) -> str:
    return value.to_bytes(size, "little").hex()


def load_scenarios(path: Path | None = None) -> dict[str, Any]:
    return json.loads((path or SCENARIOS_PATH).read_text())


def scenarios_sha256(path: Path | None = None) -> str:
    return hashlib.sha256((path or SCENARIOS_PATH).read_bytes()).hexdigest()


def inventory_text_by_rva(path: Path | None = None) -> dict[int, str]:
    """The reviewed listing's decoded text per RVA: mnemonic plus operands."""
    inventory = json.loads((path or INVENTORY_PATH).read_text())
    return {hexint(item["rva"]): item["operands"] for item in inventory["instructions"]}


def canonical_digest(canonical: dict[str, Any]) -> str:
    """Stable digest of the run-dependent block; the metadata block is excluded on purpose."""
    blob = json.dumps(canonical, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(blob).hexdigest()


# ---------------------------------------------------------------- expectation resolution


def _bases(scenarios: dict[str, Any]) -> dict[str, int]:
    memory = scenarios["memory"]
    constants = scenarios["constants"]
    rsp0 = hexint(memory["rsp0"])
    return {
        "ctx": hexint(memory["ctx"]),
        "handle": hexint(memory["handle"]),
        "buffer": hexint(memory["buffer"]),
        "stack": rsp0,
        "stack_minus_8": rsp0 - 8,
        "rsp0": rsp0,
        "rbp_initial": hexint(constants["rbp_initial"]),
        "return_sentinel": hexint(constants["return_sentinel"]),
    }


def expected_loads(scenario: dict[str, Any], scenarios: dict[str, Any]) -> list[tuple]:
    bases = _bases(scenarios)
    declared = scenarios["expected_loads"][scenario["path"]]
    return [(DATA_SPACE, bases[item["base"]], item["width"]) for item in declared]


def expected_stores(scenario: dict[str, Any], scenarios: dict[str, Any]) -> list[tuple]:
    bases = _bases(scenarios)
    layout = scenarios["layout"]
    slot = scenario["slot"] * layout["slot_stride"]
    ctx = bases["ctx"]
    addresses = {
        "stack_minus_8": bases["stack_minus_8"],
        "ctx_words": ctx + hexint(layout["word_count_base"]) + slot,
        "ctx_pointer": ctx + hexint(layout["pointer_base"]) + slot,
        "ctx_dirty": ctx + hexint(layout["dirty"]),
    }
    values = {
        "rbp_initial": little_endian(bases["rbp_initial"], 8),
        "words": little_endian(hexint(scenario["expected_words"]), 4),
        "buffer_pointer": little_endian(bases["buffer"], 8),
        "0x01": little_endian(1, 1),
    }
    declared = scenarios["expected_stores"]
    return [
        (DATA_SPACE, addresses[item["base"]], item["width"], values[item["value"]])
        for item in declared
    ]


def _rows(rows: list[list[str]], with_value: bool) -> list[tuple]:
    out = []
    for row in rows:
        item = (row[0], hexint(row[1]), int(row[2]))
        out.append(item if not with_value else item + (row[3],))
    return out


# ---------------------------------------------------- the frozen constructed state

#: Every general-purpose register the freeze declares, plus the one it declines to assert.
GENERAL_PURPOSE = (
    "RAX", "RBX", "RCX", "RDX", "RSI", "RDI", "RBP", "RSP",
    "R8", "R9", "R10", "R11", "R12", "R13", "R14", "R15",
)


def fill_pattern(memory: dict[str, Any], size: int) -> bytes:
    """The declared byte pattern for the context and stack regions.

    The freeze owns the formula; this only interprets the declared kind, so the reconstruction
    cannot drift from what the emulator constructs.
    """
    pattern = memory["fill_pattern"]
    if pattern["kind"] != "linear_byte_pattern":
        raise ValueError(f"unknown fill pattern kind {pattern['kind']!r}")
    multiplier = pattern["multiplier"]
    increment = pattern["increment"]
    return bytes(((index * multiplier) + increment) & 0xFF for index in range(size))


def resolve_register(symbol: str, names: dict[str, int]) -> int:
    """Resolve one frozen register symbol. An unknown symbol is an error, never a zero."""
    if symbol in names:
        return names[symbol]
    if symbol.startswith("0x"):
        return hexint(symbol)
    raise ValueError(f"unknown register symbol {symbol!r}")


def initial_registers(scenario: dict[str, Any], scenarios: dict[str, Any]) -> dict[str, int]:
    """The frozen initial register file for one scenario."""
    bases = _bases(scenarios)
    names = {
        "slot": scenario["slot"],
        "buffer": bases["buffer"],
        "handle": bases["handle"],
        "rbp_initial": bases["rbp_initial"],
        "rsp0": bases["rsp0"],
        "r9d_zero_extended": hexint(scenario["r9d"]),
    }
    declared = scenarios["initial_registers"]
    return {name: resolve_register(declared[name], names) for name in GENERAL_PURPOSE}


def expected_final_registers(
    scenario: dict[str, Any], scenarios: dict[str, Any]
) -> tuple[dict[str, int], dict[str, int], list[str]]:
    """The frozen final register expectations: (asserted, must be preserved, not asserted).

    Only the declared table decides which registers are asserted; nothing here adds one.
    """
    bases = _bases(scenarios)
    declared = scenarios["expected_final_registers"]
    preserved_names = list(declared["preserved"])
    not_asserted = list(declared["not_asserted"])
    initial = initial_registers(scenario, scenarios)
    names = {
        "ctx": bases["ctx"],
        "slot_times_stride": scenario["slot"] * scenarios["layout"]["slot_stride"],
        "expected_rdx": hexint(scenario["expected_rdx"]),
        "expected_r9": hexint(scenario["expected_r9"]),
        "rsp0_plus_8": bases["rsp0"] + 8,
        "rbp_initial": bases["rbp_initial"],
    }
    asserted = {
        name: resolve_register(value, names)
        for name, value in declared.items()
        if name not in ("preserved", "not_asserted")
    }
    return asserted, {name: initial[name] for name in preserved_names}, not_asserted


def expected_windows(scenarios: dict[str, Any]) -> tuple[bytes, bytes]:
    """(context, stack) window bytes exactly as the freeze constructs them, before execution."""
    memory = scenarios["memory"]
    layout = scenarios["layout"]
    constants = scenarios["constants"]

    ctx_base = hexint(memory["ctx"])
    ctx = fill_pattern(memory, hexint(memory["ctx_size"]))
    ctx_start = hexint(layout["canary_before_start"])
    ctx_length = hexint(layout["canary_after_end"]) - ctx_start
    ctx_window = ctx[ctx_start:ctx_start + ctx_length]

    stack_base = hexint(memory["stack_base"])
    stack = bytearray(fill_pattern(memory, hexint(memory["stack_size"])))
    sentinel_at = hexint(memory["rsp0"]) - stack_base
    stack[sentinel_at:sentinel_at + 8] = hexint(constants["return_sentinel"]).to_bytes(8, "little")
    stack_start = hexint(memory["rsp0"]) - hexint(layout["stack_window_before"])
    stack_length = hexint(layout["stack_window_before"]) + hexint(layout["stack_window_after"])
    offset = stack_start - stack_base
    return ctx_window, bytes(stack[offset:offset + stack_length])


# ---------------------------------------------------------------- validation


def validate_receipt(
    receipt: dict[str, Any], *, scenarios_path: Path | None = None, mapper: Any = None
) -> list[str]:
    """Validate a committed receipt, including its determinism block."""
    failures: list[str] = []

    def check(condition: bool, message: str) -> None:
        if not condition:
            failures.append(message)

    scenarios = load_scenarios(scenarios_path)
    check(receipt.get("schema") == RECEIPT_SCHEMA, f"receipt schema is {receipt.get('schema')!r}")
    statement = receipt.get("statement")
    check(
        isinstance(statement, str) and "P-code" in statement,
        "receipt does not state that the evidence is P-code emulation of the original instructions",
    )
    check(
        receipt.get("limits") == scenarios["limits"],
        "receipt does not carry the frozen limits verbatim",
    )
    determinism = receipt.get("determinism", {})
    digests = determinism.get("canonical_sha256", [])
    check(determinism.get("runs") == 2, "receipt does not record two runs")
    check(len(digests) == 2, "receipt does not record two canonical digests")
    check(digests[:1] == digests[1:2], "the two runs produced different canonical blocks")
    check(determinism.get("identical") is True, "receipt does not assert canonical determinism")

    canonical = receipt.get("canonical")
    if canonical is None:
        failures.append("receipt has no canonical block")
        return failures
    check(
        canonical_digest(canonical) == (digests[0] if digests else None),
        "canonical block does not hash to its recorded digest",
    )
    failures += validate_observations(
        {"schema": OBSERVATIONS_SCHEMA, "canonical": canonical},
        scenarios_path=scenarios_path,
        mapper=mapper,
    )
    return failures


def validate_observations(
    observations: dict[str, Any],
    *,
    scenarios_path: Path | None = None,
    inventory_text: dict[int, str] | None = None,
    mapper: Any = None,
) -> list[str]:
    """Compare one run's observation record against the frozen scenarios.

    The frozen file is identified by its own bytes: `scenarios_path` selects it, and the digest
    the run recorded is compared against the digest of those bytes, never of a re-serialisation.
    `mapper` is an optional open MachOMapper used for the independent check of executed
    instruction bytes against the pinned binary.
    """
    path = Path(scenarios_path) if scenarios_path is not None else SCENARIOS_PATH
    scenarios = load_scenarios(path)
    texts = inventory_text if inventory_text is not None else inventory_text_by_rva()
    failures: list[str] = []

    def check(condition: bool, message: str) -> None:
        if not condition:
            failures.append(message)

    check(observations.get("schema") == OBSERVATIONS_SCHEMA,
          f"observations schema is {observations.get('schema')!r}")
    canonical = observations.get("canonical", {})
    check(canonical.get("function") == scenarios["function"], "function identity")
    check(canonical.get("entry_rva") == scenarios["entry_rva"], "entry RVA")
    check(canonical.get("end_rva") == scenarios["end_rva"], "end RVA")
    pinned = paths.binary_sha256()
    check(scenarios.get("binary_sha256") == pinned,
          "the frozen scenarios carry a pinned binary digest that is not the repo's target digest")
    check(canonical.get("binary_sha256") == pinned,
          "the run recorded a pinned binary digest that is not the repo's target digest")
    check(canonical.get("executable_sha256") == pinned,
          "the emulated program identity is not the pinned binary")
    check(canonical.get("scenarios_sha256") == scenarios_sha256(path),
          "run did not consume the frozen scenarios byte-for-byte")

    results = canonical.get("results", [])
    check([item.get("id") for item in results] == [s["id"] for s in scenarios["scenarios"]],
          "scenario ids or their order differ from the frozen list")

    for scenario, result in zip(scenarios["scenarios"], results):
        _validate_scenario(check, scenario, result, scenarios, texts, mapper)
    return failures


def _validate_scenario(
    check: Failure,
    scenario: dict[str, Any],
    result: dict[str, Any],
    scenarios: dict[str, Any],
    texts: dict[int, str],
    mapper: Any,
) -> None:
    tag = scenario["id"]
    bases = _bases(scenarios)

    check(result.get("path") == scenario["path"], f"{tag}: path differs from the frozen scenario")
    check(result.get("slot") == scenario["slot"], f"{tag}: slot differs from the frozen scenario")
    check(hexint(result.get("r9d", "0xdeadbeef")) == hexint(scenario["r9d"]),
          f"{tag}: recorded r9d {result.get('r9d')} is not the frozen {scenario['r9d']}")
    check(result.get("qualified") is True, f"{tag}: run not qualified: {result.get('reasons')}")
    check(result.get("reasons") == [], f"{tag}: run carried reasons: {result.get('reasons')}")
    check(result.get("uninitialized_reads") == [],
          f"{tag}: the constructed state was read uninitialized: {result.get('uninitialized_reads')}")

    # The initial register file: what was loaded must be what the freeze declares, register by
    # register. A run that deviates from any declared initial register is not the frozen run.
    initial = initial_registers(scenario, scenarios)
    recorded_initial = result.get("initial_registers", {})
    for name, value in initial.items():
        check(hexint(recorded_initial.get(name, "0xdeadbeef")) == value,
              f"{tag}: initial {name} is {recorded_initial.get(name)}, "
              f"the freeze declares {value:#x}")
    check(set(recorded_initial) == set(GENERAL_PURPOSE),
          f"{tag}: the run did not record every general-purpose initial register")

    # Execution: the exact ordered PC sequence, each decoded instruction, and its bytes.
    path = scenarios["paths"][scenario["path"]]
    declared_rvas = [hexint(rva) for rva in path["rvas"]]
    executed = result.get("executed", [])
    check([hexint(row[0]) for row in executed] == declared_rvas,
          f"{tag}: executed PC sequence differs from the frozen {scenario['path']} path")
    check(len(executed) == path["length"],
          f"{tag}: executed {len(executed)} instructions, expected {path['length']}")
    total_bytes = 0
    for row in executed:
        rva = hexint(row[0])
        raw = bytes.fromhex(row[1])
        total_bytes += len(raw)
        check(texts.get(rva) == row[2],
              f"{tag}: decoded text at 0x{rva:x} is {row[2]!r}, listing says {texts.get(rva)!r}")
        if mapper is not None:
            check(mapper.read_bytes_at_rva(scenarios["image_base"], rva, len(raw)) == raw,
                  f"{tag}: executed bytes at 0x{rva:x} do not match the pinned binary")
    check(total_bytes == path["instruction_bytes"],
          f"{tag}: executed {total_bytes} instruction bytes, expected {path['instruction_bytes']}")

    # Access traces: exact expected sequences, not merely permitted sets.
    check(_rows(result.get("loads", []), False) == expected_loads(scenario, scenarios),
          f"{tag}: data load sequence differs from the frozen expectation: {result.get('loads')}")
    stores = expected_stores(scenario, scenarios)
    check(_rows(result.get("stores", []), True) == stores,
          f"{tag}: executed store sequence differs from the frozen expectation: {result.get('stores')}")
    check(_rows(result.get("data_written", []), True) == stores,
          f"{tag}: state-level write trace differs from the executed store sequence: "
          f"{result.get('data_written')}")

    # Return boundary and register outcome, from the freeze's own declared expectations: the
    # asserted registers, and the registers that must still hold their frozen initial value.
    registers = result.get("final_registers", {})
    asserted, preserved, not_asserted = expected_final_registers(scenario, scenarios)
    check(not (set(asserted) & set(preserved)) and not (set(asserted) & set(not_asserted))
          and not (set(preserved) & set(not_asserted)),
          f"{tag}: the frozen register table declares a register more than once")
    for name, value in asserted.items():
        check(hexint(registers.get(name, "0xdeadbeef")) == value,
              f"{tag}: final {name} is {registers.get(name)}, the freeze expects {value:#x}")
    for name, value in preserved.items():
        check(hexint(registers.get(name, "0xdeadbeef")) == value,
              f"{tag}: final {name} is {registers.get(name)}, the freeze requires it to hold "
              f"its initial {value:#x}")
    check(all(name in GENERAL_PURPOSE or name == "RFLAGS" for name in not_asserted),
          f"{tag}: the freeze declines to assert something that is not a register: {not_asserted}")

    _validate_windows(check, tag, result, scenarios, bases)


def _validate_windows(
    check: Failure,
    tag: str,
    result: dict[str, Any],
    scenarios: dict[str, Any],
    bases: dict[str, int],
) -> None:
    """Context memory: the recorded pre-state is the frozen one, only the declared writes
    changed, and canaries and pads included."""
    layout = scenarios["layout"]
    frozen_ctx, frozen_stack = expected_windows(scenarios)
    window_base = bases["ctx"] + hexint(layout["canary_before_start"])
    window_length = hexint(layout["canary_after_end"]) - hexint(layout["canary_before_start"])
    check(hexint(result.get("ctx_window_base", "0x0")) == window_base,
          f"{tag}: recorded context window is not the declared observation window")
    before = bytes.fromhex(result.get("ctx_window_before", ""))
    after = bytes.fromhex(result.get("ctx_window_after", ""))
    if len(before) != window_length or len(after) != window_length:
        check(False, f"{tag}: recorded context window has the wrong length")
        return
    # The recorded pre-state must be the state the freeze declares, independently reconstructed
    # here. Without this, before/after agreement only shows the record is self-consistent.
    check(before == frozen_ctx,
          f"{tag}: recorded context pre-state is not the frozen constructed state "
          f"({sum(a != b for a, b in zip(before, frozen_ctx))} of {window_length} bytes differ)")
    changed = {index for index in range(window_length) if before[index] != after[index]}
    declared_changes: set[int] = set()
    for _, address, width, value in expected_stores(result_scenario(result, scenarios), scenarios):
        if window_base <= address and address + width <= window_base + window_length:
            start = address - window_base
            declared_changes.update(range(start, start + width))
            check(after[start:start + width] == bytes.fromhex(value),
                  f"{tag}: stored value at 0x{address:x} differs from the frozen expectation")
    # Containment, not equality: a write whose bytes equal what was already there (s05 stores a
    # zero byte over a zero prefill byte) leaves no byte-level trace. Such a write is still an
    # executed store and is covered by the exhaustive store traces above.
    check(changed <= declared_changes,
          f"{tag}: context bytes changed outside the declared writes: "
          f"{sorted(hex(window_base + index) for index in changed - declared_changes)}")

    # Stack: the recorded pre-state is the frozen one, exactly the eight saved bytes changed,
    # and the return sentinel is intact.
    stack_base = bases["rsp0"] - hexint(layout["stack_window_before"])
    stack_length = hexint(layout["stack_window_before"]) + hexint(layout["stack_window_after"])
    check(hexint(result.get("stack_window_base", "0x0")) == stack_base,
          f"{tag}: recorded stack window is not the declared frame window")
    stack_before = bytes.fromhex(result.get("stack_window_before", ""))
    stack_after = bytes.fromhex(result.get("stack_window_after", ""))
    if len(stack_before) != stack_length or len(stack_after) != stack_length:
        check(False, f"{tag}: recorded stack window has the wrong length")
        return
    check(stack_before == frozen_stack,
          f"{tag}: recorded stack pre-state is not the frozen constructed state "
          f"({sum(a != b for a, b in zip(stack_before, frozen_stack))} of {stack_length} bytes differ)")
    saved_start = bases["rsp0"] - stack_base - 8
    saved = set(range(saved_start, saved_start + 8))
    stack_changed = {
        index for index in range(stack_length) if stack_before[index] != stack_after[index]
    }
    check(stack_changed == saved,
          f"{tag}: stack changed outside the saved frame word: "
          f"{sorted(hex(stack_base + index) for index in stack_changed ^ saved)}")
    check(stack_after[saved_start:saved_start + 8] == bytes.fromhex(little_endian(bases["rbp_initial"], 8)),
          f"{tag}: the saved frame word is not the caller's RBP")
    sentinel_start = bases["rsp0"] - stack_base
    check(stack_after[sentinel_start:sentinel_start + 8] == bytes.fromhex(little_endian(bases["return_sentinel"], 8)),
          f"{tag}: the return sentinel slot was disturbed")


def result_scenario(result: dict[str, Any], scenarios: dict[str, Any]) -> dict[str, Any]:
    """The frozen scenario row a recorded result belongs to."""
    for scenario in scenarios["scenarios"]:
        if scenario["id"] == result.get("id"):
            return scenario
    raise KeyError(result.get("id"))


# ---------------------------------------------------------------- running


def default_project_dir() -> Path:
    return paths.ghidra_project_dir(paths.local_binary_root())


def default_project_name() -> str:
    return corpus.project_name_for_sha(paths.binary_sha256())


def default_program_name() -> str:
    """A Ghidra import names the program after the file it imported."""
    return paths.executable_path().name


def run_pilot(
    output_path: Path,
    *,
    scenarios_path: Path = SCENARIOS_PATH,
    project_dir: Path | None = None,
    project_name: str | None = None,
    program_name: str | None = None,
    process_timeout: int = PROCESS_TIMEOUT,
) -> Path:
    """Run every frozen scenario through the emulator and write the observation record.

    The pinned digests handed to the script come from `reverse_engineering/target.json`, not from
    the scenario file, so the script can refuse to execute unless the loaded program is the pinned
    target - both its executable identity and the instruction bytes actually present in the loaded
    program's memory - even if the scenario file has been altered.
    """
    function = load_scenarios(scenarios_path)["function"]
    corpus.analyze_headless(
        project_dir or default_project_dir(),
        project_name or default_project_name(),
        "-process",
        program_name or default_program_name(),
        "-noanalysis",
        "-readOnly",
        "-postScript",
        GHIDRA_SCRIPT,
        str(scenarios_path),
        str(output_path),
        paths.binary_sha256(),
        paths.code_window(function)["sha256"],
        process_timeout=process_timeout,
    )
    return Path(output_path)


def run_twice(work_dir: Path, *, scenarios_path: Path = SCENARIOS_PATH,
              schema: str = RECEIPT_SCHEMA, statement: str = STATEMENT) -> dict[str, Any]:
    """Two independent runs of one frozen corpus; the receipt carries the second only as a digest.

    The corpus is a parameter so a later freeze (RE2's differential corpus) can reuse this runner
    without touching the emulator script, which reads the corpus file it is handed.
    """
    work_dir.mkdir(parents=True, exist_ok=True)
    first = json.loads(run_pilot(work_dir / "run1.json", scenarios_path=scenarios_path).read_text())
    second = json.loads(run_pilot(work_dir / "run2.json", scenarios_path=scenarios_path).read_text())
    return build_receipt(first, second, scenarios_path=scenarios_path, schema=schema,
                         statement=statement)


def build_receipt(first: dict[str, Any], second: dict[str, Any], *,
                  scenarios_path: Path = SCENARIOS_PATH, schema: str = RECEIPT_SCHEMA,
                  statement: str = STATEMENT) -> dict[str, Any]:
    """The receipt for one corpus: the limits come from the corpus, the label from the caller."""
    digest = canonical_digest(first["canonical"])
    repeat = canonical_digest(second["canonical"])
    return {
        "schema": schema,
        "statement": statement,
        "limits": load_scenarios(scenarios_path)["limits"],
        "environment": first["environment"],
        "determinism": {
            "runs": 2,
            "canonical_sha256": [digest, repeat],
            "identical": digest == repeat,
        },
        "canonical": first["canonical"],
    }
