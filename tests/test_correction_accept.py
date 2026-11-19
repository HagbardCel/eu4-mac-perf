"""Independent correction acceptance: adversarial and controlled-fault cases.

The fixture packages a synthetic sealed frame and a Metal replay into the same
portable case format the live pipeline uses.  The "renderer fault" is an
explicit one-pixel difference in the baseline replay; the candidate replay
removes it.  Producer identity is mocked exactly as in ``test_case_baseline``,
so the suite is portable: no native replay host, EU4 launch or GPU is needed.
Acceptance is recomputed from the validated artifacts by the existing
comparison and checkpoint engines.
"""

from __future__ import annotations

import contextlib
import io
import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from automation import (  # noqa: E402
    campaign_guard,
    case_baseline,
    correction_accept,
    gfx_frame_graph,
    gfx_independent_replay,
    gfx_replay_case,
)
from tests.test_m7_diagnosis import WIDTH, HEIGHT, DiagnosisFixture, sha  # noqa: E402

REVISION = "d" * 40
OTHER_REVISION = "e" * 40

def producer_identity(*, revision: str = REVISION) -> dict:
    return {
        "executable": "/tmp/gfx_frame_replay",
        "executable_sha256": "c" * 64,
        "build_fingerprint": "b" * 64,
        "build_stamp_sha256": "a" * 64,
        "source_revision": revision,
    }


def contract(*ids: str, allow_additional: bool = False) -> dict:
    return {
        "schema": correction_accept.CONTRACT_SCHEMA,
        "required_regressions": list(ids),
        "allow_additional": allow_additional,
    }


def _report_for(fixture, replay_dir: Path, *, status: str) -> dict:
    """Minimal portable replay report required by the packager."""
    replay = json.loads((replay_dir / "replay.json").read_text())
    checkpoints = [
        {"event": item.get("event"), "target": item.get("target"),
         "metal_file": item.get("file")}
        for item in replay.get("checkpoints") or []
    ]
    return {
        "schema": "gfx-independent-replay/v1",
        "status": status,
        "checkpoints": checkpoints,
        "first_divergence": {"event": 142, "resource": "fb:0", "target": "0"},
        "graph": gfx_frame_graph.build(fixture.frame),
    }


class CorrectionFixture:
    """One sealed capture, one baseline replay and one candidate replay."""

    def __init__(self, root: Path, *, dither: bool = False,
                 session_id: str = "session-1",
                 capture_frame_id: int = 7):
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)
        self.fixture = DiagnosisFixture(
            root / "fixture", session_id=session_id,
            capture_frame_id=capture_frame_id)
        if dither:
            self.fixture.write_frame(dither=True)
        self.frame = self.fixture.frame
        self.reference = self.fixture.reference
        self.producer = case_baseline.current_producer()

    def replay_dir(self, name: str, candidate: bytes,
                   *, status: str = "confirmed_rendering_difference") -> Path:
        self.fixture.write_metal(candidate=candidate)
        directory = self.root / name
        if directory.exists():
            shutil.rmtree(directory)
        shutil.copytree(self.fixture.metal, directory)
        replay_path = directory / "replay.json"
        replay = json.loads(replay_path.read_text())
        # A complete full-frame execution record needs an explicit stage.
        replay.setdefault("stage", "complete")
        replay_path.write_text(json.dumps(replay))
        gfx_independent_replay.write_producer_receipt(
            directory, self.frame, self.producer, replay_path)
        (directory / "replay-report.json").write_text(
            json.dumps(_report_for(self.fixture, directory, status=status)))
        return directory

    def faulty_pixels(self) -> bytes:
        pixels = bytearray(self.reference)
        pixels[0] = (pixels[0] + 7) % 256
        return bytes(pixels)

    def partial_pixels(self) -> bytes:
        # Two differing pixels: improved, but not equivalent.
        pixels = bytearray(self.reference)
        pixels[0] = (pixels[0] + 3) % 256
        pixels[4] = (pixels[4] + 3) % 256
        return bytes(pixels)

    def two_fault_pixels(self) -> bytes:
        pixels = bytearray(self.reference)
        pixels[0] = (pixels[0] + 7) % 256
        pixels[4] = (pixels[4] + 7) % 256
        return bytes(pixels)

    def package(self, replay: Path, name: str, *, case_id: str) -> Path:
        case_dir = self.root / name
        gfx_replay_case.package(self.frame, replay, case_dir,
                                shaders=self.fixture.shaders,
                                case_id=case_id)
        return case_dir

    def baseline(self, case_dir: Path, name: str) -> Path:
        path = self.root / name
        case_baseline.record(case_dir, path)
        return path

    def rewrite_receipt(self, directory: Path) -> None:
        """Simulate a producer that emitted these bytes with a valid receipt."""
        gfx_independent_replay.write_producer_receipt(
            directory, self.frame, self.producer, directory / "replay.json")

    def claims(self, before: dict, after: dict) -> tuple[dict, dict]:
        return (
            {"mismatched_pixels": before["mismatched_pixels"],
             "max_channel_error": before["max_channel_error"]},
            {"mismatched_pixels": after["mismatched_pixels"],
             "max_channel_error": after["max_channel_error"]},
        )

    def exact_regression(self, name: str, case_id: str) -> tuple[Path, Path]:
        replay = self.replay_dir(name, self.reference)
        case_dir = self.package(replay, f"{name}-case", case_id=case_id)
        return case_dir, replay


