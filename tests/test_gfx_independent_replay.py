"""Independent replay producer identity and environment evidence.

The replay child receives an inherited environment with a fixed set of fault
switches scrubbed and requested faults injected.  The producer receipt must
describe that exact resolved environment, not the caller's ambient one.
"""

from __future__ import annotations

import json
import os
import struct
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from automation import gfx_independent_replay  # noqa: E402
from tests.test_m7_diagnosis import DiagnosisFixture  # noqa: E402


def _recipe(**kwargs):
    return gfx_independent_replay.replay_recipe(**kwargs)


class ReplayEnvironmentTests(unittest.TestCase):
    def test_resolve_scrubs_ambient_and_injects_explicit_faults(self):
        ambient = {
            "EU4_FRAME_REPLAY_DISABLE_RT_FLIP": "1",
            "EU4_FRAME_REPLAY_SAMPLER_SHIFT": "9",
            "EU4_FRAME_REPLAY_PRECISE_MATH": "1",
            "PATH": "/bin",
        }
        env = gfx_independent_replay.resolve_replay_environment(
            ambient=ambient,
            faults={"sampler_shift": 2,
                    "disable_render_target_vflip": True})
        self.assertEqual(env, {
            "EU4_FRAME_REPLAY_DISABLE_RT_FLIP": "1",
            "EU4_FRAME_REPLAY_SAMPLER_SHIFT": "2",
            "EU4_FRAME_REPLAY_PRECISE_MATH": "1",
            "PATH": "/bin",
        })

    def test_recipe_distinguishes_default_ambient_scrubbed_and_injected(self):
        canonical = _recipe(env={}, ambient={})
        self.assertFalse(canonical["diagnostic"])
        self.assertEqual(canonical["effective_env"], {})
        self.assertEqual(canonical["scrubbed_env"], [])

        inherited = _recipe(
            env={"EU4_FRAME_REPLAY_PRECISE_MATH": "1"},
            ambient={"EU4_FRAME_REPLAY_PRECISE_MATH": "1"})
        self.assertTrue(inherited["diagnostic"])
        self.assertEqual(inherited["inherited_env"],
                         ["EU4_FRAME_REPLAY_PRECISE_MATH"])
        self.assertNotEqual(canonical["sha256"], inherited["sha256"])

        scrubbed = _recipe(
            env={},
            ambient={"EU4_FRAME_REPLAY_DISABLE_RT_FLIP": "1",
                     "EU4_FRAME_REPLAY_SAMPLER_SHIFT": "3"})
        self.assertFalse(scrubbed["diagnostic"])
        self.assertEqual(scrubbed["scrubbed_env"],
                         ["EU4_FRAME_REPLAY_DISABLE_RT_FLIP",
                          "EU4_FRAME_REPLAY_SAMPLER_SHIFT"])
        # A scrubbed override did not affect execution, so it is not part of
        # the recipe identity.
        self.assertEqual(scrubbed["sha256"], canonical["sha256"])

        injected = _recipe(
            env={"EU4_FRAME_REPLAY_SAMPLER_SHIFT": "2"},
            ambient={},
            faults={"sampler_shift": 2})
        self.assertTrue(injected["diagnostic"])
        self.assertEqual(injected["injected_env"],
                         ["EU4_FRAME_REPLAY_SAMPLER_SHIFT"])
        self.assertEqual(injected["faults"], {"sampler_shift": 2})
        self.assertNotEqual(canonical["sha256"], injected["sha256"])

    def test_explicit_defaults_canonicalize_but_effective_values_do_not(self):
        canonical = _recipe(env={}, ambient={})
        explicit_default = _recipe(
            env={"EU4_FRAME_REPLAY_PRECISE_MATH": "0",
                 "EU4_FRAME_REPLAY_NO_CULL": "0",
                 "EU4_FRAME_REPLAY_DEBUG_DESCRIPTOR": "0"},
            ambient={})
        # DEBUG_DESCRIPTOR presence alone selects diagnostics even when its
        # value is "0"; the other explicit defaults are inert.
        self.assertTrue(explicit_default["diagnostic"])
        self.assertEqual(
            explicit_default["effective_env"],
            {"EU4_FRAME_REPLAY_DEBUG_DESCRIPTOR": "0"})
        self.assertNotEqual(canonical["sha256"],
                            explicit_default["sha256"])

        inert_only = _recipe(
            env={"EU4_FRAME_REPLAY_PRECISE_MATH": "0",
                 "EU4_FRAME_REPLAY_NO_CULL": "0"},
            ambient={})
        self.assertFalse(inert_only["diagnostic"])
        self.assertEqual(inert_only["sha256"], canonical["sha256"])

        effective = _recipe(
            env={"EU4_FRAME_REPLAY_PRECISE_MATH": "1"}, ambient={})
        self.assertTrue(effective["diagnostic"])
        self.assertNotEqual(canonical["sha256"], effective["sha256"])

    def test_sampler_fault_selectors_are_inert_without_a_shift(self):
        recipe = _recipe(
            env={"EU4_FRAME_REPLAY_FAULT_EVENT": "4",
                 "EU4_FRAME_REPLAY_FAULT_FRAME": "2"},
            ambient={})
        self.assertFalse(recipe["diagnostic"])
        self.assertEqual(recipe["effective_env"], {})


