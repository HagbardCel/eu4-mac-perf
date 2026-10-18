"""RE0 offline execution pilot (`_GfxSetConstantBuffer`, entry RVA 0x15ec36a).

Always (CI, no corpus): the frozen scenarios are internally consistent, both declared execution
paths are the reviewed listing walked from the entry, the declared observation windows cover
every write target, and the committed receipt validates against the frozen expectations.

With the local corpus present: executed instruction bytes are re-checked against the pinned
binary. With Ghidra and the analysed project present: the pilot is re-run twice and must
reproduce the committed receipt's canonical block.

Nothing here launches or attaches to the game.
"""

from __future__ import annotations

import json
import os
import re
import tempfile
import unittest
from pathlib import Path

from reverse_engineering import paths
from reverse_engineering import reconstruction_pilot as pilot
from reverse_engineering.macho_rva import MachOMapper, assert_pinned_executable

RECEIPT_PATH = pilot.PILOT_DIR / "observations-2026-10-18.json"
RECEIPT_PRESENT = RECEIPT_PATH.is_file()
BINARY_PRESENT = paths.executable_path().is_file()
PROJECT_PRESENT = pilot.default_project_dir().is_dir()


def ghidra_available() -> bool:
    root = os.environ.get("GHIDRA_INSTALL")
    if not root:
        return False
    return (Path(root).expanduser() / "support" / "analyzeHeadless").is_file()


def inventory_by_rva() -> dict[int, dict]:
    inventory = json.loads(pilot.INVENTORY_PATH.read_text())
    return {pilot.hexint(item["rva"]): item for item in inventory["instructions"]}


def derive_path(take_jump: bool) -> list[int]:
    """Walk the reviewed listing from the entry; the freeze must equal this walk.

    Sequential advance; a conditional jump takes its operand target (a VA) or falls through;
    an unconditional jump always takes its target; stop after the return.
    """
    scenarios = pilot.load_scenarios()
    listing = inventory_by_rva()
    base = scenarios["image_base"]
    pc = pilot.hexint(scenarios["entry_rva"])
    path: list[int] = []
    while True:
        instruction = listing[pc]
        path.append(pc)
        mnemonic = instruction["mnemonic"].upper()
        if mnemonic == "RET":
            return path
        if mnemonic in ("JZ", "JMP"):
            target = int(re.findall(r"0x[0-9a-f]+", instruction["operands"])[-1], 16) - base
            pc = target if (mnemonic == "JMP" or take_jump) else pc + instruction["length"]
        else:
            pc += instruction["length"]


