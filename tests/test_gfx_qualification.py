"""M7.0 static-scene qualification contract.

These regressions pin the corrected contract for PR #195:

* engine day / update identity / caller composition are correspondence evidence,
  never pixel-staticity;
* only valid, internally consistent, session-owned full-resolution frame dumps
  can establish staticity;
* the stable run must contain the compared frame, and ``stable_frames`` is
  honoured;
* the pixel path cannot promote a pair the sequence aligner rejected;
* a confirmed renderer difference needs healthy execution;
* the offline case classifies exactly like the campaign it came from.
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from automation import gfx_align, gfx_case, gfx_dual_run, gfx_qualification  # noqa: E402

try:
    import numpy as np
except Exception:  # noqa: BLE001
    np = None

SESSION = "session-static"
REFERENCE_DIGEST = "a" * 64
CANDIDATE_DIGEST = "b" * 64


def frame(index, digest, *, session=SESSION, valid=True, gpu_completed=True,
          origin="lower_left", meta_index=None, meta_digest=None,
          meta_session=None, raw_file=None, raw_bytes=None, width=8, height=6,
          meta=True):
    """One preserved frame record in the campaign's stop-dump shape."""
    if not meta:
        return {"frame_index": index, "sha256": digest, "valid": valid,
                "preserved": True, "reasons": []}
    return {
        "frame_index": index,
        "sha256": digest,
        "valid": valid,
        "preserved": True,
        "reasons": [] if valid else ["declared invalid"],
        "meta": {
            "schema": "gfx-frame-dump/v1",
            "frame_index": index if meta_index is None else meta_index,
            "session": session if meta_session is None else meta_session,
            "width": width,
            "height": height,
            "origin": origin,
            "pixel_format": "BGRA8",
            "raw_file": raw_file or f"captured/frame-{index}.bgra",
            "raw_bytes": width * height * 4 if raw_bytes is None else raw_bytes,
            "sha256": digest if meta_digest is None else meta_digest,
            "gpu_completed": gpu_completed,
        },
    }


def stream(*frames):
    return {"captured": list(frames), "missing": []}


def _found(frames, index):
    for item in frames:
        if item.get("frame_index") == index and item.get("meta"):
            return {str(index): item["meta"]}
    return {}


def run(reference_frames, candidate_frames, *, required=2, health="healthy",
        pair_status="aligned", comparison_status="aligned",
        difference_status="fail", reference_index=4, candidate_index=4,
        session=SESSION, classification=None, identity="0xd09cad25b3166452",
        statistics=None):
    pair = {
        "frame_index": reference_index,
        "candidate_frame_index": candidate_index,
        "status": pair_status,
        "reference": {"update_identity": identity, "game_day": 56456976},
        "candidate": {"update_identity": identity, "game_day": 56456976},
        "dumps": {
            "reference": {"found": _found(reference_frames, reference_index)},
            "candidate": {"found": _found(candidate_frames, candidate_index)},
        },
        "comparison": {
            "status": difference_status,
            "reasons": ["max_channel_error 200 > 8"],
            "alignment": {"status": comparison_status},
            "statistics": statistics or {
                "mismatched_pixels": 120,
                "pixels": 1000,
                "mismatched_fraction": 0.12,
                "max_channel_error": 200,
                "mean_abs_error": 3.0,
            },
        },
    }
    result = {
        "schema": "gfx-dual-run/v1",
        "session_id": session,
        "scenario": {"settle": {"stable_frames": required}},
        "health": {"verdict": health, "instrumentation_failures": [],
                   "first_failure": None},
        "frames": {
            "paired": 1,
            "aligned": 1 if pair_status == "aligned" else 0,
            "not_aligned": 0 if pair_status == "aligned" else 1,
            "comparison": {
                "compared": 1,
                "passed": 0,
                "failed": 1 if difference_status == "fail" else 0,
                "unverified": 0,
                "not_compared": 0,
                "first_failure": pair,
            },
        },
        "dumps": {"reference": stream(*reference_frames),
                  "candidate": stream(*candidate_frames)},
        "stability": {"status": "weak"},
        "stop": {"reason": "frame_difference"},
        "result": "stopped",
    }
    if classification is not None:
        result["classification"] = classification
    return result


def classify(result, **kwargs):
    return gfx_dual_run.classify_campaign(
        result, result["scenario"], max_frames=kwargs.pop("max_frames", 1))


