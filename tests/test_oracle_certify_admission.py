"""E1 session admission: fail closed, and do not trust session.json."""
from __future__ import annotations

import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import eu4_gfx_live_capture as live

from oracle.certify import certify_session
from oracle.certify import evidence
from oracle.certify.errors import CertifyError
from oracle.function import fixture
from tests.test_oracle_certify_fixtures import (
    SESSION,
    build_session,
    candidate_for,
    evidence_refusal_transcript,
    flip_exit_canary,
    live_transcript,
    narrow_policy,
    reseal,
    tree_sha256,
    write_stub,
)


def _certify(root: Path, executable: Path, policy: Path, out: Path, **candidate_kwargs):
    before = tree_sha256(root)
    certificate = certify_session(
        root, fixture.load_descriptor(), policy,
        candidate_for(executable, **candidate_kwargs),
        certificate_dir=out)
    after = tree_sha256(root)
    if before != after:
        raise AssertionError("certify_session modified the sealed evidence directory")
    if (root / "session.json").exists() or (root / "summary.md").exists():
        raise AssertionError("certify_session created acquisition reports")
    return certificate


class AcquisitionConstantTests(unittest.TestCase):
    def test_constants_are_pinned_to_the_pr_d_contract(self) -> None:
        self.assertEqual(evidence.QUOTA, live.QUOTA)
        self.assertEqual(evidence.THREADS, live.THREADS)
        self.assertEqual(evidence.ACCEPTANCE, live.ACCEPTANCE)
        self.assertEqual(evidence.EPOCH, live.EPOCH)
        self.assertEqual(evidence.SCENARIOS, frozenset(live.SCENARIOS))
        self.assertEqual(evidence.SUMMARY_HEADER, live.SUMMARY_HEADER)
        self.assertEqual(evidence.STARTUP_HEADER, live.STARTUP_HEADER)
        self.assertEqual(evidence.DONE_HEADER, live.DONE_HEADER)
        entry = int(fixture.load_descriptor()["binary"]["entry_rva"], 16)
        self.assertEqual(entry, live.TARGET_RVA)

    def test_certifier_does_not_call_the_live_reporter(self) -> None:
        source = "\n".join(path.read_text(encoding="utf-8")
                           for path in Path(evidence.__file__).parent.glob("*.py"))
        self.assertNotIn("import eu4_gfx_live_capture", source)
        self.assertNotIn("eu4_gfx_live_capture.report", source)

    def test_public_callable_stays_unbound_on_a_class(self) -> None:
        class Holder:
            certify = certify_session

        self.assertIs(Holder().certify, certify_session)


class AdmissionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.work = Path(tempfile.mkdtemp(prefix="e1-admit-"))
        self.addCleanup(lambda: shutil.rmtree(self.work, ignore_errors=True))
        self.stub = self.work / "stub.sh"
        write_stub(self.stub)
        self.policy = narrow_policy(self.work / "policy.json")
        self.out = self.work / "out"

    def test_missing_tampered_extra_and_omitted_transcripts_fail_closed(self) -> None:
        missing = build_session()
        self.addCleanup(lambda: shutil.rmtree(missing, ignore_errors=True))
        (missing / "manifest.json").unlink()
        cert = _certify(missing, self.stub, self.policy, self.out / "missing")
        self.assertEqual(cert["reference_admission"]["session"]["verdict"], "ineligible")
        self.assertEqual(cert["promotion"]["status"], "failed")
        self.assertTrue(any("manifest" in reason
                            for reason in cert["reference_admission"]["session"]["reasons"]))

        tampered = build_session()
        self.addCleanup(lambda: shutil.rmtree(tampered, ignore_errors=True))
        capture = next((tampered / "captures").glob("*.capture"))
        capture.write_bytes(capture.read_bytes().replace(b"sequence=1", b"sequence=2", 1))
        cert = _certify(tampered, self.stub, self.policy, self.out / "tampered")
        self.assertEqual(cert["reference_admission"]["session"]["verdict"], "ineligible")
        self.assertTrue(any("manifest digest" in reason
                            for reason in cert["reference_admission"]["session"]["reasons"]))

        extra = build_session()
        self.addCleanup(lambda: shutil.rmtree(extra, ignore_errors=True))
        (extra / "captures" / f"{SESSION}.50.capture").write_text(
            live_transcript(50, "s03"), encoding="utf-8")
        cert = _certify(extra, self.stub, self.policy, self.out / "extra")
        self.assertEqual(cert["reference_admission"]["session"]["verdict"], "ineligible")
        self.assertTrue(any("omits" in reason or "written count" in reason
                            for reason in cert["reference_admission"]["session"]["reasons"]))

        omitted = build_session()
        self.addCleanup(lambda: shutil.rmtree(omitted, ignore_errors=True))
        next((omitted / "captures").glob("*.capture")).unlink()
        cert = _certify(omitted, self.stub, self.policy, self.out / "omitted")
        self.assertEqual(cert["reference_admission"]["session"]["verdict"], "ineligible")
        self.assertTrue(any("missing" in reason
                            for reason in cert["reference_admission"]["session"]["reasons"]))

    def test_identity_pid_negative_and_accounting_fail_session_admission(self) -> None:
        cases = (
            ("identity", {"startup_overrides": {"session": "other-session"}},
             "startup session"),
            ("pid", {"summary_overrides": {"pid": "999"}}, "PIDs do not agree"),
            ("negative", {"summary_overrides": {"intercepted": "-1"}}, "negative"),
            ("equation", {"summary_overrides": {"completed": "1"}}, "completed"),
        )
        for name, kwargs, expected in cases:
            with self.subTest(case=name):
                root = build_session(**kwargs)
                self.addCleanup(lambda path=root: shutil.rmtree(path, ignore_errors=True))
                reseal(root)
                cert = _certify(root, self.stub, self.policy, self.out / name)
                self.assertEqual(cert["reference_admission"]["session"]["verdict"],
                                 "ineligible")
                self.assertEqual(cert["promotion"]["status"], "failed")
                blob = " ".join(cert["reference_admission"]["session"]["reasons"])
                self.assertIn(expected, blob)

    def test_unqualified_transcripts_are_accounted_for_and_excluded(self) -> None:
        texts = [live_transcript(thread, "s03" if thread % 2 == 0 else "s10")
                 for thread in range(50)]
        texts.append(flip_exit_canary(live_transcript(50, "s03")))
        root = build_session(transcripts=texts)
        self.addCleanup(lambda: shutil.rmtree(root, ignore_errors=True))
        excluded = f"captures/{SESSION}.50.capture"
        with mock.patch("oracle.function.replay.replay_capture",
                        wraps=__import__("oracle.function.replay", fromlist=["replay_capture"]).replay_capture) as spy:
            cert = _certify(root, self.stub, self.policy, self.out / "unqualified")
        self.assertEqual(cert["reference_admission"]["session"]["verdict"], "eligible")
        self.assertEqual(cert["reference_admission"]["fixtures"]["verdict"], "eligible")
        self.assertEqual(cert["evidence"]["eligible"], 50)
        self.assertEqual(cert["evidence"]["ineligible"], 1)
        self.assertEqual(cert["evidence"]["transcript_count"], 51)
        self.assertTrue(any(excluded in reason
                            for reason in cert["reference_admission"]["fixtures"]["reasons"]))
        self.assertTrue(all(excluded not in failure
                            for failure in cert["evaluation"]["failures"]))
        self.assertEqual(spy.call_count, 50)
        self.assertEqual(cert["evaluation"]["infrastructure_refusal"], 50)
        self.assertEqual(cert["promotion"]["status"], "failed")
        self.assertTrue(any("infrastructure" in reason
                            for reason in cert["promotion"]["reasons"]))
        self.assertNotIn("eligible mismatch", cert["promotion"]["reasons"])

    def test_non_utf8_evidence_fails_closed(self) -> None:
        root = build_session()
        self.addCleanup(lambda: shutil.rmtree(root, ignore_errors=True))
        capture = sorted((root / "captures").glob("*.capture"))[0]
        capture.write_bytes(b"\xff\xfe not utf-8")
        reseal(root)
        cert = _certify(root, self.stub, self.policy, self.out / "utf8")
        self.assertEqual(cert["promotion"]["status"], "failed")
        self.assertEqual(cert["reference_admission"]["session"]["verdict"], "ineligible")
        self.assertTrue(any("UTF-8" in reason
                            for reason in cert["reference_admission"]["session"]["reasons"]))

    def test_explicitly_synthetic_session_is_not_live_grounded(self) -> None:
        root = build_session(synthetic_flag=1)
        self.addCleanup(lambda: shutil.rmtree(root, ignore_errors=True))
        cert = _certify(root, self.stub, self.policy, self.out / "synthetic")
        self.assertEqual(cert["promotion"]["status"], "failed")
        self.assertIs(cert["evidence"]["live_grounded"], False)
        self.assertIs(cert["evidence"]["explicitly_synthetic"], True)
        self.assertNotEqual(cert["evidence_tier"], "tier1-live-sealed-session")
        self.assertNotIn("tier1-live-sealed-session", __import__("json").dumps(cert))

    def test_certificate_is_written_outside_the_sealed_directory(self) -> None:
        root = build_session(count=1, summary_overrides={"written": "1", "admitted": "1",
                                                         "completed": "1", "intercepted": "1",
                                                         "threads": "1"})
        # count=1 is ineligible on the acceptance floor; the write target is the point.
        self.addCleanup(lambda: shutil.rmtree(root, ignore_errors=True))
        before = tree_sha256(root)
        with self.assertRaises(CertifyError):
            certify_session(root, fixture.load_descriptor(), self.policy,
                            candidate_for(self.stub), certificate_dir=root)
        self.assertEqual(tree_sha256(root), before)
        self.assertFalse((root / "tier1-certificate.json").exists())

    def test_evidence_refusal_is_an_admission_inconsistency(self) -> None:
        texts = [evidence_refusal_transcript(thread) for thread in range(50)]
        root = build_session(transcripts=texts)
        self.addCleanup(lambda: shutil.rmtree(root, ignore_errors=True))
        cert = _certify(root, self.stub, self.policy, self.out / "evidence")
        self.assertEqual(cert["reference_admission"]["session"]["verdict"], "eligible")
        self.assertEqual(cert["evaluation"]["evidence_refusal"], 50)
        self.assertEqual(cert["evaluation"]["infrastructure_refusal"], 0)
        self.assertEqual(cert["evaluation"]["mismatch"], 0)
        self.assertEqual(cert["promotion"]["status"], "failed")
        self.assertTrue(any("admission inconsistency" in reason
                            for reason in cert["promotion"]["reasons"]))

    def test_identical_inputs_produce_an_identical_certificate(self) -> None:
        root = build_session()
        self.addCleanup(lambda: shutil.rmtree(root, ignore_errors=True))
        first = _certify(root, self.stub, self.policy, self.out / "a", identity="same")
        second = _certify(root, self.stub, self.policy, self.out / "b", identity="same")
        self.assertEqual(first, second)
        self.assertEqual((self.out / "a" / "tier1-certificate.json").read_bytes(),
                         (self.out / "b" / "tier1-certificate.json").read_bytes())
        changed = _certify(root, self.stub, self.policy, self.out / "c",
                           identity="different-identity")
        self.assertNotEqual(first["candidate"]["build_identity"],
                            changed["candidate"]["build_identity"])
        self.assertNotEqual((self.out / "a" / "tier1-certificate.json").read_bytes(),
                            (self.out / "c" / "tier1-certificate.json").read_bytes())


if __name__ == "__main__":
    unittest.main()
