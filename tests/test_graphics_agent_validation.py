import unittest

from reverse_engineering.graphics_agent.validation import (
    relevance_heuristic,
    validate_claim_evidence_refs,
)


class ValidationTests(unittest.TestCase):
    def test_irrelevant_edge_for_buffer_claim(self) -> None:
        claim = {
            "statement": "This function allocates all vertex buffers",
            "evidence_ids": ["call-edge:abc:0x1:dead"],
        }
        known = {"call-edge:abc:0x1:dead"}
        self.assertEqual(validate_claim_evidence_refs(claim, "abc" + "0" * 61, known), [])
        meta = {"ultimate_external_symbol": "_glDrawElements"}
        self.assertFalse(relevance_heuristic(claim["statement"], meta))


if __name__ == "__main__":
    unittest.main()
