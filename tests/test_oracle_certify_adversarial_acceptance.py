"""E2: independent E1 acceptance tests (contract + negative controls + integration)."""
from __future__ import annotations

import importlib.util
import tempfile
import unittest
from pathlib import Path

from oracle.function import replay

from tests.oracle_certify_acceptance.acceptance_candidate import (
    prepare_acceptance_candidate,
    re1_canonical_source_sha256,
)
from tests.oracle_certify_acceptance.contract import (
    assert_build_binding_honest,
    assert_build_identity_binding_failure,
    assert_candidate_binding_failure,
    assert_evidence_not_live_grounded,
    assert_evaluation_counts,
    assert_promotion,
    assert_promotion_certified,
    assert_promotion_scoped_for_diversity,
    assert_rejects_false_certification,
    false_certifier_stand_in,
    forged_executable_binding_stand_in,
    unrelated_failure_stand_in,
)
from tests.oracle_certify_acceptance.fixtures import (
    ACCEPTANCE,
    assert_mock_session_capture_identities,
    build_forty_nine_one_mismatch_session,
    build_mock_live_session,
    case_a_delete_one_capture,
    case_b_descriptor_pin_mismatch,
    case_b_pid_disagreement,
    case_empty_metadata_resealed,
    capture_paths,
    verify_manifest_matches_files,
)
from tests.oracle_certify_acceptance.import_gate import (
    certify_import_status,
    load_certify_session,
)
from tests.oracle_certify_acceptance.integration import (
    MOCK_CERTIFIED_DIVERSITY_FLOOR,
    MOCK_SCOPED_DIVERSITY_FLOOR,
    assert_session_immutable,
    call_certify,
    call_certify_production_binding,
    tree_sha256,
    write_test_policy,
)
from tests.oracle_certify_acceptance.production_binding import (
    production_binding_compatible,
    production_candidate,
    production_record,
)
from tests.oracle_certify_acceptance.re1_harness import (
    oracle_certify_present,
    require_re1_harness_for_integration,
    require_re1_harness_for_preflight,
)


def _tally_replay(root: Path, harness: Path) -> tuple[int, int, int]:
    matches = mismatches = refused = 0
    for path in capture_paths(root):
        result = replay.replay_capture(path.read_text(encoding="utf-8"), harness)
        verdict = result["verdict"]
        if verdict == "match":
            matches += 1
        elif verdict == "mismatch":
            mismatches += 1
        elif verdict == "refused":
            refused += 1
        else:
            raise AssertionError(f"unexpected replay verdict: {verdict}")
    return matches, mismatches, refused


def _assert_fifty_match_preflight(root: Path, harness: Path) -> None:
    matches, mismatches, refused = _tally_replay(root, harness)
    if refused:
        raise AssertionError(f"preflight saw {refused} refused replays")
    if matches != 50 or mismatches != 0:
        raise AssertionError(
            f"preflight expected 50 match, got {matches} match and {mismatches} mismatch")


def _assert_forty_nine_one_preflight(root: Path, harness: Path) -> None:
    matches, mismatches, refused = _tally_replay(root, harness)
    if refused:
        raise AssertionError(f"preflight saw {refused} refused replays")
    if matches != 49 or mismatches != 1:
        raise AssertionError(
            f"preflight expected 49 match and 1 mismatch, got "
            f"{matches} match and {mismatches} mismatch")


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
                assert_build_binding_honest(
                    cert, path, expected_source_sha256=re1_canonical_source_sha256())

    def test_unrelated_failure_stand_in_rejects_binding_helper(self):
        cert = unrelated_failure_stand_in()
        with tempfile.NamedTemporaryFile() as tmp:
            path = Path(tmp.name)
            path.write_bytes(b"executable-bytes")
            with self.assertRaises(AssertionError):
                assert_candidate_binding_failure(
                    cert,
                    submitted_forged_digest="0" * 64,
                    executable_path=path,
                )


