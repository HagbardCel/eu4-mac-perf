#!/usr/bin/env python3
"""TR1: the execution-observation/v1 consumer, native replay and P-code importer.

Handwritten traces are test doubles (evidence ``handwritten-synthetic``). Their
entry state is the generic harness inventory: whole allocations, the stack
region, raw contents, registers and the pointer edges the harness built. The
fixture builder derives every dependency, effect and kept edge from the events;
no test hands it a per-function memory map.

Fixture checks run everywhere over fabricated bases. Native checks record the
reference run and replay the fixture in a fresh process at new bases; they skip
when this host cannot execute x86-64.
"""
from __future__ import annotations

import copy
import json
import shutil
import struct
import subprocess
import tempfile
import unittest
from pathlib import Path

from reconstruction_trace import fixture, pcode, schema
from reconstruction_trace.harness import native

STACK_SIZE = 64
STACK = {"id": "stack", "kind": "stack", "size": STACK_SIZE, "bytes": bytes(range(64)).hex()}
RSP_EDGE = {"from": {"register": "rsp"}, "to": {"region": "stack", "offset": STACK_SIZE}}

FLAG_X, FLAG_Y = 0x1111, 0x2222
B_VALUE, ADDEND = 0x2a, 5
OVERLAP_P = bytes(range(0xa0, 0xb0))
OVERLAP_Q = bytes(range(0x50, 0x58))


def _heap(region_id: str, data: bytes) -> dict:
    return {"id": region_id, "kind": "heap", "size": len(data), "bytes": data.hex()}


def _edge(register: str, region: str, offset: int = 0) -> dict:
    return {"from": {"register": register}, "to": {"region": region, "offset": offset}}


def _stored(region: str, offset: int, target: str) -> dict:
    return {"from": {"region": region, "offset": offset}, "to": {"region": target, "offset": 0}}


def _fabricate(regions: list[dict], pointers: list[dict]) -> dict:
    """The harness inventory at fixed fabricated bases, for host-independent checks."""
    bases = {r["id"]: 0x100000 * (index + 1) for index, r in enumerate(regions)}
    data = {r["id"]: bytearray.fromhex(r["bytes"]) for r in regions}
    registers = {name: "0x0" for name in native.REGISTERS}
    for edge in pointers:
        value = bases[edge["to"]["region"]] + edge["to"]["offset"]
        source = edge["from"]
        if "register" in source:
            registers[source["register"]] = hex(value)
        else:
            data[source["region"]][source["offset"]:source["offset"] + 8] = (
                value.to_bytes(8, "little"))
    return {
        "registers": registers,
        "regions": [{"id": r["id"], "kind": r["kind"], "base": hex(bases[r["id"]]),
                     "size": r["size"], "bytes": data[r["id"]].hex(),
                     "preservation": "required"} for r in regions],
        "pointers": pointers,
    }


class _Inventory:
    def __init__(self, entry_state: dict):
        self.regions = {region["id"]: region for region in entry_state["regions"]}
        self.pc = 0x1000

    def data(self, region: str, offset: int, size: int) -> bytes:
        return bytes.fromhex(self.regions[region]["bytes"])[offset:offset + size]

    def event(self, kind: str, region: str, offset: int, value: bytes) -> dict:
        self.pc += 4
        return {"kind": kind, "pc": hex(self.pc),
                "address": hex(int(self.regions[region]["base"], 16) + offset),
                "size": len(value), "value": value.hex(), "value_source": "observed"}


def _trace(function_id: str, entry_state: dict, events: list[dict], exit_state: dict) -> dict:
    return {
        "schema": schema.SCHEMA, "function_id": function_id, "binary": None,
        "evidence": "handwritten-synthetic",
        "instrumentation": {"name": "handwritten", "version": "v1"},
        "entry_state": entry_state, "events": events, "exit_state": exit_state,
        "completeness": "complete", "incomplete_reasons": [], "unresolved_dependencies": [],
    }


# Each subject: construction request, handwritten events over the recorded inventory,
# and the independently computed result (RAX and region bytes after the call).

