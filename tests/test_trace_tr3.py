#!/usr/bin/env python3
"""TR3 adapter and the controlled one-shot subject.

Synthetic observations are test doubles. The committed observation is a Rosetta
capture of this package's subject, not an EU4 trace. Hosts other than the
certifying Mac check that artifact; that check is not a fresh native execution.
Nothing here launches EU4.
"""
from __future__ import annotations

import copy
import hashlib
import json
import platform
import tempfile
import unittest
from pathlib import Path

from reconstruction_trace import fixture, schema
from reconstruction_trace.tr3 import adapter, image, native, observe
from reverse_engineering.macho_rva import build_synthetic_macho_64

ROOT = Path(__file__).resolve().parents[1]
CONTEXT_SIZE = 0x130
HANDLE_BASE = 0x100000
CONTEXT_BASE = 0x200000
BUFFER_BASE = 0x300000
FIRST_DWORD = 0x11
FUNCTION_ID = "_GfxSetConstantBuffer"


def _words_from_buffer(first: int) -> int:
    return ((first + 3) & 0xffffffff) >> 2


def _words_from_selector(selector: int) -> int:
    signed = int.from_bytes(((selector + 3) & 0xffffffff).to_bytes(4, "little"),
                            "little", signed=True)
    magnitude = abs(signed) // 4
    return (-magnitude if signed < 0 else magnitude) & 0xffffffff


def _heap(region_id: str, base: int, data: bytes, preservation: str = "required") -> dict:
    return {"id": region_id, "kind": "heap", "base": hex(base), "size": len(data),
            "bytes": data.hex(), "preservation": preservation}


def _bind(regions: list[dict], pointers: list[dict]) -> dict[str, str]:
    data = {region["id"]: bytearray.fromhex(region["bytes"]) for region in regions}
    bases = {region["id"]: int(region["base"], 16) for region in regions}
    registers = {}
    for edge in pointers:
        target = bases[edge["to"]["region"]] + edge["to"]["offset"]
        source = edge["from"]
        if "register" in source:
            registers[source["register"]] = hex(target)
        else:
            data[source["region"]][source["offset"]:source["offset"] + 8] = (
                target.to_bytes(8, "little"))
    for region in regions:
        region["bytes"] = data[region["id"]].hex()
    return registers


def _event(kind: str, base: int, offset: int, value: bytes, pc: int) -> dict:
    return {"kind": kind, "pc": hex(pc), "address": hex(base + offset),
            "size": len(value), "value": value.hex(), "value_source": "observed"}


def _trace(regions, pointers, events, registers, rax: int | None) -> dict:
    exit_registers = {} if rax is None else {"rax": hex(rax)}
    return {
        "schema": schema.SCHEMA, "function_id": FUNCTION_ID, "binary": None,
        "evidence": "handwritten-synthetic",
        "instrumentation": {"name": "handwritten", "version": "v1"},
        "entry_state": {"registers": registers, "regions": regions, "pointers": pointers},
        "events": events, "exit_state": {"registers": exit_registers},
        "completeness": "complete", "incomplete_reasons": [], "unresolved_dependencies": [],
    }