class StaticityPredicateTests(unittest.TestCase):
    def staticity(self, reference, candidate, *, required=2, frames=None):
        return gfx_align.scene_staticity(
            {"reference": reference, "candidate": candidate},
            required=required,
            frames=frames or {"reference": 4, "candidate": 4},
            session=SESSION,
        )

    def test_identity_is_not_staticity(self):
        # Same day/identity, changed pixels: a stable-looking pair that is not
        # static.  Identity-only evidence must never verify it.
        value = self.staticity(
            stream(frame(4, REFERENCE_DIGEST), frame(5, "c" * 64)),
            stream(frame(4, CANDIDATE_DIGEST), frame(5, "d" * 64)),
        )
        self.assertFalse(value["verified"])
        result = run(
            [frame(4, REFERENCE_DIGEST), frame(5, "c" * 64)],
            [frame(4, CANDIDATE_DIGEST), frame(5, "d" * 64)],
        )
        self.assertEqual(classify(result)["status"], "incomplete")

    def test_invalid_dump_with_repeated_digest_is_not_static(self):
        value = self.staticity(
            stream(frame(4, REFERENCE_DIGEST, valid=False),
                   frame(5, REFERENCE_DIGEST, valid=False)),
            stream(frame(4, CANDIDATE_DIGEST, valid=False),
                   frame(5, CANDIDATE_DIGEST, valid=False)),
        )
        self.assertFalse(value["verified"])
        for role in ("reference", "candidate"):
            self.assertIn("not marked valid",
                          " ".join(value["roles"][role]["problems"]))

    def test_missing_metadata_is_not_static(self):
        value = self.staticity(
            stream(frame(4, REFERENCE_DIGEST, meta=False),
                   frame(5, REFERENCE_DIGEST, meta=False)),
            stream(frame(4, CANDIDATE_DIGEST, meta=False),
                   frame(5, CANDIDATE_DIGEST, meta=False)),
        )
        self.assertFalse(value["verified"])
        self.assertIn("metadata is absent",
                      " ".join(value["roles"]["reference"]["problems"]))

    def test_wrong_session_dump_is_not_static(self):
        value = self.staticity(
            stream(frame(4, REFERENCE_DIGEST, meta_session="other"),
                   frame(5, REFERENCE_DIGEST, meta_session="other")),
            stream(frame(4, CANDIDATE_DIGEST, meta_session="other"),
                   frame(5, CANDIDATE_DIGEST, meta_session="other")),
        )
        self.assertFalse(value["verified"])
        self.assertIn("different session",
                      " ".join(value["roles"]["reference"]["problems"]))

    def test_inconsistent_metadata_is_not_static(self):
        value = self.staticity(
            stream(frame(4, REFERENCE_DIGEST, meta_digest="0" * 64),
                   frame(5, REFERENCE_DIGEST)),
            stream(frame(4, CANDIDATE_DIGEST), frame(5, CANDIDATE_DIGEST)),
        )
        self.assertFalse(value["verified"])
        self.assertIn("disagrees with its metadata",
                      " ".join(value["roles"]["reference"]["problems"]))

    def test_unfinished_gpu_frame_is_not_static(self):
        value = self.staticity(
            stream(frame(4, REFERENCE_DIGEST, gpu_completed=False),
                   frame(5, REFERENCE_DIGEST)),
            stream(frame(4, CANDIDATE_DIGEST), frame(5, CANDIDATE_DIGEST)),
        )
        self.assertFalse(value["verified"])

    def test_stable_streak_must_contain_the_compared_frame(self):
        value = self.staticity(
            stream(frame(10, REFERENCE_DIGEST), frame(11, REFERENCE_DIGEST),
                   frame(12, REFERENCE_DIGEST), frame(14, "c" * 64)),
            stream(frame(10, CANDIDATE_DIGEST), frame(11, CANDIDATE_DIGEST),
                   frame(12, CANDIDATE_DIGEST), frame(14, "d" * 64)),
            frames={"reference": 14, "candidate": 14},
        )
        self.assertFalse(value["verified"])
        self.assertEqual(value["roles"]["reference"]["max_streak"], 3)
        self.assertEqual(value["roles"]["reference"]["pair_local_streak"], 1)
        self.assertIn("1-frame stable run",
                      " ".join(value["roles"]["reference"]["problems"]))

    def test_gapped_indices_do_not_form_a_streak(self):
        value = self.staticity(
            stream(frame(4, REFERENCE_DIGEST), frame(6, REFERENCE_DIGEST)),
            stream(frame(4, CANDIDATE_DIGEST), frame(6, CANDIDATE_DIGEST)),
        )
        self.assertFalse(value["verified"])
        self.assertEqual(value["roles"]["reference"]["max_streak"], 1)

    def test_conflicting_duplicate_index_is_rejected(self):
        value = self.staticity(
            stream(frame(4, REFERENCE_DIGEST), frame(4, "c" * 64),
                   frame(5, REFERENCE_DIGEST)),
            stream(frame(4, CANDIDATE_DIGEST), frame(5, CANDIDATE_DIGEST)),
        )
        self.assertFalse(value["verified"])
        self.assertIn("conflicting digests",
                      " ".join(value["roles"]["reference"]["problems"]))

    def test_multiple_required_frames_are_honoured(self):
        two = {
            "reference": stream(frame(4, REFERENCE_DIGEST),
                                frame(5, REFERENCE_DIGEST)),
            "candidate": stream(frame(4, CANDIDATE_DIGEST),
                                frame(5, CANDIDATE_DIGEST)),
        }
        self.assertTrue(gfx_align.scene_staticity(
            two, required=2, frames={"reference": 4, "candidate": 4},
            session=SESSION)["verified"])
        three = gfx_align.scene_staticity(
            two, required=3, frames={"reference": 4, "candidate": 4},
            session=SESSION)
        self.assertFalse(three["verified"])
        self.assertEqual(three["required_frames"], 3)
        self.assertIn("2-frame stable run",
                      " ".join(three["roles"]["reference"]["problems"]))

    def test_required_stable_frames_uses_the_campaign_setting(self):
        self.assertEqual(
            gfx_qualification.required_stable_frames(
                {"settle": {"stable_frames": 3}}), 3)
        self.assertEqual(
            gfx_qualification.required_stable_frames({"settle": {}}), 2)
        self.assertEqual(gfx_qualification.required_stable_frames({}), 2)

    def test_three_required_frames_reject_a_two_frame_campaign(self):
        result = run([frame(4, REFERENCE_DIGEST), frame(5, REFERENCE_DIGEST)],
                     [frame(4, CANDIDATE_DIGEST), frame(5, CANDIDATE_DIGEST)],
                     required=3)
        self.assertEqual(classify(result)["status"], "incomplete")