def _select_case(flag: int):
    regions = [_heap("p", struct.pack("<IIII", flag, 0xdeadbeef, FLAG_X, FLAG_Y)), STACK]

    def events(inv: _Inventory) -> list[dict]:
        taken = 8 if int.from_bytes(inv.data("p", 0, 4), "little") else 12
        return [inv.event("read", "p", 0, inv.data("p", 0, 4)),
                inv.event("read", "p", taken, inv.data("p", taken, 4))]

    return "synth:select", regions, [_edge("rdi", "p"), RSP_EDGE], events, (
        FLAG_X if flag else FLAG_Y, {})


def _overlap_case():
    regions = [_heap("p", OVERLAP_P), _heap("q", OVERLAP_Q), STACK]

    def events(inv: _Inventory) -> list[dict]:
        first = struct.pack("<I", 0x11223344)
        return [inv.event("write", "p", 0, first),
                inv.event("read", "p", 0, first),
                inv.event("write", "p", 2, struct.pack("<H", 0xbeef)),
                inv.event("read", "p", 0, b"\x44\x33\xef\xbe" + inv.data("p", 4, 4)),
                inv.event("write", "q", 0, first)]

    final_p = b"\x44\x33\xef\xbe" + OVERLAP_P[4:]
    return "synth:overlap", regions, [_edge("rdi", "p"), _edge("rsi", "q"), RSP_EDGE], \
        events, (int.from_bytes(final_p[:8], "little"),
                 {"p": final_p, "q": struct.pack("<I", 0x11223344) + OVERLAP_Q[4:]})


def _chase_case():
    regions = [_heap("a", struct.pack("<QII", 0, ADDEND, 0)),
               _heap("b", struct.pack("<II", B_VALUE, 0)),
               _heap("d", bytes(8)), STACK]
    pointers = [_stored("a", 0, "b"), _edge("rdi", "a"), _stored("d", 0, "b"),
                _edge("rsi", "d"), RSP_EDGE]

    def events(inv: _Inventory) -> list[dict]:
        total = struct.pack("<I", B_VALUE + ADDEND)
        return [inv.event("read", "a", 0, inv.data("a", 0, 8)),
                inv.event("read", "b", 0, inv.data("b", 0, 4)),
                inv.event("read", "a", 8, inv.data("a", 8, 4)),
                inv.event("write", "b", 4, total)]

    result = (B_VALUE + ADDEND) & 0xffffffff
    return "synth:chase", regions, pointers, events, (
        result, {"b": struct.pack("<II", B_VALUE, result)})


def _alias_case(aliased: bool):
    initial = struct.pack("<II", 1, 2)
    regions = [_heap("a", initial), _heap("c", initial), STACK]
    target = "a" if aliased else "c"
    pointers = [_edge("rdi", "a"), _edge("rsi", target, 4), RSP_EDGE]

    def events(inv: _Inventory) -> list[dict]:
        seven = struct.pack("<I", 7)
        read = seven if aliased else inv.data("c", 4, 4)
        return [inv.event("write", "a", 4, seven), inv.event("read", target, 4, read)]

    return "synth:alias", regions, pointers, events, (
        7 if aliased else 2, {"a": struct.pack("<II", 1, 7)})


def _fabricated_trace(case) -> dict:
    function_id, regions, pointers, events, (rax, _) = case
    entry_state = _fabricate(regions, pointers)
    return _trace(function_id, entry_state, events(_Inventory(entry_state)),
                  {"registers": {"rax": hex(rax)}})


def _ranges(fixture_record: dict, key: str) -> list[tuple[str, int, int]]:
    return [(row["region"], row["offset"], row["size"])
            for row in fixture_record["fixture"][key]]


