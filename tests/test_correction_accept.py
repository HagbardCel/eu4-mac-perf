"""Independent correction acceptance: adversarial and controlled-fault cases.

The fixture packages a synthetic sealed frame and a Metal replay into the same
portable case format the live pipeline uses.  The "renderer fault" is an
explicit one-pixel difference in the baseline replay; the candidate replay
removes it.  Nothing here executes EU4 or a GPU replay: acceptance is recomputed
from the validated artifacts by the existing comparison and checkpoint engines.
"""

from __future__ import annotations

import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from automation import (  # noqa: E402
    case_baseline,
    correction_accept,
    gfx_frame_graph,
    gfx_independent_replay,
    gfx_replay_case,
)
from tests.test_m7_diagnosis import WIDTH, HEIGHT, DiagnosisFixture, sha  # noqa: E402


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

    def __init__(self, root: Path, *, dither: bool = False):
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)
        self.fixture = DiagnosisFixture(root / "fixture")
        if dither:
            self.fixture.write_frame(dither=True)
        self.frame = self.fixture.frame
        self.reference = self.fixture.reference
        self.producer = case_baseline.current_producer()
        self._sequence = 0

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

    def claims(self, before: dict, after: dict) -> tuple[dict, dict]:
        return (
            {"mismatched_pixels": before["mismatched_pixels"],
             "max_channel_error": before["max_channel_error"]},
            {"mismatched_pixels": after["mismatched_pixels"],
             "max_channel_error": after["max_channel_error"]},
        )


class CorrectionAcceptanceTests(unittest.TestCase):
    def setUp(self):
        base = ROOT / ".local" / "test-runs"
        base.mkdir(parents=True, exist_ok=True)
        self.temporary = tempfile.TemporaryDirectory(dir=base)
        self.root = Path(self.temporary.name)
        self.model = CorrectionFixture(self.root / "model")

    def tearDown(self):
        self.temporary.cleanup()

    def acceptance(self, *, after: Path, claims, **kwargs):
        before = self.model.replay_dir("before", self.model.faulty_pixels())
        case_dir = self.model.package(before, "case", case_id="controlled")
        baseline = self.model.baseline(case_dir, "baseline.json")
        return correction_accept.evaluate(
            case_dir=case_dir,
            baseline_path=baseline,
            candidate_revision=kwargs.pop("candidate_revision", "candidate-1"),
            after_replay=after,
            claimed_before=claims[0],
            claimed_after=claims[1],
            baseline_revision=kwargs.pop("baseline_revision", "baseline-1"),
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
        self.assertEqual(
            report["after"]["statistics"]["mismatched_pixels"], 0)

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

    def test_valid_partial_improvement_with_fewer_pixels_is_accepted(self):
        # Baseline has two differing pixels; the candidate fixes one.
        baseline = bytearray(self.model.reference)
        baseline[0] = (baseline[0] + 7) % 256
        baseline[4] = (baseline[4] + 7) % 256
        before = self.model.replay_dir("before2", bytes(baseline))
        case_dir = self.model.package(before, "case2", case_id="partial")
        baseline_record = self.model.baseline(case_dir, "baseline2.json")
        after = self.model.replay_dir("after2", self.model.partial_pixels())
        report = correction_accept.evaluate(
            case_dir=case_dir,
            baseline_path=baseline_record,
            candidate_revision="candidate-2",
            after_replay=after,
            claimed_before={"mismatched_pixels": 2, "max_channel_error": 7},
            claimed_after={"mismatched_pixels": 2, "max_channel_error": 3},
            baseline_revision="baseline-1")
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
        # The candidate path points at the unmodified baseline replay while the
        # conclusion claims a corrected result.
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
        # Rewrite the packaged baseline Metal readback: its recorded digest no
        # longer describes the artifact that would be reused.
        (case_dir / "replay" / "metal-frame.bgra").write_bytes(self.model.reference)
        report = correction_accept.evaluate(
            case_dir=case_dir,
            baseline_path=baseline_path,
            candidate_revision="candidate-3",
            after_replay=after,
            claimed_before={"mismatched_pixels": 1, "max_channel_error": 7},
            claimed_after={"mismatched_pixels": 0, "max_channel_error": 0},
            baseline_revision="baseline-1")
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

    def test_regression_failure_hidden_behind_improvement_is_rejected(self):
        after = self.model.replay_dir("after-reg", self.model.reference)
        before = self.model.replay_dir("before-reg",
                                       self.model.faulty_pixels())
        case_dir = self.model.package(before, "case-reg", case_id="regression-main")
        baseline_path = self.model.baseline(case_dir, "baseline-reg.json")
        broken = bytearray(self.model.reference)
        broken[8] = (broken[8] + 1) % 256
        regression = self.model.replay_dir("regression", bytes(broken))
        regression_case = self.model.package(regression, "case-regression",
                                             case_id="regression-broken")
        report = correction_accept.evaluate(
            case_dir=case_dir,
            baseline_path=baseline_path,
            candidate_revision="candidate-4",
            after_replay=after,
            claimed_before={"mismatched_pixels": 1, "max_channel_error": 7},
            claimed_after={"mismatched_pixels": 0, "max_channel_error": 0},
            baseline_revision="baseline-1",
            regressions=[regression_case])
        self.assertEqual(report["result"], "rejected", report)
        codes = {item["code"] for item in report["problems"]}
        self.assertIn("regression_failed", codes)

    def test_unsupported_state_is_not_claimed_as_qualification(self):
        model = CorrectionFixture(self.root / "dither", dither=True)
        after = model.replay_dir("after-dither", model.reference)
        before = model.replay_dir("before-dither", model.faulty_pixels())
        case_dir = model.package(before, "case-dither", case_id="dither")
        baseline_path = model.baseline(case_dir, "baseline-dither.json")
        report = correction_accept.evaluate(
            case_dir=case_dir,
            baseline_path=baseline_path,
            candidate_revision="candidate-5",
            after_replay=after,
            claimed_before={"mismatched_pixels": 1, "max_channel_error": 7},
            claimed_after={"mismatched_pixels": 0, "max_channel_error": 0},
            baseline_revision="baseline-1")
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
            candidate_revision="same-rev", baseline_revision="same-rev")
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
        json.dumps(report)


if __name__ == "__main__":
    unittest.main()