class RendererDifferenceGateTests(unittest.TestCase):
    def test_valid_static_aligned_pair_with_injected_difference(self):
        result = run([frame(4, REFERENCE_DIGEST), frame(5, REFERENCE_DIGEST)],
                     [frame(4, CANDIDATE_DIGEST), frame(5, CANDIDATE_DIGEST)])
        value = classify(result)
        self.assertEqual(value["status"], "rendering_difference")
        evidence = value["renderer_difference"]
        self.assertTrue(evidence["supported"])
        # Numerical statistics stay visible next to the attribution.
        self.assertIn("max_channel_error 200", " ".join(value["reasons"]))

    def test_unknown_health_cannot_establish_a_renderer_difference(self):
        frames = ([frame(4, REFERENCE_DIGEST), frame(5, REFERENCE_DIGEST)],
                  [frame(4, CANDIDATE_DIGEST), frame(5, CANDIDATE_DIGEST)])
        for health in ("unknown", "", None):
            result = run(*frames, health=health)
            value = classify(result)
            self.assertEqual(value["status"], "incomplete", health)
            self.assertIn("GPU health", " ".join(value["reasons"]))
            self.assertFalse(value["renderer_difference"]["supported"])

    def test_missing_health_evidence_is_not_healthy(self):
        result = run([frame(4, REFERENCE_DIGEST), frame(5, REFERENCE_DIGEST)],
                     [frame(4, CANDIDATE_DIGEST), frame(5, CANDIDATE_DIGEST)])
        result.pop("health")
        self.assertEqual(classify(result)["status"], "incomplete")

    def test_instrumentation_failure_cannot_establish_a_renderer_difference(self):
        result = run([frame(4, REFERENCE_DIGEST), frame(5, REFERENCE_DIGEST)],
                     [frame(4, CANDIDATE_DIGEST), frame(5, CANDIDATE_DIGEST)])
        result["health"]["instrumentation_failures"] = ["capture_missing"]
        value = classify(result)
        self.assertNotEqual(value["status"], "rendering_difference")
        self.assertEqual(value["status"], "infrastructure_failure")

    def test_alignment_rejection_cannot_be_promoted_by_pixels(self):
        # The sequence aligner rejects the pair (caller composition differs and
        # the workload relation exceeds tolerance); identical pixels must not
        # turn it into a comparison.
        reference = {"frame_index": 4, "width": 8, "height": 6, "draws": 120,
                     "caller_signature": "0xaaa", "update_identity": "0x10"}
        candidate = {"frame_index": 4, "width": 8, "height": 6, "draws": 200,
                     "caller_signature": "0xbbb", "update_identity": "0x10"}
        correspondence = gfx_align.pair_correspondence(
            reference, candidate, draw_tolerance=0.30)
        self.assertEqual(correspondence["status"], gfx_align.AMBIGUOUS)
        result = gfx_dual_run._compare_pair(
            {"frame_index": 4, "candidate_frame_index": 4,
             "status": correspondence["status"]},
            {"frame_index": 4, "width": 8, "height": 6, "draws": 120,
             "caller_signature": "0xaaa", "update_identity": "0x10",
             "raw_file": "/nonexistent.bgra"},
            {"frame_index": 4, "width": 8, "height": 6, "draws": 200,
             "caller_signature": "0xbbb", "update_identity": "0x10",
             "raw_file": "/nonexistent.bgra"},
            {"comparison": {"policy": "tolerant", "max_channel_error": 8,
                            "max_mismatched_fraction": 0.01,
                            "max_mean_abs_error": 1.5, "regions": {}}},
        )
        self.assertEqual(result["status"], "not_compared")
        self.assertIn("authoritative alignment rejected", result["reason"])
        # The observation is retained.
        self.assertTrue((result.get("alignment") or {}).get("status"))

    def test_missing_pair_status_cannot_be_promoted(self):
        result = gfx_dual_run._compare_pair(
            {"frame_index": 4, "candidate_frame_index": 4},
            {"frame_index": 4}, {"frame_index": 4},
            {"comparison": {"policy": "tolerant"}},
        )
        self.assertEqual(result["status"], "not_compared")
        self.assertIn("authoritative alignment rejected", result["reason"])

    @unittest.skipUnless(np is not None, "numpy is required")
    def test_different_draw_batching_with_shared_identity_is_still_compared(self):
        with tempfile.TemporaryDirectory() as temporary:
            import hashlib
            root = Path(temporary)
            pixel = np.full((4, 4, 4), 40, dtype=np.uint8)
            reference_path = root / "reference.bgra"
            candidate_path = root / "candidate.bgra"
            reference_path.write_bytes(
                pixel[::-1, :, [2, 1, 0, 3]].tobytes())
            candidate_path.write_bytes(
                np.repeat(np.repeat(pixel, 2, axis=0), 2, axis=1).tobytes())

            def meta(path, width, height, draws, signature, origin):
                payload = path.read_bytes()
                return {"schema": "gfx-frame-dump/v1", "frame_index": 4,
                        "width": width, "height": height, "origin": origin,
                        "pixel_format": "BGRA8", "row_stride": width * 4,
                        "raw_bytes": len(payload),
                        "sha256": hashlib.sha256(payload).hexdigest(),
                        "raw_file": str(path), "draws": draws,
                        "caller_signature": signature,
                        "update_identity": "0x10"}

            reference = meta(reference_path, 4, 4, 300, "0xaaa", "lower_left")
            candidate = meta(candidate_path, 8, 8, 350, "0xbbb", "top_left")
            status = gfx_align.pair_correspondence(
                reference, candidate, draw_tolerance=0.30)["status"]
            self.assertEqual(status, gfx_align.ALIGNED)
            result = gfx_dual_run._compare_pair(
                {"frame_index": 4, "candidate_frame_index": 4,
                 "status": status},
                reference, candidate,
                {"comparison": {"policy": "tolerant", "max_channel_error": 8,
                                "max_mismatched_fraction": 0.01,
                                "max_mean_abs_error": 1.5, "regions": {}},
                 "settle": {"draw_tolerance": 0.30}},
            )
            self.assertEqual(result["status"], "pass", result)


