"""E2: independent E1 acceptance tests (contract + negative controls).

``oracle.certify.certify_session`` integration tests run only when E1 is present
on the evaluation tree. This file is E2-owned; E1 agents must not edit it.
"""
from __future__ import annotations

import importlib
import unittest

from tests.oracle_certify_acceptance.contract import (
    assert_evidence_not_live_grounded,
    assert_promotion,
    assert_rejects_false_certification,
    assert_trusted_build_required,
    false_certifier_stand_in,
    self_declared_build_stand_in,
)
from tests.oracle_certify_acceptance.fixtures import (
    ACCEPTANCE,
    build_mock_live_session,
)

try:
    certify_session = importlib.import_module("oracle.certify").certify_session
except (ImportError, AttributeError):
    certify_session = None


class NegativeControlTests(unittest.TestCase):
    """Stand-ins must fail the same assertions the implementation must pass."""

    def test_false_certifier_stand_in_is_rejected(self):
        cert = false_certifier_stand_in()
        with self.assertRaises(AssertionError):
            assert_rejects_false_certification(cert)

    def test_self_declared_build_without_trusted_binding_is_rejected(self):
        cert = self_declared_build_stand_in()
        with self.assertRaises(AssertionError):
            assert_trusted_build_required(cert["candidate"])


class ExplicitSyntheticEvidenceTests(unittest.TestCase):
    def test_synthetic_labeled_certificate_cannot_be_live_grounded(self):
        cert = false_certifier_stand_in()
        cert["evidence"]["explicitly_synthetic"] = True
        cert["evidence_tier"] = "tier1-live-sealed-session"
        with self.assertRaises(AssertionError):
            assert_evidence_not_live_grounded(cert)


class MockSessionFixtureTests(unittest.TestCase):
    def test_mock_live_session_has_manifest_and_fifty_captures(self):
        root = build_mock_live_session()
        self.assertTrue((root / "manifest.json").is_file())
        captures = list((root / "captures").glob("*.capture"))
        self.assertEqual(len(captures), ACCEPTANCE)

    def test_omitted_capture_reduces_manifest_file_set(self):
        root = build_mock_live_session(omit_capture=0)
        captures = list((root / "captures").glob("*.capture"))
        self.assertEqual(len(captures), ACCEPTANCE - 1)


@unittest.skipIf(certify_session is None,
                 "oracle.certify.certify_session not on this tree (E2 prep)")
class CertifySessionImplementationTests(unittest.TestCase):
    """Runs in a disposable E1+E2 worktree or after E1 merges."""

    def test_explicitly_synthetic_session_is_not_certified(self):
        root = build_mock_live_session(synthetic_flag=1)
        # Implementation must supply policy, candidate with trusted_build, etc.
        self.skipTest("wired when E1 exposes certify_session fixtures helper")

    def test_mock_live_session_reports_mocked_limitation(self):
        self.skipTest("wired when E1 certify_session is implemented")


if __name__ == "__main__":
    unittest.main()
