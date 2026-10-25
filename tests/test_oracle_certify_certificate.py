"""E1 candidate binding, coverage promotion, and replay accounting."""
from __future__ import annotations

import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from oracle.certify import certify_session
from oracle.certify.certify import TRUSTED_BUILD_FLAGS, frozen_source_sha256
from oracle.certify.policy import load_policy
from oracle.function import fixture, replay
from tests.test_oracle_certify_fixtures import (
    POLICY_PATH,
    build_session,
    certify_with_test_record,
    compile_re1_harness,
    narrow_policy,
    rewrite_word_count,
    tree_sha256,
    write_stub,
)


def _certify(root: Path, executable: Path, policy: Path, out: Path, **candidate_kwargs):
    before = tree_sha256(root)
    certificate = certify_with_test_record(
        certify_session, root, policy, executable, out, **candidate_kwargs)
    if tree_sha256(root) != before:
        raise AssertionError("certify_session modified the sealed evidence directory")
    return certificate


class PublishTests(unittest.TestCase):
    def setUp(self) -> None:
        self.work = Path(tempfile.mkdtemp(prefix="e1-publish-"))
        self.addCleanup(lambda: shutil.rmtree(self.work, ignore_errors=True))
        self.stub = self.work / "stub.sh"
        write_stub(self.stub)
        self.policy = narrow_policy(self.work / "policy.json")
        self.root = build_session()
        self.addCleanup(lambda: shutil.rmtree(self.root, ignore_errors=True))

    def test_symlink_certificate_target_does_not_modify_the_seal(self) -> None:
        before = tree_sha256(self.root)
        cert_dir = self.work / "cert-out"
        cert_dir.mkdir()
        (cert_dir / "tier1-certificate.json").symlink_to(self.root / "manifest.json")
        _certify(self.root, self.stub, self.policy, cert_dir)
        self.assertEqual(tree_sha256(self.root), before)
        self.assertTrue((cert_dir / "tier1-certificate.json").is_file())
        self.assertFalse((cert_dir / "tier1-certificate.json").is_symlink())


class BindingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.work = Path(tempfile.mkdtemp(prefix="e1-bind-"))
        self.addCleanup(lambda: shutil.rmtree(self.work, ignore_errors=True))
        self.stub = self.work / "stub.sh"
        write_stub(self.stub)
        self.policy = narrow_policy(self.work / "policy.json")
        self.root = build_session()
        self.addCleanup(lambda: shutil.rmtree(self.root, ignore_errors=True))

    def test_changed_source_executable_and_flags_invalidate_the_certificate(self) -> None:
        honest = _certify(self.root, self.stub, self.policy, self.work / "honest")
        self.assertEqual(honest["candidate"]["source_sha256"], frozen_source_sha256())
        self.assertEqual(honest["candidate"]["build_flags"], list(TRUSTED_BUILD_FLAGS))
        self.assertIs(honest["candidate"]["executed_matches_build"], True)
        self.assertIs(honest["evidence"]["live_grounded"], False)
        self.assertNotEqual(honest["evidence_tier"], "tier1-live-sealed-session")

        forged_source = _certify(
            self.root, self.stub, self.policy, self.work / "source",
            source_sha256="ab" * 32)
        self.assertEqual(forged_source["promotion"]["status"], "failed")
        self.assertEqual(forged_source["candidate"]["source_sha256"], frozen_source_sha256())
        self.assertNotEqual(forged_source["candidate"]["source_sha256"], "ab" * 32)
        self.assertTrue(any("source_sha256" in reason
                            for reason in forged_source["promotion"]["reasons"]))
        self.assertEqual(forged_source["reference_admission"]["session"]["verdict"],
                         "eligible")

        forged_executable = _certify(
            self.root, self.stub, self.policy, self.work / "exe",
            executable_sha256="f" * 64)
        self.assertEqual(forged_executable["promotion"]["status"], "failed")
        self.assertIs(forged_executable["candidate"]["executed_matches_build"], False)
        self.assertNotEqual(forged_executable["candidate"]["executable_sha256"], "f" * 64)
        blob = " ".join(forged_executable["promotion"]["reasons"]
                        + forged_executable["evaluation"]["failures"])
        self.assertIn("executable digest", blob)
        self.assertIn("executed_matches_build", blob)
        self.assertEqual(forged_executable["reference_admission"]["session"]["verdict"],
                         "eligible")
        self.assertEqual(forged_executable["reference_admission"]["fixtures"]["verdict"],
                         "eligible")

        forged_flags = _certify(
            self.root, self.stub, self.policy, self.work / "flags",
            flags=["-O3"])
        self.assertEqual(forged_flags["promotion"]["status"], "failed")
        self.assertEqual(forged_flags["candidate"]["build_flags"], ["-O3"])
        self.assertTrue(any("build flags" in reason
                            for reason in forged_flags["promotion"]["reasons"]))
        self.assertNotEqual(honest["candidate"]["build_flags"],
                            forged_flags["candidate"]["build_flags"])

    def test_registered_policy_loads_against_the_frozen_descriptor(self) -> None:
        policy = load_policy(POLICY_PATH, fixture.load_descriptor(),
                             __import__("hashlib").sha256(
                                 fixture.DESCRIPTOR_PATH.read_bytes()).hexdigest())
        self.assertEqual(policy.function_id, "gfx:set-constant-buffer")
        self.assertGreaterEqual(policy.min_distinct_behaviors, 12)
        self.assertIn("slot:0", policy.required_cells)
        self.assertIn("slot:5", policy.required_cells)
        self.assertIn("input:zero", policy.required_cells)