class CaseAgreementTests(unittest.TestCase):
    def test_case_and_campaign_agree_on_static_scene(self):
        result = run([frame(4, REFERENCE_DIGEST), frame(5, REFERENCE_DIGEST)],
                     [frame(4, CANDIDATE_DIGEST), frame(5, CANDIDATE_DIGEST)])
        pair = result["frames"]["comparison"]["first_failure"]
        self.assertEqual(classify(result)["status"], "rendering_difference")
        self.assertEqual(
            gfx_case._classify(result, pair, pair["comparison"]),
            gfx_case.CONFIRMED_RENDERING)

    def test_case_and_campaign_agree_on_unverified_scene(self):
        result = run([frame(4, REFERENCE_DIGEST)],
                     [frame(4, CANDIDATE_DIGEST)],
                     classification="rendering_difference")
        pair = result["frames"]["comparison"]["first_failure"]
        self.assertEqual(classify(result)["status"], "incomplete")
        self.assertEqual(
            gfx_case._classify(result, pair, pair["comparison"]),
            gfx_case.INCOMPLETE)

    def test_case_cannot_exceed_the_campaign_verdict(self):
        result = run([frame(4, REFERENCE_DIGEST), frame(5, REFERENCE_DIGEST)],
                     [frame(4, CANDIDATE_DIGEST), frame(5, CANDIDATE_DIGEST)],
                     classification="incomplete")
        pair = result["frames"]["comparison"]["first_failure"]
        self.assertEqual(
            gfx_case._classify(result, pair, pair["comparison"]),
            gfx_case.INCOMPLETE)

    def test_case_maps_an_alignment_failure_to_unaligned(self):
        result = run([frame(4, REFERENCE_DIGEST), frame(5, REFERENCE_DIGEST)],
                     [frame(4, CANDIDATE_DIGEST), frame(5, CANDIDATE_DIGEST)],
                     pair_status="ambiguous", comparison_status="ambiguous",
                     classification="alignment_failure")
        pair = result["frames"]["comparison"]["first_failure"]
        self.assertEqual(
            gfx_case._classify(result, pair, pair["comparison"]),
            gfx_case.UNALIGNED)