class SchemaTests(unittest.TestCase):
    def setUp(self) -> None:
        self.trace = _fabricated_trace(_chase_case())

    def assertRejected(self, trace: dict) -> None:
        with self.assertRaises(schema.TraceError):
            schema.validate(trace)

    def test_contract_example_is_admitted(self) -> None:
        example = {
            "schema": "execution-observation/v1", "function_id": "synth:chase",
            "binary": None, "evidence": "handwritten-synthetic",
            "instrumentation": {"name": "handwritten", "version": "v1"},
            "entry_state": {
                "registers": {"rdi": "0x100000"},
                "regions": [
                    {"id": "a", "kind": "heap", "base": "0x100000", "size": 16,
                     "bytes": "00002000000000002a00000000000000", "preservation": "required"},
                    {"id": "b", "kind": "heap", "base": "0x200000", "size": 4,
                     "bytes": "2a000000", "preservation": "required"}],
                "pointers": [
                    {"from": {"region": "a", "offset": 0}, "to": {"region": "b", "offset": 0}},
                    {"from": {"register": "rdi"}, "to": {"region": "a", "offset": 0}}]},
            "events": [{"kind": "read", "pc": "0x1000", "address": "0x100000", "size": 8,
                        "value": "0000200000000000", "value_source": "observed"}],
            "exit_state": {"registers": {"rax": "0x2a"}},
            "completeness": "complete", "incomplete_reasons": [], "unresolved_dependencies": [],
        }
        self.assertIs(schema.validate(example), example)

    def test_encoding_is_enforced(self) -> None:
        for bad in (0x100000, "0x0100000", "0X100000", "0xABC", "100000"):
            trace = copy.deepcopy(self.trace)
            trace["events"][0]["address"] = bad
            self.assertRejected(trace)
        trace = copy.deepcopy(self.trace)
        trace["events"][0]["value"] = trace["events"][0]["value"][:-2]
        self.assertRejected(trace)
        trace = copy.deepcopy(self.trace)
        trace["events"][0]["size"] = 8.0
        self.assertRejected(trace)
        trace = copy.deepcopy(self.trace)
        stack = next(r for r in trace["entry_state"]["regions"] if r["id"] == "stack")
        stack["bytes"] = stack["bytes"].upper()
        self.assertRejected(trace)

    def test_contract_closes_keys_evidence_and_event_kinds(self) -> None:
        for edit in (lambda t: t.update(extra=1),
                     lambda t: t.update(evidence="qbdi"),
                     lambda t: t["events"].append({"kind": "branch", "pc": "0x1"}),
                     lambda t: t["events"][0].update(flags="r")):
            trace = copy.deepcopy(self.trace)
            edit(trace)
            self.assertRejected(trace)

    def test_regions_carry_bytes_or_bytes_absent(self) -> None:
        region = self.trace["entry_state"]["regions"][2]
        for edit in (lambda r: r.update(bytes_absent=True), lambda r: r.pop("bytes"),
                     lambda r: r.update(preservation="optional")):
            trace = copy.deepcopy(self.trace)
            edit(next(r for r in trace["entry_state"]["regions"] if r["id"] == region["id"]))
            self.assertRejected(trace)

    def test_call_frames_nest_with_matching_targets(self) -> None:
        enter = {"kind": "call_enter", "pc": "0x2000", "target": "0x3000"}
        empty = copy.deepcopy(self.trace)
        empty["events"][1:1] = [enter, {"kind": "call_exit", "target": "0x3000"}]
        self.assertRejected(empty)
        with self.assertRaises(schema.TraceError):
            fixture.qualify(empty)
        covered = copy.deepcopy(self.trace)
        covered["events"][0:0] = [enter]
        covered["events"].insert(2, {"kind": "call_exit", "target": "0x3000"})
        schema.validate(covered)
        explicit = copy.deepcopy(self.trace)
        explicit["events"][1:1] = [enter, {"kind": "call_exit", "target": "0x3000",
                                           "effects_complete": True, "effects": []}]
        schema.validate(explicit)
        for tail in ([{"kind": "call_exit", "target": "0x3001"}], []):
            trace = copy.deepcopy(self.trace)
            trace["events"][1:1] = [enter, *tail]
            self.assertRejected(trace)
        trace = copy.deepcopy(self.trace)
        trace["events"].insert(0, {"kind": "call_exit", "target": "0x3000"})
        self.assertRejected(trace)

    def test_completeness_invariants(self) -> None:
        trace = copy.deepcopy(self.trace)
        trace["incomplete_reasons"] = ["dropped event"]
        self.assertRejected(trace)
        trace = copy.deepcopy(self.trace)
        trace["unresolved_dependencies"] = [{"address": "0x1", "size": 1, "reason": "x"}]
        self.assertRejected(trace)
        trace = copy.deepcopy(self.trace)
        del trace["events"][0]["value_source"]
        self.assertRejected(trace)
        trace = copy.deepcopy(self.trace)
        del trace["events"][0]["value"], trace["events"][0]["value_source"]
        self.assertRejected(trace)
        trace["completeness"] = "incomplete"
        self.assertRejected(trace)
        trace["incomplete_reasons"] = ["value unknown"]
        schema.validate(trace)
        trace = copy.deepcopy(self.trace)
        trace["events"][0]["value_source"] = "derived-scenario"
        self.assertRejected(trace)
        trace["events"][0]["provenance"] = "scenario field"
        schema.validate(trace)

    def test_disagreeing_overlapping_regions_are_not_complete(self) -> None:
        trace = copy.deepcopy(self.trace)
        a = trace["entry_state"]["regions"][0]
        trace["entry_state"]["regions"].append(
            {"id": "a2", "kind": "heap", "base": a["base"], "size": 4, "bytes": "ffffffff"})
        self.assertRejected(trace)
        trace["entry_state"]["regions"][-1]["bytes"] = a["bytes"][:8]
        schema.validate(trace)


