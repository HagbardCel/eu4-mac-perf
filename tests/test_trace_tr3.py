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
import os
import platform
import subprocess
import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from unittest import mock

from reconstruction_trace import fixture, schema
from reconstruction_trace.tr3 import adapter, gfx, image, native, observe, path, run as tr3_run, startup
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
        self.assertIsNone(native.subject_gap(receipt["probe_output"]))
        reconstructed = native.observation_from_run(
            receipt["probe_output"]["run"], receipt["probe_output"]["qbdi_version"])
        self.assertEqual(trace, reconstructed)
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

    def test_tampered_cell_write_disagrees_with_raw_output(self) -> None:
        trace = json.loads((native.HERE / "observation.json").read_text())
        receipt = json.loads((native.HERE / "receipt.json").read_text())
        tampered = copy.deepcopy(trace)
        write = next(event for event in tampered["events"] if event["kind"] == "write")
        write["value"] = (native.DECOY_ADDRESS + 2).to_bytes(8, "little").hex()
        with self.assertRaises(AssertionError):
            self._assert_controlled_capture(tampered, receipt)

    def test_missing_inc_stays_complete_and_is_refused(self) -> None:
        receipt = json.loads((native.HERE / "receipt.json").read_text())
        raw = copy.deepcopy(receipt["probe_output"])
        self.assertEqual(raw["run"]["instructions"][1]["mnemonic"], "INC64r")
        del raw["run"]["instructions"][1]
        self.assertEqual(raw["run"]["dropped_instructions"], 0)
        self.assertEqual(raw["run"]["dropped_accesses"], 0)
        self.assertEqual(raw["run"]["dropped_transfers"], 0)
        traced = native.observation_from_run(raw["run"], raw["qbdi_version"])
        self.assertEqual(traced["completeness"], "complete")
        self.assertIsNotNone(native.subject_gap(raw))

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

    def test_runtime_site_applies_the_slide_once(self) -> None:
        preferred, slide, entry = 0x100000000, 0x5000, 0x15EC36A
        header = preferred + slide
        site = image.runtime_site(preferred, slide, entry, header)
        self.assertEqual(site, header + entry)
        self.assertNotEqual(site, header + slide + entry)
        with self.assertRaises(ValueError):
            image.runtime_site(preferred, slide, entry, preferred)

    def test_mapped_bytes_are_checked_independently(self) -> None:
        code = bytes(range(16))
        image_base, fileoff, end = 0x100000000, 0x200, 8
        payload = build_synthetic_macho_64(image_base=image_base, segment_fileoff=fileoff,
                                           code_bytes=code)
        pin = {"file_sha256": hashlib.sha256(payload).hexdigest(),
               "window_sha256": hashlib.sha256(code[:end]).hexdigest(),
               "image_base": image_base, "entry_rva": 0, "end_rva": end}
        path_ = Path(tempfile.mkstemp(suffix=".macho")[1])
        path_.write_bytes(payload)
        try:
            self.assertIsNone(image.check_mapped(path_, pin, code[:end]))
            flipped = bytearray(code[:end])
            flipped[0] ^= 0xff
            self.assertEqual(image.check_mapped(path_, pin, bytes(flipped)), "mapped bytes mismatch")
            eu4 = dict(pin, file_sha256="b3d38876abf4e61cdae57509186d7cb7dcb03bfeaca4c95c10c713794715141d",
                       window_sha256="8e5f94bf3f132cbf17397928c72df44b7fb6d24bcc95575702c457fb48e32943",
                       entry_rva=0x15EC36A, end_rva=0x15EC3B5)
            self.assertEqual(image.check(path_, eu4), "file hash mismatch")
        finally:
            path_.unlink(missing_ok=True)

    def test_far_rel32_leaves_the_entry_unchanged(self) -> None:
        entry = bytearray(b"\x90" * 5)
        self.assertEqual(image.apply_rel32(entry, 0x1000, 0x1000 + 5 + (1 << 31)), "rel32 out of range")
        self.assertEqual(bytes(entry), b"\x90" * 5)
        near = bytearray(b"\x90" * 5)
        self.assertIsNone(image.apply_rel32(near, 0x1000, 0x1100))
        self.assertEqual(bytes(near), bytes([0xE9, 0xFB, 0x00, 0x00, 0x00]))

    def _branch(self, take_jz: bool) -> tuple[list[dict], bytes, dict]:
        inventory = path.load_inventory()
        entry = int(inventory["entry_rva"], 16)
        window = bytes(range(int(inventory["end_rva"], 16) - entry))
        by_rva = {int(inst["rva"], 16): inst for inst in inventory["instructions"]}
        rva = entry
        records = []
        while True:
            inst = by_rva[rva]
            offset = rva - entry
            records.append({"rva": rva, "size": inst["length"],
                            "bytes": window[offset:offset + inst["length"]]})
            if inst["mnemonic"] == "RET":
                break
            choices = path.successors(inst, inventory["image_base"])
            rva = choices[1] if take_jz and inst["mnemonic"] == "JZ" else choices[0]
        return records, window, inventory

    def test_each_branch_is_one_complete_path(self) -> None:
        # Synthetic window bytes. This does not authenticate EU4's 75-byte pin.
        for take_jz in (False, True):
            records, window, inventory = self._branch(take_jz)
            self.assertLess(len(records), inventory["instruction_count"])
            self.assertIsNone(path.path_gap(records, inventory, window))
            self.assertEqual(records[0]["rva"], path.ENTRY_RVA)
            self.assertEqual(records[-1]["rva"], path.RET_RVA)

    def test_deleted_register_move_is_not_a_path(self) -> None:
        records, window, inventory = self._branch(False)
        removed = next(record for record in records if record["rva"] == 0x15EC371)
        self.assertEqual(removed["size"], 2)
        gapped = [record for record in records if record is not removed]
        drops = {"instructions": 0, "accesses": 0, "transfers": 0}
        self.assertEqual(drops["instructions"], 0)
        self.assertEqual(path.path_gap(gapped, inventory, window),
                         "0x15ec36e does not continue to 0x15ec373")

    def _assert_startup(self, observation: dict, receipt: dict) -> None:
        self.assertEqual(receipt["claims"], startup.CLAIMS)
        self.assertIn("untraced", receipt["concurrency"])
        raw = receipt["probe_output"]
        self.assertIsNone(native.subject_gap(raw))
        self.assertEqual(observation, native.observation_from_run(raw["run"], raw["qbdi_version"]))
        qualified = fixture.qualify(observation)
        self.assertTrue(qualified["fixture_qualified"], qualified["reasons"])
        self.assertEqual(adapter.prepare_scenario(observation)["refusal"], "unauthenticated-target")
        hook = receipt["hook"]
        self.assertEqual(hook["count"], 1)
        self.assertEqual(hook["qbdi_invocations"], 1)
        self.assertEqual(hook["native_call_count"], 2)
        self.assertTrue(hook["restored_before_qbdi"])
        self.assertTrue(hook["entry_bytes_match_after"])
        self.assertEqual(receipt["proc_translated"], 1)
        loaded = image.runtime_site(int(raw["image"]["preferred_base"], 16),
                                    int(raw["image"]["slide"], 16),
                                    int(raw["image"]["entry_rva"], 16),
                                    int(raw["image"]["header"], 16))
        self.assertEqual(loaded, int(raw["image"]["runtime_site"], 16))
        self.assertEqual(loaded, int(raw["image"]["header"], 16) + int(raw["image"]["entry_rva"], 16))
        decoy = native.DECOY_ADDRESS
        regions = {region["id"]: region for region in observation["entry_state"]["regions"]}
        targets = [int(regions[edge["to"]["region"]]["base"], 16) + edge["to"]["offset"]
                   for edge in observation["entry_state"]["pointers"]]
        self.assertNotIn(decoy, targets)
        self.assertEqual(bytes.fromhex(regions["cell"]["bytes"]), decoy.to_bytes(8, "little"))
        for name, digest in receipt["probe"]["sources_sha256"].items():
            self.assertEqual(hashlib.sha256((native.HERE / name).read_bytes()).hexdigest(), digest,
                             name)

    def test_committed_startup_receipt(self) -> None:
        """Read the committed startup capture. This is not a fresh native execution."""
        receipt = json.loads((native.HERE / "startup_receipt.json").read_text())
        observation = json.loads((native.HERE / "startup_observation.json").read_text())
        schema.validate(observation)
        self.assertEqual(receipt["observation_sha256"],
                         hashlib.sha256((native.HERE / "startup_observation.json").read_bytes()).hexdigest())
        self._assert_startup(observation, receipt)

    def test_startup_interception_certifies_the_milestone(self) -> None:
        """The macOS startup run certifies the milestone and does not skip."""
        if platform.system() != "Darwin":
            self.assertNotEqual(
                platform.system(), "Darwin",
                "this host checks the committed startup receipt and does not execute the subject")
            return
        reason = native.unsupported_host_reason()
        self.assertIsNone(reason, reason)
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            startup.write_capture(out, out / "build")
            observation = json.loads((out / "startup_observation.json").read_text())
            receipt = json.loads((out / "startup_receipt.json").read_text())
        schema.validate(observation)
        self._assert_startup(observation, receipt)

    def _darwin_subject(self) -> bool:
        if platform.system() != "Darwin":
            self.assertNotEqual(
                platform.system(), "Darwin",
                "this host checks the committed startup receipt and does not execute the subject")
            return False
        reason = native.unsupported_host_reason()
        self.assertIsNone(reason, reason)
        return True

    def _run_target(self, build_dir: Path, host: Path, dylib: Path, symbol: str,
                    *, legacy: bool = False, log_name: str = "insert.json"):
        prepared = startup.prepare_target(host, symbol, startup.CEILING)
        log = build_dir / log_name
        env = startup.target_env(dylib, log, prepared["descriptor"], preferred=prepared["preferred"],
                                 legacy=legacy)
        proc, raw = startup.acquire(host, env, log)
        return proc, raw, prepared

    def _valid_env(self, build_dir: Path, host: Path, dylib: Path, log_name: str):
        prepared = startup.prepare_target(host, "_tr3_subject", startup.CEILING)
        log = build_dir / log_name
        env = startup.target_env(dylib, log, prepared["descriptor"], preferred=prepared["preferred"])
        return prepared, log, env

    def _assert_instruction_bytes(self, raw: dict, window: bytes) -> None:
        mapped = bytes.fromhex(raw["mapped_window"])
        self.assertEqual(mapped, window)
        site = int(raw["image"]["runtime_site"], 16)
        self.assertGreaterEqual(len(raw["run"]["instructions"]), 1)
        for inst in raw["run"]["instructions"]:
            offset = int(inst["pc"], 16) - site
            size = inst["inst_size"]
            self.assertEqual(bytes.fromhex(inst["bytes"]), mapped[offset:offset + size])

    @staticmethod
    def _norm_snap(blob: bytes):
        regs = tuple(int.from_bytes(blob[i:i + 8], "little") for i in range(0, 128, 8))
        flags = int.from_bytes(blob[128:136], "little") & 0xCD5
        fx = blob[144:656]
        parts = [fx[0:2], fx[2:4], fx[4:5], fx[24:28]]
        for index in range(8):
            parts.append(fx[32 + 16 * index:32 + 16 * index + 10])
        parts.append(fx[160:416])
        return regs, flags, b"".join(parts)

    def test_two_leaves_share_one_dylib(self) -> None:
        if not self._darwin_subject():
            return
        with tempfile.TemporaryDirectory() as tmp:
            build_dir = Path(tmp)
            host, dylib = startup.build(build_dir)
            digest = hashlib.sha256(dylib.read_bytes()).hexdigest()
            subject_proc, subject, subject_prep = self._run_target(
                build_dir, host, dylib, "_tr3_subject", log_name="subject.json")
            leaf_proc, leaf, leaf_prep = self._run_target(
                build_dir, host, dylib, "_tr3_leaf_b", log_name="leaf.json")
            self.assertEqual(subject_proc.returncode, 0, subject_proc.stderr)
            self.assertEqual(leaf_proc.returncode, 0, leaf_proc.stderr)
            self.assertEqual(hashlib.sha256(dylib.read_bytes()).hexdigest(), digest)
            for raw, prepared in ((subject, subject_prep), (leaf, leaf_prep)):
                desc = prepared["descriptor"]
                self.assertEqual(raw["image"]["entry_rva"], desc["function"]["entry_rva"])
                self.assertEqual(raw["expected_file_sha256"], desc["binary"]["sha256"])
                self.assertEqual(raw["expected_window_sha256"], desc["binary"]["window_sha256"])
                self.assertGreaterEqual(prepared["window_len"], 5)
                self.assertLessEqual(prepared["window_len"], 75)
            self._assert_instruction_bytes(subject, subject_prep["window"])
            self._assert_instruction_bytes(leaf, leaf_prep["window"])
            self.assertNotEqual(subject_prep["window"], leaf_prep["window"])
            self.assertTrue(subject["run"]["execution_complete"], subject["run"])
            self.assertTrue(leaf["run"]["execution_complete"], leaf["run"])
            site = int(leaf["image"]["runtime_site"], 16)
            end = site + leaf_prep["window_len"]
            branched = [inst for inst in leaf["run"]["instructions"]
                        if not inst["is_return"]
                        and int(inst["next_pc"], 16) != int(inst["pc"], 16) + inst["inst_size"]]
            self.assertTrue(branched)
            for inst in branched:
                nxt = int(inst["next_pc"], 16)
                self.assertTrue(site <= nxt < end)

    def test_intercepted_leaf_matches_native_snapshot(self) -> None:
        if not self._darwin_subject():
            return
        with tempfile.TemporaryDirectory() as tmp:
            build_dir = Path(tmp)
            host, dylib = startup.build(build_dir)
            proc, raw, _prepared = self._run_target(
                build_dir, host, dylib, "_tr3_leaf_b", log_name="leaf.json")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        host_out = json.loads(proc.stdout)
        self.assertEqual(host_out["snaps"], 2)
        entry = self._norm_snap(bytes.fromhex(host_out["snap_entry0"]))
        self.assertEqual(entry, self._norm_snap(bytes.fromhex(host_out["snap_entry1"])))
        intercepted = self._norm_snap(bytes.fromhex(host_out["snap_exit0"]))
        native_exit = self._norm_snap(bytes.fromhex(host_out["snap_exit1"]))
        self.assertNotEqual(entry, intercepted)
        self.assertEqual(intercepted, native_exit)
        self.assertEqual(raw["hook"]["count"], 1)
        self.assertTrue(raw["run"]["execution_complete"])

    def _assert_rejected_transfer(self, build_dir: Path, host: Path, dylib: Path, symbol: str,
                                  reason: str) -> None:
        _proc, raw, prepared = self._run_target(
            build_dir, host, dylib, symbol, log_name=f"{reason}.json")
        self.assertTrue(raw["sealed"])
        self.assertIsNone(raw["refusal"])
        self.assertFalse(raw["run"]["execution_complete"])
        boundary = raw["run"]["rejected_boundary"]
        if reason == "call":
            self.assertEqual(boundary["reason"], "call")
            self.assertEqual(boundary["from"], "preinst")
            self.assertNotIn(boundary["pc"], [inst["pc"] for inst in raw["run"]["instructions"]])
            self.assertFalse(any(inst["is_call"] for inst in raw["run"]["instructions"]))
            return
        if boundary is None or boundary.get("from") != "postinst":
            self.fail("blocked: post-instruction callback did not observe the out-of-range transfer")
        self.assertEqual(boundary["reason"], "out-of-range")
        site = int(raw["image"]["runtime_site"], 16)
        end = site + prepared["window_len"]
        pc = int(boundary["pc"], 16)
        self.assertTrue(pc < site or pc >= end)
        self.assertTrue(any(int(inst["next_pc"], 16) == pc for inst in raw["run"]["instructions"]))

    def test_call_and_jump_stay_incomplete(self) -> None:
        if not self._darwin_subject():
            return
        with tempfile.TemporaryDirectory() as tmp:
            build_dir = Path(tmp)
            host, dylib = startup.build(build_dir)
            self._assert_rejected_transfer(build_dir, host, dylib, "_tr3_calls", "call")
            self._assert_rejected_transfer(build_dir, host, dylib, "_tr3_jump_out", "out-of-range")

    def _assert_budget_refusal(self, build_dir: Path, host: Path, dylib: Path, edit, needle: str) -> None:
        prepared, log, env = self._valid_env(build_dir, host, dylib, "budget.json")
        edit(env)
        proc, raw = startup.acquire(host, env, log)
        self.assertFalse(raw["patched"])
        self.assertEqual(raw["hook"]["count"], 0)
        self.assertIn(needle, raw["refusal"])
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(json.loads(proc.stdout)["entry"], prepared["window"][:5].hex())

    def test_bad_budgets_refuse_before_the_hook(self) -> None:
        if not self._darwin_subject():
            return
        with tempfile.TemporaryDirectory() as tmp:
            build_dir = Path(tmp)
            host, dylib = startup.build(build_dir)
            prepared = startup.prepare_target(host, "_tr3_subject", startup.CEILING)
            minimum = startup.envelope(prepared["window_len"])
            self._assert_budget_refusal(
                build_dir, host, dylib, lambda env: env.pop("TR3_MAX_ACCESSES"),
                "budget was not supplied")
            self._assert_budget_refusal(
                build_dir, host, dylib, lambda env: env.__setitem__("TR3_MAX_INSTRUCTIONS", "0"),
                "budget is not positive")
            self._assert_budget_refusal(
                build_dir, host, dylib,
                lambda env: env.__setitem__("TR3_MAX_BYTES", str(startup.CEILING["max_bytes"] + 1)),
                "budget exceeds the capture ceiling")
            self._assert_budget_refusal(
                build_dir, host, dylib,
                lambda env: env.__setitem__("TR3_MAX_BYTES", str(minimum - 1)), str(minimum))

    def test_short_window_fallthrough_is_incomplete(self) -> None:
        if not self._darwin_subject():
            return
        with tempfile.TemporaryDirectory() as tmp:
            build_dir = Path(tmp)
            host, dylib = startup.build(build_dir)
            prepared = startup.prepare_target(host, "_tr3_subject", startup.CEILING)
            desc = copy.deepcopy(prepared["descriptor"])
            entry = prepared["entry_rva"]
            short = prepared["window"][:6]
            desc["function"]["ranges"] = [[hex(entry), hex(entry + len(short))]]
            desc["binary"]["window_sha256"] = hashlib.sha256(short).hexdigest()
            log = build_dir / "short.json"
            env = startup.target_env(dylib, log, desc, preferred=prepared["preferred"])
            _proc, raw = startup.acquire(host, env, log)
        self.assertTrue(raw["sealed"])
        self.assertIsNone(raw["refusal"])
        self.assertFalse(raw["run"]["execution_complete"])
        site = int(raw["image"]["runtime_site"], 16)
        end = site + len(short)
        outside = [inst for inst in raw["run"]["instructions"]
                   if not inst["is_return"] and not site <= int(inst["next_pc"], 16) < end]
        if not outside:
            self.fail("blocked: post-instruction callback did not observe fallthrough past the window")

    def test_runtime_overflow_stays_incomplete(self) -> None:
        if not self._darwin_subject():
            return
        with tempfile.TemporaryDirectory() as tmp:
            build_dir = Path(tmp)
            host, dylib = startup.build(build_dir)
            prepared, log, env = self._valid_env(build_dir, host, dylib, "overflow.json")
            env["TR3_MAX_INSTRUCTIONS"] = "1"
            proc, raw = startup.acquire(host, env, log)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertTrue(raw["patched"])
            self.assertGreater(len(raw["run"]["instructions"]), 0)
            self.assertGreater(raw["run"]["dropped_instructions"], 0)
            self.assertFalse(raw["run"]["execution_complete"])
            self.assertTrue(raw["sealed"])

            _prepared, log, env = self._valid_env(build_dir, host, dylib, "bytes.json")
            env["TR3_MAX_BYTES"] = str(startup.envelope(prepared["window_len"]) + 1)
            proc, raw = startup.acquire(host, env, log)
            self.assertTrue(raw["sealed"])
            self.assertFalse(raw["run"]["execution_complete"])
            self.assertIsNotNone(raw["run"]["entry_fpr"])
            self.assertIsNotNone(raw["mapped_window"])
            self.assertGreater(raw["run"]["dropped_instructions"] + raw["run"]["dropped_bytes"], 0)

            _prepared, log, env = self._valid_env(build_dir, host, dylib, "access.json")
            env["TR3_MAX_ACCESSES"] = "1"
            proc, raw = startup.acquire(host, env, log)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        kept = sum(len(inst["accesses"]) for inst in raw["run"]["instructions"])
        self.assertGreater(kept, 0)
        self.assertGreater(raw["run"]["dropped_accesses"], 0)
        self.assertFalse(raw["run"]["execution_complete"])
        self.assertTrue(raw["sealed"])

    def test_descriptor_is_the_launch_configuration(self) -> None:
        preferred = 0x100000000
        entry = 0x1000
        desc = {
            "schema": "trace-target/v1",
            "binary": {"sha256": "a" * 64, "image_base": hex(preferred), "window_sha256": "b" * 64},
            "function": {"name": "tr3_subject", "entry_rva": hex(entry),
                         "ranges": [[hex(entry), hex(entry + 10)]]},
            "trigger": {"mode": "first-invocation"},
            "capture": dict(startup.CEILING),
        }
        env = startup.target_env(Path("lib.dylib"), Path("log.json"), desc, preferred=preferred)
        self.assertEqual(env["TR3_ENTRY_RVA"], hex(entry))
        self.assertEqual(env["TR3_MAX_INSTRUCTIONS"], str(startup.CEILING["max_instructions"]))
        moved = copy.deepcopy(desc)
        moved["function"]["entry_rva"] = hex(entry + 16)
        moved["function"]["ranges"] = [[hex(entry + 16), hex(entry + 26)]]
        moved["capture"]["max_instructions"] = 7
        changed = startup.target_env(Path("lib.dylib"), Path("log.json"), moved, preferred=preferred)
        self.assertEqual(changed["TR3_ENTRY_RVA"], hex(entry + 16))
        self.assertEqual(changed["TR3_WINDOW_LEN"], "10")
        self.assertEqual(changed["TR3_MAX_INSTRUCTIONS"], "7")
        wrong = copy.deepcopy(desc)
        wrong["binary"]["image_base"] = hex(preferred + 0x1000)
        with self.assertRaises(RuntimeError) as caught:
            startup.target_env(Path("lib.dylib"), Path("log.json"), wrong, preferred=preferred)
        self.assertIn("image base", str(caught.exception))

    def _planted_success(self) -> tuple[dict, dict, str, str]:
        receipt = json.loads((native.HERE / "startup_receipt.json").read_text())
        file_hash, window_hash = "a" * 64, "b" * 64
        raw = copy.deepcopy(receipt["probe_output"])
        raw["expected_file_sha256"] = raw["file_sha256"] = file_hash
        raw["expected_window_sha256"] = raw["window_sha256"] = window_hash
        return raw, receipt["host_result"], file_hash, window_hash

    def test_stale_insert_log_is_not_this_run(self) -> None:
        raw, host_out, file_hash, window_hash = self._planted_success()
        proc = subprocess.CompletedProcess(["tr3_host"], 0, stdout=json.dumps(host_out), stderr="")
        startup.accept(proc, raw, file_hash, window_hash)
        with tempfile.TemporaryDirectory() as tmp:
            log = Path(tmp) / "insert.json"
            log.write_text(json.dumps(raw))

            def runner(argv, **_kwargs):
                return subprocess.CompletedProcess(argv, 0, stdout=proc.stdout, stderr="")

            with self.assertRaises(RuntimeError) as caught:
                startup.acquire(Path("tr3_host"), {}, log, runner)
            self.assertIn("no log", str(caught.exception))

    def test_measured_file_digest_must_match_this_host(self) -> None:
        raw, host_out, file_hash, window_hash = self._planted_success()
        raw["file_sha256"] = "c" * 64
        proc = subprocess.CompletedProcess(["tr3_host"], 0, stdout=json.dumps(host_out), stderr="")
        with tempfile.TemporaryDirectory() as tmp:
            log = Path(tmp) / "insert.json"
            log.write_text(json.dumps(raw))
            with self.assertRaises(RuntimeError) as caught:
                startup.accept(proc, json.loads(log.read_text()), file_hash, window_hash)
        self.assertIn("file_sha256", str(caught.exception))

    def test_dropped_access_refuses_before_a_receipt(self) -> None:
        raw, host_out, file_hash, window_hash = self._planted_success()
        raw["run"]["dropped_accesses"] = 1
        proc = subprocess.CompletedProcess(["tr3_host"], 0, stdout=json.dumps(host_out), stderr="")
        with self.assertRaises(RuntimeError) as caught:
            startup.accept(proc, raw, file_hash, window_hash)
        self.assertIn("incomplete capture", str(caught.exception))

    def _assert_auth_refusal(self, proc, raw, reason: str, entry: str) -> None:
        self.assertEqual(raw["refusal"], reason)
        self.assertFalse(raw["patched"])
        self.assertEqual(json.loads(proc.stdout)["entry"], entry)

    def test_wrong_file_hash_refuses_without_a_patch(self) -> None:
        if platform.system() != "Darwin":
            self.assertNotEqual(
                platform.system(), "Darwin",
                "this host checks the committed startup receipt and does not execute the subject")
            return
        reason = native.unsupported_host_reason()
        self.assertIsNone(reason, reason)
        with tempfile.TemporaryDirectory() as tmp:
            proc, raw, built = startup.launch(Path(tmp), file_hash="0" * 64)
        self._assert_auth_refusal(proc, raw, "file hash mismatch", built["entry"])

    def test_wrong_window_hash_refuses_without_a_patch(self) -> None:
        if platform.system() != "Darwin":
            self.assertNotEqual(
                platform.system(), "Darwin",
                "this host checks the committed startup receipt and does not execute the subject")
            return
        reason = native.unsupported_host_reason()
        self.assertIsNone(reason, reason)
        with tempfile.TemporaryDirectory() as tmp:
            proc, raw, built = startup.launch(Path(tmp), window_hash="0" * 64)
        self._assert_auth_refusal(proc, raw, "window hash mismatch", built["entry"])

    def _synth(self, take_jz: bool, trace: dict, **drops: int) -> dict:
        records, window, _inventory = self._branch(take_jz)
        run = {"ran": True, "entered": True, "dropped_instructions": 0,
               "dropped_accesses": 0, "dropped_transfers": 0}
        run.update(drops)
        return {"evidence": "handwritten-synthetic", "records": records, "window": window,
                "trace": trace, "run": run}

    def _assert_not_native(self, result: dict) -> None:
        observation = result["observation"]
        if observation is None:
            return
        native_claim = (observation["function_id"] == gfx.FUNCTION_ID
                        and observation["evidence"] == "qbdi-rosetta"
                        and isinstance(observation["binary"], dict))
        self.assertFalse(native_claim)

    def test_buffer_read_branch_prepares_a_scenario(self) -> None:
        result = gfx.interpret([self._synth(True, _invocation(slot=0))])
        observation = result["observation"]
        schema.validate(observation)
        qualified = fixture.qualify(observation)
        self.assertTrue(qualified["fixture_qualified"], qualified["reasons"])
        prepared = adapter.prepare_scenario(observation)
        self.assertIsNone(prepared["refusal"])
        self.assertIsNotNone(prepared["scenario"])

    def test_explicit_length_branch_stays_unauthenticated(self) -> None:
        trace = _invocation(slot=1, selector=0x10, read_buffer=False)
        result = gfx.interpret([self._synth(False, trace)])
        observation = result["observation"]
        schema.validate(observation)
        self.assertTrue(fixture.qualify(observation)["fixture_qualified"])
        self.assertEqual(adapter.prepare_scenario(observation)["refusal"],
                         "unauthenticated-buffer-pointer")

    def test_cfg_defects_are_refused_with_zero_drops(self) -> None:
        records, window, _inventory = self._branch(False)
        base = {"evidence": "handwritten-synthetic", "window": window,
                "trace": _invocation(slot=1, selector=0x10, read_buffer=False),
                "run": {"ran": True, "entered": True, "dropped_instructions": 0,
                        "dropped_accesses": 0, "dropped_transfers": 0}}
        deleted = [record for record in records if record["rva"] != 0x15EC371]
        swapped = [records[0], records[2], records[1], *records[3:]]
        wrong = [dict(records[0], size=2), *records[1:]]
        expected = (
            (deleted, "0x15ec36e does not continue to 0x15ec373"),
            (swapped, "0x15ec36a does not continue to 0x15ec36e"),
            (wrong, "size mismatch at 0x15ec36a"),
        )
        for defective, reason in expected:
            result = gfx.interpret([{**base, "records": defective}])
            self.assertEqual(result["diagnostic"], reason)
            self.assertNotIn("dropped", reason)
            self.assertEqual(result["observation"]["completeness"], "incomplete")
            self.assertFalse(fixture.qualify(result["observation"])["fixture_qualified"])

    def test_missing_region_stays_unqualified(self) -> None:
        trace = _invocation(slot=0)
        trace["entry_state"]["regions"] = [region for region in trace["entry_state"]["regions"]
                                            if region["id"] != "ctx"]
        trace["entry_state"]["pointers"] = [
            edge for edge in trace["entry_state"]["pointers"]
            if edge["to"]["region"] != "ctx" and edge["from"].get("region") != "ctx"]
        result = gfx.interpret([self._synth(True, trace)])
        observation = result["observation"]
        self.assertEqual(observation["completeness"], "incomplete")
        self.assertFalse(fixture.qualify(observation)["fixture_qualified"])
        self.assertEqual(adapter.prepare_scenario(observation)["refusal"], "unqualified")

    def _qbdi(self, instructions: list[dict], *, handle: int = 0x100000,
              stack: int = 0x400000, pc: str | None = None) -> dict:
        registers = {name: "0x0" for name in native.ENTRY_REGISTERS}
        registers["rdi"] = hex(handle)
        registers["rsp"] = hex(stack + 248)
        return {
            "failure": None, "ran": True, "entered": True, "memory_recording": True,
            "exec_transfers": [], "dropped_instructions": 0, "dropped_accesses": 0,
            "dropped_transfers": 0,
            "regions": [
                {"id": "handle", "kind": "heap", "base": hex(handle), "size": 8, "bytes": "00" * 8},
                {"id": "stack", "kind": "stack", "base": hex(stack), "size": 256, "bytes": "00" * 256},
            ],
            "pointers": [
                {"from": {"register": "rdi"}, "to": {"region": "handle", "offset": 0}},
                {"from": {"register": "rsp"}, "to": {"region": "stack", "offset": 248}},
            ],
            "entry_gpr": registers, "exit_gpr": {"rax": "0x0"}, "instructions": instructions,
        }

    def _qbdi_instruction(self, pc: str, *, may_load: bool = False, may_store: bool = False,
                          load_size: int = 0, store_size: int = 0,
                          accesses: list[dict] | None = None, inst_size: int = 4) -> dict:
        return {
            "pc": pc, "next_pc": "0x1004", "inst_size": inst_size, "mnemonic": "MOV64mr",
            "disassembly": "mov qword ptr [rdi], rax", "may_load": may_load, "may_store": may_store,
            "load_size": load_size, "store_size": store_size, "is_call": False, "is_return": False,
            "accesses": [] if accesses is None else accesses,
        }

    def _access(self, pc: str, address: str, *, value: str = "0x0", size: int = 8,
                type_: int = 2, flags: int = 0) -> dict:
        return {"inst_address": pc, "address": address, "value": value, "size": size,
                "type": type_, "flags": flags}

    def test_native_identity_requires_this_capture(self) -> None:
        site = 0x100000000 + 0x5000 + 0x15EC36A
        image_meta = {"preferred_base": hex(0x100000000), "slide": hex(0x5000),
                      "header": hex(0x100000000 + 0x5000), "entry_rva": hex(0x15EC36A),
                      "runtime_site": hex(site)}
        captured = {
            "evidence": "qbdi-rosetta", "qbdi_version": "test",
            "expected_file_sha256": gfx.PIN_FILE, "expected_window_sha256": gfx.PIN_WINDOW,
            "file_sha256": gfx.PIN_FILE, "window_sha256": gfx.PIN_WINDOW,
            "image": image_meta, "run": self._qbdi([]),
        }
        for candidate in (
            captured,
            {**captured, "file_sha256": "ab" * 32},
            {**captured, "image": {**image_meta, "runtime_site": hex(site + 0x5000)}},
            {key: value for key, value in captured.items() if key != "image"},
        ):
            result = gfx.interpret([candidate], mapped=bytes(75))
            self._assert_not_native(result)
            self.assertIsNotNone(result["diagnostic"])
            self.assertNotEqual(result["observation"]["function_id"], gfx.FUNCTION_ID)
            self.assertIsNone(result["observation"]["binary"])
        code = bytes(range(16))
        payload = build_synthetic_macho_64(image_base=0x100000000, segment_fileoff=0x200,
                                           code_bytes=code)
        path_ = Path(tempfile.mkstemp(suffix=".macho")[1])
        path_.write_bytes(payload)
        try:
            result = gfx.interpret([captured], image_path=path_, mapped=code[:8])
        finally:
            path_.unlink(missing_ok=True)
        self._assert_not_native(result)
        self.assertIn("file hash mismatch", result["observation"]["incomplete_reasons"])
        self.assertNotEqual(result["observation"]["function_id"], gfx.FUNCTION_ID)

    def test_qbdi_value_is_little_endian_bytes(self) -> None:
        pc = "0x1000"
        run = self._qbdi([self._qbdi_instruction(
            pc, may_store=True, store_size=8,
            accesses=[self._access(pc, "0x100000", value="0x100000001", size=8)])])
        result = gfx.interpret([{"evidence": "qbdi-rosetta", "qbdi_version": "test", "run": run}])
        event = result["observation"]["events"][0]
        self.assertEqual(event["value"], "0100000001000000")
        self.assertEqual(event["value_source"], "observed")
        self.assertNotIn("0x", event["value"])

    def test_uncertain_qbdi_access_stays_unqualified(self) -> None:
        pc = "0x1000"
        unknown = self._qbdi([self._qbdi_instruction(
            pc, may_store=True, store_size=8,
            accesses=[self._access(pc, "0x100000", value="0x100000001", size=8,
                                   flags=native.MEMORY_UNKNOWN_VALUE)])])
        unclassified = self._qbdi([self._qbdi_instruction(
            pc, may_store=True, store_size=8,
            accesses=[self._access(pc, "0x100000", value="0x1", size=8, type_=0)])])
        missing = self._qbdi([self._qbdi_instruction(pc, may_load=True, load_size=8, accesses=[])])
        cases = (
            (unknown, "has no known value"),
            (unclassified, "unclassified access type"),
            (missing, "mayLoad without a recorded read"),
        )
        for run, sentence in cases:
            result = gfx.interpret([{"evidence": "qbdi-rosetta", "qbdi_version": "test", "run": run}])
            observation = result["observation"]
            schema.validate(observation)
            self.assertEqual(observation["completeness"], "incomplete")
            self.assertEqual(result["diagnostic"], "image was not authenticated")
            self.assertEqual(result["diagnostic"], observation["incomplete_reasons"][0])
            self.assertTrue(any(sentence in reason for reason in observation["incomplete_reasons"]))
            self.assertFalse(fixture.qualify(observation)["fixture_qualified"])
            self.assertNotEqual(observation["function_id"], gfx.FUNCTION_ID)

    def test_unauthenticated_window_is_not_a_cfg_diagnostic(self) -> None:
        site = 0x100000000 + 0x5000 + 0x15EC36A
        pc = hex(site)
        run = self._qbdi([self._qbdi_instruction(
            pc, inst_size=1, may_store=True, store_size=8,
            accesses=[self._access(pc, "0x100000", value="0x1", size=8)])])
        candidate = {
            "evidence": "qbdi-rosetta", "qbdi_version": "test", "run": run,
            "image": {"preferred_base": hex(0x100000000), "slide": hex(0x5000),
                      "header": hex(0x100000000 + 0x5000), "entry_rva": hex(0x15EC36A),
                      "runtime_site": pc},
        }
        result = gfx.interpret([candidate], mapped=bytes(75))
        observation = result["observation"]
        self.assertEqual(result["diagnostic"], "image was not authenticated")
        self.assertNotIn("path does not end at the ret", observation["incomplete_reasons"])
        self.assertEqual(observation["completeness"], "incomplete")
        self.assertFalse(fixture.qualify(observation)["fixture_qualified"])
        self._assert_not_native(result)

    def test_captured_handle_does_not_invent_a_context(self) -> None:
        pc = "0x1000"
        context = 0x200000
        run = self._qbdi([self._qbdi_instruction(
            pc, may_store=True, store_size=4,
            accesses=[self._access(pc, hex(context), value="0x1", size=4)])])
        candidate = {
            "evidence": "qbdi-rosetta", "qbdi_version": "test", "run": run,
            "supplied_regions": [_heap("ctx", context, bytes(0x130))],
        }
        result = gfx.interpret([candidate])
        observation = result["observation"]
        schema.validate(observation)
        self._assert_not_native(result)
        self.assertEqual([region["id"] for region in observation["entry_state"]["regions"]],
                         ["handle", "stack"])
        self.assertEqual(observation["events"][0]["address"], hex(context))
        self.assertEqual(observation["completeness"], "incomplete")
        self.assertTrue(any("write outside the harness inventory" in reason
                            for reason in observation["incomplete_reasons"]))
        self.assertEqual(result["diagnostic"], observation["incomplete_reasons"][0])
        self.assertFalse(fixture.qualify(observation)["fixture_qualified"])
        self.assertIn("untraced", result["limits"])

    def test_prior_incomplete_synthetic_trace_stays_incomplete(self) -> None:
        trace = _invocation(slot=0)
        trace["completeness"] = "incomplete"
        trace["incomplete_reasons"] = ["already incomplete"]
        result = gfx.interpret([self._synth(True, trace)])
        observation = result["observation"]
        self.assertEqual(observation["completeness"], "incomplete")
        self.assertEqual(observation["incomplete_reasons"][0], "already incomplete")
        self.assertEqual(result["diagnostic"], "already incomplete")
        self.assertFalse(fixture.qualify(observation)["fixture_qualified"])

    def test_hook_refusal_builds_no_observation(self) -> None:
        later = self._synth(True, _invocation(slot=0))
        result = gfx.interpret([{"refusal": "rel32 out of range"}, later])
        self.assertIsNone(result["observation"])
        self.assertEqual(result["diagnostic"], "rel32 out of range")

    def test_incomplete_first_run_is_the_one_kept(self) -> None:
        first = self._synth(True, _invocation(slot=0), dropped_accesses=1)
        second = self._synth(True, _invocation(slot=0))
        second["trace"]["entry_state"]["registers"]["r8"] = "0x99"
        result = gfx.interpret([first, second])
        observation = result["observation"]
        self.assertEqual(observation["completeness"], "incomplete")
        self.assertNotIn("r8", observation["entry_state"]["registers"])
        self.assertEqual(adapter.prepare_scenario(observation)["refusal"], "unqualified")
        self.assertIsNone(adapter.prepare_scenario(observation)["scenario"])