class ReplayIntegrationTests(unittest.TestCase):
    harness: Path | None = None

    @classmethod
    def setUpClass(cls) -> None:
        cls.harness = compile_re1_harness()
        if cls.harness is None:
            raise unittest.SkipTest("no C++ compiler on PATH; RE1 replay was not run")

    def setUp(self) -> None:
        self.work = Path(tempfile.mkdtemp(prefix="e1-replay-"))
        self.addCleanup(lambda: shutil.rmtree(self.work, ignore_errors=True))
        self.root = build_session()
        self.addCleanup(lambda: shutil.rmtree(self.root, ignore_errors=True))
        assert self.harness is not None
        self.harness_path = self.harness

    def test_matching_session_certifies_inside_the_declared_scope(self) -> None:
        policy = narrow_policy(self.work / "policy.json", floor=2,
                               optional=["alias:handle"])
        first_dir = self.work / "once"
        second_dir = self.work / "twice"
        with mock.patch("oracle.function.replay.replay_capture",
                        wraps=replay.replay_capture) as spy:
            first = _certify(self.root, self.harness_path, policy, first_dir)
        second = _certify(self.root, self.harness_path, policy, second_dir)
        self.assertEqual(spy.call_count, 50)
        self.assertEqual(first["evaluation"]["match"], 50)
        self.assertEqual(first["evaluation"]["mismatch"], 0)
        self.assertEqual(first["promotion"]["status"], "certified")
        self.assertEqual(first["coverage"]["missing_required"], [])
        self.assertIn("alias:handle", first["coverage"]["missing_optional"])
        self.assertTrue(any("optional-cell-missing" in item
                            for item in first["limitations"]))
        self.assertIs(first["evidence"]["live_grounded"], False)
        self.assertNotEqual(first["evidence_tier"], "tier1-live-sealed-session")
        self.assertIs(first["candidate"]["executed_matches_build"], True)
        self.assertEqual(first, second)
        self.assertEqual((first_dir / "tier1-certificate.json").read_bytes(),
                         (second_dir / "tier1-certificate.json").read_bytes())

    def test_missing_diversity_floor_is_scoped_not_certified(self) -> None:
        policy = narrow_policy(self.work / "policy.json", floor=3)
        cert = _certify(self.root, self.harness_path, policy, self.work / "out")
        self.assertEqual(cert["evaluation"]["match"], 50)
        self.assertEqual(cert["evaluation"]["mismatch"], 0)
        self.assertEqual(cert["coverage"]["missing_required"], [])
        self.assertIs(cert["coverage"]["diversity_met"], False)
        self.assertEqual(cert["promotion"]["status"], "scoped")
        self.assertTrue(any("diversity floor" in reason
                            for reason in cert["promotion"]["reasons"]))

    def test_missing_required_cell_is_scoped(self) -> None:
        policy = narrow_policy(self.work / "policy.json", floor=2, slots=[0, 2, 3])
        cert = _certify(self.root, self.harness_path, policy, self.work / "out")
        self.assertEqual(cert["evaluation"]["match"], 50)
        self.assertIn("slot:0", cert["coverage"]["missing_required"])
        self.assertEqual(cert["promotion"]["status"], "scoped")
        self.assertTrue(any("required coverage" in reason
                            for reason in cert["promotion"]["reasons"]))

    def test_registered_policy_does_not_certify_two_behaviors(self) -> None:
        cert = _certify(self.root, self.harness_path, POLICY_PATH, self.work / "out")
        self.assertEqual(cert["evaluation"]["match"], 50)
        self.assertTrue(cert["coverage"]["missing_required"])
        self.assertIs(cert["coverage"]["diversity_met"], False)
        self.assertEqual(cert["promotion"]["status"], "scoped")
        self.assertIs(cert["evidence"]["live_grounded"], False)

    def test_a_qualified_mismatch_is_evaluated(self) -> None:
        def mutate(thread: int, text: str) -> str:
            if thread != 0:
                return text
            return rewrite_word_count(text, slot=2, word_count=99)

        root = build_session(mutator=mutate)
        self.addCleanup(lambda: shutil.rmtree(root, ignore_errors=True))
        policy = narrow_policy(self.work / "policy.json", floor=2)
        with mock.patch("oracle.function.replay.replay_capture",
                        wraps=replay.replay_capture) as spy:
            cert = _certify(root, self.harness_path, policy, self.work / "out")
        self.assertEqual(spy.call_count, 50)
        self.assertEqual(cert["evaluation"]["match"], 49)
        self.assertEqual(cert["evaluation"]["mismatch"], 1)
        self.assertEqual(cert["evaluation"]["infrastructure_refusal"], 0)
        self.assertEqual(cert["promotion"]["status"], "failed")
        self.assertIn("eligible mismatch", cert["promotion"]["reasons"])


if __name__ == "__main__":
    unittest.main()
