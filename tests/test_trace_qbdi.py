#!/usr/bin/env python3
"""TR0: QBDI under Rosetta probe and receipt invariants (offline, macOS/x86_64)."""
from __future__ import annotations

import copy
import json
import re
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from reconstruction_trace.qbdi import producer  # noqa: E402

HEX = re.compile(r"^0x(0|[1-9a-f][0-9a-f]*)$")
RESULTS = {"expected_accesses_match", "instrumentation_complete", "fixture_qualified"}


def committed_receipt() -> dict:
    return json.loads((producer.HERE / "receipt.json").read_text())


def instruction(raw: dict, mode: str, mnemonic: str) -> dict:
    run = next(r for r in raw["runs"] if r["mode"] == mode)
    return next(i for i in run["instructions"] if i["mnemonic"] == mnemonic)


class TraceQbdiReceiptTests(unittest.TestCase):
    def assert_observation_encoding(self, observation: dict) -> None:
        self.assertEqual(observation["schema"], "execution-observation/v1")
        self.assertEqual(observation["evidence"], "qbdi-rosetta")
        self.assertIsNone(observation["binary"])
        for value in observation["entry_state"]["registers"].values():
            self.assertRegex(value, HEX)
        for region in observation["entry_state"]["regions"]:
            self.assertRegex(region["base"], HEX)
            self.assertEqual(len(region["bytes"]), 2 * region["size"])
        open_frames = []
        for event in observation["events"]:
            if event["kind"] == "call_enter":
                open_frames.append(event["target"])
            elif event["kind"] == "call_exit":
                self.assertEqual(event["target"], open_frames.pop())
            else:
                self.assertIn(event["kind"], ("read", "write"))
                self.assertRegex(event["address"], HEX)
                if "value" in event:
                    self.assertEqual(len(event["value"]), 2 * event["size"])
                    self.assertEqual(event["value_source"], "observed")
        if observation["completeness"] == "complete":
            self.assertEqual(open_frames, [])
            self.assertEqual(observation["incomplete_reasons"], [])
            self.assertEqual(observation["unresolved_dependencies"], [])
        else:
            self.assertTrue(observation["incomplete_reasons"]
                            or observation["unresolved_dependencies"])

    def assert_receipt(self, receipt: dict, out_dir: Path) -> None:
        self.assertEqual(receipt["schema"], producer.RECEIPT_SCHEMA)
        self.assertIn(receipt["decision"], producer.OUTCOMES)
        self.assertRegex(receipt["probe"]["executable_sha256"], r"^[0-9a-f]{64}$")
        self.assertEqual(receipt["qbdi"]["version"], "0.12.1")
        self.assertEqual(receipt["host"]["rosetta"]["sysctl.proc_translated"], 1)
        self.assertTrue(receipt["commands"]["build"] and receipt["commands"]["run"])
        self.assertIsNone(receipt["measurements"]["slowdown_threshold"])
        self.assertGreater(receipt["measurements"]["peak_rss_bytes"], 0)
        self.assertGreater(receipt["measurements"]["vm_call_ns"], 0)
        self.assertLessEqual({"threads", "signals", "exceptions"}, set(receipt["qbdi_limits"]))
        self.assertIn(receipt["interception"]["result"], ("supported", "unsupported"))
        if receipt["interception"]["result"] == "unsupported":
            self.assertTrue(receipt["interception"]["reason"])

        for mode in producer.MODES:
            run = receipt["runs"][mode]
            self.assertEqual(set(run["results"]), RESULTS)
            for result in run["results"].values():
                self.assertIsInstance(result["value"], bool)
            self.assertFalse(run["results"]["fixture_qualified"]["value"])
            if run["completeness"] != "complete":
                self.assertIn("trace is incomplete",
                              run["results"]["fixture_qualified"]["blocked_by"])
            for flagged in run["results"]["instrumentation_complete"]["flagged_accesses"]:
                self.assertIsInstance(flagged["flags"], int)
                self.assertTrue(flagged["flag_names"])
            boundary = run["call_boundary"]
            self.assertIn(boundary["result"], ("supported", "unsupported"))
            if boundary["result"] == "unsupported":
                self.assertTrue(boundary["reason"].startswith("call-boundary unsupported"))
            else:
                self.assertEqual(boundary["call_enter"]["target"], boundary["helper_target"])
                self.assertEqual(boundary["call_exit"]["target"], boundary["helper_target"])
                self.assertTrue(boundary["declared_nested_accesses_inside"])
            path = out_dir / run["observation"]
            self.assertEqual(producer.sha256_file(path), run["observation_sha256"])
            observation = json.loads(path.read_text())
            self.assertEqual(observation["completeness"], run["completeness"])
            self.assert_observation_encoding(observation)

    def test_committed_receipt_invariants(self) -> None:
        self.assert_receipt(committed_receipt(), producer.HERE)

    def test_committed_receipt_is_derived_from_its_probe_output(self) -> None:
        receipt = committed_receipt()
        evaluation = producer.evaluate(receipt["probe_output"])
        self.assertEqual(evaluation["decision"], receipt["decision"])
        self.assertEqual(evaluation["interception"], receipt["interception"])
        for mode in producer.MODES:
            committed = {k: v for k, v in receipt["runs"][mode].items()
                         if not k.startswith("observation")}
            self.assertEqual(evaluation["runs"][mode], committed)
            observation = json.loads((producer.HERE / receipt["runs"][mode]["observation"])
                                     .read_text())
            self.assertEqual(evaluation["observations"][mode], observation)

    def test_unknown_value_flag_stays_explicit_and_incomplete(self) -> None:
        raw = copy.deepcopy(committed_receipt()["probe_output"])
        imul = instruction(raw, "vm_call", "IMUL64rm")
        imul["accesses"][0]["flags"] = producer.MEMORY_UNKNOWN_VALUE
        evaluation = producer.evaluate(raw)
        run = evaluation["runs"]["vm_call"]
        self.assertEqual(run["completeness"], "incomplete")
        self.assertFalse(run["results"]["instrumentation_complete"]["value"])
        self.assertFalse(run["results"]["expected_accesses_match"]["value"])
        self.assertFalse(run["results"]["fixture_qualified"]["value"])
        self.assertIn("trace is incomplete", run["results"]["fixture_qualified"]["blocked_by"])
        flagged = run["results"]["instrumentation_complete"]["flagged_accesses"]
        self.assertEqual([f["flags"] for f in flagged], [producer.MEMORY_UNKNOWN_VALUE])
        self.assertEqual(flagged[0]["flag_names"], ["MEMORY_UNKNOWN_VALUE"])
        event = next(e for e in evaluation["observations"]["vm_call"]["events"]
                     if e.get("pc") == imul["pc"])
        self.assertNotIn("value", event)
        self.assertEqual(evaluation["decision"], producer.OUTCOMES[1])

    def _unusable_width(self, flags: int, size: int) -> None:
        raw = copy.deepcopy(committed_receipt()["probe_output"])
        access = instruction(raw, "vm_call", "IMUL64rm")["accesses"][0]
        pc = access["inst_address"]
        access["flags"] = flags
        access["size"] = size
        evaluation = producer.evaluate(raw)
        run = evaluation["runs"]["vm_call"]
        self.assertEqual(run["completeness"], "incomplete")
        self.assertFalse(run["results"]["instrumentation_complete"]["value"])
        self.assertFalse(run["results"]["fixture_qualified"]["value"])
        events = [event for event in evaluation["observations"]["vm_call"]["events"]
                  if event.get("pc") == pc and event["kind"] in ("read", "write")]
        self.assertEqual(events, [])
        flagged = [row for row in run["results"]["instrumentation_complete"]["flagged_accesses"]
                   if row["address"] == access["address"]]
        self.assertEqual([(row["size"], row["flags"], row["type"]) for row in flagged],
                         [(size, flags, access["type"])])

    def test_unknown_size_is_omitted_and_stays_in_the_receipt(self) -> None:
        self._unusable_width(producer.MEMORY_UNKNOWN_SIZE, 0)

    def test_non_positive_size_without_flags_is_not_complete(self) -> None:
        self._unusable_width(0, 0)

    def test_known_answers_do_not_imply_instrumentation_complete(self) -> None:
        raw = copy.deepcopy(committed_receipt()["probe_output"])
        instruction(raw, "vm_call", "PUSH64r")["accesses"].clear()
        run = producer.evaluate(raw)["runs"]["vm_call"]
        self.assertTrue(run["results"]["expected_accesses_match"]["value"])
        self.assertFalse(run["results"]["instrumentation_complete"]["value"])
        self.assertEqual(run["completeness"], "incomplete")
        self.assertFalse(run["results"]["fixture_qualified"]["value"])

    def test_missing_call_boundary_is_reported_unsupported(self) -> None:
        raw = copy.deepcopy(committed_receipt()["probe_output"])
        instruction(raw, "vm_call", "CALL64pcrel32")["is_call"] = False
        evaluation = producer.evaluate(raw)
        run = evaluation["runs"]["vm_call"]
        self.assertEqual(run["call_boundary"]["result"], "unsupported")
        self.assertTrue(run["call_boundary"]["reason"].startswith("call-boundary unsupported"))
        self.assertTrue(run["results"]["expected_accesses_match"]["value"])
        self.assertEqual(evaluation["decision"], producer.OUTCOMES[1])
        self.assertNotEqual(evaluation["decision"], producer.OUTCOMES[0])

    def test_wrong_known_answer_is_not_a_reliable_trace(self) -> None:
        raw = copy.deepcopy(committed_receipt()["probe_output"])
        access = instruction(raw, "vm_call", "IMUL64rm")["accesses"][0]
        access["value"] = "0x0"
        evaluation = producer.evaluate(raw)
        run = evaluation["runs"]["vm_call"]
        self.assertFalse(run["results"]["expected_accesses_match"]["value"])
        self.assertEqual(evaluation["decision"], producer.OUTCOMES[1])
        self.assertNotEqual(evaluation["decision"], producer.OUTCOMES[0])

    def test_dropped_instruction_is_not_a_reliable_trace(self) -> None:
        raw = copy.deepcopy(committed_receipt()["probe_output"])
        run_raw = next(item for item in raw["runs"] if item["mode"] == "vm_call")
        run_raw["dropped_instructions"] = 1
        evaluation = producer.evaluate(raw)
        run = evaluation["runs"]["vm_call"]
        self.assertFalse(run["results"]["instrumentation_complete"]["value"])
        self.assertEqual(evaluation["decision"], producer.OUTCOMES[1])
        self.assertNotEqual(evaluation["decision"], producer.OUTCOMES[0])

    def test_execution_transfer_stays_an_unresolved_dependency(self) -> None:
        raw = copy.deepcopy(committed_receipt()["probe_output"])
        run_raw = next(item for item in raw["runs"] if item["mode"] == "vm_call")
        run_raw["exec_transfers"] = ["0x1"]
        evaluation = producer.evaluate(raw)
        run = evaluation["runs"]["vm_call"]
        self.assertFalse(run["results"]["instrumentation_complete"]["value"])
        self.assertEqual(evaluation["decision"], producer.OUTCOMES[4])
        self.assertNotEqual(evaluation["decision"], producer.OUTCOMES[1])

    def test_probe_rebuilds_and_runs_under_rosetta(self) -> None:
        reason = producer.unsupported_host_reason()
        if reason:
            self.skipTest(reason)
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "out"
            try:
                receipt = producer.produce(out, Path(tmp) / "build")
            except producer.HostUnsupported as exc:
                self.skipTest(str(exc))
            self.assert_receipt(receipt, out)
            self.assertEqual(receipt["declared_expectations"]["native_result"],
                             producer.expected_result())
            vm_call = receipt["runs"]["vm_call"]
            self.assertEqual(vm_call["completeness"], "complete")
            self.assertTrue(vm_call["results"]["expected_accesses_match"]["value"])
            self.assertTrue(vm_call["results"]["instrumentation_complete"]["value"])


if __name__ == "__main__":
    unittest.main()