class ProducerReceiptEnvironmentTests(unittest.TestCase):
    def setUp(self):
        base = ROOT / ".local" / "test-runs"
        base.mkdir(parents=True, exist_ok=True)
        self.temporary = tempfile.TemporaryDirectory(dir=base)
        self.root = Path(self.temporary.name)
        self.fixture = DiagnosisFixture(self.root / "fixture")
        self.out = self.root / "out"

    def tearDown(self):
        self.temporary.cleanup()

    def test_receipt_environment_matches_the_child_environment(self):
        captured: dict = {}

        def fake_run(command, **kwargs):
            captured["env"] = dict(kwargs["env"])
            out = Path(command[command.index("--out") + 1])
            (out / "replay.json").write_text(json.dumps({
                "schema": "gfx-independent-replay/v1",
                "status": "ok",
                "checkpoints": [],
                "frame": {"raw_file": "metal-frame.bgra"},
            }))
            (out / "metal-frame.bgra").write_bytes(b"\0" * 64)
            return subprocess.CompletedProcess(command, 0, "", "")

        producer = {
            "executable": "/tmp/fake-replay",
            "executable_sha256": "a" * 64,
            "build_fingerprint": "b" * 64,
            "build_stamp_sha256": "c" * 64,
            "source_revision": "d" * 40,
        }
        ambient = {
            "EU4_FRAME_REPLAY_PRECISE_MATH": "1",
            "EU4_FRAME_REPLAY_DISABLE_RT_FLIP": "1",
            "EU4_FRAME_REPLAY_SAMPLER_SHIFT": "9",
            "PATH": os.environ.get("PATH", "/bin"),
        }
        faults = {"sampler_shift": 2, "disable_render_target_vflip": True}
        diagnosis = {
            "evaluation": {}, "checkpoints": [],
            "checkpoint_validation": {}, "binding_audit": {},
            "state_coverage": {}, "pixels": {"final": {}},
            "execution": {"status": "ok"}, "graph": {},
            "classification": "diagnostic", "qualification": {},
            "first_divergence": None,
        }
        with mock.patch.dict(os.environ, ambient, clear=True), \
                mock.patch.object(
                    gfx_independent_replay.m6_frame, "build_replay_host",
                    return_value=Path("/tmp/fake-replay")), \
                mock.patch.object(
                    gfx_independent_replay.m6_frame, "snapshot_replay_host",
                    return_value=(Path("/tmp/fake-replay"), producer)), \
                mock.patch.object(
                    gfx_independent_replay, "analyse_frame",
                    return_value=diagnosis), \
                mock.patch.object(
                    gfx_independent_replay.subprocess, "run",
                    side_effect=fake_run):
            gfx_independent_replay._replay_locked(
                self.fixture.frame, self.out, faults=faults)

        receipt = json.loads(
            (self.out / "producer-receipt.json").read_text())
        recipe = receipt["recipe"]
        expected = gfx_independent_replay.resolve_replay_environment(
            ambient=ambient, faults=faults)
        self.assertEqual(captured["env"], expected)
        self.assertEqual(set(recipe["consumed_env"]),
                         {key for key in expected
                          if key.startswith("EU4_FRAME_REPLAY_")})
        for key, value in recipe["consumed_env"].items():
            self.assertEqual(captured["env"][key], value)
        self.assertEqual(recipe["injected_env"],
                         ["EU4_FRAME_REPLAY_DISABLE_RT_FLIP",
                          "EU4_FRAME_REPLAY_SAMPLER_SHIFT"])
        self.assertEqual(recipe["inherited_env"],
                         ["EU4_FRAME_REPLAY_PRECISE_MATH"])
        self.assertEqual(recipe["scrubbed_env"],
                         ["EU4_FRAME_REPLAY_DISABLE_RT_FLIP",
                          "EU4_FRAME_REPLAY_SAMPLER_SHIFT"])
        self.assertTrue(recipe["diagnostic"])
        self.assertEqual(recipe["faults"], faults)

        alternative = gfx_independent_replay.replay_recipe(
            env=dict(captured["env"],
                     **{"EU4_FRAME_REPLAY_PRECISE_MATH": "0"}),
            ambient=ambient, faults={"sampler_shift": 3},
            shaders=self.fixture.shaders)
        self.assertNotEqual(recipe["sha256"], alternative["sha256"])


if __name__ == "__main__":
    unittest.main()

class R32FComparisonTests(unittest.TestCase):
    def test_negative_float_ulp_distance_is_ordered(self):
        reference = struct.pack("<f", -1.0) * 4
        neighbour = struct.pack("<I", 0xBF7FFFFF) * 4
        result = gfx_independent_replay.compare_r32f(
            reference, neighbour, 2, 2)
        self.assertFalse(result["exact_bitwise"])
        self.assertEqual(result["mismatched_pixels"], 4)
        self.assertEqual(result["max_ulp_error"], 1)

    def test_r32f_is_compared_as_exact_float_bits(self):
        reference = struct.pack("<f", 1.0) * 4
        identical = gfx_independent_replay.compare_r32f(
            reference, reference, 2, 2)
        self.assertEqual(identical["format_scope"], "R32F")
        self.assertEqual(identical["mismatched_pixels"], 0)
        self.assertTrue(identical["exact_bitwise"])
        self.assertEqual(identical["max_channel_error"], 0)
        changed = bytearray(reference)
        changed[0] ^= 1
        different = gfx_independent_replay.compare_r32f(
            reference, bytes(changed), 2, 2)
        self.assertEqual(different["mismatched_pixels"], 1)
        self.assertFalse(different["exact_bitwise"])
        self.assertGreater(different["max_ulp_error"], 0)
        self.assertEqual(different["max_channel_error"], 0)
        self.assertEqual(different["alpha_different_pixels"], 0)