class TraceTr3LiveLaunchTests(unittest.TestCase):
    def test_live_launch_refuses_without_operator_authorization(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.object(tr3_run.startup, "build") as build, \
                 mock.patch("subprocess.Popen") as popen:
                with redirect_stdout(StringIO()) as out:
                    status = tr3_run.main(["--output", str(Path(tmp) / "live.json")])
        self.assertEqual(status, 1)
        self.assertEqual(out.getvalue().strip(), tr3_run.UNAUTHORIZED)
        build.assert_not_called()
        popen.assert_not_called()

    def test_live_launch_refuses_a_failed_image_check(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.object(Path, "is_file", return_value=True), \
                 mock.patch.object(tr3_run.image, "check", return_value="file hash mismatch") as check, \
                 mock.patch.object(tr3_run.startup, "build") as build, \
                 mock.patch("subprocess.Popen") as popen:
                with redirect_stdout(StringIO()) as out:
                    status = tr3_run.main(
                        ["--authorize-live", "--output", str(Path(tmp) / "live.json")])
        self.assertEqual(status, 1)
        self.assertEqual(out.getvalue().strip(), "refused: file hash mismatch")
        check.assert_called_once()
        build.assert_not_called()
        popen.assert_not_called()

    def test_live_candidate_refusal_is_the_diagnostic(self) -> None:
        result = gfx.interpret([tr3_run.live_candidate({"refusal": "rel32 out of range"})])
        self.assertIsNone(result["observation"])
        self.assertEqual(result["diagnostic"], "rel32 out of range")
        self.assertEqual(result["limits"], gfx.LIMITS)

    def test_live_launch_keeps_the_first_candidate_only(self) -> None:
        first = tr3_run.live_candidate({"refusal": "rel32 out of range"})
        second = tr3_run.live_candidate({"refusal": "QBDI executed no block"})
        result = gfx.interpret([first, second])
        self.assertIsNone(result["observation"])
        self.assertEqual(result["diagnostic"], "rel32 out of range")

    def test_read_log_returns_none_for_a_missing_log(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            self.assertIsNone(tr3_run.read_log(Path(tmp) / "insert.json"))

    def _child(self) -> "_Child":
        return _Child()

    def _seal(self, path: Path, session: str, pid: int, **overrides: object) -> dict:
        raw = {
            "sealed": True, "session": session, "pid": pid, "refusal": None,
            "proc_translated": 1, "patched": True, "qbdi_version": "test",
            "hook": {"count": 1, "qbdi_invocations": 1, "restored_before_qbdi": True,
                     "entry_bytes_match_after": True},
            "run": {
                "failure": None, "ran": True, "entered": True, "memory_recording": True,
                "exec_transfers": [], "dropped_instructions": 0, "dropped_accesses": 0,
                "dropped_transfers": 0, "regions": [], "pointers": [],
                "entry_gpr": {name: "0x0" for name in native.ENTRY_REGISTERS},
                "exit_gpr": {"rax": "0x0"}, "instructions": [],
            },
        }
        raw.update(overrides)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(raw))
        return raw

    def _launch(self, tmp: str, popen, *, output: Path | None = None, timeout: float = 0,
                exe_args: object = None, path: Path | None = None, gog: Path | None = None,
                build: mock.Mock | None = None):
        root = Path(tmp)
        exe = root / "gog" / "eu4"
        exe.parent.mkdir(parents=True, exist_ok=True)
        exe.write_bytes(b"eu4")
        launcher = root / "launcher-settings.json"
        launcher.write_text(json.dumps({
            "exePath": "./eu4.app/Contents/MacOS/eu4",
            "exeArgs": ["-nographics"] if exe_args is None else exe_args,
        }))
        out = root / "live.json" if output is None else output
        build_mock = build if build is not None else mock.Mock(
            return_value=(root / "host", root / "dylib"))
        with mock.patch.object(tr3_run.startup, "build", build_mock), \
             mock.patch.object(tr3_run.base, "GOG_LAUNCHER", launcher), \
             mock.patch.object(tr3_run.base, "GOG_EXE", exe if gog is None else gog), \
             mock.patch.object(tr3_run.base, "running_game_pid", return_value=None), \
             mock.patch.object(tr3_run.subprocess, "Popen", side_effect=popen):
            status = tr3_run.launch_live(
                out, {"sha256": "aa", "image_base": 1}, {"sha256": "bb"},
                exe if path is None else path, timeout=timeout)
        return status, out, build_mock

    def test_relative_output_is_resolved_before_launch(self) -> None:
        captured: dict = {}

        def popen(argv, cwd, env, stdout, stderr, **_kwargs):
            captured.update(log=env["TR3_LOG"], cwd=cwd, stdout=stdout.name, stderr=stderr.name,
                            session=env["TR3_SESSION"])
            child = _Child()
            self._seal(Path(env["TR3_LOG"]), env["TR3_SESSION"], child.pid, refusal="rel32 out of range")
            return child

        with tempfile.TemporaryDirectory() as tmp:
            previous = os.getcwd()
            os.chdir(tmp)
            try:
                status, out, _build = self._launch(tmp, popen, output=Path("results/live.json"))
                payload = json.loads(out.resolve().read_text())
            finally:
                os.chdir(previous)
        log = Path(captured["log"])
        self.assertTrue(log.is_absolute())
        self.assertTrue(Path(captured["stdout"]).is_absolute())
        self.assertTrue(Path(captured["stderr"]).is_absolute())
        self.assertEqual(log.parent, Path(captured["stdout"]).parent)
        self.assertEqual(log.parent, Path(captured["stderr"]).parent)
        self.assertEqual(log.parent.name, f".tr3-{captured['session']}")
        self.assertNotEqual(log.parent, Path(captured["cwd"]))
        self.assertEqual(status, 1)
        self.assertEqual(payload["outcome"], "hook_refused")

    def test_existing_output_refuses_before_launch(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            ready = Path(tmp) / "live.json"
            ready.write_text("{}\n")
            status, _out, build = self._launch(tmp, lambda *args, **kwargs: self.fail("launched"),
                                                build=mock.Mock())
        self.assertEqual(status, 1)
        build.assert_not_called()

    def test_planted_insert_log_is_ignored(self) -> None:
        planted = {"sealed": True, "session": "ab" * 16, "pid": 1, "refusal": None,
                   "hook": {"count": 1}}

        def popen(*_args, **_kwargs):
            return _Child()

        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "insert.json").write_text(json.dumps(planted))
            status, out, _build = self._launch(tmp, popen)
            text = out.read_text()
        payload = json.loads(text)
        self.assertEqual(status, 1)
        self.assertIsNone(payload["raw"])
        self.assertEqual(payload["outcome"], "capture_missing")
        self.assertNotIn("ab" * 16, text)

    def test_wrong_session_is_not_fresh_evidence(self) -> None:
        def popen(argv, cwd, env, stdout, stderr, **_kwargs):
            child = _Child()
            child.returncode = 0
            self._seal(Path(env["TR3_LOG"]), "ff" * 16, child.pid)
            return child

        with tempfile.TemporaryDirectory() as tmp:
            status, out, _build = self._launch(tmp, popen)
            payload = json.loads(out.read_text())
        self.assertEqual(status, 1)
        self.assertIsNone(payload["raw"])
        self.assertEqual(payload["outcome"], "capture_missing")
        self.assertIn("does not match", payload["diagnostic"])

    def test_wrong_pid_is_not_fresh_evidence(self) -> None:
        def popen(argv, cwd, env, stdout, stderr, **_kwargs):
            child = _Child()
            child.returncode = 0
            self._seal(Path(env["TR3_LOG"]), env["TR3_SESSION"], child.pid + 1)
            return child

        with tempfile.TemporaryDirectory() as tmp:
            status, out, _build = self._launch(tmp, popen)
            payload = json.loads(out.read_text())
        self.assertIsNone(payload["raw"])
        self.assertEqual(status, 1)
        self.assertIn("does not match", payload["diagnostic"])

    def test_hook_attestation_failure_is_not_a_capture(self) -> None:
        def popen(argv, cwd, env, stdout, stderr, **_kwargs):
            child = _Child()
            self._seal(Path(env["TR3_LOG"]), env["TR3_SESSION"], child.pid, patched=False)
            return child

        with tempfile.TemporaryDirectory() as tmp:
            status, out, _build = self._launch(tmp, popen)
            payload = json.loads(out.read_text())
        self.assertEqual(status, 1)
        self.assertEqual(payload["outcome"], "hook_unattested")
        self.assertIn("patched", payload["diagnostic"])
        self.assertIsNone(payload["observation"])
        self.assertFalse(payload["raw"]["patched"])

    def test_constructor_refusal_stays_hook_refused(self) -> None:
        def popen(argv, cwd, env, stdout, stderr, **_kwargs):
            child = _Child()
            self._seal(Path(env["TR3_LOG"]), env["TR3_SESSION"], child.pid,
                       refusal="rel32 out of range", patched=False,
                       hook={"count": 0, "qbdi_invocations": 0,
                             "restored_before_qbdi": False, "entry_bytes_match_after": False})
            return child

        with tempfile.TemporaryDirectory() as tmp:
            status, out, _build = self._launch(tmp, popen)
            payload = json.loads(out.read_text())
        self.assertEqual(status, 1)
        self.assertEqual(payload["outcome"], "hook_refused")
        self.assertEqual(payload["diagnostic"], "rel32 out of range")
        self.assertEqual(payload["raw"]["refusal"], "rel32 out of range")

    def test_mapped_window_is_passed_unchanged(self) -> None:
        window = bytes(range(75))
        seen: dict = {}
        real = tr3_run.gfx.interpret

        def spy(candidates, **kwargs):
            seen["mapped"] = kwargs.get("mapped")
            return real(candidates, **kwargs)

        def popen(argv, cwd, env, stdout, stderr, **_kwargs):
            child = _Child()
            self._seal(Path(env["TR3_LOG"]), env["TR3_SESSION"], child.pid,
                       mapped_window=window.hex())
            return child

        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.object(tr3_run.gfx, "interpret", side_effect=spy):
                status, out, _build = self._launch(tmp, popen)
            payload = json.loads(out.read_text())
        self.assertEqual(seen["mapped"], window)
        self.assertEqual(status, 0)
        self.assertEqual(payload["outcome"], "captured_incomplete")
        self.assertEqual(payload["qualification"], "not claimed")
        self.assertEqual(payload["observation"]["completeness"], "incomplete")
        self.assertIsNone(payload["observation"]["binary"])

    def test_missing_mapped_window_is_not_authenticated(self) -> None:
        seen: dict = {}
        real = tr3_run.gfx.interpret

        def spy(candidates, **kwargs):
            seen["mapped"] = kwargs.get("mapped")
            return real(candidates, **kwargs)

        def popen(argv, cwd, env, stdout, stderr, **_kwargs):
            child = _Child()
            self._seal(Path(env["TR3_LOG"]), env["TR3_SESSION"], child.pid)
            return child

        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.object(tr3_run.gfx, "interpret", side_effect=spy):
                status, out, _build = self._launch(tmp, popen)
            payload = json.loads(out.read_text())
        self.assertIsNone(seen["mapped"])
        self.assertEqual(status, 0)
        self.assertEqual(payload["outcome"], "captured_incomplete")
        self.assertEqual(payload["diagnostic"], "image was not authenticated")
        self.assertEqual(payload["qualification"], "not claimed")
        self.assertIsNotNone(payload["observation"])

    def test_capture_is_observed_before_termination(self) -> None:
        child = _Child()

        def popen(argv, cwd, env, stdout, stderr, **_kwargs):
            self._seal(Path(env["TR3_LOG"]), env["TR3_SESSION"], child.pid)
            return child

        with tempfile.TemporaryDirectory() as tmp:
            status, out, _build = self._launch(tmp, popen)
            payload = json.loads(out.read_text())
        self.assertIsNone(child.polls[0])
        self.assertTrue(child.terminated)
        self.assertEqual(payload["termination"]["classification"], "capture_stop")
        self.assertEqual(payload["termination"]["returncode"], -15)
        self.assertEqual(status, 0)
        self.assertEqual(payload["outcome"], "captured_incomplete")

    def test_exit_during_poll_keeps_the_return_code(self) -> None:
        child = _Child()

        def popen(argv, cwd, env, stdout, stderr, **_kwargs):
            child.log = Path(env["TR3_LOG"])
            child.session = env["TR3_SESSION"]
            return child

        def poll(self):
            if self.returncode is None:
                self.returncode = 3
                self._owner._seal(self.log, self.session, self.pid)
            self.polls.append(self.returncode)
            return self.returncode

        child.poll = poll.__get__(child, _Child)
        child._owner = self
        with tempfile.TemporaryDirectory() as tmp:
            status, out, _build = self._launch(tmp, popen)
            payload = json.loads(out.read_text())
        self.assertEqual(payload["termination"]["returncode"], 3)
        self.assertEqual(payload["termination"]["classification"], "unexpected_crash")
        self.assertEqual(payload["outcome"], "captured_incomplete")
        self.assertEqual(status, 0)
        self.assertFalse(child.terminated)

    def test_process_lookup_error_keeps_a_sealed_capture(self) -> None:
        child = _Child()

        def popen(argv, cwd, env, stdout, stderr, **_kwargs):
            self._seal(Path(env["TR3_LOG"]), env["TR3_SESSION"], child.pid)
            return child

        def terminate(self):
            self.terminated = True
            self.returncode = 0
            raise ProcessLookupError("gone")

        child.terminate = terminate.__get__(child, _Child)
        with tempfile.TemporaryDirectory() as tmp:
            status, out, _build = self._launch(tmp, popen)
            payload = json.loads(out.read_text())
        self.assertEqual(status, 0)
        self.assertEqual(payload["outcome"], "captured_incomplete")
        self.assertEqual(payload["termination"]["returncode"], 0)
        self.assertEqual(payload["termination"]["action"], "none")
        self.assertIsNotNone(payload["raw"])

    def test_interpret_failure_keeps_the_sealed_log(self) -> None:
        def popen(argv, cwd, env, stdout, stderr, **_kwargs):
            child = _Child()
            self._seal(Path(env["TR3_LOG"]), env["TR3_SESSION"], child.pid)
            return child

        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.object(tr3_run.gfx, "interpret", side_effect=RuntimeError("bad qbdi")):
                status, out, _build = self._launch(tmp, popen)
            payload = json.loads(out.read_text())
        self.assertEqual(status, 1)
        self.assertEqual(payload["outcome"], "interpretation_failed")
        self.assertIn("bad qbdi", payload["diagnostic"])
        self.assertTrue(payload["raw"]["sealed"])
        self.assertIsNone(payload["observation"])

    def test_exception_after_launch_stops_the_child_and_keeps_the_code(self) -> None:
        child = _Child()

        def popen(*_args, **_kwargs):
            return child

        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.object(tr3_run, "await_record", side_effect=RuntimeError("boom")):
                status, out, _build = self._launch(tmp, popen)
            payload = json.loads(out.read_text())
        self.assertTrue(child.terminated)
        self.assertEqual(status, 1)
        self.assertEqual(payload["outcome"], "process_failed")
        self.assertEqual(payload["termination"]["returncode"], -15)
        self.assertEqual(payload["termination"]["action"], "terminate")
        self.assertEqual(payload["termination"]["wait"], "error")
        self.assertEqual(payload["termination"]["classification"], "launcher_stop")
        self.assertIn("boom", payload["diagnostic"])

    def test_live_launch_drops_the_host_symbol_check(self) -> None:
        captured: dict = {}

        def popen(argv, cwd, env, stdout, stderr, **_kwargs):
            captured["check"] = env.get("TR3_CHECK_SYMBOL", "")
            child = _Child()
            child.returncode = 0
            return child

        os.environ["TR3_CHECK_SYMBOL"] = "1"
        try:
            with tempfile.TemporaryDirectory() as tmp:
                self._launch(tmp, popen)
        finally:
            os.environ.pop("TR3_CHECK_SYMBOL", None)
        self.assertEqual(captured["check"], "")

    def test_cleanup_oserror_keeps_the_sealed_log(self) -> None:
        child = _Child()

        def popen(argv, cwd, env, stdout, stderr, **_kwargs):
            self._seal(Path(env["TR3_LOG"]), env["TR3_SESSION"], child.pid)
            return child

        def terminate(self):
            raise OSError("busy")

        def kill(self):
            self.killed = True
            raise OSError("unkillable")

        child.terminate = terminate.__get__(child, _Child)
        child.kill = kill.__get__(child, _Child)
        with tempfile.TemporaryDirectory() as tmp:
            status, out, _build = self._launch(tmp, popen)
            payload = json.loads(out.read_text())
        self.assertTrue(child.killed)
        self.assertIsNone(child.returncode)
        self.assertEqual(status, 1)
        self.assertTrue(payload["raw"]["sealed"])
        self.assertEqual(payload["outcome"], "captured_incomplete")
        self.assertFalse(payload["termination"]["stopped"])
        self.assertIn("unkillable", payload["termination"]["error"])
        self.assertIn("process cleanup failed", payload["diagnostic"])

    def test_sigkill_wait_is_bounded(self) -> None:
        child = _Child()
        child.waits: list = []

        def popen(argv, cwd, env, stdout, stderr, **_kwargs):
            self._seal(Path(env["TR3_LOG"]), env["TR3_SESSION"], child.pid)
            return child

        def terminate(self):
            self.terminated = True

        def kill(self):
            self.killed = True

        def wait(self, timeout=None):
            self.waits.append(timeout)
            raise subprocess.TimeoutExpired(cmd="./eu4", timeout=timeout)

        child.terminate = terminate.__get__(child, _Child)
        child.kill = kill.__get__(child, _Child)
        child.wait = wait.__get__(child, _Child)
        with tempfile.TemporaryDirectory() as tmp:
            status, out, _build = self._launch(tmp, popen)
            payload = json.loads(out.read_text())
        self.assertEqual(child.waits, [tr3_run.STOP_WAIT_S, tr3_run.STOP_WAIT_S])
        self.assertTrue(child.killed)
        self.assertEqual(status, 1)
        self.assertFalse(payload["termination"]["stopped"])
        self.assertIn("kill wait timed out", payload["termination"]["error"])
        self.assertTrue(payload["raw"]["sealed"])
        self.assertEqual(payload["outcome"], "captured_incomplete")

    def test_launcher_args_must_be_strings(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            status, _out, build = self._launch(
                tmp, lambda *args, **kwargs: self.fail("launched"), exe_args="nope",
                build=mock.Mock())
        self.assertEqual(status, 1)
        build.assert_not_called()

    def test_executable_must_be_the_gog_binary(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            other = Path(tmp) / "other"
            other.write_bytes(b"x")
            status, _out, build = self._launch(
                tmp, lambda *args, **kwargs: self.fail("launched"), path=other, build=mock.Mock())
        self.assertEqual(status, 1)
        build.assert_not_called()

    def test_complete_trace_does_not_claim_qualification(self) -> None:
        def popen(argv, cwd, env, stdout, stderr, **_kwargs):
            child = _Child()
            self._seal(Path(env["TR3_LOG"]), env["TR3_SESSION"], child.pid)
            return child

        def fake(_candidates, **_kwargs):
            return {"diagnostic": None, "observation": {"completeness": "complete"},
                    "limits": gfx.LIMITS}

        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.object(tr3_run.gfx, "interpret", side_effect=fake):
                status, out, _build = self._launch(tmp, popen)
            payload = json.loads(out.read_text())
        self.assertEqual(status, 0)
        self.assertEqual(payload["outcome"], "captured_complete")
        self.assertEqual(payload["qualification"], "not claimed")


class _Child:
    def __init__(self) -> None:
        self.pid = 4242
        self.returncode = None
        self.terminated = False
        self.killed = False
        self.polls: list = []

    def poll(self):
        self.polls.append(self.returncode)
        return self.returncode

    def terminate(self) -> None:
        self.terminated = True
        if self.returncode is None:
            self.returncode = -15

    def kill(self) -> None:
        self.killed = True
        self.returncode = -9

    def wait(self, timeout: float | None = None) -> int:
        if self.returncode is None:
            raise subprocess.TimeoutExpired(cmd="./eu4", timeout=timeout)
        return self.returncode


if __name__ == "__main__":
    unittest.main()