class ExplicitSyntheticEvidenceTests(unittest.TestCase):
    def test_synthetic_labeled_certificate_cannot_be_live_grounded(self):
        cert = false_certifier_stand_in()
        cert["evidence"]["explicitly_synthetic"] = True
        cert["evidence_tier"] = "tier1-live-sealed-session"
        with self.assertRaises(AssertionError):
            assert_evidence_not_live_grounded(cert)


class FortyNineOnePreflightTests(unittest.TestCase):
    def test_replay_capture_sees_one_mismatch_among_fifty(self):
        harness = require_re1_harness_for_preflight()
        root = build_forty_nine_one_mismatch_session()
        _assert_forty_nine_one_preflight(root, harness)


class MockSessionFixtureTests(unittest.TestCase):
    def test_mock_live_session_has_manifest_and_fifty_captures(self):
        root = build_mock_live_session()
        self.assertTrue((root / "manifest.json").is_file())
        self.assertEqual(len(capture_paths(root)), ACCEPTANCE)
        verify_manifest_matches_files(root)
        assert_mock_session_capture_identities(root)

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
        if oracle_certify_present():
            cls.harness = require_re1_harness_for_integration()
        else:
            cls.harness = None

    def _candidate(self, tmp: Path) -> dict:
        return prepare_acceptance_candidate(tmp / "candidate")

    def test_case_a_missing_capture_fails_session_admission(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = build_mock_live_session()
            case_a_delete_one_capture(root)
            before = tree_sha256(root)
            cert = call_certify(
                self.certify_session, root, self._candidate(Path(tmp)))
            assert_session_immutable(before, tree_sha256(root))
            self.assertEqual(cert["reference_admission"]["session"]["verdict"],
                             "ineligible")

    def test_case_b_descriptor_pin_mismatch_fails_admission(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = build_mock_live_session()
            case_b_descriptor_pin_mismatch(root)
            cert = call_certify(
                self.certify_session, root, self._candidate(Path(tmp)))
            self.assertEqual(cert["reference_admission"]["session"]["verdict"],
                             "ineligible")

    def test_case_b_pid_disagreement_fails_admission(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = build_mock_live_session()
            case_b_pid_disagreement(root)
            cert = call_certify(
                self.certify_session, root, self._candidate(Path(tmp)))
            self.assertEqual(cert["reference_admission"]["session"]["verdict"],
                             "ineligible")

    def test_explicitly_synthetic_session_is_not_certified(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = build_mock_live_session(synthetic_flag=1)
            cert = call_certify(
                self.certify_session, root, self._candidate(Path(tmp)))
            assert_promotion(cert, "failed")

    def test_forty_nine_one_mismatch_preflight_and_certify_failed(self):
        harness = require_re1_harness_for_integration()
        root = build_forty_nine_one_mismatch_session()
        _assert_forty_nine_one_preflight(root, harness)
        with tempfile.TemporaryDirectory() as tmp:
            cert = call_certify(
                self.certify_session, root, self._candidate(Path(tmp)))
            assert_promotion(cert, "failed")
            assert_evaluation_counts(cert, match=49, mismatch=1)

    def test_sealed_evidence_bytes_unchanged(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = build_mock_live_session()
            before = tree_sha256(root)
            call_certify(self.certify_session, root, self._candidate(Path(tmp)))
            assert_session_immutable(before, tree_sha256(root))

    def test_fifty_matching_observations_certified(self):
        harness = require_re1_harness_for_integration()
        root = build_mock_live_session()
        _assert_fifty_match_preflight(root, harness)
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp)
            policy = write_test_policy(
                work / "policy.json",
                min_distinct_behaviors=MOCK_CERTIFIED_DIVERSITY_FLOOR,
            )
            cert = call_certify(
                self.certify_session,
                root,
                self._candidate(work),
                policy_path=policy,
            )
            assert_promotion_certified(
                cert, min_distinct_behaviors=MOCK_CERTIFIED_DIVERSITY_FLOOR)

    def test_diversity_floor_not_met_yields_scoped(self):
        harness = require_re1_harness_for_integration()
        root = build_mock_live_session()
        _assert_fifty_match_preflight(root, harness)
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp)
            policy = write_test_policy(
                work / "policy.json",
                min_distinct_behaviors=MOCK_SCOPED_DIVERSITY_FLOOR,
            )
            cert = call_certify(
                self.certify_session,
                root,
                self._candidate(work),
                policy_path=policy,
            )
            assert_promotion_scoped_for_diversity(
                cert, min_distinct_behaviors=MOCK_SCOPED_DIVERSITY_FLOOR)

    def test_forged_executable_identity_fails_certification(self):
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp)
            root = build_mock_live_session()
            candidate = self._candidate(work)
            forged_digest = "f" * 64
            executable = Path(candidate["harness_path"])
            candidate["executable_sha256"] = forged_digest
            cert = call_certify(self.certify_session, root, candidate)
            assert_candidate_binding_failure(
                cert,
                submitted_forged_digest=forged_digest,
                executable_path=executable,
            )

    def test_empty_metadata_resealed_fails_session_admission(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = build_mock_live_session()
            case_empty_metadata_resealed(root)
            before = tree_sha256(root)
            cert = call_certify(
                self.certify_session, root, self._candidate(Path(tmp)))
            assert_session_immutable(before, tree_sha256(root))
            self.assertEqual(cert["reference_admission"]["session"]["verdict"],
                             "ineligible")

    def test_certificate_symlink_into_seal_does_not_modify_seal(self):
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp)
            root = build_mock_live_session()
            before = tree_sha256(root)
            cert_dir = work / "cert-out"
            cert_dir.mkdir()
            (cert_dir / "tier1-certificate.json").symlink_to(root / "manifest.json")
            call_certify(
                self.certify_session,
                root,
                self._candidate(work),
                output_dir=cert_dir,
            )
            assert_session_immutable(before, tree_sha256(root))


@unittest.skipIf(importlib.util.find_spec("oracle.certify") is None,
                 "oracle.certify absent — E2 preparation only")
class ProductionBindingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        ok, reason = production_binding_compatible()
        if not ok:
            raise unittest.SkipTest(reason)
        if production_record() is None:
            raise unittest.SkipTest(
                "production build record absent (merge E1 #86 trusted-build JSON)")
        status, detail = certify_import_status()
        if status == "broken":
            raise AssertionError(f"oracle.certify broken: {detail}")
        cls.certify_session = load_certify_session()
        assert cls.certify_session is not None

    def test_approved_harness_matches_production_record(self):
        harness = require_re1_harness_for_integration()
        root = build_mock_live_session()
        _assert_fifty_match_preflight(root, harness)
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp)
            policy = write_test_policy(
                work / "policy.json",
                min_distinct_behaviors=MOCK_CERTIFIED_DIVERSITY_FLOOR,
            )
            candidate = production_candidate(work / "candidate")
            cert = call_certify_production_binding(
                self.certify_session,
                root,
                candidate,
                policy_path=policy,
            )
            assert_promotion_certified(
                cert, min_distinct_behaviors=MOCK_CERTIFIED_DIVERSITY_FLOOR)


@unittest.skipIf(importlib.util.find_spec("oracle.certify") is None,
                 "oracle.certify absent — E2 preparation only")
class ProductionBindingNegativeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        ok, reason = production_binding_compatible()
        if not ok:
            raise unittest.SkipTest(reason)
        if production_record() is None:
            raise unittest.SkipTest(
                "production build record absent (merge E1 #86 trusted-build JSON)")
        status, detail = certify_import_status()
        if status == "broken":
            raise AssertionError(f"oracle.certify broken: {detail}")
        cls.certify_session = load_certify_session()
        assert cls.certify_session is not None

    def test_forged_build_identity_fails_with_production_record(self):
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp)
            root = build_mock_live_session()
            candidate = production_candidate(work / "candidate")
            forged_identity = "forged-production-build-identity"
            candidate["build_identity"] = forged_identity
            cert = call_certify_production_binding(
                self.certify_session, root, candidate)
            assert_build_identity_binding_failure(
                cert, submitted_forged_identity=forged_identity)


if __name__ == "__main__":
    unittest.main()
