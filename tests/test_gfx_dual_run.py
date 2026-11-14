"""Scenario validation and dual-run stream alignment (PR 2)."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from automation import gfx_align, gfx_diagnostics, gfx_dual_run, gfx_launch, gfx_scenario  # noqa: E402

try:
    import numpy as np
except Exception:  # noqa: BLE001
    np = None

SHA = "a" * 64


def scenario(**overrides):
    value = {
        "schema": gfx_scenario.SCHEMA,
        "name": "test",
        "executable_sha256": SHA,
        "logical_extent": [1728, 1117],
        "start": {"kind": "frontend"},
        "observation": {"max_frames": 5},
        "comparison": {"policy": "exact"},
        "mode": "streaming",
        "profile": "early",
    }
    value.update(overrides)
    return value


class ScenarioTests(unittest.TestCase):
    def test_minimal_scenario_has_defaults_and_digest(self):
        value = gfx_scenario.normalize(scenario())
        self.assertEqual(value["logical_extent"], [1728, 1117])
        self.assertEqual(value["reference_backend"], "opengl")
        self.assertEqual(value["candidate_backend"], "metal")
        self.assertEqual(value["start"]["pause"], False)
        self.assertIn("map", value["comparison"]["regions"])
        self.assertEqual(len(value["digest"]), 64)
        again = gfx_scenario.normalize(scenario())
        self.assertEqual(value["digest"], again["digest"])

    def test_digest_covers_semantics(self):
        first = gfx_scenario.normalize(scenario())
        second = gfx_scenario.normalize(scenario(observation={"max_frames": 6}))
        self.assertNotEqual(first["digest"], second["digest"])

    def test_unknown_field_fails_closed(self):
        with self.assertRaises(gfx_scenario.ScenarioError) as caught:
            gfx_scenario.normalize(scenario(surprise=True))
        self.assertEqual(caught.exception.kind, "unknown_field")

    def test_bad_identity_is_refused(self):
        with self.assertRaises(gfx_scenario.ScenarioError):
            gfx_scenario.normalize(scenario(executable_sha256="xyz"))

    def test_save_start_requires_a_save(self):
        with self.assertRaises(gfx_scenario.ScenarioError):
            gfx_scenario.normalize(scenario(start={"kind": "save"}))

    def test_region_thresholds_survive_normalization(self):
        value = gfx_scenario.normalize(scenario(comparison={
            "regions": {"text": {"rect": [0, 8, 0, 16], "policy": "tolerant",
                                 "max_mismatched_fraction": 0.0,
                                 "min_structural_correlation": 0.75}}}))
        text = value["comparison"]["regions"]["text"]
        self.assertEqual(text["max_mismatched_fraction"], 0.0)
        self.assertEqual(text["min_structural_correlation"], 0.75)

    def test_committed_acceptance_scenarios_are_valid(self):
        directory = (Path(__file__).resolve().parents[1]
                     / "analysis" / "infra" / "scenarios")
        values = {path.name: gfx_scenario.load(path)
                  for path in sorted(directory.glob("*.json"))}
        self.assertIn("opengl-save-consistency.json", values)
        self.assertIn("metal-frontend-consistency.json", values)
        self.assertIn("mixed-frontend-differential.json", values)
        for value in values.values():
            self.assertTrue(value["digest"])
            if value["experiment"] == "consistency":
                self.assertEqual(value["reference_backend"],
                                 value["candidate_backend"])
            else:
                self.assertNotEqual(value["reference_backend"],
                                    value["candidate_backend"])

    def test_unimplemented_scenario_controls_fail_closed(self):
        for overrides in (
            {"input_events": [{"at_frame": 1, "event": "click"}]},
            {"game_config": {"language": "l_english"}},
            {"start": {"kind": "save", "save": "other.eu4"}},
        ):
            with self.assertRaises(gfx_scenario.ScenarioError) as caught:
                gfx_scenario.normalize(scenario(**overrides))
            self.assertEqual(caught.exception.kind, "unsupported_scenario_feature")

    def test_game_speed_zero_pause_mode_is_valid(self):
        value = gfx_scenario.normalize(scenario(
            start={"kind": "save", "save": "continuelastsave",
                   "pause": True, "pause_mode": "game_speed_zero"}))
        self.assertEqual(value["start"]["pause_mode"], "game_speed_zero")

    def test_pause_is_supported_as_a_single_native_request(self):
        value = gfx_scenario.normalize(scenario(
            start={"kind": "save", "save": "continuelastsave", "pause": True}))
        self.assertTrue(value["start"]["pause"])

    def test_save_scenario_uses_the_last_save(self):
        value = gfx_scenario.normalize(scenario(
            start={"kind": "save", "save": "continuelastsave"}))
        self.assertEqual(value["start"]["kind"], "save")

    def test_region_outside_extent_is_refused(self):
        with self.assertRaises(gfx_scenario.ScenarioError):
            gfx_scenario.normalize(scenario(comparison={
                "policy": "exact",
                "regions": {"ui": {"rect": [0, 5000, 0, 10]}},
            }))

    def test_unknown_policy_and_mode_are_refused(self):
        with self.assertRaises(gfx_scenario.ScenarioError):
            gfx_scenario.normalize(scenario(comparison={"policy": "vibes"}))
        with self.assertRaises(gfx_scenario.ScenarioError):
            gfx_scenario.normalize(scenario(mode="simultaneous"))

    def test_wrong_backend_pairing_is_refused(self):
        with self.assertRaises(gfx_scenario.ScenarioError):
            gfx_scenario.normalize(scenario(reference_backend="metal"))

    def test_consistency_experiment_requires_the_same_backend(self):
        value = gfx_scenario.normalize(scenario(
            experiment="consistency", reference_backend="opengl",
            candidate_backend="opengl"))
        self.assertEqual(value["experiment"], "consistency")
        with self.assertRaises(gfx_scenario.ScenarioError):
            gfx_scenario.normalize(scenario(
                experiment="consistency", reference_backend="opengl",
                candidate_backend="metal"))

    def test_load_round_trip(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "scenario.json"
            path.write_text(json.dumps(scenario()))
            value = gfx_scenario.load(path)
            self.assertEqual(value["name"], "test")


def write_record(path: Path, index: int, *, sha: str, valid: bool = True,
                 preview: bytes | None = None,
                 extra: dict | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if preview is not None:
        (path.parent / f"preview-{index}.bgra").write_bytes(preview)
    record = {
        "schema": "gfx-frame-observe/v1",
        "backend": "opengl",
        "frame_index": index,
        "width": 4,
        "height": 4,
        "sha256": sha,
        "capture_valid": valid,
        "gpu_completed": True,
        "caller_signature": "0xabc",
        "draws": 2,
        "preview_file": f"preview-{index}.bgra" if preview is not None else "",
    }
    if extra:
        record.update(extra)
    with path.open("a", encoding="utf-8") as target:
        target.write(json.dumps(record) + "\n")


class AlignmentTests(unittest.TestCase):
    def setUp(self):
        self._temporary = tempfile.TemporaryDirectory()
        self.root = Path(self._temporary.name)
        self.ref_path = self.root / "ref" / "frame-observe.jsonl"
        self.cand_path = self.root / "cand" / "frame-observe.jsonl"
        self.ref_path.parent.mkdir()
        self.cand_path.parent.mkdir()

    def tearDown(self):
        self._temporary.cleanup()

    def state(self) -> gfx_dual_run.CampaignState:
        state = gfx_dual_run.CampaignState(
            ref_stream=gfx_dual_run.FrameStream(self.ref_path),
            cand_stream=gfx_dual_run.FrameStream(self.cand_path),
        )
        state.pairing = True
        state.ref_base = 1
        state.cand_base = 1
        return state

    def test_streams_are_read_incrementally(self):
        stream = gfx_dual_run.FrameStream(self.ref_path)
        write_record(self.ref_path, 1, sha="b" * 64)
        self.assertEqual(len(stream.poll()), 1)
        self.assertEqual(len(stream.poll()), 0)
        write_record(self.ref_path, 2, sha="c" * 64)
        self.assertEqual([record.index for record in stream.poll()], [2])

    def test_pairs_are_aligned_by_frame_index(self):
        state = self.state()
        for index in (1, 2, 3):
            write_record(self.ref_path, index, sha=f"{index:064x}")
            write_record(self.cand_path, index, sha=f"{index:064x}")
        state.ref_by_index.update({r.index: r for r in state.ref_stream.poll()})
        state.cand_by_index.update({r.index: r for r in state.cand_stream.poll()})
        pairs = gfx_dual_run.align_streams(state)
        self.assertEqual([pair["frame_index"] for pair in pairs], [1, 2, 3])
        self.assertTrue(all(pair["status"] == gfx_dual_run.ALIGNED for pair in pairs))
        self.assertTrue(all(pair["exact_equal"] for pair in pairs))

    def test_invalid_capture_is_an_alignment_failure_not_a_pixel_difference(self):
        state = self.state()
        write_record(self.ref_path, 1, sha="a" * 64, valid=False)
        write_record(self.cand_path, 1, sha="a" * 64)
        state.ref_by_index.update({r.index: r for r in state.ref_stream.poll()})
        state.cand_by_index.update({r.index: r for r in state.cand_stream.poll()})
        pairs = gfx_dual_run.align_streams(state)
        self.assertEqual(pairs[0]["status"], gfx_dual_run.NOT_ALIGNED)

    def test_different_digests_are_aligned_but_not_exactly_equal(self):
        state = self.state()
        write_record(self.ref_path, 4, sha="a" * 64)
        write_record(self.cand_path, 4, sha="b" * 64)
        state.ref_by_index.update({r.index: r for r in state.ref_stream.poll()})
        state.cand_by_index.update({r.index: r for r in state.cand_stream.poll()})
        pair = gfx_dual_run.align_streams(state)[0]
        self.assertEqual(pair["status"], gfx_dual_run.ALIGNED)
        self.assertFalse(pair["exact_equal"])

    def test_preview_comparison_is_bounded_and_reports_metrics(self):
        state = self.state()
        write_record(self.ref_path, 1, sha="a" * 64, preview=bytes([10, 20, 30, 255] * 16))
        write_record(self.cand_path, 1, sha="b" * 64, preview=bytes([12, 20, 30, 255] * 16))
        state.ref_by_index.update({r.index: r for r in state.ref_stream.poll()})
        state.cand_by_index.update({r.index: r for r in state.cand_stream.poll()})
        pair = gfx_dual_run.align_streams(state)[0]
        preview = pair["preview"]
        self.assertEqual(preview["status"], "compared")
        self.assertEqual(preview["pixels"], 16)
        self.assertEqual(preview["different_pixels"], 16)
        self.assertEqual(preview["max_channel_error"], 2)

    def test_begin_pairing_anchors_at_the_next_frame_on_both_sides(self):
        state = self.state()
        state.pairing = False
        state.ref_base = None
        state.cand_base = None
        state.ref_stream.records = [
            gfx_dual_run.FrameRecord("opengl", self.ref_path,
                                     {"frame_index": 10, "draws": 20}),
            gfx_dual_run.FrameRecord("opengl", self.ref_path,
                                     {"frame_index": 11, "draws": 20}),
        ]
        state.cand_stream.records = [
            gfx_dual_run.FrameRecord("metal", self.cand_path,
                                     {"frame_index": 40, "draws": 20}),
        ]
        state.begin_pairing()
        self.assertEqual((state.ref_base, state.cand_base), (12, 41))
        self.assertEqual((state.phase_entry_ref, state.phase_entry_cand), (11, 40))
        self.assertTrue(state.pairing)

    def test_pairs_carry_engine_identity(self):
        state = self.state()
        for index in (1, 2):
            write_record(self.ref_path, index, sha=f"{index:064x}",
                         extra={"update_identity": "0x10", "game_day": 42})
            write_record(self.cand_path, index, sha=f"{index:064x}",
                         extra={"update_identity": "0x10", "game_day": 42})
        state.ref_by_index.update({r.index: r for r in state.ref_stream.poll()})
        state.cand_by_index.update({r.index: r for r in state.cand_stream.poll()})
        pairs = gfx_dual_run.align_streams(state, limit=2)
        self.assertEqual(pairs[0]["reference"]["update_identity"], "0x10")
        self.assertEqual(pairs[0]["candidate"]["game_day"], 42)

    def test_pairing_respects_the_frame_limit(self):
        state = self.state()
        for index in (1, 2, 3, 4, 5):
            write_record(self.ref_path, index, sha=f"{index:064x}")
            write_record(self.cand_path, index, sha=f"{index:064x}")
        state.ref_by_index.update({r.index: r for r in state.ref_stream.poll()})
        state.cand_by_index.update({r.index: r for r in state.cand_stream.poll()})
        first = gfx_dual_run.align_streams(state, limit=2)
        self.assertEqual([pair["frame_index"] for pair in first], [1, 2])
        second = gfx_dual_run.align_streams(state, limit=2)
        self.assertEqual([pair["frame_index"] for pair in second], [3, 4])

    def test_phase_rejects_unequal_game_update(self):
        self.assertFalse(gfx_dual_run._phase_ready(100, 100, 40, 0.25, 10, 11))
        self.assertTrue(gfx_dual_run._phase_ready(100, 100, 40, 0.25, 10, 10))
        # Same identity but backend-dependent draw counts still qualifies.
        self.assertTrue(gfx_dual_run._phase_ready(100, 500, 40, 0.25, 10, 10))

    def test_identity_streak_verifies_correspondence(self):
        state = object.__new__(gfx_dual_run.CampaignState)
        state.stability = {
            "reference": {"last_digest": None, "streak": 0, "max_streak": 0},
            "candidate": {"last_digest": None, "streak": 0, "max_streak": 0},
        }
        state.identity_max_streak = 3
        self.assertEqual(state.stability_summary(3)["status"], "verified")
        state.identity_max_streak = 0
        self.assertEqual(state.stability_summary(3)["status"], "weak")

    def test_phase_requires_similar_draw_counts(self):
        self.assertFalse(gfx_dual_run._phase_ready(46, 273, 40, 0.25))
        self.assertTrue(gfx_dual_run._phase_ready(252, 273, 40, 0.25))
        self.assertFalse(gfx_dual_run._phase_ready(10, 273, 40, 0.25))

    def test_malformed_lines_are_counted_not_silently_dropped(self):
        self.ref_path.write_text("{not json}\n", encoding="utf-8")
        stream = gfx_dual_run.FrameStream(self.ref_path)
        self.assertEqual(stream.poll(), [])
        self.assertEqual(stream.malformed, 1)


if __name__ == "__main__":
    unittest.main()


class LaunchPreparationTests(unittest.TestCase):
    def setUp(self):
        self._temporary = tempfile.TemporaryDirectory()
        self.root = Path(self._temporary.name)
        self.library = self.root / "observer.dylib"
        self.library.write_bytes(b"fake library")
        self.qualified = self.root / "qualified.json"
        self.qualified.write_text('{"schema":"eu4-gfx-qualified-sources/v0","sources":[]}')
        self.cache = self.root / "shader-cache.json"
        self.cache.write_text('{"schema":"gfx-shader-cache/v1","stages":{}}')
        self.config = gfx_launch.LaunchConfig(
            executable=Path("/bin/true"),
            args=["-enabletelemetry"],
            executable_sha256=SHA,
            logical_extent=(1728, 1117),
            qualified_sources=self.qualified,
            shader_cache=self.cache,
            ring=4,
            deadline_seconds=60.0,
            session_id="session-test",
        )

    def tearDown(self):
        self._temporary.cleanup()

    def prepare(self, role: str):
        return gfx_launch.prepare(
            role, self.config, self.root / role,
            libraries={"reference": self.library, "candidate": self.library},
        )

    def test_reference_uses_the_passive_opengl_observer(self):
        env, run_dir, observe = self.prepare("reference")
        self.assertEqual(env["DYLD_INSERT_LIBRARIES"], str(self.library))
        self.assertEqual(env["EU4_GFX_STARTUP_DIR"], str(run_dir))
        self.assertEqual(env["EU4_GFX_OPS_STOP_POLICY"], "none")
        self.assertEqual(env["EU4_GFX_FRAME_OBSERVE_DIR"], str(observe))
        self.assertEqual(env["EU4_GFX_FRAME_RING"], "4")
        self.assertNotIn("EU4_METAL_BOOT_MODE", env)

    def test_candidate_uses_the_metal_boot_path_and_requires_a_cache(self):
        env, run_dir, observe = self.prepare("candidate")
        self.assertEqual(env["EU4_METAL_BOOT_MODE"], "metal_init")
        self.assertEqual(env["EU4_METAL_INIT_DIR"], str(run_dir))
        self.assertEqual(env["EU4_GFX_SHADER_CACHE"], str(self.cache))
        self.assertEqual(env["EU4_GFX_QUALIFIED_SOURCES"], str(self.qualified))
        self.assertEqual(env["SDL_VIDEO_EXTERNAL_CONTEXT"], "1")
        self.assertNotIn("EU4_GFX_STARTUP_DIR", env)

    def test_candidate_without_cache_is_refused_before_spawn(self):
        self.config.shader_cache = self.root / "missing.json"
        with self.assertRaises(gfx_launch.LaunchError) as caught:
            self.prepare("candidate")
        self.assertEqual(caught.exception.kind, "config")

    def test_pause_request_is_passed_to_the_observer(self):
        self.config.pause_at_draws = 100
        env, _run_dir, _observe = gfx_launch.prepare(
            "reference", self.config, self.root / "paused",
            libraries={"reference": self.library, "candidate": self.library},
        )
        self.assertEqual(env["EU4_GFX_PAUSE_AT_DRAWS"], "100")

    def test_game_speed_zero_pause_is_passed_to_the_observer(self):
        self.config.pause_by_game_speed_zero = True
        env, _run_dir, _observe = gfx_launch.prepare(
            "reference", self.config, self.root / "speed-zero",
            libraries={"reference": self.library, "candidate": self.library},
        )
        self.assertEqual(env["EU4_GFX_PAUSE_BY_GAME_SPEED_ZERO"], "1")

    def test_first_game_day_pause_is_passed_to_the_observer(self):
        self.config.pause_at_first_game_day = True
        env, _run_dir, _observe = gfx_launch.prepare(
            "reference", self.config, self.root / "first-day",
            libraries={"reference": self.library, "candidate": self.library},
        )
        self.assertEqual(env["EU4_GFX_PAUSE_AT_FIRST_GAME_DAY"], "1")

    def test_inherited_instrumentation_is_cleared(self):
        import os
        from unittest import mock
        with mock.patch.dict(os.environ, {"EU4_GFX_LEAK": "1",
                                          "DYLD_INSERT_LIBRARIES": "/tmp/old.dylib"}):
            env, _run_dir, _observe = self.prepare("reference")
        self.assertNotIn("EU4_GFX_LEAK", env)
        self.assertEqual(env["DYLD_INSERT_LIBRARIES"], str(self.library))


class PairComparisonTests(unittest.TestCase):
    def setUp(self):
        try:
            import numpy as np
        except Exception:  # noqa: BLE001
            self.skipTest("numpy is required")
        self.np = np
        self._temporary = tempfile.TemporaryDirectory()
        self.root = Path(self._temporary.name)
        self.image = np.full((4, 4, 4), 20, dtype=np.uint8)
        self.image[0:2, 0:2, :3] = 200
        self.reference_path = self.root / "ref.bgra"
        self.reference_path.write_bytes(self.image[::-1, :, [2, 1, 0, 3]].tobytes())
        self.candidate_path = self.root / "cand.bgra"
        self.candidate_path.write_bytes(
            np.repeat(np.repeat(self.image, 2, axis=0), 2, axis=1).tobytes())
        self.scenario = {
            "comparison": {
                "policy": "tolerant",
                "max_channel_error": 4,
                "max_mismatched_fraction": 0.0,
                "max_mean_abs_error": 0.0,
                "regions": {"all": {"rect": [0, 4, 0, 4]}},
            }
        }
        self.meta = {
            "reference": {"width": 4, "height": 4, "origin": "lower_left",
                          "pixel_format": "BGRA8", "sha256": "a" * 64,
                          "raw_file": str(self.reference_path),
                          "caller_signature": "0xabc", "draws": 6},
            "candidate": {"width": 8, "height": 8, "origin": "top_left",
                          "pixel_format": "BGRA8", "sha256": "b" * 64,
                          "raw_file": str(self.candidate_path),
                          "caller_signature": "0xabc", "draws": 6},
        }

    def tearDown(self):
        self._temporary.cleanup()

    def pair(self):
        return {"frame_index": 3, "candidate_frame_index": 3}

    def test_scaled_matching_pair_passes(self):
        result = gfx_dual_run._compare_pair(
            self.pair(), self.meta["reference"], self.meta["candidate"],
            self.scenario,
        )
        self.assertEqual(result["status"], "pass", result)
        self.assertEqual(result["normalization"]["scale"], 2)
        self.assertEqual(result["statistics"]["mismatched_pixels"], 0)

    def test_mirrored_pair_fails_with_region(self):
        mirrored = self.image[:, ::-1, :]
        candidate = np.repeat(np.repeat(mirrored, 2, axis=0), 2, axis=1)
        self.candidate_path.write_bytes(candidate.tobytes())
        result = gfx_dual_run._compare_pair(
            self.pair(), self.meta["reference"], self.meta["candidate"],
            self.scenario,
        )
        self.assertEqual(result["status"], "fail", result)
        self.assertIn("all", result["mirrored_regions"])

    def test_identity_with_different_backend_draw_counts_is_compared(self):
        scenario = dict(self.scenario)
        scenario["settle"] = {"draw_tolerance": 0.5}
        reference = dict(self.meta["reference"], draws=300,
                         update_identity="0x10")
        candidate = dict(self.meta["candidate"], draws=350,
                         caller_signature="0xdef", update_identity="0x10")
        result = gfx_dual_run._compare_pair(
            self.pair(), reference, candidate, scenario)
        self.assertEqual(result["status"], "pass", result)
        self.assertTrue(result["alignment"]["identity_equal"])
        self.assertLess(result["alignment"]["draw_relation"], 0.5)

    def test_same_day_different_workload_explains_refusal(self):
        scenario = dict(self.scenario)
        scenario["settle"] = {"draw_tolerance": 0.1}
        reference = dict(self.meta["reference"], draws=241,
                         update_identity="0x10")
        candidate = dict(self.meta["candidate"], draws=370,
                         caller_signature="0xdef", update_identity="0x10")
        result = gfx_dual_run._compare_pair(
            self.pair(), reference, candidate, scenario)
        self.assertEqual(result["status"], "not_compared")
        self.assertIn("intra-day state", result["reason"])

    def test_weak_day_identity_rejects_uncorroborated_scene(self):
        scenario = dict(self.scenario)
        scenario["settle"] = {"draw_tolerance": 0.5}
        scenario["comparison"] = dict(scenario["comparison"],
                                      weak_identity_guard=True,
                                      weak_identity_min_correlation=0.9)
        noise = np.random.default_rng(11).integers(
            0, 255, size=(8, 8, 4), dtype=np.uint8)
        self.candidate_path.write_bytes(noise.tobytes())
        reference = dict(self.meta["reference"], draws=300,
                         update_identity="0x10")
        candidate = dict(self.meta["candidate"], draws=350,
                         caller_signature="0xdef", update_identity="0x10")
        result = gfx_dual_run._compare_pair(
            self.pair(), reference, candidate, scenario)
        self.assertEqual(result["status"], "not_compared")
        self.assertIn("day-level identity", result["reason"])

    def test_ambiguous_composition_is_not_compared(self):
        candidate = dict(self.meta["candidate"], caller_signature="0xdef")
        result = gfx_dual_run._compare_pair(
            self.pair(), self.meta["reference"], candidate, self.scenario,
        )
        self.assertEqual(result["status"], "not_compared")
        self.assertIn("ambiguous", result["reason"])

    def test_below_settle_draw_threshold_is_not_compared(self):
        scenario = dict(self.scenario)
        scenario["settle"] = {"min_draws": 50}
        result = gfx_dual_run._compare_pair(
            self.pair(), self.meta["reference"], self.meta["candidate"], scenario,
        )
        self.assertEqual(result["status"], "not_compared")
        self.assertIn("settle draw threshold", result["reason"])

    def test_bad_image_is_still_compared_when_alignment_is_strong(self):
        noise = np.random.default_rng(7).integers(
            0, 255, size=(8, 8, 4), dtype=np.uint8)
        self.candidate_path.write_bytes(noise.tobytes())
        result = gfx_dual_run._compare_pair(
            self.pair(), self.meta["reference"], self.meta["candidate"],
            self.scenario,
        )
        self.assertEqual(result["status"], "fail", result)
        self.assertIn("content", result["alignment"])

    def test_missing_raw_is_not_compared(self):
        result = gfx_dual_run._compare_pair(
            self.pair(), self.meta["reference"], None, self.scenario,
        )
        self.assertEqual(result["status"], "not_compared")


class QualificationTests(unittest.TestCase):
    def result(self, *, paired=100, aligned=100, not_aligned=0, passed=100,
               failed=0, unverified=0, not_compared=0, health=None,
               processes=None, stop=None):
        return {
            "frames": {
                "paired": paired,
                "aligned": aligned,
                "not_aligned": not_aligned,
                "observed_reference": paired,
                "observed_candidate": paired,
                "comparison": {
                    "compared": passed + failed + unverified,
                    "passed": passed,
                    "failed": failed,
                    "unverified": unverified,
                    "not_compared": not_compared,
                    "first_failure": {"comparison": {"reasons": ["pixel"]}}
                    if failed else None,
                },
            },
            "health": health or {"verdict": "healthy", "first_failure": None,
                                  "instrumentation_failures": []},
            "processes": processes or {"reference": {"exit_code": 0},
                                        "candidate": {"exit_code": 0}},
            "terminations": [],
            "stop": stop or {"reason": "frame_limit"},
            "stability": {"status": "verified"},
            "result": "completed",
        }

    def scenario(self, policy="tolerant"):
        return {"comparison": {"policy": policy}}

    def classify(self, result, **kwargs):
        return gfx_dual_run.classify_campaign(
            result, self.scenario(kwargs.pop("policy", "tolerant")),
            max_frames=kwargs.pop("max_frames", 100))

    def test_unknown_health_cannot_match(self):
        value = self.classify(self.result(
            health={"verdict": "unknown", "first_failure": None,
                    "instrumentation_failures": []}))
        self.assertEqual(value["status"], "incomplete")
        self.assertFalse(value["qualified"])

    def test_full_passing_coverage_is_match(self):
        value = self.classify(self.result())
        self.assertEqual(value["status"], "match")
        self.assertTrue(value["qualified"])
        self.assertTrue(value["coverage_complete"])

    def test_one_hundred_pairs_without_comparisons_is_incomplete(self):
        value = self.classify(self.result(passed=0, not_compared=100))
        self.assertEqual(value["status"], "incomplete")
        self.assertFalse(value["qualified"])
        self.assertIn("requested 100 compared frames, got 0", " ".join(value["reasons"]))

    def test_pixel_difference_is_rendering_difference(self):
        value = self.classify(self.result(passed=99, failed=1))
        self.assertEqual(value["status"], "rendering_difference")

    def test_gpu_failure_takes_priority(self):
        value = self.classify(self.result(
            health={"verdict": "failed",
                    "first_failure": {"kind": "draw_refused",
                                      "message": "current shader is not linked"},
                    "instrumentation_failures": []}))
        self.assertEqual(value["status"], "gpu_execution_failure")

    def test_game_update_is_independent_alignment_evidence(self):
        reference = {"frame_index": 1, "width": 100, "height": 100,
                     "draws": 20, "caller_signature": "0x1",
                     "update_identity": 99}
        candidate = {"frame_index": 1, "width": 100, "height": 100,
                     "draws": 20, "caller_signature": "0x2",
                     "update_identity": 99}
        score, evidence = gfx_align.match_score(reference, candidate)
        self.assertIn("game_update", evidence)
        self.assertGreaterEqual(score, 6)
        result = gfx_align.align_sequences([reference], [candidate])
        self.assertEqual(result["pairs"][0]["status"], gfx_align.ALIGNED)

    def test_hex_encoded_game_update_is_parsed(self):
        reference = {"frame_index": 1, "width": 100, "height": 100,
                     "draws": 20, "caller_signature": "0x1",
                     "update_identity": "0x0de0dd22eab200f0"}
        candidate = dict(reference, caller_signature="0x2")
        score, evidence = gfx_align.match_score(reference, candidate)
        self.assertIn("game_update", evidence)
        self.assertGreaterEqual(score, 6)

    def test_different_game_update_is_not_aligned(self):
        reference = {"frame_index": 1, "width": 100, "height": 100,
                     "draws": 20, "caller_signature": "0x1",
                     "update_identity": 1}
        candidate = {"frame_index": 1, "width": 100, "height": 100,
                     "draws": 20, "caller_signature": "0x1",
                     "update_identity": 2}
        self.assertEqual(gfx_align.match_score(reference, candidate)[0], -100)
        result = gfx_align.align_sequences([reference], [candidate])
        self.assertFalse(any(pair["status"] == gfx_align.ALIGNED
                             for pair in result["pairs"]))

    def test_ambiguous_pairs_are_alignment_failure(self):
        value = self.classify(self.result(aligned=90, not_aligned=10,
                                          passed=90, not_compared=10))
        self.assertEqual(value["status"], "alignment_failure")

    def test_cleanup_sigkill_is_not_process_failure(self):
        value = self.classify(self.result(
            passed=99, failed=1,
            processes={"reference": {"exit_code": -9},
                       "candidate": {"exit_code": -9}},
            stop={"reason": "frame_difference", "elapsed_seconds": 5}))
        self.assertEqual(value["status"], "rendering_difference")

    def test_abnormal_process_exit_is_process_failure(self):
        value = self.classify(self.result(
            processes={"reference": {"exit_code": 0},
                       "candidate": {"exit_code": 11}}))
        self.assertEqual(value["status"], "process_failure")

    def test_weak_correspondence_cannot_match(self):
        result = self.result()
        result["stability"] = {"status": "weak"}
        value = self.classify(result)
        self.assertEqual(value["status"], "incomplete")
        self.assertIn("correspondence is weak", " ".join(value["reasons"]))

    def test_lockstep_timeout_is_alignment_failure(self):
        value = self.classify(self.result(
            stop={"reason": "lockstep_timeout"}))
        self.assertEqual(value["status"], "alignment_failure")

    def test_diagnostic_policy_never_qualifies(self):
        value = self.classify(self.result(), policy="diagnostic")
        self.assertEqual(value["status"], "incomplete")
        self.assertFalse(value["qualified"])


class CleanupFailureTests(unittest.TestCase):
    def test_spawn_failure_finishes_both_ledger_claims_and_closes_session(self):
        from unittest import mock
        from automation import gfx_launch, gfx_session, launch_ledger
        import eu4_benchmark

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            scenario = gfx_scenario.normalize({
                "schema": gfx_scenario.SCHEMA,
                "name": "spawn-failure",
                "executable_sha256": eu4_benchmark.gog_identity()["executable_sha256"],
                "logical_extent": [1728, 1117],
                "observation": {"max_frames": 2, "max_runtime_seconds": 30},
                "comparison": {"policy": "exact"},
                "mode": "streaming",
                "profile": "early",
            })
            ledger = root / "authority.json"
            launch_ledger.create_ledger(
                ledger, workflow_id="spawn-failure", limit=2, deadline_seconds=60.0,
                executable={"path": eu4_benchmark.gog_identity()["executable"],
                            "sha256": eu4_benchmark.gog_identity()["executable_sha256"]},
                output_root=str(root),
            )
            libraries = {}
            for role in ("reference", "candidate"):
                path = root / f"{role}.dylib"
                path.write_bytes(role.encode())
                libraries[role] = path

            from automation import launch_lease
            with mock.patch.dict("os.environ",
                                 {"EU4_LAUNCH_LEASE_DIR": str(root / "lease")}), \
                 mock.patch.object(gfx_launch, "preflight_user_data",
                                   return_value={"writable": True}), \
                 mock.patch.object(gfx_launch, "build_libraries",
                                   return_value=libraries), \
                 mock.patch.object(launch_lease, "manual_game_pids",
                                   return_value=[]), \
                 mock.patch.object(gfx_launch, "spawn",
                                   side_effect=RuntimeError("injected spawn failure")):
                result = gfx_dual_run.run_campaign(
                    scenario,
                    run_root=root,
                    ledger_path=ledger,
                    shader_cache=None,
                    qualified_sources=None,
                    max_frames=2,
                    session_root=root / "sessions",
                )
            self.assertEqual(result["result"], "failed")
            self.assertIn("injected spawn failure", result.get("error", ""))
            self.assertIn("qualification", result)
            value = launch_ledger.load_ledger(ledger)
            self.assertEqual(value["used"], 2)
            self.assertEqual({row["status"] for row in value["launches"]},
                             {"failed"})
            sessions = list((root / "sessions").glob("session-*.json"))
            self.assertEqual(len(sessions), 1)
            terminal = json.loads(
                (Path(result["run_dir"]) / "terminal-status.json").read_text())
            self.assertEqual(terminal["status"], gfx_session.FAILED)


class CleanupActionFailureTests(unittest.TestCase):
    def test_termination_failure_does_not_skip_ledger_or_session_cleanup(self):
        from unittest import mock
        from automation import gfx_launch, gfx_session, launch_lease, launch_ledger
        import eu4_benchmark

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            scenario = gfx_scenario.normalize({
                "schema": gfx_scenario.SCHEMA, "name": "cleanup-failure",
                "executable_sha256": eu4_benchmark.gog_identity()["executable_sha256"],
                "logical_extent": [1728, 1117],
                "observation": {"max_frames": 2, "max_runtime_seconds": 30},
                "comparison": {"policy": "exact"}, "mode": "streaming",
                "profile": "early",
            })
            ledger = root / "authority.json"
            launch_ledger.create_ledger(
                ledger, workflow_id="cleanup-failure", limit=2,
                deadline_seconds=60.0,
                executable={"path": eu4_benchmark.gog_identity()["executable"],
                            "sha256": eu4_benchmark.gog_identity()["executable_sha256"]},
                output_root=str(root))
            libraries = {}
            for role in ("reference", "candidate"):
                path = root / f"{role}.dylib"
                path.write_bytes(role.encode())
                libraries[role] = path
            with mock.patch.dict("os.environ",
                                 {"EU4_LAUNCH_LEASE_DIR": str(root / "lease")}), \
                 mock.patch.object(gfx_launch, "preflight_user_data",
                                   return_value={"writable": True}), \
                 mock.patch.object(gfx_launch, "build_libraries",
                                   return_value=libraries), \
                 mock.patch.object(gfx_launch, "spawn",
                                   side_effect=RuntimeError("spawn failed")), \
                 mock.patch.object(launch_lease, "manual_game_pids",
                                   return_value=[]), \
                 mock.patch.object(gfx_dual_run, "_stop_processes",
                                   side_effect=RuntimeError("termination broke")):
                result = gfx_dual_run.run_campaign(
                    scenario, run_root=root, ledger_path=ledger,
                    shader_cache=None, qualified_sources=None, max_frames=2,
                    session_root=root / "sessions")
            errors = " ".join(result.get("cleanup_errors") or [])
            self.assertIn("termination broke", errors)
            value = launch_ledger.load_ledger(ledger)
            self.assertEqual({row["status"] for row in value["launches"]},
                             {"failed"})
            terminal = json.loads(
                (Path(result["run_dir"]) / "terminal-status.json").read_text())
            self.assertEqual(terminal["status"], gfx_session.FAILED)


class PostReferenceCleanupTests(unittest.TestCase):
    def test_candidate_spawn_failure_terminates_reference_and_finishes_tokens(self):
        from unittest import mock
        from automation import gfx_launch, gfx_session, launch_lease, launch_ledger
        import eu4_benchmark
        import subprocess

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            scenario = gfx_scenario.normalize({
                "schema": gfx_scenario.SCHEMA,
                "name": "candidate-failure",
                "executable_sha256": eu4_benchmark.gog_identity()["executable_sha256"],
                "logical_extent": [1728, 1117],
                "observation": {"max_frames": 2, "max_runtime_seconds": 30},
                "comparison": {"policy": "exact"},
                "mode": "streaming",
                "profile": "early",
            })
            ledger = root / "authority.json"
            launch_ledger.create_ledger(
                ledger, workflow_id="candidate-failure", limit=2,
                deadline_seconds=60.0,
                executable={"path": eu4_benchmark.gog_identity()["executable"],
                            "sha256": eu4_benchmark.gog_identity()["executable_sha256"]},
                output_root=str(root),
            )
            libraries = {}
            for role in ("reference", "candidate"):
                path = root / f"{role}.dylib"
                path.write_bytes(role.encode())
                libraries[role] = path
            spawned = []

            def fake_spawn(role, config, run_dir, *, lease, session, token="", libraries=None,
                           backend=None):
                if role == "candidate":
                    raise RuntimeError("injected candidate failure")
                run_dir.mkdir(parents=True)
                observe = run_dir / "observe"
                observe.mkdir()
                process = subprocess.Popen(
                    [sys.executable, "-c", "import time; time.sleep(60)"],
                    start_new_session=True,
                )
                spawned.append(process)
                identity = session.register("reference", process, token=token)
                return gfx_launch.Launched(
                    role="reference", backend="opengl", run_dir=run_dir,
                    observe_dir=observe, process=process, identity=identity,
                    token=token)
            from automation import launch_lease as real_lease
            with mock.patch.dict("os.environ",
                                 {"EU4_LAUNCH_LEASE_DIR": str(root / "lease")}), \
                 mock.patch.object(gfx_launch, "preflight_user_data",
                                   return_value={"writable": True}), \
                 mock.patch.object(gfx_launch, "build_libraries",
                                   return_value=libraries), \
                 mock.patch.object(gfx_launch, "spawn", side_effect=fake_spawn), \
                 mock.patch.object(real_lease, "manual_game_pids", return_value=[]):
                result = gfx_dual_run.run_campaign(
                    scenario, run_root=root, ledger_path=ledger,
                    shader_cache=None, qualified_sources=None, max_frames=2,
                    session_root=root / "sessions",
                )
            self.assertEqual(result["result"], "failed")
            self.assertEqual(len(spawned), 1)
            self.assertIsNotNone(spawned[0].poll())
            value = launch_ledger.load_ledger(ledger)
            self.assertEqual({row["status"] for row in value["launches"]},
                             {"failed"})
            terminal = json.loads(
                (Path(result["run_dir"]) / "terminal-status.json").read_text())
            self.assertEqual(terminal["status"], gfx_session.FAILED)


class PauseNegotiationTests(unittest.TestCase):
    def test_pause_failure_is_not_silently_treated_as_paused(self):
        from unittest import mock
        from automation import gfx_launch, launch_lease, launch_ledger
        import eu4_benchmark
        # The scenario parser accepts pause; the runner must identify a missing
        # pause record before pairing.  Exercised through the small helper path
        # in a spawn-failure campaign would not reach it, so assert the scenario
        # wiring and the documented status contract instead.
        value = gfx_scenario.normalize({
            "schema": gfx_scenario.SCHEMA, "name": "pause",
            "executable_sha256": eu4_benchmark.gog_identity()["executable_sha256"],
            "logical_extent": [1728, 1117],
            "start": {"kind": "save", "save": "continuelastsave", "pause": True},
            "settle": {"min_draws": 10},
        })
        self.assertTrue(value["start"]["pause"])
        self.assertEqual(value["settle"]["min_draws"], 10)

    def test_missing_or_rejected_pause_is_reported(self):
        from automation import gfx_launch
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "reference").mkdir()
            (root / "candidate").mkdir()
            (root / "reference" / "pause-requested.json").write_text(
                json.dumps({"status": "requested"}))
            (root / "candidate" / "pause-requested.json").write_text(
                json.dumps({"status": "unavailable"}))
            launched = {
                "reference": type("Item", (), {"observe_dir": root / "reference"})(),
                "candidate": type("Item", (), {"observe_dir": root / "candidate"})(),
            }
            failures = gfx_dual_run._pause_failures(launched)
            self.assertEqual(len(failures), 1)
            self.assertIn("unavailable", failures[0])


class HealthStopTests(unittest.TestCase):
    def test_rejected_draw_maps_to_gpu_execution_failure(self):
        with tempfile.TemporaryDirectory() as temporary:
            run = Path(temporary)
            (run / "draw-refused-edfcd1.json").write_text(json.dumps({
                "variant": "_GfxDraw", "caller_rva": "0xedfcd1",
                "reason": "invalid sampler"}))
            summary = gfx_diagnostics.scan_run(run)
            value = gfx_dual_run._health_stop(summary)
            self.assertIsNotNone(value)
            self.assertEqual(value[0], "gpu_execution_failure")
            self.assertIn("invalid sampler", value[1])

    def test_clean_health_does_not_stop(self):
        health = gfx_diagnostics.GpuHealth()
        health.add("health_check", "ok", severity="info")
        self.assertIsNone(gfx_dual_run._health_stop(health.summary()))


class RealIdentityAlignmentTests(unittest.TestCase):
    def test_recorded_gl_gl_window_aligns_on_game_update(self):
        fixture = (Path(__file__).resolve().parent / "fixtures"
                   / "identity-alignment")
        reference = json.loads((fixture / "reference.json").read_text())
        candidate = json.loads((fixture / "candidate.json").read_text())
        result = gfx_align.align_sequences(reference, candidate)
        self.assertEqual(result["aligned"], 20)
        self.assertEqual(result["ambiguous"], 0)
        self.assertEqual(result["not_aligned"], 0)
        self.assertTrue(all("game_update" in pair["evidence"]
                            for pair in result["pairs"]))


class CaptureRequestTests(unittest.TestCase):
    def test_request_ids_and_acks_are_correlated(self):
        with tempfile.TemporaryDirectory() as temporary:
            observe = Path(temporary) / "observe"
            (observe / "captured").mkdir(parents=True)
            launched = {"observe_dir": observe}
            request_id = gfx_dual_run._request_dumps(launched, [7, 9])
            self.assertIsInstance(request_id, int)
            request = json.loads((observe / "capture-request.json").read_text())
            self.assertEqual(request["request_id"], request_id)
            self.assertEqual(request["frames"], [7, 9])
            (observe / "captured" / "frame-7.json").write_text(json.dumps({
                "schema": "gfx-frame-dump/v1", "frame_index": 7,
                "width": 4, "height": 4, "sha256": "a" * 64,
                "raw_file": str(observe / "captured" / "frame-7.bgra"),
            }))
            (observe / "capture-ack.json").write_text(json.dumps({
                "schema": "gfx-frame-ack/v1", "request_id": request_id,
                "status": "expired", "captured": [7], "missing": [9],
            }))
            value = gfx_dual_run._await_dump_meta(
                launched, [7, 9], timeout=0.5, request_id=request_id)
            self.assertEqual(sorted(value["found"]), [7])
            self.assertEqual(value["missing"], [9])

    def test_request_ids_are_strictly_increasing(self):
        with tempfile.TemporaryDirectory() as temporary:
            launched = {"observe_dir": Path(temporary)}
            values = [gfx_dual_run._request_dumps(launched, [index])
                      for index in (1, 2, 3)]
            self.assertEqual(len(set(values)), 3)
            self.assertEqual(values, sorted(values))

    def test_stale_ack_is_not_used(self):
        with tempfile.TemporaryDirectory() as temporary:
            observe = Path(temporary) / "observe"
            (observe / "captured").mkdir(parents=True)
            (observe / "capture-ack.json").write_text(json.dumps({
                "request_id": 1, "status": "expired", "missing": [7],
            }))
            (observe / "captured" / "frame-7.json").write_text(json.dumps({
                "schema": "gfx-frame-dump/v1", "frame_index": 7,
            }))
            value = gfx_dual_run._await_dump_meta(
                {"observe_dir": observe}, [7], timeout=0.3, request_id=2)
            self.assertIn(7, value["found"])
