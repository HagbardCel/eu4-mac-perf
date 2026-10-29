#!/usr/bin/env python3
"""TR2: qualify one committed QBDI trace and replay its original function."""
from __future__ import annotations

import copy
import hashlib
import json
import shutil
import tempfile
import unittest
from pathlib import Path

from reconstruction_trace import fixture, schema
from reconstruction_trace.harness import native
from reconstruction_trace.qbdi import producer

OBSERVATION = producer.HERE / "observation-vm_call.json"
# The helper writes this stack slot and a later instruction reads it back.
# The store to the output pointer is not read again, so it cannot show the
# disagreement.
WRITE_THEN_READ = "0x7fa3e8107f50"
MISSING_INPUT = "0x7fa3e2f077d8"
STUB = """\
.section __TEXT,__text
.globl _trace_subject_tr0_chase
_trace_subject_tr0_chase:
    jmp _tr0_chase
"""


def _accept(trace: dict) -> dict:
    if trace["evidence"] != "qbdi-rosetta":
        raise AssertionError("a Ghidra trace does not pass this integration")
    schema.validate(trace)
    result = fixture.qualify(trace)
    if not result["fixture_qualified"]:
        raise AssertionError(result["reasons"])
    return result["fixture"]


class QbdiFixtureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.workdir = Path(tempfile.mkdtemp(prefix="eu4-tr2-qbdi-"))
        stub = cls.workdir / "stub.S"
        stub.write_text(STUB)
        try:
            cls.harness = native.load(cls.workdir, (stub, producer.HERE / "probe_subject.c"))
        except native.Unavailable as reason:
            shutil.rmtree(cls.workdir, ignore_errors=True)
            raise unittest.SkipTest(str(reason)) from reason

    @classmethod
    def tearDownClass(cls) -> None:
        shutil.rmtree(cls.workdir, ignore_errors=True)

    def test_replay_of_committed_qbdi_trace(self) -> None:
        receipt = json.loads((producer.HERE / "receipt.json").read_text())
        self.assertEqual(receipt["decision"], producer.OUTCOMES[0])
        self.assertEqual(receipt["interception"]["result"], "supported")
        expected_rax = receipt["declared_expectations"]["exit_rax"]
        expected_out = receipt["declared_expectations"]["native_result"]
        self.assertEqual(expected_rax, "0x96")
        self.assertEqual(expected_out, 150)

        original_bytes = OBSERVATION.read_bytes()
        original_sha = hashlib.sha256(original_bytes).hexdigest()
        trace = json.loads(original_bytes)
        self.assertEqual(trace["completeness"], "complete")
        self.assertEqual(trace["function_id"], "synth:tr0_chase")
        built = _accept(trace)
        ghidra = copy.deepcopy(trace)
        ghidra["evidence"] = "ghidra-pcode"
        with self.assertRaises(AssertionError):
            _accept(ghidra)

        replayed = self.harness.replay(built)
        self.assertEqual(native.compare(built, replayed), [])
        self.assertEqual(replayed["registers"]["rax"], expected_rax)
        observed = int.from_bytes(bytes.fromhex(replayed["regions"]["heap3"]), "little")
        self.assertEqual(observed, expected_out)

        missing = copy.deepcopy(trace)
        region = next(item for item in missing["entry_state"]["regions"] if item["id"] == "heap2")
        del region["bytes"]
        region["bytes_absent"] = True
        rejected = fixture.qualify(schema.validate(missing))
        self.assertFalse(rejected["fixture_qualified"])
        self.assertIn({"address": MISSING_INPUT, "size": 8,
                       "reason": "read of absent initial bytes"},
                      rejected["unresolved_dependencies"])

        written = next(event for event in trace["events"]
                       if event["kind"] == "write" and event["address"] == WRITE_THEN_READ)
        self.assertEqual(written["size"], 8)
        broken = copy.deepcopy(trace)
        broken["events"] = [event for event in broken["events"]
                            if not (event["kind"] == "write" and event["address"] == WRITE_THEN_READ
                                    and event["pc"] == written["pc"])]
        disagreed = fixture.qualify(schema.validate(broken))
        self.assertFalse(disagreed["fixture_qualified"])
        self.assertTrue(any(
            reason.startswith(f"read at {WRITE_THEN_READ} ") and WRITE_THEN_READ in reason
            for reason in disagreed["reasons"]))

        self.assertEqual(hashlib.sha256(OBSERVATION.read_bytes()).hexdigest(), original_sha)


if __name__ == "__main__":
    unittest.main()