class FrozenScenarioTests(unittest.TestCase):
    """Checks the frozen scenario file itself — the pilot's only source of expectations."""

    def setUp(self) -> None:
        self.scenarios = pilot.load_scenarios()

    def test_pins_the_target_binary(self) -> None:
        self.assertEqual(self.scenarios["schema"], "eu4-re0-pilot-scenarios/v1")
        self.assertEqual(self.scenarios["function"], "_GfxSetConstantBuffer")
        self.assertEqual(self.scenarios["binary_sha256"], paths.binary_sha256())
        self.assertEqual(self.scenarios["image_base"], paths.load_target()["image_base"])
        self.assertTrue(self.scenarios["frozen_before_execution"])

    def test_paths_are_the_reviewed_listing_walked(self) -> None:
        listing = inventory_by_rva()
        for name, take_jump in (("length_from_buffer", True), ("explicit_length", False)):
            declared = [pilot.hexint(rva) for rva in self.scenarios["paths"][name]["rvas"]]
            derived = derive_path(take_jump)
            self.assertEqual(declared, derived, f"{name}: frozen path is not the listing walk")
            self.assertEqual(self.scenarios["paths"][name]["length"], len(derived))
            self.assertEqual(
                self.scenarios["paths"][name]["instruction_bytes"],
                sum(listing[rva]["length"] for rva in derived),
            )
            self.assertEqual(declared[-1], pilot.hexint(self.scenarios["end_rva"]) - 1)
            self.assertEqual(listing[declared[-1]]["mnemonic"].upper(), "RET")

    def test_path_expectations_agree_across_the_two_paths(self) -> None:
        """Both paths end at the same original return instruction."""
        ends = {self.scenarios["paths"][name]["rvas"][-1] for name in self.scenarios["paths"]}
        self.assertEqual(ends, {"0x15ec3b4"})
        self.assertEqual(len(derive_path(True)), 15)
        self.assertEqual(len(derive_path(False)), 18)

    def test_scenario_inputs_agree_with_their_declared_path(self) -> None:
        slots = set()
        for scenario in self.scenarios["scenarios"]:
            self.assertIn(scenario["path"], self.scenarios["paths"])
            self.assertIn(scenario["slot"], range(6))
            slots.add(scenario["slot"])
            if scenario["path"] == "length_from_buffer":
                self.assertEqual(pilot.hexint(scenario["r9d"]), 0xFFFFFFFF)
                self.assertIsNotNone(scenario["buffer_first_dword"])
            else:
                self.assertNotEqual(pilot.hexint(scenario["r9d"]), 0xFFFFFFFF)
        self.assertEqual(slots, set(range(6)), "the freeze must sweep every declared slot")

    def test_hand_derived_expectations_are_internally_consistent(self) -> None:
        for scenario in self.scenarios["scenarios"]:
            words = pilot.hexint(scenario["expected_words"])
            self.assertLess(words, 1 << 32)
            # The final R9 is the 32-bit word count, zero-extended by the caller contract.
            self.assertEqual(pilot.hexint(scenario["expected_r9"]), words)
            if scenario["path"] == "length_from_buffer":
                # The buffer path never touches the arithmetic selector.
                self.assertEqual(pilot.hexint(scenario["expected_rdx"]), 0)

    def test_declared_windows_cover_every_write_target(self) -> None:
        layout = self.scenarios["layout"]
        ctx = pilot.hexint(self.scenarios["memory"]["ctx"])
        stride = layout["slot_stride"]
        canary_before = pilot.hexint(layout["canary_before_start"])
        canary_after = pilot.hexint(layout["canary_after_end"])
        window = set(range(ctx + canary_before, ctx + canary_after))
        for slot in range(6):
            for key in ("word_count_base", "pointer_base"):
                start = ctx + pilot.hexint(layout[key]) + slot * stride
                self.assertTrue(set(range(start, start + 8)) <= window, f"{key} slot {slot}")
        self.assertIn(ctx + pilot.hexint(layout["dirty"]), window)
        # Both canary bands sit outside the declared observation region.
        self.assertLessEqual(canary_before + 0x10, pilot.hexint(layout["observation_start"]))
        self.assertGreaterEqual(canary_after, pilot.hexint(layout["observation_end"]))

        rsp0 = pilot.hexint(self.scenarios["memory"]["rsp0"])
        before = pilot.hexint(layout["stack_window_before"])
        after = pilot.hexint(layout["stack_window_after"])
        self.assertTrue(rsp0 - before <= rsp0 - 8 and rsp0 + 8 <= rsp0 + after,
                        "stack window must cover the saved frame word and the return slot")

    def test_declared_access_sequences_have_the_expected_shape(self) -> None:
        self.assertEqual(
            [(item["base"], item["width"]) for item in self.scenarios["expected_stores"]],
            [("stack_minus_8", 8), ("ctx_words", 4), ("ctx_pointer", 8), ("ctx_dirty", 1)],
        )
        # The handle is read first on both paths; the buffer count only on the buffer path;
        # the saved frame word and the return slot are read by the epilogue.
        self.assertEqual(
            self.scenarios["expected_loads"]["length_from_buffer"],
            [
                {"base": "handle", "width": 8},
                {"base": "buffer", "width": 4},
                {"base": "stack_minus_8", "width": 8},
                {"base": "stack", "width": 8},
            ],
        )
        self.assertEqual(
            self.scenarios["expected_loads"]["explicit_length"],
            [
                {"base": "handle", "width": 8},
                {"base": "stack_minus_8", "width": 8},
                {"base": "stack", "width": 8},
            ],
        )


@unittest.skipUnless(RECEIPT_PRESENT, "committed pilot receipt not present")
class ReceiptTests(unittest.TestCase):
    """The committed receipt is validated in CI without Ghidra, a project or the corpus."""

    def setUp(self) -> None:
        self.receipt = json.loads(RECEIPT_PATH.read_text())

    def test_receipt_matches_the_frozen_scenarios(self) -> None:
        self.assertEqual(pilot.validate_receipt(self.receipt), [])

    def test_determinism_block_hashes_the_canonical_block(self) -> None:
        determinism = self.receipt["determinism"]
        digests = determinism["canonical_sha256"]
        self.assertEqual(determinism["runs"], 2)
        self.assertEqual(len(digests), 2)
        self.assertEqual(digests[0], digests[1])
        self.assertTrue(determinism["identical"])
        self.assertEqual(pilot.canonical_digest(self.receipt["canonical"]), digests[0])


@unittest.skipUnless(RECEIPT_PRESENT and BINARY_PRESENT, "pinned binary or receipt not present")
class PinnedBinaryByteTests(unittest.TestCase):
    def test_executed_bytes_match_the_pinned_binary(self) -> None:
        executable = paths.executable_path()
        assert_pinned_executable(executable)
        with MachOMapper(executable) as mapper:
            self.assertEqual(
                pilot.validate_receipt(json.loads(RECEIPT_PATH.read_text()), mapper=mapper), []
            )


@unittest.skipUnless(
    ghidra_available() and PROJECT_PRESENT and BINARY_PRESENT and RECEIPT_PRESENT,
    "GHIDRA_INSTALL, the analysed project or the pinned binary not present",
)
class PilotExecutionTests(unittest.TestCase):
    """Re-runs the frozen scenarios and must reproduce the committed canonical block."""

    def test_two_runs_reproduce_the_committed_receipt(self) -> None:
        with tempfile.TemporaryDirectory() as work:
            receipt = pilot.run_twice(Path(work))
        self.assertTrue(receipt["determinism"]["identical"], "two runs disagree")
        committed = json.loads(RECEIPT_PATH.read_text())
        self.assertEqual(
            pilot.canonical_digest(receipt["canonical"]),
            pilot.canonical_digest(committed["canonical"]),
            "re-run does not reproduce the committed canonical block",
        )
        self.assertEqual(pilot.validate_receipt(receipt), [])


if __name__ == "__main__":
    unittest.main()
