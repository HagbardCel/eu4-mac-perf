"""Operational failure classes are distinct and fail closed (M7-P2 A3)."""

from __future__ import annotations

import unittest

from automation import agent_tools


def envelope(status="ok", reason="", *, diagnostic=None, verdict=None,
             evidence=None, qualified=None):
    return agent_tools._envelope(
        "eu4_replay", status, {}, reason=reason, diagnostic=diagnostic,
        verdict=verdict, evidence=evidence, qualified=qualified)


class FailureTaxonomyTests(unittest.TestCase):
    def test_completed_difference_is_not_a_tool_failure(self):
        value = envelope("ok", diagnostic="complete", verdict="different",
                         evidence="valid")
        self.assertEqual(value["failure"]["kind"], None)
        self.assertEqual(value["failure"]["class"], "comparison")
        self.assertEqual(value["outcome"]["failure_kind"], None)

    def test_qualified_match_has_no_failure(self):
        value = envelope("ok", diagnostic="complete", verdict="equal",
                         evidence="valid", qualified=True)
        self.assertIsNone(value["failure"]["kind"])
        self.assertTrue(value["outcome"]["qualified"])

    def test_timeout_is_not_success(self):
        value = envelope("failed", "replay timed out after 60s",
                         diagnostic="failed")
        self.assertFalse(value["ok"])
        self.assertEqual(value["failure"]["kind"], "execution_timeout")

    def test_missing_credential_is_a_blocker(self):
        value = envelope("unavailable", "MISSING_CREDENTIAL",
                         diagnostic="unavailable")
        self.assertEqual(value["failure"]["kind"], "missing_credential")
        self.assertEqual(value["failure"]["class"], "blocker")

    def test_unavailable_graphics_is_a_blocker(self):
        value = envelope("unavailable", "Metal device is unavailable",
                         diagnostic="unavailable")
        self.assertEqual(value["failure"]["kind"], "graphics_unavailable")

    def test_invalid_evidence_is_not_a_comparison(self):
        value = envelope("invalid", "digest drift",
                         diagnostic="invalid", evidence="invalid")
        self.assertEqual(value["failure"]["kind"], "evidence_invalid")

    def test_missing_evidence_is_blocked(self):
        value = envelope("not_found", "case directory does not exist",
                         diagnostic="unavailable")
        self.assertEqual(value["failure"]["kind"], "evidence_missing")

    def test_refused_policy_is_distinct(self):
        value = envelope("refused", "live launch is not authorized",
                         diagnostic="refused")
        self.assertEqual(value["failure"]["kind"], "policy_refusal")

    def test_authoritative_is_not_mistaken_for_authentication(self):
        value = envelope("invalid", "observation is not authoritative",
                         diagnostic="invalid")
        self.assertEqual(value["failure"]["kind"], "evidence_invalid")


if __name__ == "__main__":
    unittest.main()
