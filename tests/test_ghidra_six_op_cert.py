import unittest

from reverse_engineering.graphics_agent.ghidra_six_op_cert import (
    SixOpCertificationError,
    certify_operation_row,
    certify_six_op_bundle,
)
RVA = "0x0000000000005000"


class GhidraSixOpCertTests(unittest.TestCase):
    def test_disassembly_requires_instructions(self) -> None:
        row = {
            "entry_rva": RVA,
            "operation": "disassembly",
            "status": "complete_for_requested_scope",
            "facts": [{"kind": "instruction", "mnemonic": "nop"}],
        }
        certify_operation_row("disassembly", row, RVA)

    def test_meaningless_unresolved_rejected_for_inventory_function(self) -> None:
        row = {
            "entry_rva": RVA,
            "operation": "disassembly",
            "status": "unresolved",
            "status_detail": "no_function_at_rva",
            "facts": [],
        }
        with self.assertRaises(SixOpCertificationError):
            certify_operation_row("disassembly", row, RVA, inventory_has_function=True)

    def test_wrong_entry_rva_rejected(self) -> None:
        row = {
            "entry_rva": "0x0000000000006000",
            "operation": "signature",
            "status": "complete_for_requested_scope",
            "facts": [{"kind": "signature", "name": "fn"}],
        }
        with self.assertRaises(SixOpCertificationError):
            certify_operation_row("signature", row, RVA)

    def test_references_partial_with_facts_ok(self) -> None:
        row = {
            "entry_rva": RVA,
            "operation": "references",
            "status": "partial",
            "facts": ['{"kind":"reference","from_rva":"0x1","to_rva":"0x2"}'],
        }
        certify_operation_row("references", row, RVA)

    def test_bundle_requires_all_ops(self) -> None:
        with self.assertRaises(SixOpCertificationError):
            certify_six_op_bundle({"disassembly": [{"entry_rva": RVA, "operation": "disassembly", "status": "complete_for_requested_scope", "facts": [{"kind": "instruction", "mnemonic": "x"}]}]}, RVA)


if __name__ == "__main__":
    unittest.main()
