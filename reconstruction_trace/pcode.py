"""Import the frozen ``_GfxSetConstantBuffer`` P-code receipt (TRACE_V1.md, P-code importer).

The receipt (``eu4-re0-pilot-receipt/v1``) is read, never executed. Its scenario
SHA must bind the frozen ``scenarios.json``. Each result becomes one
``execution-observation/v1`` trace with evidence ``ghidra-pcode``:

- the entry inventory is the two recorded windows (context and stack);
- stores carry the receipt's own written values and are ``observed``;
- load rows carry only an address and a width. A load value is reconstructed
  per byte from the latest earlier store in the ordered trace, the recorded
  ``*_window_before`` bytes, or the scenario's ``buffer_first_dword``, and is
  ``derived-scenario`` with its sources named in ``provenance``;
- a load byte with no source, or with sources that disagree, leaves that load
  without a value and the trace ``incomplete``.

The order of memory events comes from the receipt's ``executed`` list: each
instruction is classified as a load, a store or neither, and consumes the next
row of the matching list. An instruction whose memory access is not classified
also leaves the trace incomplete.
"""
from __future__ import annotations

import json
from hashlib import sha256
from pathlib import Path

from reconstruction_trace import schema

ROOT = Path(__file__).resolve().parents[1]
PILOT = ROOT / "analysis" / "reconstruction" / "_GfxSetConstantBuffer" / "pilot"
RECEIPT_PATH = PILOT / "observations-2026-10-18.json"
SCENARIOS_PATH = PILOT / "scenarios.json"
RECEIPT_SCHEMA = "eu4-re0-pilot-receipt/v1"
SCENARIOS_SCHEMA = "eu4-re0-pilot-scenarios/v1"
FUNCTION_ID = "_GfxSetConstantBuffer"


class PcodeError(ValueError):
    """The receipt or scenario file is not the frozen, mutually bound pair."""


def _hex(value: str) -> str:
    return hex(int(value, 16))


def _accesses(text: str) -> list[str] | None:
    """Memory accesses of one decoded instruction, or None when unclassified."""
    mnemonic, _, operands = text.partition(" ")
    if mnemonic == "PUSH":
        return ["store"]
    if mnemonic in ("POP", "RET"):
        return ["load"]
    if mnemonic == "CALL":
        return None
    if mnemonic == "LEA" or "[" not in operands:
        return []
    if mnemonic != "MOV":
        return None
    return ["store"] if "[" in operands.split(",", 1)[0] else ["load"]


