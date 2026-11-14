"""Scenario validation and dual-run stream alignment (PR 2)."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from automation import gfx_dual_run, gfx_launch, gfx_scenario  # noqa: E402

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
        self.assertEqual(value["start"]["pause"], True)
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

    def test_load_round_trip(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "scenario.json"
            path.write_text(json.dumps(scenario()))
            value = gfx_scenario.load(path)
            self.assertEqual(value["name"], "test")


def write_record(path: Path, index: int, *, sha: str, valid: bool = True,
                 preview: bytes | None = None) -> None:
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
        candidate = self.np.repeat(self.np.repeat(mirrored, 2, axis=0), 2, axis=1)
        self.candidate_path.write_bytes(candidate.tobytes())
        result = gfx_dual_run._compare_pair(
            self.pair(), self.meta["reference"], self.meta["candidate"],
            self.scenario,
        )
        self.assertEqual(result["status"], "fail", result)
        self.assertIn("all", result["mirrored_regions"])

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

    def test_missing_raw_is_not_compared(self):
        result = gfx_dual_run._compare_pair(
            self.pair(), self.meta["reference"], None, self.scenario,
        )
        self.assertEqual(result["status"], "not_compared")