def _invocation(*, slot: int, selector: int = 0xffffffff, read_buffer: bool = True,
                context_size: int = CONTEXT_SIZE, context_base: int = CONTEXT_BASE,
                buffer_base: int | None = BUFFER_BASE, buffer_size: int = 4,
                preservation: str = "required", rax: int | None = None,
                same_region: bool = False, first_dword: int = FIRST_DWORD,
                stored_pointer: int | None = None) -> dict:
    """One synthetic `_GfxSetConstantBuffer` observation."""
    context = bytearray((index + 1) & 0xff for index in range(context_size))
    regions = [_heap("handle", HANDLE_BASE, bytes(8))]
    pointers = [
        {"from": {"register": "rdi"}, "to": {"region": "handle", "offset": 0}},
        {"from": {"region": "handle", "offset": 0}, "to": {"region": "ctx", "offset": 0}},
    ]
    if same_region:
        regions.append(_heap("ctx", context_base, bytes(context), preservation))
        pointers.append({"from": {"register": "rsi"}, "to": {"region": "ctx", "offset": 0}})
        buffer_at = context_base
        buffer_id = "ctx"
        buffer_offset = 0
    else:
        if buffer_base is None:
            buffer_base = BUFFER_BASE
        if buffer_base < context_base + context_size and buffer_base + buffer_size > context_base:
            overlap = max(0, min(context_base + context_size, buffer_base + buffer_size) - buffer_base)
            start = buffer_base - context_base
            buffer = bytearray(context[start:start + overlap])
            buffer.extend((index + 7) & 0xff for index in range(buffer_size - overlap))
        else:
            buffer = bytearray(first_dword.to_bytes(4, "little"))
            buffer.extend(bytes(buffer_size - 4))
        regions.append(_heap("ctx", context_base, bytes(context), preservation))
        regions.append(_heap("buf", buffer_base, bytes(buffer)))
        pointers.append({"from": {"register": "rsi"}, "to": {"region": "buf", "offset": 0}})
        buffer_at = buffer_base
        buffer_id = "buf"
        buffer_offset = 0
    registers = _bind(regions, pointers)
    registers["rcx"] = hex(slot)
    registers["r9"] = hex(selector)
    by_id = {region["id"]: region for region in regions}
    bases = {region["id"]: int(region["base"], 16) for region in regions}
    events = [_event("read", bases["handle"], 0, bytes.fromhex(by_id["handle"]["bytes"]), 0x1000)]
    if read_buffer:
        data = bytes.fromhex(by_id[buffer_id]["bytes"])
        events.append(_event("read", bases[buffer_id], buffer_offset, data[buffer_offset:buffer_offset + 4],
                             0x1004))
    if selector == 0xffffffff:
        words = _words_from_buffer(int.from_bytes(
            bytes.fromhex(by_id[buffer_id]["bytes"])[buffer_offset:buffer_offset + 4], "little"))
    else:
        words = _words_from_selector(selector)
    pointer = buffer_at if stored_pointer is None else stored_pointer
    word_at = 0xc8 + 16 * (slot if slot < 6 else 0)
    pointer_at = 0xd0 + 16 * (slot if slot < 6 else 0)
    events.extend([
        _event("write", context_base, word_at, words.to_bytes(4, "little"), 0x1008),
        _event("write", context_base, pointer_at, pointer.to_bytes(8, "little"), 0x100c),
        _event("write", context_base, 0x128, b"\x01", 0x1010),
    ])
    return _trace(regions, pointers, events, registers,
                  context_base if rax is None else rax)


def _candidate(prepared: dict, mutate=None) -> dict:
    post = bytearray.fromhex(prepared["expected_post"])
    if mutate is not None:
        mutate(post)
    return {"rax": "ctx", "buffer": "ARG", "post": post.hex()}