class FixtureTests(unittest.TestCase):
    def test_conditional_read_derives_a_different_dependency_per_branch(self) -> None:
        taken = fixture.qualify(_fabricated_trace(_select_case(1)))
        fallthrough = fixture.qualify(_fabricated_trace(_select_case(0)))
        self.assertTrue(taken["fixture_qualified"], taken["reasons"])
        self.assertTrue(fallthrough["fixture_qualified"], fallthrough["reasons"])
        self.assertEqual(_ranges(taken, "dependencies"), [("p", 0, 4), ("p", 8, 4)])
        self.assertEqual(_ranges(fallthrough, "dependencies"), [("p", 0, 4), ("p", 12, 4)])

    def test_read_after_write_is_not_a_new_initial_value(self) -> None:
        result = fixture.qualify(_fabricated_trace(_overlap_case()))
        self.assertTrue(result["fixture_qualified"], result["reasons"])
        # Two reads of p cover [0, 8); only [4, 8) was read before this invocation wrote it.
        self.assertEqual(_ranges(result, "dependencies"), [("p", 4, 4)])
        self.assertEqual(result["fixture"]["dependencies"][0]["bytes"], OVERLAP_P[4:8].hex())

    def test_partial_overlapping_write_is_represented(self) -> None:
        result = fixture.qualify(_fabricated_trace(_overlap_case()))
        effects = {row["region"]: row for row in result["fixture"]["effects"]}
        self.assertEqual((effects["p"]["offset"], effects["p"]["size"]), (0, 4))
        self.assertEqual(effects["p"]["bytes"], "4433efbe")
        self.assertEqual(effects["q"]["bytes"], "44332211")
        self.assertNotIn("FUNCTION-CAPTURE", json.dumps(result))

    def test_write_only_destination_cannot_satisfy_preservation_from_unknown_bytes(self) -> None:
        trace = _fabricated_trace(_overlap_case())
        q = next(r for r in trace["entry_state"]["regions"] if r["id"] == "q")
        del q["bytes"]
        q["bytes_absent"] = True
        result = fixture.qualify(trace)
        self.assertFalse(result["fixture_qualified"])
        self.assertIsNone(result["fixture"])
        self.assertEqual(
            [(d["address"], d["size"]) for d in result["unresolved_dependencies"]],
            [(hex(int(q["base"], 16) + 4), 4)])
        q["preservation"] = "excluded"
        result = fixture.qualify(trace)
        self.assertTrue(result["fixture_qualified"], result["reasons"])
        self.assertNotIn("q", {row["region"] for row in result["fixture"]["effects"]})

    def test_missing_input_region_fails_with_address_and_size(self) -> None:
        trace = _fabricated_trace(_chase_case())
        b = next(r for r in trace["entry_state"]["regions"] if r["id"] == "b")
        trace["entry_state"]["regions"].remove(b)
        trace["entry_state"]["pointers"] = [
            edge for edge in trace["entry_state"]["pointers"] if edge["to"]["region"] != "b"]
        result = fixture.qualify(trace)
        self.assertFalse(result["fixture_qualified"])
        missing = {"address": b["base"], "size": 4, "reason": "missing input region"}
        self.assertIn(missing, result["unresolved_dependencies"])
        self.assertIn(f"missing input region: address {b['base']} size 4", result["reasons"])

    def test_aliases_follow_allocation_identity_not_byte_equality(self) -> None:
        aliased = fixture.qualify(_fabricated_trace(_alias_case(True)))
        self.assertTrue(aliased["fixture_qualified"], aliased["reasons"])
        targets = {edge["from"]["register"]: (edge["to"]["region"], edge["to"]["offset"])
                   for edge in aliased["fixture"]["pointers"] if edge["from"]["register"] != "rsp"}
        self.assertEqual(targets, {"rdi": ("a", 0), "rsi": ("a", 4)})
        self.assertEqual(aliased["fixture"]["dependencies"], [])
        separate = fixture.qualify(_fabricated_trace(_alias_case(False)))
        self.assertTrue(separate["fixture_qualified"], separate["reasons"])
        self.assertEqual(_ranges(separate, "dependencies"), [("c", 4, 4)])

    def test_only_used_pointer_edges_are_kept(self) -> None:
        result = fixture.qualify(_fabricated_trace(_chase_case()))
        self.assertTrue(result["fixture_qualified"], result["reasons"])
        self.assertEqual(result["fixture"]["pointers"],
                         [_stored("a", 0, "b"), _edge("rdi", "a"), RSP_EDGE])

    def test_incomplete_trace_never_becomes_a_fixture(self) -> None:
        trace = _fabricated_trace(_chase_case())
        trace["completeness"] = "incomplete"
        trace["incomplete_reasons"] = ["an event was dropped"]
        result = fixture.qualify(trace)
        self.assertFalse(result["fixture_qualified"])
        self.assertIsNone(result["fixture"])

    def test_pointer_edge_must_match_the_recorded_value(self) -> None:
        trace = copy.deepcopy(_fabricated_trace(_chase_case()))
        edge = next(item for item in trace["entry_state"]["pointers"]
                    if item["from"].get("region") == "a")
        edge["to"] = {"region": "stack", "offset": 0}
        with self.assertRaises(schema.TraceError):
            fixture.qualify(trace)

    def test_pointer_source_windows_overlap_by_absolute_address(self) -> None:
        trace = copy.deepcopy(_fabricated_trace(_chase_case()))
        entry = trace["entry_state"]
        original = next(region for region in entry["regions"] if region["id"] == "a")
        alias = dict(original)
        alias["id"] = "alias"
        entry["regions"].append(alias)
        entry["pointers"].append(_stored("alias", 0, "b"))
        with self.assertRaises(schema.TraceError):
            fixture.qualify(trace)

    def test_excluded_stack_write_is_not_an_observable_effect(self) -> None:
        entry = _fabricate([STACK], [RSP_EDGE])
        stack = entry["regions"][0]
        stack["preservation"] = "excluded"
        inv = _Inventory(entry)
        written = b"\x11" * 8
        events = [inv.event("write", "stack", 16, written),
                  inv.event("read", "stack", 16, written)]
        result = fixture.qualify(_trace("synth:stack", entry, events,
                                         {"registers": {"rax": "0x0"}}))
        self.assertTrue(result["fixture_qualified"], result["reasons"])
        self.assertEqual(result["fixture"]["effects"], [])
        self.assertEqual(result["fixture"]["dependencies"], [])

    def test_excluded_stack_read_of_absent_bytes_does_not_qualify(self) -> None:
        entry = _fabricate([STACK], [RSP_EDGE])
        stack = entry["regions"][0]
        value = bytes.fromhex(stack["bytes"])[16:24]
        inv = _Inventory(entry)
        events = [inv.event("read", "stack", 16, value)]
        stack["preservation"] = "excluded"
        del stack["bytes"]
        stack["bytes_absent"] = True
        result = fixture.qualify(_trace("synth:stack", entry, events,
                                         {"registers": {"rax": "0x0"}}))
        self.assertFalse(result["fixture_qualified"])
        self.assertIsNone(result["fixture"])
        self.assertIn({"address": hex(int(stack["base"], 16) + 16), "size": 8,
                       "reason": "read of absent initial bytes"},
                      result["unresolved_dependencies"])

    def test_read_value_must_match_recorded_state(self) -> None:
        trace = _fabricated_trace(_chase_case())
        trace["events"][1]["value"] = "ffffffff"
        self.assertFalse(fixture.qualify(trace)["fixture_qualified"])


class NativeReplayTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.workdir = Path(tempfile.mkdtemp(prefix="eu4-tr1-"))
        try:
            cls.harness = native.load(cls.workdir)
        except native.Unavailable as reason:
            shutil.rmtree(cls.workdir, ignore_errors=True)
            raise unittest.SkipTest(str(reason))

    @classmethod
    def tearDownClass(cls) -> None:
        shutil.rmtree(cls.workdir, ignore_errors=True)

    def _record(self, case) -> tuple[dict, dict]:
        function_id, regions, pointers, events, _ = case
        recorded = self.harness.record(function_id, regions, pointers, {}, ["rax"])
        trace = _trace(function_id, recorded["entry_state"],
                       events(_Inventory(recorded["entry_state"])), recorded["exit_state"])
        result = fixture.qualify(trace)
        self.assertTrue(result["fixture_qualified"], result["reasons"])
        return trace, result["fixture"]

    def _assert_replay_matches(self, case) -> tuple[dict, dict, dict]:
        """Record, build, replay fresh; check against the fixture and the independent result."""
        trace, built = self._record(case)
        replayed = self.harness.replay(built)
        self.assertEqual(native.compare(built, replayed), [])
        rax, final = case[-1]
        self.assertEqual(int(replayed["registers"]["rax"], 16), rax)
        for region_id, data in final.items():
            self.assertEqual(bytes.fromhex(replayed["regions"][region_id]), data)
        for region in trace["entry_state"]["regions"]:
            self.assertNotEqual(replayed["bases"][region["id"]], int(region["base"], 16))
        return trace, built, replayed

    def test_fresh_replay_matches_independent_result_on_both_branches(self) -> None:
        self._assert_replay_matches(_select_case(1))
        self._assert_replay_matches(_select_case(0))

    def test_fresh_replay_of_overlapping_writes(self) -> None:
        self._assert_replay_matches(_overlap_case())

    def test_aliased_pointers_replay_into_one_allocation(self) -> None:
        _, _, replayed = self._assert_replay_matches(_alias_case(True))
        self.assertEqual(int(replayed["registers"]["rax"], 16), 7)
        _, _, separate = self._assert_replay_matches(_alias_case(False))
        self.assertEqual(int(separate["registers"]["rax"], 16), 2)

    def test_heap_pointer_between_allocations_is_relocated(self) -> None:
        trace, built, replayed = self._assert_replay_matches(_chase_case())
        original = {r["id"]: int(r["base"], 16) for r in trace["entry_state"]["regions"]}
        self.assertNotEqual(replayed["bases"]["a"], original["a"])
        self.assertNotEqual(replayed["bases"]["b"], original["b"])
        stored = int.from_bytes(bytes.fromhex(replayed["entry"]["a"])[:8], "little")
        self.assertEqual(stored, replayed["bases"]["b"])
        # d's unused stored address is not a kept edge, so it is copied unchanged.
        self.assertEqual(replayed["entry"]["d"],
                         next(r for r in trace["entry_state"]["regions"]
                              if r["id"] == "d")["bytes"])

        unrelocated = copy.deepcopy(built)
        unrelocated["pointers"] = [edge for edge in built["pointers"]
                                   if "register" in edge["from"]]
        try:
            replayed = self.harness.replay(unrelocated)
        except native.HarnessError:
            return
        self.assertNotEqual(native.compare(unrelocated, replayed), [])
        self.assertNotEqual(int(replayed["registers"]["rax"], 16), B_VALUE + ADDEND)

    def test_extra_subject_is_called_through_the_entry_stub(self) -> None:
        (self.workdir / "extra").mkdir()
        source = self.workdir / "fifth.S"
        source.write_text(
            ".section __TEXT,__text\n"
            ".globl _trace_subject_fifth\n"
            "_trace_subject_fifth:\n"
            "    movq %rdi, %rax\n"
            "    incq %rax\n"
            "    ret\n")
        extra = native.load(self.workdir / "extra", (source,))
        exported = subprocess.run(["nm", str(extra.binary)], capture_output=True, text=True,
                                  check=True)
        self.assertIn("T _trace_subject_fifth", exported.stdout)
        recorded = extra.record("synth:fifth", [STACK], [RSP_EDGE], {"rdi": "0x2a"}, ["rax"])
        self.assertEqual(recorded["exit_state"]["registers"]["rax"], "0x2b")
        built = fixture.qualify(_trace("synth:fifth", recorded["entry_state"], [],
                                        recorded["exit_state"]))
        self.assertTrue(built["fixture_qualified"], built["reasons"])
        replayed = extra.replay(built["fixture"])
        self.assertEqual(native.compare(built["fixture"], replayed), [])
        self.assertEqual(replayed["registers"]["rax"], "0x2b")
        for name in ("missing", "printf"):
            with self.assertRaises(native.HarnessError) as caught:
                extra.record(f"synth:{name}", [STACK], [RSP_EDGE], {}, ["rax"])
            self.assertIn("synthetic subject symbol was not found", str(caught.exception))


class PcodeImportTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.traces = pcode.import_receipt()
        cls.receipt = json.loads(pcode.RECEIPT_PATH.read_text())

    def test_scenario_sha_binds_the_scenario_file(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            altered = Path(directory) / "scenarios.json"
            altered.write_bytes(pcode.SCENARIOS_PATH.read_bytes() + b"\n")
            with self.assertRaises(pcode.PcodeError):
                pcode.import_receipt(scenarios_path=altered)

    def test_every_result_becomes_one_ghidra_pcode_trace(self) -> None:
        ids = [result["id"] for result in self.receipt["canonical"]["results"]]
        self.assertEqual(list(self.traces), ids)
        for trace in self.traces.values():
            self.assertEqual(trace["evidence"], "ghidra-pcode")
            schema.validate(trace)

    def test_receipt_values_are_observed_and_scenario_loads_derived(self) -> None:
        results = {r["id"]: r for r in self.receipt["canonical"]["results"]}
        for scenario_id, trace in self.traces.items():
            writes = [e for e in trace["events"] if e["kind"] == "write"]
            self.assertEqual([(e["address"], e["size"], e["value"]) for e in writes],
                             [(hex(int(a, 16)), int(n), v)
                              for _, a, n, v in results[scenario_id]["stores"]])
            self.assertTrue(all(e["value_source"] == "observed" for e in writes))
            for read in (e for e in trace["events"] if e["kind"] == "read" and "value" in e):
                self.assertEqual(read["value_source"], "derived-scenario")
                self.assertTrue(read["provenance"])
        reads = {e["address"]: e for e in self.traces["s01"]["events"] if e["kind"] == "read"}
        self.assertEqual(reads["0x300000000"]["value"], "0d000000")
        self.assertIn("buffer_first_dword", reads["0x300000000"]["provenance"])
        self.assertEqual(reads["0x4000000f8"]["value"], "5758595a5b5c5d4d")
        self.assertIn("store", reads["0x4000000f8"]["provenance"])
        self.assertEqual(reads["0x400000100"]["value"], "28292a2b2c2d2e2f")
        self.assertIn("stack_window_before", reads["0x400000100"]["provenance"])
        s13_reads = [e["address"] for e in self.traces["s13"]["events"] if e["kind"] == "read"]
        self.assertNotIn("0x300000000", s13_reads)

    def test_unreconstructable_load_stays_incomplete_and_is_never_a_fixture(self) -> None:
        for trace in self.traces.values():
            handle = next(e for e in trace["events"] if e["address"] == "0x200010000")
            self.assertNotIn("value", handle)
            self.assertEqual(trace["completeness"], "incomplete")
            self.assertEqual([(d["address"], d["size"])
                              for d in trace["unresolved_dependencies"]], [("0x200010000", 8)])
            result = fixture.qualify(trace)
            self.assertFalse(result["fixture_qualified"])
            self.assertIsNone(result["fixture"])


if __name__ == "__main__":
    unittest.main()