class HistoricalReclassificationTests(unittest.TestCase):
    """Recorded Experiment A sessions reclassify from renderer blame."""

    def experiment_a(self, *, session, frame_index, draws, mismatched, maximum):
        frames = ([frame(frame_index, "a500e416" + "0" * 56,
                         session=session)],
                  [frame(frame_index, "90f0f9bd" + "0" * 56,
                         session=session)])
        result = run(
            *frames, session=session,
            reference_index=frame_index, candidate_index=frame_index,
            classification="rendering_difference",
            statistics={"mismatched_pixels": mismatched, "pixels": 1930176,
                        "mismatched_fraction": mismatched / 1930176,
                        "max_channel_error": maximum,
                        "mean_abs_error": 0.56426},
        )
        result["frames"]["comparison"]["first_failure"]["reference"].update(
            {"draws": draws, "caller_signature": "0x9dcd9b35e4e083b6"})
        result["frames"]["comparison"]["first_failure"]["candidate"].update(
            {"draws": draws, "caller_signature": "0x9dcd9b35e4e083b6"})
        return result

    def test_recorded_live_a6_is_not_a_renderer_difference(self):
        result = self.experiment_a(
            session="20261114T181230Z-d28f6fee", frame_index=74, draws=370,
            mismatched=26042, maximum=228)
        value = classify(result)
        self.assertEqual(value["status"], "incomplete")
        joined = " ".join(value["reasons"])
        self.assertIn("pair-local static scene is unverified", joined)
        self.assertIn("max_channel_error 200", joined)
        self.assertFalse(value["qualified"])

    def test_recorded_live_a10_is_not_a_renderer_difference(self):
        result = self.experiment_a(
            session="20261114T181230Z-d28f6fee", frame_index=75, draws=368,
            mismatched=15647, maximum=220)
        value = classify(result)
        self.assertEqual(value["status"], "incomplete")
        self.assertIn("pair-local static scene is unverified",
                      " ".join(value["reasons"]))


if __name__ == "__main__":
    unittest.main()