class CorrectionAcceptanceTests(unittest.TestCase):
    def setUp(self):
        base = ROOT / ".local" / "test-runs"
        base.mkdir(parents=True, exist_ok=True)
        self.temporary = tempfile.TemporaryDirectory(dir=base)
        self.root = Path(self.temporary.name)
        for name, value in (
                ("_current_producer", producer_identity()),
                ("current_producer", producer_identity())):
            patcher = mock.patch.object(case_baseline, name,
                                        return_value=dict(value))
            patcher.start()
            self.addCleanup(patcher.stop)
        self.model = CorrectionFixture(self.root / "model")

    def tearDown(self):
        self.temporary.cleanup()

    def build_model(self, *, before_pixels=None):
        before_pixels = (self.model.faulty_pixels()
                         if before_pixels is None else before_pixels)
        before = self.model.replay_dir("before", before_pixels)
        case_dir = self.model.package(before, "case", case_id="controlled")
        baseline = self.model.baseline(case_dir, "baseline.json")
        return case_dir, baseline

    def rewrite_request(self, directory: Path, mutate) -> dict:
        """Change the published plan and rebind the shadow report to it."""
        path = directory / "checkpoint-request.json"
        request = json.loads(path.read_text())
        mutate(request)
        path.write_text(json.dumps(request))
        shadow_path = directory / "shadow-report.json"
        shadow = json.loads(shadow_path.read_text())
        shadow["checkpoint_request_sha256"] = sha(path.read_bytes())
        shadow["checkpoint_count"] = len(request.get("checkpoints") or [])
        shadow_path.write_text(json.dumps(shadow))
        return request

    def acceptance(self, *, after: Path, claims, **kwargs):
        case_dir, baseline = self.build_model()
        return correction_accept.evaluate(
            case_dir=case_dir,
            baseline_path=baseline,
            candidate_revision=kwargs.pop("candidate_revision", REVISION),
            after_replay=after,
            claimed_before=claims[0],
            claimed_after=claims[1],
            baseline_revision=kwargs.pop("baseline_revision", "baseline-1"),
            qualification_contract=kwargs.pop(
                "qualification_contract", contract()),
            **kwargs)

    def test_one_pixel_fault_is_corrected_and_accepted(self):
        after = self.model.replay_dir("after", self.model.reference)
        before_stats = {"mismatched_pixels": 1, "max_channel_error": 7}
        after_stats = {"mismatched_pixels": 0, "max_channel_error": 0}
        report = self.acceptance(
            after=after,
            claims=self.model.claims(before_stats, after_stats))
        self.assertEqual(report["result"], "accepted", report)
        self.assertTrue(report["improved"])
        self.assertTrue(report["pixel_perfect"], report)
        self.assertTrue(report["authoritative_checkpoint_exact"], report)
        self.assertEqual(
            report["after"]["statistics"]["mismatched_pixels"], 0)
        self.assertEqual(report["candidate"]["revision"], REVISION)

    def test_corrupted_intermediate_cannot_earn_exactness(self):
        # Reproducer from the review: exact final pixels must not cover damage
        # to an authoritative intermediate captured by the same case.
        after = self.model.replay_dir("after-corrupt", self.model.reference)
        intermediates = sorted(after.glob("intermediate-*.bgra"))
        self.assertTrue(intermediates, "fixture published no intermediates")
        pixels = bytearray(intermediates[0].read_bytes())
        pixels[0] ^= 1
        intermediates[0].write_bytes(bytes(pixels))
        # A genuine producer would sign the bytes it emitted; rebind them so
        # this exercises the checkpoint predicate, not receipt tamper checks.
        self.model.rewrite_receipt(after)
        report = self.acceptance(
            after=after,
            claims=self.model.claims(
                {"mismatched_pixels": 1, "max_channel_error": 7},
                {"mismatched_pixels": 0, "max_channel_error": 0}))
        self.assertNotEqual(report["result"], "accepted", report)
        self.assertFalse(report["pixel_perfect"], report)
        self.assertFalse(report["authoritative_checkpoint_exact"], report)
        self.assertIn("authoritative_checkpoint_regression",
                      {item["code"] for item in report["problems"]})
        self.assertEqual(
            report["after"]["checkpoint_authority"]["divergent"], 1)

    def test_intermediate_changed_after_receipt_is_invalid(self):
        after = self.model.replay_dir("after-tamper", self.model.reference)
        intermediate = sorted(after.glob("intermediate-*.bgra"))[0]
        pixels = bytearray(intermediate.read_bytes())
        pixels[0] ^= 1
        intermediate.write_bytes(bytes(pixels))
        report = self.acceptance(
            after=after,
            claims=self.model.claims(
                {"mismatched_pixels": 1, "max_channel_error": 7},
                {"mismatched_pixels": 0, "max_channel_error": 0}))
        self.assertEqual(report["result"], "invalid_evidence", report)
        self.assertIn("replay_receipt_mismatch",
                      json.dumps(report["problems"]))

    def test_receipt_without_intermediate_binding_stays_inspectable(self):
        after = self.model.replay_dir("after-legacy-receipt",
                                      self.model.reference)
        receipt_path = after / "producer-receipt.json"
        receipt = json.loads(receipt_path.read_text())
        receipt["result"].pop("checkpoint_files", None)
        receipt_path.write_text(json.dumps(receipt))
        report = self.acceptance(
            after=after,
            claims=self.model.claims(
                {"mismatched_pixels": 1, "max_channel_error": 7},
                {"mismatched_pixels": 0, "max_channel_error": 0}))
        self.assertEqual(report["result"], "accepted", report)
        self.assertTrue(report["pixel_perfect"], report)
        self.assertTrue(any("does not bind its intermediate" in item
                            for item in report["limitations"]))

    def test_persistent_intermediate_difference_is_not_exact(self):
        # A checkpoint that was divergent in the baseline and remains divergent
        # is reported separately: partial progress is allowed, but the
        # candidate still cannot be called pixel perfect.
        before = self.model.replay_dir("before-corrupt",
                                       self.model.faulty_pixels())
        for path in sorted(before.glob("intermediate-*.bgra")):
            pixels = bytearray(path.read_bytes())
            pixels[0] ^= 1
            path.write_bytes(bytes(pixels))
        self.model.rewrite_receipt(before)
        case_dir = self.model.package(before, "case-corrupt",
                                      case_id="corrupt-baseline")
        baseline = self.model.baseline(case_dir, "baseline-corrupt.json")
        after = self.model.replay_dir("after-persistent",
                                      self.model.reference)
        for path in sorted(after.glob("intermediate-*.bgra")):
            pixels = bytearray(path.read_bytes())
            pixels[0] ^= 1
            path.write_bytes(bytes(pixels))
        self.model.rewrite_receipt(after)
        report = correction_accept.evaluate(
            case_dir=case_dir,
            baseline_path=baseline,
            candidate_revision=REVISION,
            after_replay=after,
            claimed_before={"mismatched_pixels": 1, "max_channel_error": 7},
            claimed_after={"mismatched_pixels": 0, "max_channel_error": 0},
            baseline_revision="baseline-1",
            qualification_contract=contract())
        self.assertEqual(report["result"], "accepted", report)
        self.assertFalse(report["pixel_perfect"], report)
        self.assertFalse(report["authoritative_checkpoint_exact"], report)
        self.assertNotIn("authoritative_checkpoint_regression",
                         {item["code"] for item in report["problems"]})

    def test_empty_published_request_is_invalid(self):
        after = self.model.replay_dir("after-empty-request",
                                      self.model.reference)
        self.rewrite_request(
            after, lambda request: request.update(checkpoints=[]))
        report = self.acceptance(
            after=after,
            claims=self.model.claims(
                {"mismatched_pixels": 1, "max_channel_error": 7},
                {"mismatched_pixels": 0, "max_channel_error": 0}))
        self.assertEqual(report["result"], "invalid_evidence", report)
        self.assertIn("missing_required_request",
                      json.dumps(report["problems"]))

    def test_substituted_published_request_is_invalid(self):
        after = self.model.replay_dir("after-substituted-request",
                                      self.model.reference)

        def mutate(request):
            request["checkpoints"][0]["event"] = 999

        self.rewrite_request(after, mutate)
        report = self.acceptance(
            after=after,
            claims=self.model.claims(
                {"mismatched_pixels": 1, "max_channel_error": 7},
                {"mismatched_pixels": 0, "max_channel_error": 0}))
        self.assertEqual(report["result"], "invalid_evidence", report)
        self.assertIn("unexpected_request", json.dumps(report["problems"]))

    def test_worse_candidate_is_rejected(self):
        after = self.model.replay_dir("after-worse", self.model.partial_pixels())
        report = self.acceptance(
            after=after,
            claims=self.model.claims(
                {"mismatched_pixels": 1, "max_channel_error": 7},
                {"mismatched_pixels": 2, "max_channel_error": 3}))
        self.assertEqual(report["result"], "rejected", report)
        self.assertIn("not_improved",
                      {item["code"] for item in report["problems"]})

    def test_valid_partial_improvement_is_accepted_not_equivalent(self):
        case_dir, baseline = self.build_model(
            before_pixels=self.model.two_fault_pixels())
        after = self.model.replay_dir("after2", self.model.partial_pixels())
        report = correction_accept.evaluate(
            case_dir=case_dir,
            baseline_path=baseline,
            candidate_revision=REVISION,
            after_replay=after,
            claimed_before={"mismatched_pixels": 2, "max_channel_error": 7},
            claimed_after={"mismatched_pixels": 2, "max_channel_error": 3},
            baseline_revision="baseline-1",
            qualification_contract=contract())
        self.assertEqual(report["result"], "accepted", report)
        self.assertTrue(report["improved"])
        self.assertFalse(report["pixel_perfect"])

    def test_fabricated_improvement_statistics_are_rejected(self):
        after = self.model.replay_dir("after-fabricated",
                                      self.model.partial_pixels())
        report = self.acceptance(
            after=after,
            claims=self.model.claims(
                {"mismatched_pixels": 1, "max_channel_error": 7},
                {"mismatched_pixels": 0, "max_channel_error": 0}))
        self.assertEqual(report["result"], "invalid_evidence", report)
        self.assertIn("claim_disagrees",
                      {item["code"] for item in report["problems"]})

    def test_substituted_candidate_artifact_is_rejected(self):
        substituted = self.model.replay_dir(
            "substituted", self.model.faulty_pixels())
        report = self.acceptance(
            after=substituted,
            claims=self.model.claims(
                {"mismatched_pixels": 1, "max_channel_error": 7},
                {"mismatched_pixels": 0, "max_channel_error": 0}))
        self.assertEqual(report["result"], "invalid_evidence", report)
        self.assertIn("claim_disagrees",
                      {item["code"] for item in report["problems"]})

    def test_stale_candidate_build_receipt_is_rejected(self):
        after = self.model.replay_dir("after-stale", self.model.reference)
        receipt = json.loads((after / "producer-receipt.json").read_text())
        receipt["producer"]["build_fingerprint"] = "0" * 64
        (after / "producer-receipt.json").write_text(json.dumps(receipt))
        report = self.acceptance(
            after=after,
            claims=self.model.claims(
                {"mismatched_pixels": 1, "max_channel_error": 7},
                {"mismatched_pixels": 0, "max_channel_error": 0}))
        self.assertEqual(report["result"], "invalid_evidence", report)
        self.assertIn("candidate_build_stale",
                      {item["code"] for item in report["problems"]})

    def test_changed_baseline_artifact_invalidates_reuse(self):
        after = self.model.replay_dir("after-change", self.model.reference)
        before = self.model.replay_dir("before-change",
                                       self.model.faulty_pixels())
        case_dir = self.model.package(before, "case-change",
                                      case_id="changed")
        baseline_path = self.model.baseline(case_dir, "baseline-change.json")
        (case_dir / "replay" / "metal-frame.bgra").write_bytes(self.model.reference)
        report = correction_accept.evaluate(
            case_dir=case_dir,
            baseline_path=baseline_path,
            candidate_revision=REVISION,
            after_replay=after,
            claimed_before={"mismatched_pixels": 1, "max_channel_error": 7},
            claimed_after={"mismatched_pixels": 0, "max_channel_error": 0},
            baseline_revision="baseline-1",
            qualification_contract=contract())
        self.assertEqual(report["result"], "invalid_evidence", report)
        self.assertIn("baseline_replay_changed",
                      {item["code"] for item in report["problems"]})

    def test_failed_replay_with_valid_checkpoints_is_rejected(self):
        after = self.model.replay_dir("after-failed", self.model.reference)
        replay = json.loads((after / "replay.json").read_text())
        replay["command_status"] = 5
        (after / "replay.json").write_text(json.dumps(replay))
        report = self.acceptance(
            after=after,
            claims=self.model.claims(
                {"mismatched_pixels": 1, "max_channel_error": 7},
                {"mismatched_pixels": 0, "max_channel_error": 0}))
        self.assertEqual(report["result"], "invalid_evidence", report)
        self.assertIn("after_unverified",
                      {item["code"] for item in report["problems"]})

    def test_foreign_reference_replay_is_rejected(self):
        after = self.model.replay_dir("after-fref", self.model.reference)
        replay = json.loads((after / "replay.json").read_text())
        replay["manifest_reference_sha256"] = sha(b"another-frame")
        (after / "replay.json").write_text(json.dumps(replay))
        report = self.acceptance(
            after=after,
            claims=self.model.claims(
                {"mismatched_pixels": 1, "max_channel_error": 7},
                {"mismatched_pixels": 0, "max_channel_error": 0}))
        self.assertEqual(report["result"], "invalid_evidence", report)

    def test_unbound_alternate_readback_is_rejected(self):
        after = self.model.replay_dir("after-alt", self.model.reference)
        shutil.copy2(after / "metal-frame.bgra", after / "alternate.bgra")
        replay = json.loads((after / "replay.json").read_text())
        replay["frame"]["raw_file"] = "alternate.bgra"
        (after / "replay.json").write_text(json.dumps(replay))
        report = self.acceptance(
            after=after,
            claims=self.model.claims(
                {"mismatched_pixels": 1, "max_channel_error": 7},
                {"mismatched_pixels": 0, "max_channel_error": 0}))
        self.assertEqual(report["result"], "invalid_evidence", report)
        details = " ".join(item.get("detail", "")
                           for item in report["problems"])
        self.assertIn("unbound_readback", details)

    def test_revision_not_bound_to_the_tested_build_is_rejected(self):
        after = self.model.replay_dir("after-rev", self.model.reference)
        report = self.acceptance(
            after=after,
            claims=self.model.claims(
                {"mismatched_pixels": 1, "max_channel_error": 7},
                {"mismatched_pixels": 0, "max_channel_error": 0}),
            candidate_revision=OTHER_REVISION)
        self.assertEqual(report["result"], "invalid_evidence", report)
        self.assertIn("candidate_revision_mismatch",
                      {item["code"] for item in report["problems"]})

    def test_receipt_without_a_build_revision_is_rejected(self):
        after = self.model.replay_dir("after-norev", self.model.reference)
        receipt_path = after / "producer-receipt.json"
        receipt = json.loads(receipt_path.read_text())
        receipt["producer"].pop("source_revision", None)
        receipt_path.write_text(json.dumps(receipt))
        report = self.acceptance(
            after=after,
            claims=self.model.claims(
                {"mismatched_pixels": 1, "max_channel_error": 7},
                {"mismatched_pixels": 0, "max_channel_error": 0}))
        self.assertEqual(report["result"], "invalid_evidence", report)
        self.assertIn("candidate_revision_unbound",
                      {item["code"] for item in report["problems"]})

    def test_regression_with_its_own_sealed_capture_is_accepted(self):
        after = self.model.replay_dir("after-own-cap", self.model.reference)
        case_dir, baseline_path = self.build_model()
        other = CorrectionFixture(self.root / "other", session_id="session-2",
                                  capture_frame_id=9)
        replay = other.replay_dir("regression-other", other.reference)
        regression_case = other.package(
            replay, "case-regression-other", case_id="regression-other-capture")
        primary_seal = json.loads(
            (case_dir / "capture" / "frame-seal.json").read_text())
        regression_seal = json.loads(
            (regression_case / "capture" / "frame-seal.json").read_text())
        self.assertNotEqual(primary_seal["session_id"],
                            regression_seal["session_id"])
        report = correction_accept.evaluate(
            case_dir=case_dir,
            baseline_path=baseline_path,
            candidate_revision=REVISION,
            after_replay=after,
            claimed_before={"mismatched_pixels": 1, "max_channel_error": 7},
            claimed_after={"mismatched_pixels": 0, "max_channel_error": 0},
            baseline_revision="baseline-1",
            regressions=[regression_case],
            claimed_regressions=["regression-other-capture"],
            qualification_contract=contract("regression-other-capture"))
        self.assertEqual(report["result"], "accepted", report)
        self.assertEqual(report["regression_inventory"]["verified"],
                         ["regression-other-capture"])

    def test_regression_replay_identity_must_match_its_own_seal(self):
        after = self.model.replay_dir("after-bad-reg-id", self.model.reference)
        case_dir, baseline_path = self.build_model()
        other = CorrectionFixture(self.root / "other-bad", session_id="session-3",
                                  capture_frame_id=11)
        replay = other.replay_dir("regression-bad-id", other.reference)
        document = json.loads((replay / "replay.json").read_text())
        document["manifest_reference_sha256"] = sha(b"another-frame")
        (replay / "replay.json").write_text(json.dumps(document))
        gfx_independent_replay.write_producer_receipt(
            replay, other.frame, other.producer, replay / "replay.json")
        (replay / "replay-report.json").write_text(
            json.dumps(_report_for(other.fixture, replay,
                                   status="confirmed_rendering_difference")))
        regression_case = other.package(
            replay, "case-regression-bad-id", case_id="regression-bad-id")
        report = correction_accept.evaluate(
            case_dir=case_dir,
            baseline_path=baseline_path,
            candidate_revision=REVISION,
            after_replay=after,
            claimed_before={"mismatched_pixels": 1, "max_channel_error": 7},
            claimed_after={"mismatched_pixels": 0, "max_channel_error": 0},
            baseline_revision="baseline-1",
            regressions=[regression_case],
            claimed_regressions=["regression-bad-id"],
            qualification_contract=contract("regression-bad-id"))
        self.assertEqual(report["result"], "invalid_evidence", report)
        self.assertIn("regression_identity",
                      {item["code"] for item in report["problems"]})

    def test_regression_receipt_from_another_capture_is_rejected(self):
        after = self.model.replay_dir("after-bad-receipt",
                                      self.model.reference)
        case_dir, baseline_path = self.build_model()
        other = CorrectionFixture(self.root / "other-receipt",
                                  session_id="session-4",
                                  capture_frame_id=13)
        replay = other.replay_dir("regression-bad-receipt", other.reference)
        receipt_path = replay / "producer-receipt.json"
        receipt = json.loads(receipt_path.read_text())
        receipt["capture"]["session_id"] = "session-1"
        receipt_path.write_text(json.dumps(receipt))
        regression_case = other.package(
            replay, "case-regression-bad-receipt",
            case_id="regression-bad-receipt")
        report = correction_accept.evaluate(
            case_dir=case_dir,
            baseline_path=baseline_path,
            candidate_revision=REVISION,
            after_replay=after,
            claimed_before={"mismatched_pixels": 1, "max_channel_error": 7},
            claimed_after={"mismatched_pixels": 0, "max_channel_error": 0},
            baseline_revision="baseline-1",
            regressions=[regression_case],
            claimed_regressions=["regression-bad-receipt"],
            qualification_contract=contract("regression-bad-receipt"))
        self.assertEqual(report["result"], "invalid_evidence", report)
        self.assertIn("regression_unverified",
                      {item["code"] for item in report["problems"]})

    def test_regression_failure_hidden_behind_improvement_is_rejected(self):
        after = self.model.replay_dir("after-reg", self.model.reference)
        case_dir, baseline_path = self.build_model()
        broken = bytearray(self.model.reference)
        broken[8] = (broken[8] + 1) % 256
        regression = self.model.replay_dir("regression", bytes(broken))
        regression_case = self.model.package(regression, "case-regression",
                                             case_id="regression-broken")
        report = correction_accept.evaluate(
            case_dir=case_dir,
            baseline_path=baseline_path,
            candidate_revision=REVISION,
            after_replay=after,
            claimed_before={"mismatched_pixels": 1, "max_channel_error": 7},
            claimed_after={"mismatched_pixels": 0, "max_channel_error": 0},
            baseline_revision="baseline-1",
            regressions=[regression_case],
            claimed_regressions=["regression-broken"],
            qualification_contract=contract("regression-broken"))
        self.assertEqual(report["result"], "rejected", report)
        self.assertIn("regression_failed",
                      {item["code"] for item in report["problems"]})

    def test_valid_exact_match_regression_is_accepted(self):
        after = self.model.replay_dir("after-reg-ok", self.model.reference)
        case_dir, baseline_path = self.build_model()
        regression_case, _ = self.model.exact_regression(
            "regression-ok", "regression-ok")
        report = correction_accept.evaluate(
            case_dir=case_dir,
            baseline_path=baseline_path,
            candidate_revision=REVISION,
            after_replay=after,
            claimed_before={"mismatched_pixels": 1, "max_channel_error": 7},
            claimed_after={"mismatched_pixels": 0, "max_channel_error": 0},
            baseline_revision="baseline-1",
            regressions=[regression_case],
            claimed_regressions=["regression-ok"],
            qualification_contract=contract("regression-ok"))
        self.assertEqual(report["result"], "accepted", report)
        self.assertEqual(report["regression_inventory"]["verified"],
                         ["regression-ok"])

    def test_missing_required_regression_is_rejected(self):
        after = self.model.replay_dir("after-missing", self.model.reference)
        report = self.acceptance(
            after=after,
            claims=self.model.claims(
                {"mismatched_pixels": 1, "max_channel_error": 7},
                {"mismatched_pixels": 0, "max_channel_error": 0}),
            qualification_contract=contract("regression-ok"))
        self.assertEqual(report["result"], "invalid_evidence", report)
        self.assertIn("regression_missing",
                      {item["code"] for item in report["problems"]})

    def test_claimed_regression_without_evidence_is_rejected(self):
        after = self.model.replay_dir("after-claimed", self.model.reference)
        report = self.acceptance(
            after=after,
            claims=self.model.claims(
                {"mismatched_pixels": 1, "max_channel_error": 7},
                {"mismatched_pixels": 0, "max_channel_error": 0}),
            claimed_regressions=["regression-ok"])
        self.assertEqual(report["result"], "invalid_evidence", report)
        self.assertIn("regression_missing",
                      {item["code"] for item in report["problems"]})

    def test_duplicate_regression_is_rejected(self):
        after = self.model.replay_dir("after-dup", self.model.reference)
        case_dir, baseline_path = self.build_model()
        regression_case, _ = self.model.exact_regression(
            "regression-dup", "regression-dup")
        report = correction_accept.evaluate(
            case_dir=case_dir,
            baseline_path=baseline_path,
            candidate_revision=REVISION,
            after_replay=after,
            claimed_before={"mismatched_pixels": 1, "max_channel_error": 7},
            claimed_after={"mismatched_pixels": 0, "max_channel_error": 0},
            baseline_revision="baseline-1",
            regressions=[regression_case, regression_case],
            claimed_regressions=["regression-dup"],
            qualification_contract=contract("regression-dup"))
        self.assertEqual(report["result"], "invalid_evidence", report)
        self.assertIn("regression_duplicate",
                      {item["code"] for item in report["problems"]})

    def test_unrelated_regression_is_rejected(self):
        after = self.model.replay_dir("after-unrelated", self.model.reference)
        case_dir, baseline_path = self.build_model()
        unrelated, _ = self.model.exact_regression(
            "regression-other", "regression-other")
        report = correction_accept.evaluate(
            case_dir=case_dir,
            baseline_path=baseline_path,
            candidate_revision=REVISION,
            after_replay=after,
            claimed_before={"mismatched_pixels": 1, "max_channel_error": 7},
            claimed_after={"mismatched_pixels": 0, "max_channel_error": 0},
            baseline_revision="baseline-1",
            regressions=[unrelated],
            claimed_regressions=["regression-ok"],
            qualification_contract=contract("regression-ok"))
        self.assertEqual(report["result"], "invalid_evidence", report)
        codes = {item["code"] for item in report["problems"]}
        self.assertIn("regression_missing", codes)
        self.assertIn("regression_unexpected", codes)

    def test_failed_regression_execution_is_rejected_despite_zero_pixels(self):
        after = self.model.replay_dir("after-reg-fail", self.model.reference)
        case_dir, baseline_path = self.build_model()
        replay = self.model.replay_dir("regression-failed", self.model.reference)
        # Rebind a failed execution to the exact (zero-mismatch) pixels.
        document = json.loads((replay / "replay.json").read_text())
        document["command_status"] = 5
        (replay / "replay.json").write_text(json.dumps(document))
        gfx_independent_replay.write_producer_receipt(
            replay, self.model.frame, self.model.producer,
            replay / "replay.json")
        (replay / "replay-report.json").write_text(
            json.dumps(_report_for(self.model.fixture, replay,
                                   status="failed")))
        regression_case = self.model.package(
            replay, "case-regression-failed", case_id="regression-failed")
        report = correction_accept.evaluate(
            case_dir=case_dir,
            baseline_path=baseline_path,
            candidate_revision=REVISION,
            after_replay=after,
            claimed_before={"mismatched_pixels": 1, "max_channel_error": 7},
            claimed_after={"mismatched_pixels": 0, "max_channel_error": 0},
            baseline_revision="baseline-1",
            regressions=[regression_case],
            claimed_regressions=["regression-failed"],
            qualification_contract=contract("regression-failed"))
        self.assertEqual(report["result"], "invalid_evidence", report)
        self.assertIn("regression_incomplete",
                      {item["code"] for item in report["problems"]})

    def test_unsupported_state_is_not_claimed_as_qualification(self):
        model = CorrectionFixture(self.root / "dither", dither=True)
        after = model.replay_dir("after-dither", model.reference)
        before = model.replay_dir("before-dither", model.faulty_pixels())
        case_dir = model.package(before, "case-dither", case_id="dither")
        baseline_path = model.baseline(case_dir, "baseline-dither.json")
        report = correction_accept.evaluate(
            case_dir=case_dir,
            baseline_path=baseline_path,
            candidate_revision=REVISION,
            after_replay=after,
            claimed_before={"mismatched_pixels": 1, "max_channel_error": 7},
            claimed_after={"mismatched_pixels": 0, "max_channel_error": 0},
            baseline_revision="baseline-1",
            qualification_contract=contract())
        self.assertEqual(report["result"], "accepted", report)
        self.assertFalse(report["complete_qualification"])
        self.assertFalse(report["pixel_perfect"])

    def test_unchanged_candidate_revision_is_rejected(self):
        after = self.model.replay_dir("after-same-rev", self.model.reference)
        report = self.acceptance(
            after=after,
            claims=self.model.claims(
                {"mismatched_pixels": 1, "max_channel_error": 7},
                {"mismatched_pixels": 0, "max_channel_error": 0}),
            candidate_revision=REVISION, baseline_revision=REVISION)
        self.assertEqual(report["result"], "invalid_evidence", report)
        self.assertIn("candidate_unchanged",
                      {item["code"] for item in report["problems"]})

    def test_missing_candidate_receipt_is_rejected(self):
        after = self.model.replay_dir("after-noreceipt", self.model.reference)
        (after / "producer-receipt.json").unlink()
        report = self.acceptance(
            after=after,
            claims=self.model.claims(
                {"mismatched_pixels": 1, "max_channel_error": 7},
                {"mismatched_pixels": 0, "max_channel_error": 0}))
        self.assertEqual(report["result"], "invalid_evidence", report)
        self.assertIn("candidate_receipt_missing",
                      {item["code"] for item in report["problems"]})

    def test_report_is_machine_readable_and_explicit(self):
        after = self.model.replay_dir("after-json", self.model.reference)
        report = self.acceptance(
            after=after,
            claims=self.model.claims(
                {"mismatched_pixels": 1, "max_channel_error": 7},
                {"mismatched_pixels": 0, "max_channel_error": 0}))
        self.assertEqual(report["schema"], correction_accept.SCHEMA)
        self.assertIn(report["result"], correction_accept.RESULTS)
        self.assertIn("before", report)
        self.assertIn("after", report)
        self.assertIn("problems", report)
        self.assertIn("pixel_perfect", report)
        self.assertIn("regression_inventory", report)
        json.dumps(report)


