#!/usr/bin/env python3
"""TR2: one synthetic caller and callee, checked against their original execution.

The call-event addresses are a synthetic map this test owns. They are not
observed process addresses and they are not native symbol resolution.
"""
from __future__ import annotations

import hashlib
import json
import shutil
import struct
import tempfile
import unittest
from pathlib import Path

from reconstruction_trace import fixture, schema
from reconstruction_trace.harness import native

INITIAL = 5
STACK_SIZE = 64
# Synthetic map. Not the addresses of the loaded subjects.
CALLEE = "0x3000"
DIRECT_PC = "0x2000"
INDIRECT_PC = "0x2005"
CALLEE_SOURCE = """\
    movl (%rdi), %eax
    incl %eax
    movl %eax, (%rdi)
    ret
"""
CALLER_CANDIDATE = "call qualified callee; call qualified callee"
QUALIFICATION_SCOPE = "one increment of the dword at rdi"
COMPARATOR = "tr2-composition-v1"
HARNESS_ID = "reconstruction_trace.harness.native"
CALLER_SOURCE = """\
.globl _trace_subject_caller
_trace_subject_caller:
    leaq _trace_subject_callee(%rip), %rsi
    call _trace_subject_callee
    call *%rsi
    movl (%rdi), %eax
    ret
"""
ASSEMBLY = (
    ".section __TEXT,__text\n"
    ".globl _trace_subject_callee\n"
    "_trace_subject_callee:\n"
    + CALLEE_SOURCE
    + CALLER_SOURCE
)


def _regions() -> list[dict]:
    return [
        {"id": "buf", "kind": "heap", "size": 4, "bytes": struct.pack("<I", INITIAL).hex()},
        {"id": "stack", "kind": "stack", "size": STACK_SIZE, "bytes": bytes(STACK_SIZE).hex()},
    ]


def _pointers() -> list[dict]:
    return [
        {"from": {"register": "rdi"}, "to": {"region": "buf", "offset": 0}},
        {"from": {"register": "rsp"}, "to": {"region": "stack", "offset": STACK_SIZE}},
    ]


def _entry(stack_preservation: str) -> dict:
    bases = {"buf": 0x100000, "stack": 0x200000}
    registers = {name: "0x0" for name in native.REGISTERS}
    registers["rdi"] = hex(bases["buf"])
    registers["rsp"] = hex(bases["stack"] + STACK_SIZE)
    return {
        "registers": registers,
        "regions": [
            {"id": "buf", "kind": "heap", "base": hex(bases["buf"]), "size": 4,
             "bytes": struct.pack("<I", INITIAL).hex(), "preservation": "required"},
            {"id": "stack", "kind": "stack", "base": hex(bases["stack"]), "size": STACK_SIZE,
             "bytes": bytes(STACK_SIZE).hex(), "preservation": stack_preservation},
        ],
        "pointers": _pointers(),
    }


def _write(pc: str, address: str, value: int) -> dict:
    return {"kind": "write", "pc": pc, "address": address, "size": 4,
            "value": struct.pack("<I", value).hex(), "value_source": "observed"}


def _trace(function_id: str, entry: dict, events: list[dict], rax: int) -> dict:
    return {
        "schema": schema.SCHEMA, "function_id": function_id, "binary": None,
        "evidence": "handwritten-synthetic",
        "instrumentation": {"name": "handwritten", "version": "v1"},
        "entry_state": entry, "events": events,
        "exit_state": {"registers": {"rax": hex(rax)}},
        "completeness": "complete", "incomplete_reasons": [], "unresolved_dependencies": [],
    }


def _callee_trace() -> dict:
    entry = _entry("required")
    base = int(entry["regions"][0]["base"], 16)
    address = hex(base)
    read = {"kind": "read", "pc": "0x1000", "address": address, "size": 4,
            "value": struct.pack("<I", INITIAL).hex(), "value_source": "observed"}
    return _trace("synth:callee", entry, [read, _write("0x1004", address, INITIAL + 1)],
                  INITIAL + 1)


def _pair(pc: str, value: int) -> list[dict]:
    entry = _entry("excluded")
    address = entry["regions"][0]["base"]
    return [
        {"kind": "call_enter", "pc": pc, "target": CALLEE},
        {"kind": "call_exit", "target": CALLEE, "effects_complete": True,
         "effects": [_write(pc, address, value)]},
    ]


def _caller_trace() -> dict:
    # The caller trace records the effect each call leaves. It does not record
    # the callee body's instructions.
    events = [*_pair(DIRECT_PC, INITIAL + 1), *_pair(INDIRECT_PC, INITIAL + 2)]
    return _trace("synth:caller", _entry("excluded"), events, INITIAL + 2)


def _dword(recorded: dict) -> int:
    region = next(item for item in recorded["exit_state"]["regions"] if item["id"] == "buf")
    return int.from_bytes(bytes.fromhex(region["bytes"])[:4], "little")


def _qualified(trace: dict) -> dict:
    schema.validate(trace)
    result = fixture.qualify(trace)
    if not result["fixture_qualified"]:
        raise AssertionError(result["reasons"])
    return result["fixture"]


def _canonical(value: dict) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _qualification_note() -> dict:
    """Only an actually qualified callee fixture yields a qualification note."""
    trace = _callee_trace()
    schema.validate(trace)
    result = fixture.qualify(trace)
    if not result["fixture_qualified"]:
        raise AssertionError(result["reasons"])
    return {
        "outcome": "qualified" if result["fixture_qualified"] else "rejected",
        "scope": QUALIFICATION_SCOPE,
        "fixture_sha256": hashlib.sha256(_canonical(result["fixture"])).hexdigest(),
    }


