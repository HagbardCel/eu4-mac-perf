"""Operational failure classes are distinct and fail closed (M7-P2 A3)."""

from __future__ import annotations

import unittest

from automation import agent_tools


def envelope(status="ok", reason="", *, diagnostic=None, verdict=None,
             evidence=None, qualified=None, result=None):
    return agent_tools._envelope(
        "eu4_replay", status, result or {}, reason=reason,
        diagnostic=diagnostic, verdict=verdict, evidence=evidence,
        qualified=qualified)


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

    def test_missing_numpy_is_a_dependency_not_a_gpu_failure(self):
        value = envelope("unavailable", "numpy is not importable",
                         diagnostic="unavailable")
        self.assertEqual(value["failure"]["kind"], "dependency_unavailable")

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

    def test_invalid_shader_evidence_is_not_a_gpu_capability_failure(self):
        value = envelope("invalid", "Metal shader validation failed",
                         diagnostic="invalid")
        self.assertEqual(value["failure"]["kind"], "evidence_invalid")

    def test_shader_compile_failure_is_a_tool_failure(self):
        value = envelope("failed", "Metal shader compilation failed",
                         diagnostic="failed")
        self.assertEqual(value["failure"]["kind"], "tool_failure")

    def test_timeout_precedes_metal_wording(self):
        value = envelope("failed", "Metal replay timed out after 60s",
                         diagnostic="failed")
        self.assertEqual(value["failure"]["kind"], "execution_timeout")

    def test_completed_difference_with_metal_wording_is_a_comparison(self):
        value = envelope("ok", "Metal shader differs at event 142",
                         diagnostic="complete", verdict="different",
                         evidence="valid")
        self.assertEqual(value["failure"]["kind"], None)
        self.assertEqual(value["failure"]["class"], "comparison")

    def test_nested_invalid_verification_cannot_look_qualified(self):
        value = envelope(
            "ok", "looks complete", diagnostic="complete", verdict="equal",
            evidence="valid", qualified=True,
            result={"verification": {"ok": False, "problems": ["digest drift"]},
                    "diagnosis": {"classification": "qualified_match"}})
        self.assertFalse(value["ok"])
        self.assertEqual(value["status"], "invalid")
        self.assertIsNone(value["outcome"]["verdict"])
        self.assertFalse(value["outcome"]["qualified"])
        self.assertEqual(value["failure"]["kind"], "evidence_invalid")

    def test_nested_incomplete_diagnosis_is_not_complete(self):
        value = envelope(
            "ok", "looks complete", diagnostic="complete", verdict="different",
            result={"diagnosis": {"classification": "incomplete_diagnostics"}})
        self.assertFalse(value["ok"])
        self.assertEqual(value["outcome"]["diagnostic"], "incomplete")
        self.assertEqual(value["failure"]["kind"], "evidence_invalid")


if __name__ == "__main__":
    unittest.main()
