"""RE0 offline execution pilot (`_GfxSetConstantBuffer`, entry RVA 0x15ec36a).

Always (CI, no corpus): the frozen scenarios are internally consistent, both declared execution
paths are the reviewed listing walked from the entry, the declared constructed state is complete
and resolvable, the committed receipt validates against the frozen expectations, and forged
receipts are rejected for the reason under test.

With the local corpus present: executed instruction bytes are re-checked against the pinned
binary. With Ghidra and the analysed project present: the pilot is re-run twice and must
reproduce the committed receipt's canonical block, and a deliberately unavailable required input
must fail qualification.

Nothing here launches or attaches to the game.
"""

from __future__ import annotations

import copy
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

    # ------------------------------------------------ declarations that must be authoritative

    def test_fill_pattern_is_declared_and_reconstructs_literal_bytes(self) -> None:
        pattern = self.scenarios["memory"]["fill_pattern"]
        self.assertEqual(pattern["kind"], "linear_byte_pattern")
        # Hand-computed bytes of ((i * 55) + 17) & 0xff - not derived from the implementation.
        self.assertEqual(
            pilot.fill_pattern(self.scenarios["memory"], 6),
            bytes([0x11, 0x48, 0x7F, 0xB6, 0xED, 0x24]),
        )
        # The context and stack pre-state are both reconstructible from the freeze.
        ctx_window, stack_window = pilot.expected_windows(self.scenarios)
        self.assertEqual(len(ctx_window), 0x139 - 0xB8)
        self.assertEqual(len(stack_window), 0x20 + 0x10)
        # The stack pre-state carries the return sentinel at the return address (0x20 into the
        # window: the eight bytes before rsp0 are the saved frame word, the eight after are past
        # the return slot).
        self.assertEqual(
            stack_window[0x20:0x28],
            pilot.hexint(self.scenarios["constants"]["return_sentinel"]).to_bytes(8, "little"),
        )

    def test_initial_registers_are_declared_for_every_register(self) -> None:
        self.assertEqual(set(self.scenarios["initial_registers"]), set(pilot.GENERAL_PURPOSE))
        for scenario in self.scenarios["scenarios"]:
            resolved = pilot.initial_registers(scenario, self.scenarios)
            self.assertEqual(set(resolved), set(pilot.GENERAL_PURPOSE))

    def test_r9_starts_at_the_frozen_r9d_zero_extended(self) -> None:
        """The corrected declaration: only the upper half of R9 starts at zero."""
        for scenario in self.scenarios["scenarios"]:
            resolved = pilot.initial_registers(scenario, self.scenarios)
            self.assertEqual(resolved["R9"], pilot.hexint(scenario["r9d"]))
            self.assertLessEqual(resolved["R9"], 0xFFFFFFFF)
            self.assertEqual(resolved["RDX"], 0)
        s01 = self.scenarios["scenarios"][0]
        self.assertEqual(pilot.initial_registers(s01, self.scenarios)["R9"], 0xFFFFFFFF)

    def test_unknown_register_symbols_are_rejected(self) -> None:
        self.assertEqual(pilot.resolve_register("0x10", {}), 0x10)
        self.assertEqual(pilot.resolve_register("slot", {"slot": 3}), 3)
        with self.assertRaises(ValueError):
            pilot.resolve_register("not_a_declared_symbol", {})

    def test_expected_final_registers_close_over_every_register(self) -> None:
        asserted, preserved, not_asserted = pilot.expected_final_registers(
            self.scenarios["scenarios"][0], self.scenarios
        )
        self.assertEqual(
            set(asserted) | set(preserved) | set(not_asserted),
            set(pilot.GENERAL_PURPOSE) | {"RFLAGS"},
        )
        self.assertEqual(len(asserted) + len(preserved) + len(not_asserted), 17)
        self.assertFalse(set(asserted) & set(preserved))
        self.assertFalse(set(asserted) & set(not_asserted))

    def test_buffer_absence_semantics_are_declared(self) -> None:
        self.assertIn("buffer_first_dword", self.scenarios["scenario_field_semantics"])
        for scenario in self.scenarios["scenarios"]:
            self.assertNotIn("rsi_mapped", scenario, "redundant field was reintroduced")
        s13 = self.scenarios["scenarios"][-1]
        self.assertIsNone(s13["buffer_first_dword"])
        self.assertEqual(s13["path"], "explicit_length")


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


def _resign(receipt: dict) -> dict:
    """Re-record the canonical digest after a mutation.

    A forged receipt is self-consistent: it hashes what it carries. That is the point of these
    tests - the mutation must be caught by the semantic checks, not by a stale digest.
    """
    digest = pilot.canonical_digest(receipt["canonical"])
    receipt["determinism"]["canonical_sha256"] = [digest, digest]
    return receipt


def _flip_byte(hex_text: str, index: int) -> str:
    raw = bytearray.fromhex(hex_text)
    raw[index] ^= 0xFF
    return raw.hex()


