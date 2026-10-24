"""E2: independent E1 acceptance tests (contract + negative controls + integration)."""
from __future__ import annotations

import importlib.util
import tempfile
import unittest
from pathlib import Path

from oracle.function import replay

from tests.oracle_certify_acceptance.contract import (
    assert_build_binding_honest,
    assert_evidence_not_live_grounded,
    assert_evaluation_counts,
    assert_promotion,
    assert_rejects_false_certification,
    false_certifier_stand_in,
    forged_executable_binding_stand_in,
)
from tests.oracle_certify_acceptance.fixtures import (
    ACCEPTANCE,
    build_forty_nine_one_mismatch_session,
    build_mock_live_session,
    case_a_delete_one_capture,
    case_b_descriptor_pin_mismatch,
    case_b_pid_disagreement,
    capture_paths,
    verify_manifest_matches_files,
)
from tests.oracle_certify_acceptance.import_gate import (
    certify_import_status,
    load_certify_session,
)
from tests.oracle_certify_acceptance.integration import (
    assert_session_immutable,
    call_certify,
    tree_sha256,
)

try:
    from tests.test_function_replay import HARNESS
except ImportError:
    HARNESS = None


class NegativeControlTests(unittest.TestCase):
    def test_false_certifier_stand_in_is_rejected(self):
        with self.assertRaises(AssertionError):
            assert_rejects_false_certification(false_certifier_stand_in())

    def test_forged_executable_binding_stand_in_is_rejected(self):
        cert = forged_executable_binding_stand_in()
        with tempfile.NamedTemporaryFile() as tmp:
            path = Path(tmp.name)
            path.write_bytes(b"not-the-declared-executable")
            with self.assertRaises(AssertionError):
                assert_build_binding_honest(cert, path)


class ExplicitSyntheticEvidenceTests(unittest.TestCase):
    def test_synthetic_labeled_certificate_cannot_be_live_grounded(self):
        cert = false_certifier_stand_in()
        cert["evidence"]["explicitly_synthetic"] = True
        cert["evidence_tier"] = "tier1-live-sealed-session"
        with self.assertRaises(AssertionError):
            assert_evidence_not_live_grounded(cert)


class FortyNineOnePreflightTests(unittest.TestCase):
    @unittest.skipIf(HARNESS is None, "RE1 harness not built")
    def test_replay_capture_sees_one_mismatch_among_fifty(self):
        root = build_forty_nine_one_mismatch_session()
        matches = mismatches = 0
        for path in capture_paths(root):
            result = replay.replay_capture(path.read_text(encoding="utf-8"), HARNESS)
            if result["verdict"] == "match":
                matches += 1
            elif result["verdict"] == "mismatch":
                mismatches += 1
        self.assertEqual(matches, 49)
        self.assertEqual(mismatches, 1)


class MockSessionFixtureTests(unittest.TestCase):
    def test_mock_live_session_has_manifest_and_fifty_captures(self):
        root = build_mock_live_session()
        self.assertTrue((root / "manifest.json").is_file())
        self.assertEqual(len(capture_paths(root)), ACCEPTANCE)
        verify_manifest_matches_files(root)

    def test_case_a_tamper_breaks_manifest_consistency(self):
        root = build_mock_live_session()
        case_a_delete_one_capture(root)
        with self.assertRaises(AssertionError):
            verify_manifest_matches_files(root)


class CertifyImportGateTests(unittest.TestCase):
    def test_broken_oracle_certify_package_fails_when_present(self):
        if importlib.util.find_spec("oracle.certify") is None:
            self.skipTest("oracle.certify absent on this tree")
        status, detail = certify_import_status()
        if status == "broken":
            self.fail(f"oracle.certify is broken: {detail}")
        self.assertEqual(status, "ready")


@unittest.skipIf(importlib.util.find_spec("oracle.certify") is None,
                 "oracle.certify absent — E2 preparation only")
class CertifySessionImplementationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        status, detail = certify_import_status()
        if status == "broken":
            raise AssertionError(f"oracle.certify broken: {detail}")
        cls.certify_session = load_certify_session()
        assert cls.certify_session is not None

    def test_case_a_missing_capture_fails_session_admission(self):
        root = build_mock_live_session()
        before = tree_sha256(root)
        case_a_delete_one_capture(root)
        cert = call_certify(self.certify_session, root, _placeholder_candidate())
        assert_session_immutable(before, tree_sha256(root))
        self.assertEqual(cert["reference_admission"]["session"]["verdict"],
                         "ineligible")

    def test_case_b_descriptor_pin_mismatch_fails_admission(self):
        root = build_mock_live_session()
        case_b_descriptor_pin_mismatch(root)
        cert = call_certify(self.certify_session, root, _placeholder_candidate())
        self.assertEqual(cert["reference_admission"]["session"]["verdict"],
                         "ineligible")

    def test_case_b_pid_disagreement_fails_admission(self):
        root = build_mock_live_session()
        case_b_pid_disagreement(root)
        cert = call_certify(self.certify_session, root, _placeholder_candidate())
        self.assertEqual(cert["reference_admission"]["session"]["verdict"],
                         "ineligible")

    def test_explicitly_synthetic_session_is_not_certified(self):
        root = build_mock_live_session(synthetic_flag=1)
        cert = call_certify(self.certify_session, root, _placeholder_candidate())
        assert_promotion(cert, "failed")

    @unittest.skipIf(HARNESS is None, "RE1 harness not built")
    def test_forty_nine_one_mismatch_preflight_and_certify_failed(self):
        root = build_forty_nine_one_mismatch_session()
        matches = mismatches = 0
        for path in capture_paths(root):
            result = replay.replay_capture(path.read_text(encoding="utf-8"), HARNESS)
            if result["verdict"] == "match":
                matches += 1
            elif result["verdict"] == "mismatch":
                mismatches += 1
        self.assertEqual(matches, 49)
        self.assertEqual(mismatches, 1)
        cert = call_certify(self.certify_session, root, _placeholder_candidate())
        assert_promotion(cert, "failed")
        assert_evaluation_counts(cert, match=49, mismatch=1)

    def test_sealed_evidence_bytes_unchanged(self):
        root = build_mock_live_session()
        before = tree_sha256(root)
        call_certify(self.certify_session, root, _placeholder_candidate())
        assert_session_immutable(before, tree_sha256(root))


def _placeholder_candidate() -> dict:
    """Minimal candidate mapping; E1 must validate and establish binding."""
    return {
        "source_sha256": "0" * 64,
        "executable_sha256": "0" * 64,
        "build_identity": "acceptance-placeholder",
        "build_flags": [],
        "executed_matches_build": False,
        "harness_path": str(HARNESS or ""),
    }


if __name__ == "__main__":
    unittest.main()