def _trace(receipt: dict, scenarios: dict, scenario: dict, result: dict) -> dict:
    canonical = receipt["canonical"]
    windows = {
        "ctx_window": (int(result["ctx_window_base"], 16), result["ctx_window_before"],
                       result["ctx_window_after"], "heap"),
        "stack_window": (int(result["stack_window_base"], 16),
                         result["stack_window_before"], result["stack_window_after"],
                         "stack"),
    }
    recorded: dict[int, list[tuple[int, str]]] = {}
    for name, (base, before, _, _) in windows.items():
        for index, byte in enumerate(bytes.fromhex(before)):
            recorded.setdefault(base + index, []).append((byte, f"receipt {name}_before"))
    if scenario["buffer_first_dword"] is not None:
        buffer = int(scenarios["memory"]["buffer"], 16)
        field = f"scenarios.json scenarios[{scenario['id']}].buffer_first_dword"
        dword = int(scenario["buffer_first_dword"], 16).to_bytes(4, "little")
        for index, byte in enumerate(dword):
            recorded.setdefault(buffer + index, []).append((byte, field))

    reasons: list[str] = []
    unresolved: list[dict] = []
    if not result["qualified"] or result["reasons"] or result["uninitialized_reads"]:
        reasons.append("the receipt does not qualify this scenario")
    if result["stores"] != result["data_written"]:
        reasons.append("the receipt's two store traces disagree")

    rows = {"load": list(result["loads"]), "store": list(result["stores"])}
    written: dict[int, tuple[int, str]] = {}
    events = []
    for rva, _, text in result["executed"]:
        accesses = _accesses(text)
        if accesses is None:
            reasons.append(f"memory access of {text!r} at {rva} is not classified")
            continue
        for kind in accesses:
            if not rows[kind] or rows[kind][0][0] != "ram":
                reasons.append(f"no ram {kind} row for {text!r} at {rva}")
                continue
            row = rows[kind].pop(0)
            event = {"kind": "write" if kind == "store" else "read", "pc": _hex(rva),
                     "address": _hex(row[1]), "size": int(row[2])}
            start = int(row[1], 16)
            if kind == "store":
                event.update(value=row[3], value_source="observed")
                for index, byte in enumerate(bytes.fromhex(row[3])):
                    written[start + index] = (byte, f"receipt store at pc {event['pc']}")
                events.append(event)
                continue
            value, sources = bytearray(), []
            for at in range(start, start + event["size"]):
                candidates = [written[at]] if at in written else recorded.get(at, [])
                if len({byte for byte, _ in candidates}) != 1:
                    break
                value.append(candidates[0][0])
                sources += [source for _, source in candidates if source not in sources]
            if len(value) == event["size"]:
                event.update(value=value.hex(), value_source="derived-scenario",
                             provenance="; ".join(sources))
            else:
                reason = ("load value is not in the receipt and is not reconstructable "
                          "from scenarios.json, the ordered trace or the recorded windows")
                unresolved.append({"address": event["address"], "size": event["size"],
                                   "reason": reason})
                reasons.append(f"{reason}: address {event['address']} size {event['size']}")
            events.append(event)
    for kind, left in rows.items():
        if left:
            reasons.append(f"{len(left)} {kind} rows are not matched to executed instructions")

    def registers(values: dict) -> dict:
        return {name.lower(): _hex(value) for name, value in values.items()}

    return {
        "schema": schema.SCHEMA,
        "function_id": FUNCTION_ID,
        "binary": {"sha256": canonical["binary_sha256"],
                   "image_base": _hex(receipt["environment"]["image_base"]),
                   "entry_rva": _hex(canonical["entry_rva"])},
        "evidence": "ghidra-pcode",
        "instrumentation": {"name": receipt["environment"]["script_version"],
                            "version": f"ghidra {receipt['environment']['ghidra_version']}"},
        "entry_state": {
            "registers": registers(result["initial_registers"]),
            "regions": [{"id": name, "kind": kind, "base": hex(base),
                         "size": len(before) // 2, "bytes": before,
                         "preservation": "required"}
                        for name, (base, before, _, kind) in windows.items()],
        },
        "events": events,
        "exit_state": {
            "registers": registers(result["final_registers"]),
            "regions": [{"id": name, "base": hex(base), "size": len(after) // 2,
                         "bytes": after}
                        for name, (base, _, after, _) in windows.items()],
        },
        "completeness": "incomplete" if reasons else "complete",
        "incomplete_reasons": reasons,
        "unresolved_dependencies": unresolved,
    }


def import_receipt(receipt_path: Path = RECEIPT_PATH,
                   scenarios_path: Path = SCENARIOS_PATH) -> dict[str, dict]:
    """One validated trace per receipt result, keyed by scenario id."""
    scenario_bytes = Path(scenarios_path).read_bytes()
    receipt = json.loads(Path(receipt_path).read_text())
    if receipt.get("schema") != RECEIPT_SCHEMA:
        raise PcodeError(f"receipt schema is not {RECEIPT_SCHEMA}")
    canonical = receipt["canonical"]
    if sha256(scenario_bytes).hexdigest() != canonical["scenarios_sha256"]:
        raise PcodeError("scenarios.json is not the file the receipt's scenarios_sha256 binds")
    scenarios = json.loads(scenario_bytes)
    if scenarios.get("schema") != SCENARIOS_SCHEMA:
        raise PcodeError(f"scenario schema is not {SCENARIOS_SCHEMA}")
    if (scenarios["binary_sha256"] != canonical["binary_sha256"]
            or scenarios["entry_rva"] != canonical["entry_rva"]
            or {scenarios["function"], canonical["function"]} != {FUNCTION_ID}):
        raise PcodeError("receipt and scenarios name different subjects")
    by_id = {scenario["id"]: scenario for scenario in scenarios["scenarios"]}
    traces = {}
    for result in canonical["results"]:
        scenario = by_id.get(result["id"])
        if (scenario is None or scenario["slot"] != result["slot"]
                or scenario["path"] != result["path"]
                or int(scenario["r9d"], 16) != int(result["r9d"], 16)):
            raise PcodeError(f"result {result['id']} does not match its frozen scenario")
        traces[result["id"]] = schema.validate(_trace(receipt, scenarios, scenario, result))
    return traces