@unittest.skipUnless(RECEIPT_PRESENT, "committed pilot receipt not present")
class ForgedReceiptTests(unittest.TestCase):
    """Mutations a receipt could carry while still hashing to its own digests."""

    def setUp(self) -> None:
        self.receipt = json.loads(RECEIPT_PATH.read_text())

    def failures(self, mutate) -> list[str]:
        receipt = copy.deepcopy(self.receipt)
        mutate(receipt)
        return pilot.validate_receipt(_resign(receipt))

    def assert_rejected(self, failures: list[str], keyword: str) -> None:
        self.assertTrue(failures, "the mutation was accepted")
        self.assertTrue(
            any(keyword in message for message in failures),
            f"rejected, but not for {keyword!r}: {failures[:5]}",
        )

    def test_resigning_an_unmutated_receipt_still_validates(self) -> None:
        # Guards the helper: only the mutation may cause a rejection.
        receipt = copy.deepcopy(self.receipt)
        self.assertEqual(pilot.validate_receipt(_resign(receipt)), [])

    def test_canary_byte_changed_in_both_context_windows_is_rejected(self) -> None:
        """The case that motivated reconstructing the pre-state.

        Index 0 of the context window is inside the leading canary band, so nothing is allowed to
        write it; changing it in both the recorded before and after windows leaves the before/after
        comparison and the write containment untouched. Only comparison against the frozen
        pre-state catches it.
        """

        def mutate(receipt: dict) -> None:
            result = receipt["canonical"]["results"][0]
            for key in ("ctx_window_before", "ctx_window_after"):
                result[key] = _flip_byte(result[key], 0)

        self.assert_rejected(self.failures(mutate), "context pre-state")

    def test_canary_byte_changed_in_both_stack_windows_is_rejected(self) -> None:
        def mutate(receipt: dict) -> None:
            result = receipt["canonical"]["results"][0]
            for key in ("stack_window_before", "stack_window_after"):
                result[key] = _flip_byte(result[key], 0)

        self.assert_rejected(self.failures(mutate), "stack pre-state")

    def test_changed_recorded_r9d_is_rejected(self) -> None:
        def mutate(receipt: dict) -> None:
            receipt["canonical"]["results"][0]["r9d"] = "0x00000000"

        self.assert_rejected(self.failures(mutate), "r9d")

    def test_changed_initial_register_is_rejected(self) -> None:
        """The historical defect: recording R9 as initially zero must not validate."""

        def mutate(receipt: dict) -> None:
            receipt["canonical"]["results"][0]["initial_registers"]["R9"] = "0x0"

        self.assert_rejected(self.failures(mutate), "initial R9")

    def test_dropped_initial_register_is_rejected(self) -> None:
        def mutate(receipt: dict) -> None:
            del receipt["canonical"]["results"][0]["initial_registers"]["R9"]

        self.assert_rejected(self.failures(mutate), "initial R9")

    def test_uninitialized_read_is_rejected(self) -> None:
        def mutate(receipt: dict) -> None:
            receipt["canonical"]["results"][4]["uninitialized_reads"] = [
                ["ram", "0x300000000", "4", "EXECUTE_READ"]
            ]

        self.assert_rejected(self.failures(mutate), "uninitialized")

    def test_changed_store_trace_is_rejected(self) -> None:
        def mutate(receipt: dict) -> None:
            receipt["canonical"]["results"][0]["stores"].pop(0)

        self.assert_rejected(self.failures(mutate), "store sequence")

    def test_divergent_state_write_trace_is_rejected(self) -> None:
        def mutate(receipt: dict) -> None:
            receipt["canonical"]["results"][0]["data_written"].pop()

        self.assert_rejected(self.failures(mutate), "state-level write trace")

    def test_unqualified_result_is_rejected(self) -> None:
        def mutate(receipt: dict) -> None:
            result = receipt["canonical"]["results"][0]
            result["qualified"] = False
            result["reasons"] = ["forged"]

        self.assert_rejected(self.failures(mutate), "not qualified")


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


@unittest.skipUnless(
    ghidra_available() and PROJECT_PRESENT and BINARY_PRESENT,
    "GHIDRA_INSTALL, the analysed project or the pinned binary not present",
)
class NegativeControlTests(unittest.TestCase):
    """s13 proves an absent buffer is not read on the explicit path.

    This is the converse: on the buffer path an absent buffer is a required input, so the run
    must record the uninitialized dependency, mark itself unqualified, and be rejected by the
    validator. Both cases together show the check is not vacuous.
    """

    def test_withheld_buffer_input_fails_qualification(self) -> None:
        broken = copy.deepcopy(pilot.load_scenarios())
        target = next(s for s in broken["scenarios"] if s["path"] == "length_from_buffer")
        target["buffer_first_dword"] = None

        with tempfile.TemporaryDirectory() as work:
            scenarios_path = Path(work) / "scenarios.json"
            scenarios_path.write_text(json.dumps(broken, indent=2) + "\n")
            observations = json.loads(
                pilot.run_pilot(Path(work) / "run.json", scenarios_path=scenarios_path).read_text()
            )
            failures = pilot.validate_observations(observations, scenarios_path=scenarios_path)

        result = observations["canonical"]["results"][0]
        self.assertEqual(result["id"], target["id"])
        self.assertTrue(result["uninitialized_reads"],
                        "the withheld buffer produced no uninitialized dependency")
        self.assertIn("uninitialized", " ".join(result["reasons"]))
        self.assertIs(result["qualified"], False)
        self.assertTrue(failures, "the validator accepted a run with an unavailable required input")


if __name__ == "__main__":
    unittest.main()
