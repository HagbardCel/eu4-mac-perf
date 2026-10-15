import json
import unittest
from pathlib import Path

from reverse_engineering.graphics_agent.validation import relevance_heuristic

FIXTURE = Path(__file__).resolve().parent / "re_fixtures" / "agent_graphics" / "unsupported_claim.json"


class AgentPilotFixtureTests(unittest.TestCase):
    def test_unsupported_claim_fixture(self) -> None:
        data = json.loads(FIXTURE.read_text())
        self.assertTrue(data.get("expect_verifier_reject"))
        meta = {"ultimate_external_symbol": "_GfxDrawIndexed"}
        self.assertFalse(relevance_heuristic(data["statement"], meta))


if __name__ == "__main__":
    unittest.main()