class TraceTr3Tests(unittest.TestCase):
    def test_dropped_access_prevents_qualification(self) -> None:
        source = _invocation(slot=0)
        self.assertEqual(source["completeness"], "complete")
        traced = observe.apply_accounting(source, dropped_accesses=1)
        self.assertEqual(source["completeness"], "complete")
        self.assertEqual(traced["completeness"], "incomplete")
        self.assertEqual(traced["incomplete_reasons"],
                         ["events dropped: 0 instructions, 1 accesses, 0 transfers"])
        self.assertFalse(fixture.qualify(traced)["fixture_qualified"])

    def test_explicit_length_refuses_missing_pointer_role(self) -> None:
        # The stored dword equals the context base, which is already in the fixture.
        trace = _invocation(slot=1, selector=0x10, read_buffer=False,
                            stored_pointer=CONTEXT_BASE)
        qualified = fixture.qualify(trace)
        self.assertTrue(qualified["fixture_qualified"])
        kept = [edge["from"].get("register") for edge in qualified["fixture"]["pointers"]]
        self.assertNotIn("rsi", kept)
        prepared = adapter.prepare_scenario(trace)
        self.assertEqual(prepared["refusal"], "unauthenticated-buffer-pointer")
        self.assertEqual(adapter.words_from_selector(0x10), 4)
        self.assertEqual(adapter.words_from_selector(0x7ffffffd), 0xe0000000)

    def test_select_one_keeps_the_first_candidate(self) -> None:
        first = {"id": "selected", "completeness": "incomplete"}
        later = {"id": "eligible", "completeness": "complete"}
        self.assertIs(observe.select_one([first, later]), first)

    def test_buffer_read_slots(self) -> None:
        for slot in range(6):
            trace = _invocation(slot=slot)
            qualified = fixture.qualify(trace)
            self.assertTrue(qualified["fixture_qualified"], slot)
            effects = [(row["offset"], row["size"])
                       for row in qualified["fixture"]["effects"]]
            if slot == 5:
                self.assertEqual(effects, [(0x118, 4), (0x120, 9)])
            else:
                self.assertEqual(effects, [(0xc8 + 16 * slot, 4), (0xd0 + 16 * slot, 8),
                                           (0x128, 1)])
            prepared = adapter.prepare_scenario(trace)
            self.assertIsNone(prepared["refusal"], slot)
            self.assertEqual(prepared["scenario"]["command"],
                             f"scenario {slot} ffffffff 00000011")
            self.assertEqual(len(bytes.fromhex(prepared["scenario"]["prestate"])), CONTEXT_SIZE)
            self.assertIsNone(adapter.compare_scenario(prepared, _candidate(prepared)))

    def test_native_identity(self) -> None:
        target = json.loads((ROOT / "reverse_engineering" / "target.json").read_text())
        window = target["code_windows"][FUNCTION_ID]
        binary = {"sha256": target["sha256"], "image_base": "0x100000000",
                  "entry_rva": window["entry_rva"]}
        trace = _invocation(slot=0)
        trace["evidence"] = "qbdi-rosetta"
        trace["binary"] = binary
        self.assertIsNone(adapter.prepare_scenario(trace)["refusal"])
        for field, value in (("sha256", "0" * 64), ("entry_rva", "0x1"),
                             ("image_base", "0x200000000")):
            mutated = copy.deepcopy(trace)
            mutated["binary"][field] = value
            self.assertEqual(adapter.prepare_scenario(mutated)["refusal"],
                             "unauthenticated-target", field)
        mutated = copy.deepcopy(trace)
        mutated["function_id"] = "other"
        self.assertEqual(adapter.prepare_scenario(mutated)["refusal"], "unauthenticated-target")

    def test_synthetic_function_id(self) -> None:
        trace = _invocation(slot=0)
        trace["function_id"] = "other"
        self.assertEqual(adapter.prepare_scenario(trace)["refusal"], "unauthenticated-target")

    def test_buffer_dword_padding(self) -> None:
        padded = {0: "00000000", 1: "00000001", 0x100: "00000100", 0xffffffff: "ffffffff"}
        for first, text in padded.items():
            trace = _invocation(slot=0, first_dword=first)
            prepared = adapter.prepare_scenario(trace)
            self.assertIsNone(prepared["refusal"], hex(first))
            self.assertEqual(prepared["scenario"]["command"], f"scenario 0 ffffffff {text}")
        self.assertEqual(adapter.words_from_buffer(0xffffffff), 0)
        wrapped = fixture.qualify(_invocation(slot=0, first_dword=0xffffffff))
        word = next(row for row in wrapped["fixture"]["effects"] if row["offset"] == 0xc8)
        self.assertEqual(word["bytes"], "00000000")

    def test_explicit_length_refuses_observed_buffer_read(self) -> None:
        trace = _invocation(slot=1, selector=0x10, read_buffer=True)
        qualified = fixture.qualify(trace)
        self.assertTrue(qualified["fixture_qualified"])
        kept = [edge["from"].get("register") for edge in qualified["fixture"]["pointers"]]
        self.assertIn("rsi", kept)
        self.assertEqual(adapter.prepare_scenario(trace)["refusal"], "unsupported-buffer-read")

    def test_alias_overlap(self) -> None:
        overlapping = _invocation(slot=0, buffer_base=CONTEXT_BASE + 0x100, buffer_size=0x40)
        self.assertTrue(fixture.qualify(overlapping)["fixture_qualified"])
        self.assertEqual(adapter.prepare_scenario(overlapping)["refusal"], "unsupported-alias")
        inside = _invocation(slot=0, same_region=True)
        self.assertTrue(fixture.qualify(inside)["fixture_qualified"])
        self.assertEqual(adapter.prepare_scenario(inside)["refusal"], "unsupported-alias")

    def test_compare_rejects_mutated_candidate_post(self) -> None:
        prepared = adapter.prepare_scenario(_invocation(slot=0))
        self.assertIsNone(prepared["refusal"])
        self.assertIsNone(adapter.compare_scenario(prepared, _candidate(prepared)))

        def flip_pointer(post: bytearray) -> None:
            post[prepared["pointer_offset"]] ^= 0xff

        self.assertIsNone(adapter.compare_scenario(prepared, _candidate(prepared, flip_pointer)))

        def flip_written(post: bytearray) -> None:
            post[0x128] ^= 0xff

        self.assertEqual(adapter.compare_scenario(prepared, _candidate(prepared, flip_written)),
                         "post-byte 0x128")

        def flip_preserved(post: bytearray) -> None:
            post[0] ^= 0xff

        self.assertEqual(adapter.compare_scenario(prepared, _candidate(prepared, flip_preserved)),
                         "post-byte 0x0")
        wrong_rax = _candidate(prepared)
        wrong_rax["rax"] = "0x1"
        self.assertEqual(adapter.compare_scenario(prepared, wrong_rax), "rax")

    def test_slot_out_of_domain(self) -> None:
        trace = _invocation(slot=6)
        self.assertTrue(fixture.qualify(trace)["fixture_qualified"])
        self.assertEqual(adapter.prepare_scenario(trace)["refusal"], "unsupported-slot")

    def test_exit_rax_must_be_the_context(self) -> None:
        missing = _invocation(slot=0, rax=CONTEXT_BASE)
        missing["exit_state"]["registers"].pop("rax")
        self.assertTrue(fixture.qualify(missing)["fixture_qualified"])
        self.assertEqual(adapter.prepare_scenario(missing)["refusal"], "unauthenticated-rax")
        wrong = _invocation(slot=0, rax=CONTEXT_BASE + 1)
        self.assertTrue(fixture.qualify(wrong)["fixture_qualified"])
        self.assertEqual(adapter.prepare_scenario(wrong)["refusal"], "unauthenticated-rax")

    def test_prestate_must_be_exact_context(self) -> None:
        for size in (CONTEXT_SIZE - 1, CONTEXT_SIZE + 1):
            trace = _invocation(slot=0, context_size=size)
            self.assertTrue(fixture.qualify(trace)["fixture_qualified"], size)
            prepared = adapter.prepare_scenario(trace)
            self.assertEqual(prepared["refusal"], "incomplete-prestate", size)
            self.assertIsNone(prepared["scenario"])
        excluded = _invocation(slot=0, preservation="excluded")
        self.assertTrue(fixture.qualify(excluded)["fixture_qualified"])
        self.assertEqual(adapter.prepare_scenario(excluded)["refusal"], "incomplete-prestate")

    def test_image_checks_file_and_window_independently(self) -> None:
        code = bytes(range(16))
        image_base, fileoff, end = 0x100000000, 0x200, 8
        payload = build_synthetic_macho_64(image_base=image_base, segment_fileoff=fileoff,
                                           code_bytes=code)
        window_hash = hashlib.sha256(code[:end]).hexdigest()

        def pin(blob: bytes, window: str) -> dict:
            return {"file_sha256": hashlib.sha256(blob).hexdigest(), "window_sha256": window,
                    "image_base": image_base, "entry_rva": 0, "end_rva": end}

        def check(blob: bytes, expected: dict) -> str | None:
            path = Path(tempfile.mkstemp(suffix=".macho")[1])
            path.write_bytes(blob)
            try:
                return image.check(path, expected)
            finally:
                path.unlink(missing_ok=True)

        self.assertIsNone(check(payload, pin(payload, window_hash)))
        outside = bytearray(payload)
        outside[fileoff + end] ^= 0xff
        self.assertEqual(check(bytes(outside), pin(payload, window_hash)), "file hash mismatch")
        inside = bytearray(code)
        inside[0] ^= 0xff
        changed = build_synthetic_macho_64(image_base=image_base, segment_fileoff=fileoff,
                                           code_bytes=bytes(inside))
        self.assertEqual(check(changed, pin(changed, window_hash)), "window hash mismatch")

    def test_gaps_stay_on_the_trace(self) -> None:
        def run(instruction: dict, dropped: int = 0) -> dict:
            return {
                "failure": None, "ran": True, "entered": True, "memory_recording": True,
                "entry_gpr": {name: "0x0" for name in native.ENTRY_REGISTERS},
                "exit_gpr": {"rax": "0x0"}, "regions": [], "pointers": [],
                "instructions": [instruction], "exec_transfers": [],
                "dropped_instructions": dropped, "dropped_accesses": 0,
                "dropped_transfers": 0,
            }

        missing = native.observation_from_run(run({
            "pc": "0x10", "next_pc": "0x11", "inst_size": 1, "disassembly": "mov",
            "may_load": True, "may_store": False, "load_size": 0, "store_size": 0,
            "is_call": False, "is_return": False, "accesses": [],
        }), "0.12.1")
        self.assertEqual(missing["completeness"], "incomplete")
        self.assertIn("mayLoad without a recorded read", missing["incomplete_reasons"][0])
        self.assertFalse(fixture.qualify(missing)["fixture_qualified"])

        dropped = native.observation_from_run(run({
            "pc": "0x10", "next_pc": "0x11", "inst_size": 1, "disassembly": "nop",
            "may_load": False, "may_store": False, "load_size": 0, "store_size": 0,
            "is_call": False, "is_return": False, "accesses": [],
        }, dropped=1), "0.12.1")
        self.assertEqual(dropped["incomplete_reasons"],
                         ["events dropped: 1 instructions, 0 accesses, 0 transfers"])
        self.assertFalse(fixture.qualify(dropped)["fixture_qualified"])

    def _assert_controlled_capture(self, trace: dict, receipt: dict) -> None:
        qualified = fixture.qualify(trace)
        self.assertTrue(qualified["fixture_qualified"], qualified["reasons"])
        self.assertEqual(adapter.prepare_scenario(trace)["refusal"], "unauthenticated-target")
        self.assertEqual(trace["function_id"], native.FUNCTION_ID)
        self.assertIsNone(trace["binary"])
        self.assertEqual(trace["evidence"], "qbdi-rosetta")
        self.assertEqual(trace["completeness"], "complete")
        decoy = int(receipt["decoy_address"], 16)
        self.assertEqual(decoy, native.DECOY_ADDRESS)
        regions = {region["id"]: region for region in trace["entry_state"]["regions"]}
        self.assertEqual(bytes.fromhex(regions["cell"]["bytes"]), decoy.to_bytes(8, "little"))
        targets = []
        for edge in trace["entry_state"]["pointers"]:
            region = regions[edge["to"]["region"]]
            targets.append(int(region["base"], 16) + edge["to"]["offset"])
        self.assertNotIn(decoy, targets)
        for edge in qualified["fixture"]["pointers"]:
            region = regions[edge["to"]["region"]]
            self.assertNotEqual(int(region["base"], 16) + edge["to"]["offset"], decoy)
        hook = receipt["hook"]
        self.assertEqual(hook["count"], 1)
        self.assertEqual(hook["qbdi_invocations"], 1)
        self.assertEqual(hook["native_call_count"], 2)
        self.assertTrue(hook["restored_before_qbdi"])
        self.assertTrue(hook["entry_bytes_match_after"])
        self.assertEqual(int(hook["first_result"], 16), decoy + 1)
        self.assertEqual(int(hook["second_result"], 16), decoy + 2)
        self.assertEqual(receipt["drops"], {"instructions": 0, "accesses": 0, "transfers": 0})
        self.assertEqual(receipt["completeness"], "complete")
        self.assertEqual(receipt["host"]["rosetta"]["sysctl.proc_translated"], 1)
        self.assertEqual(receipt["qbdi"]["version"], "0.12.1")
        self.assertTrue(receipt["toolchain"]["clang"])
        self.assertEqual(receipt["host"]["probe_architecture"], "x86_64")
        for event in trace["events"]:
            self.assertNotIn("flags", event)
        self.assertTrue(any(access["flags"] == 0
                            for inst in receipt["probe_output"]["run"]["instructions"]
                            for access in inst["accesses"]))
        for name, digest in receipt["probe"]["sources_sha256"].items():
            self.assertEqual(hashlib.sha256((native.HERE / name).read_bytes()).hexdigest(),
                             digest, name)

    def test_committed_rosetta_artifact(self) -> None:
        """Read the committed capture. This is not a fresh native execution."""
        trace = json.loads((native.HERE / "observation.json").read_text())
        receipt = json.loads((native.HERE / "receipt.json").read_text())
        schema.validate(trace)
        self.assertEqual(receipt["observation_sha256"],
                         hashlib.sha256((native.HERE / "observation.json").read_bytes()).hexdigest())
        self._assert_controlled_capture(trace, receipt)

    def test_rosetta_run_certifies_the_milestone(self) -> None:
        """The macOS Rosetta run certifies the milestone and does not skip."""
        if platform.system() != "Darwin":
            self.assertNotEqual(
                platform.system(), "Darwin",
                "this host checks the committed artifact and does not execute the subject")
            return
        reason = native.unsupported_host_reason()
        self.assertIsNone(reason, reason)
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            native.write_capture(out, out / "build")
            trace = json.loads((out / "observation.json").read_text())
            receipt = json.loads((out / "receipt.json").read_text())
        schema.validate(trace)
        self._assert_controlled_capture(trace, receipt)


if __name__ == "__main__":
    unittest.main()
