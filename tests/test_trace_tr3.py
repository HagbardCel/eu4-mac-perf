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
import subprocess
import tempfile
import unittest
from pathlib import Path

from reconstruction_trace import fixture, schema
from reconstruction_trace.tr3 import adapter, gfx, image, native, observe, path, startup
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

    def test_native_identity_requires_this_capture(self) -> None:
        site = 0x100000000 + 0x5000 + 0x15EC36A
        image_meta = {"preferred_base": hex(0x100000000), "slide": hex(0x5000),
                      "header": hex(0x100000000 + 0x5000), "entry_rva": hex(0x15EC36A),
                      "runtime_site": hex(site)}
        captured = {
            "evidence": "qbdi-rosetta",
            "expected_file_sha256": gfx.PIN_FILE, "expected_window_sha256": gfx.PIN_WINDOW,
            "file_sha256": gfx.PIN_FILE, "window_sha256": gfx.PIN_WINDOW,
            "window": bytes(75), "image": image_meta,
            "run": {"ran": True, "entered": True, "dropped_instructions": 0,
                    "dropped_accesses": 0, "dropped_transfers": 0, "regions": [],
                    "pointers": [], "instructions": [], "entry_gpr": {}, "exit_gpr": {}},
        }
        cases = (
            {**captured, "file_sha256": "ab" * 32},
            {**captured, "window_sha256": "cd" * 32},
            {**captured, "image": {**image_meta, "runtime_site": hex(site + 0x5000)}},
            {key: value for key, value in captured.items() if key != "image"},
            {**captured, "window": bytes(range(75))},
        )
        for candidate in cases:
            result = gfx.interpret([candidate])
            self._assert_not_native(result)
            self.assertIsNotNone(result["diagnostic"])

    def test_captured_handle_does_not_invent_a_context(self) -> None:
        handle, stack, context = 0x100000, 0x400000, 0x200000
        candidate = {
            "evidence": "qbdi-rosetta",
            "expected_file_sha256": "11" * 32, "expected_window_sha256": "22" * 32,
            "file_sha256": "11" * 32, "window_sha256": "22" * 32, "window": bytes(75),
            "supplied_regions": [_heap("ctx", context, bytes(0x130))],
            "run": {
                "ran": True, "entered": True, "dropped_instructions": 0,
                "dropped_accesses": 0, "dropped_transfers": 0,
                "regions": [
                    {"id": "handle", "kind": "heap", "base": hex(handle), "size": 8,
                     "bytes": "00" * 8},
                    {"id": "stack", "kind": "stack", "base": hex(stack), "size": 256,
                     "bytes": "00" * 256, "preservation": "excluded"},
                ],
                "pointers": [
                    {"from": {"register": "rdi"}, "to": {"region": "handle", "offset": 0}},
                    {"from": {"register": "rsp"}, "to": {"region": "stack", "offset": 248}},
                ],
                "entry_gpr": {"rdi": hex(handle), "rsp": hex(stack + 248)},
                "exit_gpr": {},
                "instructions": [{
                    "pc": "0x1000", "inst_size": 4,
                    "accesses": [{"type": 2, "address": hex(context), "size": 4,
                                  "value": "01000000"}],
                }],
            },
        }
        result = gfx.interpret([candidate])
        observation = result["observation"]
        schema.validate(observation)
        self._assert_not_native(result)
        self.assertEqual([region["id"] for region in observation["entry_state"]["regions"]],
                         ["handle", "stack"])
        self.assertEqual(observation["completeness"], "incomplete")
        self.assertTrue(observation["unresolved_dependencies"])
        self.assertFalse(fixture.qualify(observation)["fixture_qualified"])
        self.assertIn("untraced", result["limits"])

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


if __name__ == "__main__":
    unittest.main()
