"""RE0 contract guard for `_GfxSetConstantBuffer` (entry RVA 0x15ec36a).

Always (CI, no corpus): the tracked instruction listing is internally consistent and
supports the contract's declared writes and leaf-function claims.

With the local corpus present: the listing is re-derived from the sealed renderer
snapshot and every instruction's bytes are verified against the pinned EU IV binary.
Nothing here executes game code.
"""

from __future__ import annotations

import json
import unittest
import zipfile
from pathlib import Path

from reverse_engineering import paths
from reverse_engineering.macho_rva import (
    MachOMapper,
    assert_pinned_executable,
    verify_static_row_bytes,
)

ROOT = Path(__file__).resolve().parents[1]
INVENTORY = ROOT / "analysis" / "reconstruction" / "_GfxSetConstantBuffer" / "instructions.json"
BASELINE_REVISION = "auto-v1-20261013"
SNAPSHOT_BATCH = "renderer_snapshot/v1/batches/gfx-batch-00044.zip"
ENTRY_RVA = 0x15EC36A
END_RVA = 0x15EC3B5
IMAGE_BASE = 0x100000000

SNAPSHOT_PATH = paths.local_binary_root() / "exports" / BASELINE_REVISION / SNAPSHOT_BATCH
SNAPSHOT_PRESENT = SNAPSHOT_PATH.is_file()


def load_inventory() -> dict:
    return json.loads(INVENTORY.read_text())


def sealed_row() -> dict:
    with zipfile.ZipFile(SNAPSHOT_PATH) as archive:
        for line in archive.open("static.jsonl"):
            row = json.loads(line)
            rva = row.get("entry_rva")
            rva = int(rva, 16) if isinstance(rva, str) else rva
            if rva == ENTRY_RVA:
                return row
    raise AssertionError(f"entry RVA not found in {SNAPSHOT_BATCH}")


class InstructionInventoryTests(unittest.TestCase):
    """Checks the tracked listing itself — the contract's cited evidence."""

    def setUp(self) -> None:
        self.inventory = load_inventory()
        self.instructions = self.inventory["instructions"]

    def test_identity_and_contiguous_tiling(self) -> None:
        self.assertEqual(self.inventory["function"], "_GfxSetConstantBuffer")
        self.assertEqual(self.inventory["instruction_count"], 21)
        self.assertEqual(len(self.instructions), 21)
        self.assertEqual(self.inventory["decoded_instruction_bytes"], 75)
        self.assertEqual(sum(i["length"] for i in self.instructions), 75)

        rva = ENTRY_RVA
        for instruction in self.instructions:
            self.assertEqual(int(instruction["rva"], 16), rva)
            rva += instruction["length"]
        self.assertEqual(rva, END_RVA)

    def test_declared_context_writes_are_exactly_three(self) -> None:
        writes = [i["operands"] for i in self.instructions if "ptr [RAX" in i["operands"]]
        self.assertEqual(
            writes,
            [
                "MOV dword ptr [RAX + RCX*0x1 + 0xc8],R9D",
                "MOV qword ptr [RAX + RCX*0x1 + 0xd0],RSI",
                "MOV byte ptr [RAX + 0x128],0x1",
            ],
        )

    def test_leaf_no_calls_and_no_rip_relative_access(self) -> None:
        self.assertFalse([i for i in self.instructions if i.get("call")], "call instructions present")
        self.assertFalse([i for i in self.instructions if "RIP" in i["operands"].upper()], "RIP-relative access present")

    def test_length_selector_semantics_present(self) -> None:
        operands = {i["operands"] for i in self.instructions}
        for expected in (
            "CMP R9D,-0x1",
            "MOV R9D,dword ptr [RSI]",
            "SHR R9D,0x2",
            "CMOVNS R9D,EDX",
            "SAR R9D,0x2",
        ):
            self.assertIn(expected, operands)


@unittest.skipUnless(SNAPSHOT_PRESENT, "local sealed renderer snapshot not present")
class SealedEvidenceTests(unittest.TestCase):
    """Independently re-derives the listing when the local corpus is available."""

    def test_listing_rederives_from_sealed_snapshot(self) -> None:
        row = sealed_row()
        self.assertEqual(row["instruction_count"], 21)
        self.assertEqual(row["decoded_instruction_bytes"], 75)

        tracked = self._inventory_instructions()
        sealed = row["instructions"]
        self.assertEqual(len(sealed), len(tracked))
        for source, expected in zip(sealed, tracked):
            self.assertEqual(int(source["instruction_rva"], 16), int(expected["rva"], 16))
            self.assertEqual(source["length"], expected["length"])
            self.assertEqual(source["mnemonic"], expected["mnemonic"])
            self.assertEqual(source["operands"], expected["operands"])
            self.assertFalse(
                source.get("machine_call") or source.get("pcode_is_call") or source.get("flow_type_is_call")
            )

    def test_instruction_bytes_match_pinned_binary(self) -> None:
        executable = paths.executable_path()
        if not executable.is_file():
            self.skipTest("pinned EU IV binary not present")
        assert_pinned_executable(executable)
        with MachOMapper(executable) as mapper:
            count, byte_count = verify_static_row_bytes(mapper, sealed_row(), IMAGE_BASE)
        self.assertEqual((count, byte_count), (21, 75))

    def _inventory_instructions(self) -> list:
        return [i for i in load_inventory()["instructions"]]


if __name__ == "__main__":
    unittest.main()