class CorrectionBudgetTests(unittest.TestCase):
    """The corrections allowance is enforced at the acceptance boundary."""

    def setUp(self):
        base = ROOT / ".local" / "test-runs"
        base.mkdir(parents=True, exist_ok=True)
        self.temporary = tempfile.TemporaryDirectory(dir=base)
        self.root = Path(self.temporary.name)
        producer_patch = mock.patch.object(
            case_baseline, "_current_producer",
            return_value=producer_identity())
        producer_patch.start()
        self.addCleanup(producer_patch.stop)
        producer_patch = mock.patch.object(
            case_baseline, "current_producer",
            return_value=producer_identity())
        producer_patch.start()
        self.addCleanup(producer_patch.stop)
        self.model = CorrectionFixture(self.root / "model")
        self.state_path = self.root / "state.json"
        self.guard = campaign_guard.Guard(self.state_path)

    def tearDown(self):
        self.temporary.cleanup()

    def arguments(self):
        after = self.model.replay_dir("after-budget", self.model.reference)
        before = self.model.replay_dir("before-budget", self.model.faulty_pixels())
        case_dir = self.model.package(before, "case-budget",
                                      case_id="budget-case")
        baseline = self.model.baseline(case_dir, "baseline-budget.json")
        return {
            "case_dir": case_dir,
            "baseline_path": baseline,
            "candidate_revision": REVISION,
            "after_replay": after,
            "claimed_before": {"mismatched_pixels": 1,
                               "max_channel_error": 7},
            "claimed_after": {"mismatched_pixels": 0,
                              "max_channel_error": 0},
            "baseline_revision": "baseline-1",
            "qualification_contract": contract(),
        }

    def test_first_correction_proceeds_and_the_next_is_refused(self):
        self.guard.init(campaign_id="budget", budgets={"corrections": 1,
                                                       "replays": 2})
        arguments = self.arguments()
        with mock.patch.dict(
                os.environ, {"EU4_CAMPAIGN_STATE": str(self.state_path)}):
            report = correction_accept.evaluate(**arguments)
            self.assertEqual(report["result"], "accepted", report)
            status = self.guard.status()
            self.assertEqual(status["counts"]["corrections"], 1)
            self.assertEqual(status["remaining"]["corrections"], 0)
            self.assertEqual(
                [item["state"] for item in
                 self.guard.state()["reservations"].values()], ["completed"])
            with self.assertRaises(correction_accept.AcceptanceError) as raised:
                correction_accept.evaluate(**arguments)
        self.assertEqual(raised.exception.kind, "campaign_refused")
        self.assertIn("budget_exhausted", raised.exception.detail)

    def reservation(self):
        state = self.guard.state()
        reservations = list(state["reservations"].values())
        self.assertEqual(len(reservations), 1)
        return reservations[0]

    def test_accepted_attempt_is_recorded_with_its_verdict(self):
        self.guard.init(campaign_id="budget", budgets={"corrections": 1,
                                                       "replays": 2})
        arguments = self.arguments()
        with mock.patch.dict(
                os.environ, {"EU4_CAMPAIGN_STATE": str(self.state_path)}):
            report = correction_accept.evaluate(**arguments)
        self.assertEqual(report["result"], "accepted")
        reservation = self.reservation()
        self.assertEqual(reservation["state"], "completed")
        self.assertTrue(reservation["completion_evidence"]["accepted"])
        self.assertEqual(reservation["completion_evidence"]["result"], "accepted")
        self.assertEqual(reservation["completion_evidence"]["problem_codes"], [])

    def test_rejected_attempt_is_failed_with_its_problem_codes(self):
        self.guard.init(campaign_id="budget", budgets={"corrections": 1,
                                                       "replays": 2})
        after = self.model.replay_dir("after-rejected",
                                      self.model.partial_pixels())
        before = self.model.replay_dir("before-rejected",
                                       self.model.faulty_pixels())
        case_dir = self.model.package(before, "case-rejected",
                                      case_id="rejected-case")
        baseline = self.model.baseline(case_dir, "baseline-rejected.json")
        arguments = {
            "case_dir": case_dir, "baseline_path": baseline,
            "candidate_revision": REVISION, "after_replay": after,
            "claimed_before": {"mismatched_pixels": 1, "max_channel_error": 7},
            "claimed_after": {"mismatched_pixels": 2, "max_channel_error": 3},
            "baseline_revision": "baseline-1",
            "qualification_contract": contract(),
        }
        with mock.patch.dict(
                os.environ, {"EU4_CAMPAIGN_STATE": str(self.state_path)}):
            report = correction_accept.evaluate(**arguments)
        self.assertEqual(report["result"], "rejected", report)
        reservation = self.reservation()
        self.assertEqual(reservation["state"], "failed")
        self.assertFalse(reservation["completion_evidence"]["accepted"])
        self.assertEqual(reservation["completion_evidence"]["result"], "rejected")
        self.assertIn("not_improved", reservation["completion_evidence"]["problem_codes"])

    def test_invalid_evidence_attempt_is_failed_with_its_problem_codes(self):
        self.guard.init(campaign_id="budget", budgets={"corrections": 1,
                                                       "replays": 2})
        after = self.model.replay_dir("after-invalid",
                                      self.model.partial_pixels())
        before = self.model.replay_dir("before-invalid",
                                       self.model.faulty_pixels())
        case_dir = self.model.package(before, "case-invalid",
                                      case_id="invalid-case")
        baseline = self.model.baseline(case_dir, "baseline-invalid.json")
        arguments = {
            "case_dir": case_dir, "baseline_path": baseline,
            "candidate_revision": REVISION, "after_replay": after,
            "claimed_before": {"mismatched_pixels": 1, "max_channel_error": 7},
            "claimed_after": {"mismatched_pixels": 0, "max_channel_error": 0},
            "baseline_revision": "baseline-1",
            "qualification_contract": contract(),
        }
        with mock.patch.dict(
                os.environ, {"EU4_CAMPAIGN_STATE": str(self.state_path)}):
            report = correction_accept.evaluate(**arguments)
        self.assertEqual(report["result"], "invalid_evidence", report)
        reservation = self.reservation()
        self.assertEqual(reservation["state"], "failed")
        self.assertFalse(reservation["completion_evidence"]["accepted"])
        self.assertIn("claim_disagrees",
                      reservation["completion_evidence"]["problem_codes"])

    def test_exceptional_attempt_is_failed_and_keeps_its_charge(self):
        self.guard.init(campaign_id="budget", budgets={"corrections": 1,
                                                       "replays": 2})
        arguments = self.arguments()
        arguments["case_dir"] = self.root / "missing-case"
        with mock.patch.dict(
                os.environ, {"EU4_CAMPAIGN_STATE": str(self.state_path)}):
            with self.assertRaises(correction_accept.AcceptanceError):
                correction_accept.evaluate(**arguments)
        reservation = self.reservation()
        self.assertEqual(reservation["state"], "failed")
        self.assertEqual(self.guard.state()["counts"]["corrections"], 1)

    def test_refusal_cannot_be_bypassed_through_the_cli(self):
        self.guard.init(campaign_id="budget", budgets={"corrections": 1,
                                                       "replays": 2})
        arguments = self.arguments()
        claims = self.model.claims(
            {"mismatched_pixels": 1, "max_channel_error": 7},
            {"mismatched_pixels": 0, "max_channel_error": 0})
        argv = [
            "--case", str(arguments["case_dir"]),
            "--baseline", str(arguments["baseline_path"]),
            "--after-replay", str(arguments["after_replay"]),
            "--candidate-revision", REVISION,
            "--baseline-revision", "baseline-1",
            "--before-mismatched-pixels", str(claims[0]["mismatched_pixels"]),
            "--before-max-channel-error", str(claims[0]["max_channel_error"]),
            "--after-mismatched-pixels", str(claims[1]["mismatched_pixels"]),
            "--after-max-channel-error", str(claims[1]["max_channel_error"]),
        ]
        outputs = []
        with mock.patch.dict(
                os.environ, {"EU4_CAMPAIGN_STATE": str(self.state_path)}):
            for _ in range(2):
                buffer = io.StringIO()
                with contextlib.redirect_stdout(buffer):
                    code = correction_accept.main(argv)
                outputs.append((code, json.loads(buffer.getvalue())))
        self.assertEqual(outputs[0][0], 0)
        self.assertEqual(outputs[0][1]["result"], "accepted")
        self.assertEqual(outputs[1][0], 1)
        self.assertEqual(outputs[1][1]["result"], "invalid_evidence")
        self.assertIn("campaign_refused",
                      outputs[1][1]["problems"][0]["code"])
        self.assertIn("budget_exhausted",
                      outputs[1][1]["problems"][0]["detail"])

    def test_failed_evaluation_keeps_the_attempt_charged(self):
        self.guard.init(campaign_id="budget", budgets={"corrections": 1,
                                                       "replays": 2})
        arguments = self.arguments()
        arguments["case_dir"] = self.root / "missing-case"
        with mock.patch.dict(
                os.environ, {"EU4_CAMPAIGN_STATE": str(self.state_path)}):
            with self.assertRaises(correction_accept.AcceptanceError):
                correction_accept.evaluate(**arguments)
            with self.assertRaises(correction_accept.AcceptanceError) as raised:
                correction_accept.evaluate(**arguments)
        self.assertEqual(raised.exception.kind, "campaign_refused")
        state = self.guard.state()
        self.assertEqual(state["counts"]["corrections"], 1)
        self.assertEqual(
            [item["state"] for item in state["reservations"].values()],
            ["failed"])

    def test_zero_allowance_refuses_before_reading_evidence(self):
        self.guard.init(campaign_id="budget", budgets={"corrections": 0,
                                                       "replays": 2})
        arguments = self.arguments()
        arguments["case_dir"] = self.root / "never-read"
        with mock.patch.dict(
                os.environ, {"EU4_CAMPAIGN_STATE": str(self.state_path)}):
            with self.assertRaises(correction_accept.AcceptanceError) as raised:
                correction_accept.evaluate(**arguments)
        self.assertEqual(raised.exception.kind, "campaign_refused")
        self.assertEqual(self.guard.state()["reservations"], {})


if __name__ == "__main__":
    unittest.main()
