"""P4.10 A2: one synthetic paired-state capture, one descriptor-closed fixture.

The positive tests pin the contract's two execution paths and its declared evidence
semantics: a qualified fixture is schema-qualified and descriptor-closed, the
explicit path does not require a buffer read, and closure deliberately does not
recompute the function's word count. The descriptor is checked against the frozen
RE0 pilot, the reviewed differential layout and the pinned binary.
"""
from __future__ import annotations

import copy
import json
import unittest
from pathlib import Path

from oracle.function import fixture, synthetic
from oracle.recorder import schema

ROOT = Path(__file__).resolve().parents[1]
GOLDEN = ROOT / "oracle" / "function" / "synthetic" / "golden.txt"
TARGET = ROOT / "reverse_engineering" / "target.json"
DIFFERENTIAL = (ROOT / "analysis" / "reconstruction" / "_GfxSetConstantBuffer"
                / "differential" / "scenarios.json")
PILOT = (ROOT / "analysis" / "reconstruction" / "_GfxSetConstantBuffer" / "pilot"
         / "scenarios.json")


def _json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _hex(value: str) -> int:
    return int(value, 16)


def _post_field(record: dict, descriptor: dict, field: str, slot: int) -> int:
    layout = descriptor["layout"]
    spec = descriptor["post_state"][field]
    offset = spec.get("offset")
    if offset is None:
        offset = spec["base"] + slot * spec["stride"]
    span = next(span for span in record["state"]["post"]
                if span["space"] == "heap:ctx")
    data = bytes.fromhex(span["value_hex"])
    start = offset - layout["observation_start"]
    return int.from_bytes(data[start:start + spec["width"]], "little")


class GoldenFixtureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.descriptor = fixture.load_descriptor()
        cls.golden = GOLDEN.read_text(encoding="utf-8")
        cls.materialized = fixture.materialize_capture(cls.golden, cls.descriptor)
        cls.record = cls.materialized["record"]
        cls.manifest = cls.materialized["manifest"]

    def test_the_committed_golden_is_the_producer_output(self):
        self.assertEqual(self.golden, synthetic.capture_text(self.descriptor, "s03"))
        self.assertNotIn("EFFECT", self.golden)
        self.assertNotIn("effect", self.golden.lower())

    def test_the_golden_materializes_to_a_closed_fixture(self):
        record, manifest = self.record, self.manifest
        self.assertEqual(manifest["verdict"], "qualified")
        self.assertEqual(manifest["record_digest"], schema.record_digest(record))
        self.assertEqual(manifest["domain_digest"],
                         fixture.descriptor_digest(self.descriptor))
        self.assertTrue(schema.qualifies_for_semantic(record))
        schema.semantic_digest(record)
        self.assertEqual(record["completeness"],
                         {"state": "captured", "losses": 0,
                          "unavailable_dependencies": []})
        self.assertEqual(record["effects"], {"ordered": [], "complete": True})
        self.assertIsNone(record["abi"]["return_carrier"])
        for required in ("carrier:RDI", "carrier:RSI", "carrier:RCX", "carrier:R9",
                         "read:handle", "read:ctx", "read:canary-before",
                         "read:canary-after", "read:buffer", "post:word_count",
                         "post:buffer", "post:dirty", "preserve:ctx",
                         "preserve:handle", "preserve:buffer",
                         "preserve:canary-before", "preserve:canary-after",
                         "outcome:RAX", "domain:slot", "domain:disjoint"):
            self.assertIn(required, manifest["coverage"])
        self.assertEqual(len(manifest["coverage"]), 20)

    def test_verify_fixture_recomputes_the_manifest(self):
        self.assertEqual(fixture.verify_fixture(self.golden, self.descriptor,
                                                self.manifest), self.manifest)

    def test_the_explicit_path_does_not_require_a_buffer_read(self):
        text = synthetic.capture_text(self.descriptor, "s10")
        materialized = fixture.materialize_capture(text, self.descriptor)
        self.assertEqual(materialized["manifest"]["verdict"], "qualified")
        self.assertNotIn("read:buffer", materialized["manifest"]["coverage"])
        self.assertNotIn("preserve:buffer", materialized["manifest"]["coverage"])
        self.assertIsNone(materialized["record"]["raw"]["addresses"]["buffer"])
        self.assertEqual(_post_field(materialized["record"], self.descriptor,
                                     "word_count", 3), 4)
        self.assertEqual(_post_field(materialized["record"], self.descriptor,
                                     "buffer", 3), synthetic.BUFFER)

    def test_the_record_says_it_is_synthetic_and_rax_is_an_outcome(self):
        record = self.record
        self.assertIs(record["raw"]["synthetic"], True)
        self.assertEqual(record["raw"]["symbol"], "_GfxSetConstantBuffer")
        self.assertEqual(record["raw"]["outcome"]["register"], "RAX")
        self.assertEqual(record["raw"]["outcome"]["confidence"], "observed")
        self.assertEqual(int(record["raw"]["outcome"]["value_hex"], 16),
                         synthetic.CTX)
        self.assertEqual(record["provenance"]["function_id"],
                         "gfx:set-constant-buffer")
        self.assertNotIn("return", record["raw"])

    def test_closure_does_not_recompute_the_word_count(self):
        record = copy.deepcopy(self.record)
        layout = self.descriptor["layout"]
        span = next(span for span in record["state"]["post"]
                    if span["space"] == "heap:ctx")
        data = bytearray.fromhex(span["value_hex"])
        start = (layout["word_count_base"] + 2 * layout["slot_stride"]
                 - layout["observation_start"])
        data[start:start + 4] = (99).to_bytes(4, "little")
        span["value_hex"] = data.hex()
        assessment = fixture.assess(record, self.descriptor)
        self.assertNotIn("post:word_count", assessment["missing"])
        self.assertEqual(fixture.close(record, self.descriptor)["verdict"],
                         "qualified")


class FrozenDescriptorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.descriptor = fixture.load_descriptor()
        cls.target = _json(TARGET)
        cls.differential = _json(DIFFERENTIAL)
        cls.pilot = {scenario["id"]: scenario
                     for scenario in _json(PILOT)["scenarios"]}
        cls.materialized = fixture.materialize_capture(
            GOLDEN.read_text(encoding="utf-8"), cls.descriptor)

    def test_binary_and_code_pins_match_the_repository_target(self):
        window = self.target["code_windows"]["_GfxSetConstantBuffer"]
        binary = self.descriptor["binary"]
        self.assertEqual(binary["sha256"], self.target["sha256"])
        self.assertEqual(_hex(binary["entry_rva"]), _hex(window["entry_rva"]))
        self.assertEqual(_hex(binary["end_rva"]), _hex(window["end_rva"]))
        self.assertEqual(binary["code_sha256"], window["sha256"])
        self.assertEqual(_hex(binary["image_base"]), self.target["image_base"])

    def test_layout_matches_the_reviewed_differential_contract(self):
        layout = self.descriptor["layout"]
        frozen = self.differential["layout"]
        for key in ("word_count_base", "pointer_base", "dirty", "observation_start",
                    "observation_end", "canary_before_start"):
            self.assertEqual(layout[key], _hex(frozen[key]), key)
        self.assertEqual(layout["slot_stride"], frozen["slot_stride"])
        self.assertEqual(layout["canary_before_end"], _hex(frozen["observation_start"]))
        self.assertEqual(layout["canary_after_start"], _hex(frozen["observation_end"]))
        self.assertEqual(layout["canary_after_end"], _hex(frozen["canary_after_end"]))
        self.assertEqual((layout["slot_min"], layout["slot_max"]), (0, 6))
        self.assertEqual(
            [carrier["name"] for carrier in self.descriptor["abi"]["required_carriers"]],
            ["RDI", "RSI", "RCX", "R9"])
        self.assertEqual(self.descriptor["abi"]["ignored_carriers"], ["RDX", "R8"])
        self.assertEqual(
            self.descriptor["input_regions"]["buffer"]["selector_value"], _hex("0xffffffff"))

    def test_golden_is_the_frozen_pilot_scenario(self):
        scenario = self.pilot["s03"]
        self.assertEqual(scenario["slot"], 2)
        self.assertEqual(_hex(scenario["r9d"]), 0xffffffff)
        self.assertEqual(_hex(scenario["buffer_first_dword"]), 13)
        self.assertEqual(_hex(scenario["expected_words"]), 4)
        self.assertEqual(synthetic.SCENARIOS["s03"]["slot"], scenario["slot"])
        self.assertEqual(synthetic.SCENARIOS["s03"]["r9d"], _hex(scenario["r9d"]))
        self.assertEqual(synthetic.SCENARIOS["s03"]["buffer_dword"],
                         _hex(scenario["buffer_first_dword"]))
        self.assertEqual(synthetic.SCENARIOS["s03"]["words"],
                         _hex(scenario["expected_words"]))
        record = self.materialized["record"]
        self.assertEqual(_post_field(record, self.descriptor, "word_count", 2), 4)
        self.assertEqual(_post_field(record, self.descriptor, "buffer", 2),
                         synthetic.BUFFER)
        self.assertEqual(_post_field(record, self.descriptor, "dirty", 2), 1)

    def test_explicit_variant_is_the_frozen_pilot_scenario(self):
        scenario = self.pilot["s10"]
        self.assertEqual(scenario["slot"], 3)
        self.assertEqual(_hex(scenario["r9d"]), 13)
        self.assertIsNone(scenario["buffer_first_dword"])
        self.assertEqual(_hex(scenario["expected_words"]), 4)
        self.assertEqual(synthetic.SCENARIOS["s10"]["slot"], scenario["slot"])
        self.assertEqual(synthetic.SCENARIOS["s10"]["r9d"], _hex(scenario["r9d"]))
        self.assertEqual(synthetic.SCENARIOS["s10"]["words"],
                         _hex(scenario["expected_words"]))
        record = fixture.materialize_capture(
            synthetic.capture_text(self.descriptor, "s10"), self.descriptor)["record"]
        self.assertEqual(_post_field(record, self.descriptor, "word_count", 3), 4)




if __name__ == "__main__":
    unittest.main()
