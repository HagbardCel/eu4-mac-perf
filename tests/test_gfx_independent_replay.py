"""Independent replay producer identity and environment evidence.

The replay child receives an inherited environment with a fixed set of fault
switches scrubbed and requested faults injected.  The producer receipt must
describe that exact resolved environment, not the caller's ambient one.
"""

from __future__ import annotations

import contextlib
import io
import json
import os
import shutil
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

from automation import agent_tools, gfx_independent_replay, gfx_prefix_check  # noqa: E402
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


class TrustedReferenceTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.fixture = DiagnosisFixture(self.root / "original")
        self.trusted = self.root / "trusted"
        self.record = self.trusted / "trusted-evidence.json"
        self.frame = self.trusted / "frame"
        gfx_prefix_check.prepare_trusted_evidence(
            self.fixture.frame, self.trusted, read_only=False)

    def analyse(self, frame):
        return gfx_independent_replay.analyse_frame(frame, self.fixture.metal)

    def change_record(self, **changes):
        value = json.loads(self.record.read_text())
        self.record.write_text(json.dumps(dict(value, **changes)))

    def test_original_and_verified_copy_keep_the_same_capture_authority(self):
        original = self.analyse(self.fixture.frame)
        verify = gfx_prefix_check.verify_trusted_evidence
        with mock.patch.object(gfx_prefix_check, "verify_trusted_evidence",
                               wraps=verify) as checked:
            copied = self.analyse(self.frame)
        checked.assert_called_once_with(self.trusted.resolve(), self.frame.resolve())
        self.assertEqual(original["classification"], "qualified_match")
        self.assertEqual(copied["classification"], original["classification"])
        self.assertEqual(copied["capture"]["identity"], original["capture"]["identity"])
        self.assertEqual(copied["graph"]["capture"]["source"], "seal")

    def test_invalid_trusted_reference_rejected_by_diagnosis_and_replay(self):
        original_record = self.record.read_bytes()
        blob = next((self.frame / "blobs").iterdir())
        payload = blob.read_bytes()
        for scenario, reason in (
            ("tampered", "file manifest changed"),
            ("missing", "capture directory does not match"),
            ("other_frame", "not the trusted-evidence frame"),
            ("wrong_original", "does not name the sealed capture directory"),
            ("malformed", "unreadable JSON"),
        ):
            with self.subTest(scenario=scenario):
                self.record.write_bytes(original_record)
                blob.write_bytes(payload)
                if scenario == "tampered":
                    blob.write_bytes(payload + b"invalid")
                elif scenario == "missing":
                    self.record.unlink()
                elif scenario == "other_frame":
                    self.change_record(frame_path=str(self.fixture.frame))
                elif scenario == "wrong_original":
                    self.change_record(original_capture_dir=str(self.root))
                else:
                    self.record.write_text("broken")
                diagnosis = self.analyse(self.frame)
                self.assertEqual(diagnosis["classification"], "invalid_provenance")
                self.assertIn(reason, diagnosis["capture"]["error"])
                with mock.patch.object(gfx_independent_replay.m6_frame,
                                       "build_replay_host") as build_host:
                    replay = gfx_independent_replay.replay(
                        self.frame, self.root / scenario, shaders=self.fixture.shaders)
                build_host.assert_not_called()
                self.assertEqual(replay["classification"], diagnosis["classification"])
                self.assertFalse(replay["qualification"]["qualified"])
                self.assertFalse((self.root / scenario / "metal-frame.bgra").exists())

    def test_unsealed_fixture_keeps_its_classification(self):
        (self.fixture.frame / "frame-seal.json").unlink()
        (self.fixture.metal / "shadow-report.json").unlink()
        diagnosis = self.analyse(self.fixture.frame)
        self.assertEqual(diagnosis["identity"]["status"], "unsealed")
        self.assertEqual(diagnosis["classification"], "incomplete_diagnostics")

    def test_independent_replay_verifies_once_and_rejects_a_nonzero_host_exit(self):
        verify = gfx_prefix_check.verify_trusted_evidence

        def fake_run(command, **kwargs):
            out = Path(command[command.index("--out") + 1])
            for artifact in self.fixture.metal.iterdir():
                if artifact.name != "checkpoint-request.json":
                    shutil.copyfile(artifact, out / artifact.name)
            return subprocess.CompletedProcess(command, 1, "", "host failed")

        out = self.root / "failed"
        host = self.root / "replay-host"
        replay = gfx_independent_replay
        with mock.patch.object(gfx_prefix_check, "verify_trusted_evidence",
                               wraps=verify) as checked, \
                mock.patch.object(replay.m6_frame, "build_replay_host"), \
                mock.patch.object(replay.m6_frame, "snapshot_replay_host",
                                  return_value=(host, {})), \
                mock.patch.object(replay.subprocess, "run", side_effect=fake_run):
            report = replay.replay(self.frame, out, shaders=self.fixture.shaders)
        checked.assert_called_once()
        self.assertTrue((out / "metal-frame.bgra").is_file())
        self.assertEqual(report["diagnosis"]["capture"]["validity"], "valid")
        self.assertEqual(report["classification"], "gpu_execution_failure")
        self.assertFalse(report["qualification"]["qualified"])
        self.assertEqual(replay.qualification_exit_code(report), 1)

    def test_completed_difference_agrees_in_core_cli_and_tool_envelope(self):
        self.fixture.alter_candidate()
        report = self.analyse(self.frame)
        core = gfx_independent_replay.classification_outcome(report)
        args = ["--frame", str(self.frame), "--metal", str(self.fixture.metal)]
        cli = io.StringIO()
        with contextlib.redirect_stdout(cli):
            cli_code = gfx_independent_replay.diagnose_main(
                [*args, "--out", str(self.root / "cli")])
        tool = io.StringIO()
        with contextlib.redirect_stdout(tool):
            tool_code = agent_tools.main([
                "eu4_replay", "diagnose", *args, "--out", str(self.root / "tool")])
        self.assertEqual(cli_code, 2)
        self.assertEqual(tool_code, 0)
        expected = {"execution": "ok", "diagnostic": "complete", "evidence": "valid",
                    "verdict": "different", "qualified": False}
        for outcome in (core, json.loads(cli.getvalue())["outcome"],
                        json.loads(tool.getvalue())["outcome"]):
            self.assertEqual({key: outcome[key] for key in expected}, expected)

class R32FComparisonTests(unittest.TestCase):
    def test_negative_float_ulp_distance_is_ordered(self):
        reference = struct.pack("<f", -1.0) * 4
        neighbour = struct.pack("<I", 0xBF7FFFFF) * 4
        result = gfx_independent_replay.compare_r32f(
            reference, neighbour, 2, 2)
        self.assertFalse(result["exact_bitwise"])
        self.assertEqual(result["mismatched_pixels"], 4)
        self.assertEqual(result["max_ulp_error"], 1)

    def test_r32f_bounds_cover_every_mismatch(self):
        reference = struct.pack("<f", 0.0) * 3
        candidate = (struct.pack("<f", 1.0) + struct.pack("<f", 0.0)
                     + struct.pack("<f", 1.0))
        result = gfx_independent_replay.compare_r32f(
            reference, candidate, 3, 1)
        self.assertEqual(result["mismatched_pixels"], 2)
        self.assertEqual(result["first_differing_pixel"]["x"], 0)
        self.assertEqual(result["differing_bounds"],
                         {"x": 0, "y": 0, "width": 3, "height": 1})

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
