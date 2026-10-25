"""Production build record loader and E1 binding against the frozen record."""
from __future__ import annotations

import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from oracle.certify.build_record import (
    TRUSTED_BUILD_FLAGS,
    frozen_re1_source_sha256,
    load_production_build_record,
)
from oracle.certify.certify import frozen_source_sha256
from oracle.certify.errors import CertifyError
from oracle.certify import certify_session
from oracle.function import fixture
from tests.test_oracle_certify_fixtures import (
    build_session,
    candidate_for,
    narrow_policy,
    write_stub,
)


class BuildRecordTests(unittest.TestCase):
    def test_frozen_source_includes_the_header(self) -> None:
        self.assertEqual(frozen_source_sha256(), frozen_re1_source_sha256())
        self.assertEqual(
            frozen_source_sha256(),
            "ea5b50c049e8596365f65e1407e903ae12be98a50aca539e1adeb19bab31ad75",
        )

    def test_production_record_matches_the_frozen_pin(self) -> None:
        record = load_production_build_record()
        self.assertEqual(record["schema"], "eu4-oracle-trusted-build/v1")
        self.assertEqual(record["build_identity"],
                         "re1-gfx-set-constant-buffer-acceptance")
        self.assertEqual(record["source_sha256"], frozen_re1_source_sha256())
        self.assertEqual(record["build_flags"], list(TRUSTED_BUILD_FLAGS))
        self.assertEqual(
            record["executable_sha256"],
            "61a20732d7ee91375567de9d9f764d769829bc196ad02e1edabc050739bb0560",
        )


class ProductionBindingEnforcementTests(unittest.TestCase):
    def setUp(self) -> None:
        self.work = Path(tempfile.mkdtemp(prefix="e1-record-"))
        self.addCleanup(lambda: shutil.rmtree(self.work, ignore_errors=True))
        self.stub = self.work / "stub.sh"
        write_stub(self.stub)
        self.policy = narrow_policy(self.work / "policy.json")
        self.root = build_session()
        self.addCleanup(lambda: shutil.rmtree(self.root, ignore_errors=True))

    def test_forged_build_identity_fails_against_production_record(self) -> None:
        production = load_production_build_record()
        candidate = candidate_for(self.stub, identity="forged-test-identity")
        with patch(
                "oracle.certify.certify.load_production_build_record",
                return_value=production):
            cert = certify_session(
                self.root,
                fixture.load_descriptor(),
                self.policy,
                candidate,
                certificate_dir=self.work / "forged-identity",
            )
        self.assertEqual(cert["promotion"]["status"], "failed")
        blob = " ".join(cert["promotion"]["reasons"]).lower()
        self.assertIn("build_identity", blob)

    def test_unpatched_loader_enforces_production_identity(self) -> None:
        candidate = candidate_for(self.stub, identity="not-the-production-identity")
        cert = certify_session(
            self.root,
            fixture.load_descriptor(),
            self.policy,
            candidate,
            certificate_dir=self.work / "unpatched",
        )
        self.assertEqual(cert["promotion"]["status"], "failed")
        self.assertTrue(any("production build record" in reason.lower()
                            for reason in cert["promotion"]["reasons"]))

    def test_missing_production_record_fails_binding(self) -> None:
        candidate = candidate_for(self.stub)
        with patch(
                "oracle.certify.certify.load_production_build_record",
                side_effect=CertifyError("production build record is unreadable")):
            cert = certify_session(
                self.root,
                fixture.load_descriptor(),
                self.policy,
                candidate,
                certificate_dir=self.work / "missing-record",
            )
        self.assertEqual(cert["promotion"]["status"], "failed")
        self.assertTrue(any("production build record" in reason
                            for reason in cert["promotion"]["reasons"]))


if __name__ == "__main__":
    unittest.main()