def _identity(callee_digest: str, caller_fixture: dict, qualification_note: dict) -> str:
    payload = {
        "caller_candidate": CALLER_CANDIDATE,
        "callee_implementation_digests": [callee_digest],
        "callee_qualification": qualification_note,
        "comparator_version": COMPARATOR,
        "fixtures": {"caller": caller_fixture},
        "harness": HARNESS_ID,
    }
    return hashlib.sha256(_canonical(payload)).hexdigest()


def _original_callee(value: int) -> int:
    return (value + 1) & 0xffffffff


def _defective_callee(value: int) -> int:
    return value


def _reconstructed_caller(value: int, callee) -> tuple[int, int]:
    """Return the final buffer dword and RAX; do not embed the callee's body."""
    final_dword = callee(callee(value))
    return final_dword, final_dword


def _compare_caller(observed: tuple[int, int], caller_fixture: dict) -> list[str]:
    """Compare both declared caller observations against the qualified fixture."""
    buf_effects = [
        effect for effect in caller_fixture["effects"]
        if (effect["region"], effect["offset"], effect["size"]) == ("buf", 0, 4)
    ]
    if len(buf_effects) != 1:
        raise AssertionError(f"expected one four-byte buf effect, got {buf_effects!r}")
    expected_dword = int.from_bytes(bytes.fromhex(buf_effects[0]["bytes"]), "little")
    expected_rax = int(caller_fixture["exit_registers"]["rax"], 16)
    actual_dword, actual_rax = observed
    mismatches = []
    if actual_dword != expected_dword:
        mismatches.append(f"buf dword {actual_dword}, expected {expected_dword}")
    if actual_rax != expected_rax:
        mismatches.append(f"rax {hex(actual_rax)}, expected {hex(expected_rax)}")
    return mismatches


class CompositionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.workdir = Path(tempfile.mkdtemp(prefix="eu4-tr2-"))
        source = cls.workdir / "pair.S"
        source.write_text(ASSEMBLY)
        compiled_text = source.read_text()
        calls = (
            "    leaq _trace_subject_callee(%rip), %rsi\n",
            "    call _trace_subject_callee\n",
            "    call *%rsi\n",
        )
        positions = [compiled_text.index(instruction) for instruction in calls]
        if positions != sorted(positions) or len(set(positions)) != len(calls):
            raise AssertionError("direct and indirect caller instructions are out of order")
        try:
            cls.harness = native.load(cls.workdir, (source,))
        except native.Unavailable as reason:
            shutil.rmtree(cls.workdir, ignore_errors=True)
            raise unittest.SkipTest(str(reason)) from reason

    @classmethod
    def tearDownClass(cls) -> None:
        shutil.rmtree(cls.workdir, ignore_errors=True)

    def _original(self, function_id: str) -> int:
        recorded = self.harness.record(function_id, _regions(), _pointers(), {}, ["rax"])
        return _dword(recorded)

    def test_direct_and_indirect_calls_match_original(self) -> None:
        self.assertEqual(self._original("synth:callee"), INITIAL + 1)
        self.assertEqual(self._original("synth:caller"), INITIAL + 2)

        callee = _callee_trace()
        caller = _caller_trace()
        enters = [event for event in caller["events"] if event["kind"] == "call_enter"]
        exits = [event for event in caller["events"] if event["kind"] == "call_exit"]
        self.assertEqual([event["target"] for event in enters], [CALLEE, CALLEE])
        self.assertEqual([event["target"] for event in exits], [CALLEE, CALLEE])
        indirect = enters[1]
        self.assertEqual(indirect["pc"], INDIRECT_PC)
        self.assertNotEqual(indirect["pc"], indirect["target"])
        self.assertNotIn(CALLEE, [event["pc"] for event in caller["events"] if "pc" in event])

        for trace, expected in ((callee, INITIAL + 1), (caller, INITIAL + 2)):
            built = _qualified(trace)
            replayed = self.harness.replay(built)
            self.assertEqual(native.compare(built, replayed), [])
            self.assertEqual(int(replayed["registers"]["rax"], 16), expected)
        caller_fixture = _qualified(caller)
        self.assertEqual(
            _compare_caller(_reconstructed_caller(INITIAL, _original_callee), caller_fixture),
            [],
        )

    def test_defective_callee_mismatch(self) -> None:
        caller_fixture = _qualified(_caller_trace())
        caller_fixture_bytes = _canonical(caller_fixture)
        qualification_note = _qualification_note()
        self.assertEqual(qualification_note["outcome"], "qualified")
        self.assertEqual(qualification_note["scope"], QUALIFICATION_SCOPE)
        self.assertIn("fixture_sha256", qualification_note)

        # Hash exactly the callee source that the harness compiled.
        digest = hashlib.sha256(CALLEE_SOURCE.encode("utf-8")).hexdigest()
        qualified_identity = _identity(digest, caller_fixture, qualification_note)
        # Identity sensitivity only: this does not claim the defective callee is qualified.
        defective_identity = _identity(
            hashlib.sha256(b"defective callee implementation").hexdigest(),
            caller_fixture,
            qualification_note,
        )
        self.assertNotEqual(qualified_identity, defective_identity)
        self.assertEqual(_canonical(caller_fixture), caller_fixture_bytes)

        good = _compare_caller(
            _reconstructed_caller(INITIAL, _original_callee), caller_fixture
        )
        first = _compare_caller(
            _reconstructed_caller(INITIAL, _defective_callee), caller_fixture
        )
        second = _compare_caller(
            _reconstructed_caller(INITIAL, _defective_callee), caller_fixture
        )
        self.assertEqual(good, [])
        self.assertTrue(first)
        self.assertEqual(first, second)
        self.assertEqual(_canonical(caller_fixture), caller_fixture_bytes)


if __name__ == "__main__":
    unittest.main()
