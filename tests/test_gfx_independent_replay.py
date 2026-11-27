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

from automation import (agent_tools, campaign_guard, gfx_independent_replay,  # noqa: E402
                        gfx_prefix_check)
from tests.test_gfx_diagnosis import DiagnosisFixture  # noqa: E402


def _recipe(**kwargs):
    return gfx_independent_replay.replay_recipe(**kwargs)


class ReplayEnvironmentTests(unittest.TestCase):
    def test_resolve_scrubs_ambient_and_injects_explicit_faults(self):
        ambient = {
            "EU4_FRAME_REPLAY_DISABLE_RT_FLIP": "1",
            "EU4_FRAME_REPLAY_SAMPLER_SHIFT": "9",
            "EU4_FRAME_REPLAY_FAST_MATH": "0",
            "PATH": "/bin",
        }
        env = gfx_independent_replay.resolve_replay_environment(
            ambient=ambient,
            faults={"sampler_shift": 2,
                    "disable_render_target_vflip": True})
        self.assertEqual(env, {
            "EU4_FRAME_REPLAY_DISABLE_RT_FLIP": "1",
            "EU4_FRAME_REPLAY_SAMPLER_SHIFT": "2",
            "EU4_FRAME_REPLAY_FAST_MATH": "0",
            "PATH": "/bin",
        })

    def test_recipe_distinguishes_default_ambient_scrubbed_and_injected(self):
        canonical = _recipe(env={}, ambient={})
        self.assertFalse(canonical["diagnostic"])
        self.assertEqual(canonical["effective_env"], {})
        self.assertEqual(canonical["scrubbed_env"], [])

        inherited = _recipe(
            env={"EU4_FRAME_REPLAY_FAST_MATH": "0"},
            ambient={"EU4_FRAME_REPLAY_FAST_MATH": "0"})
        self.assertTrue(inherited["diagnostic"])
        self.assertEqual(inherited["inherited_env"],
                         ["EU4_FRAME_REPLAY_FAST_MATH"])
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
            env={"EU4_FRAME_REPLAY_FAST_MATH": "1",
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
            env={"EU4_FRAME_REPLAY_FAST_MATH": "1",
                 "EU4_FRAME_REPLAY_NO_CULL": "0"},
            ambient={})
        self.assertFalse(inert_only["diagnostic"])
        self.assertEqual(inert_only["sha256"], canonical["sha256"])

        effective = _recipe(
            env={"EU4_FRAME_REPLAY_FAST_MATH": "0"}, ambient={})
        self.assertTrue(effective["diagnostic"])
        self.assertNotEqual(canonical["sha256"], effective["sha256"])

    def test_renderer_default_and_effective_math_are_bound_to_recipe(self):
        precise = _recipe(env={}, default_fast_math=False)
        explicit_precise = _recipe(
            env={"EU4_FRAME_REPLAY_FAST_MATH": "0"}, default_fast_math=False)
        ab_fast = _recipe(
            env={"EU4_FRAME_REPLAY_FAST_MATH": "1"}, default_fast_math=False)
        self.assertEqual(precise["sha256"], explicit_precise["sha256"])
        self.assertEqual(explicit_precise["consumed_env"],
                         {"EU4_FRAME_REPLAY_FAST_MATH": "0"})
        self.assertFalse(precise["diagnostic"])
        self.assertNotEqual(precise["sha256"], _recipe(env={})["sha256"])
        self.assertNotEqual(precise["sha256"], ab_fast["sha256"])
        self.assertTrue(ab_fast["diagnostic"])
        self.assertEqual(ab_fast["shader_compile_options"],
                         {"default_fast_math": False, "fast_math": True})

    def test_invalid_and_retired_overrides_fail_closed(self):
        for env in (
                {"EU4_FRAME_REPLAY_FAST_MATH": ""},
                {"EU4_FRAME_REPLAY_FAST_MATH": "01"},
                {"EU4_FRAME_REPLAY_FAST_MATH": "true"},
                {"EU4_FRAME_REPLAY_PRECISE_MATH": "1"},
                {"EU4_FRAME_REPLAY_PRECISE_MATH": "1",
                 "EU4_FRAME_REPLAY_FAST_MATH": "1"}):
            with self.subTest(env=env):
                with self.assertRaisesRegex(gfx_independent_replay.ReplayError,
                                            "invalid_shader_compile_options"):
                    gfx_independent_replay.resolve_replay_environment(ambient=env)
                with self.assertRaisesRegex(gfx_independent_replay.ReplayError,
                                            "invalid_shader_compile_options"):
                    _recipe(env=env)

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
                "shader_compile_options": {"default_fast_math": False,
                                           "fast_math": True},
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
            "EU4_FRAME_REPLAY_FAST_MATH": "1",
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
                mock.patch.object(campaign_guard, "require_active_from_env"), \
                mock.patch.object(
                    gfx_independent_replay.gfx_frame, "build_replay_host",
                    return_value=Path("/tmp/fake-replay")), \
                mock.patch.object(
                    gfx_independent_replay.gfx_frame, "snapshot_replay_host",
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
                         ["EU4_FRAME_REPLAY_FAST_MATH"])
        self.assertEqual(recipe["scrubbed_env"],
                         ["EU4_FRAME_REPLAY_DISABLE_RT_FLIP",
                          "EU4_FRAME_REPLAY_SAMPLER_SHIFT"])
        self.assertTrue(recipe["diagnostic"])
        self.assertEqual(recipe["faults"], faults)

        self.assertEqual(recipe["shader_compile_options"],
                         {"default_fast_math": False, "fast_math": True})
        canonical_env = dict(captured["env"], EU4_FRAME_REPLAY_FAST_MATH="0")
        result_path = self.out / "replay.json"
        with self.assertRaisesRegex(gfx_independent_replay.ReplayError,
                                    "disagrees with child environment"):
            gfx_independent_replay.write_producer_receipt(
                self.out, self.fixture.frame, producer, result_path,
                env=canonical_env, faults=faults, shaders=self.fixture.shaders)
        result = json.loads(result_path.read_text())
        result["shader_compile_options"]["fast_math"] = False
        result_path.write_text(json.dumps(result))
        canonical = gfx_independent_replay.write_producer_receipt(
            self.out, self.fixture.frame, producer, result_path,
            env=canonical_env, faults=faults, shaders=self.fixture.shaders)
        self.assertNotEqual(recipe["sha256"], canonical["recipe"]["sha256"])
        self.assertNotEqual(receipt["result"]["replay_json_sha256"],
                            canonical["result"]["replay_json_sha256"])


class TrustedReferenceTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.fixture = DiagnosisFixture(self.root / "original")
        self.enterContext(mock.patch.object(campaign_guard, "require_active_from_env"))
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
                with mock.patch.object(gfx_independent_replay.gfx_frame,
                                       "build_replay_host") as build_host:
                    replay = gfx_independent_replay.replay(
                        self.frame, self.root / scenario)
                build_host.assert_not_called()
                self.assertEqual(replay["diagnosis"]["classification"], diagnosis["classification"])
                self.assertFalse(replay["diagnosis"]["qualification"]["qualified"])
                self.assertFalse((self.root / scenario / "metal-frame.bgra").exists())

    def test_unsealed_fixture_keeps_its_classification(self):
        (self.fixture.frame / "frame-seal.json").unlink()
        (self.fixture.metal / "shadow-report.json").unlink()
        diagnosis = self.analyse(self.fixture.frame)
        self.assertEqual(diagnosis["identity"]["status"], "unsealed")
        self.assertEqual(diagnosis["classification"], "incomplete_diagnostics")

    def test_standalone_replay_keeps_legacy_outcome_and_reports_diagnostic(self):
        verify = gfx_prefix_check.verify_trusted_evidence
        replay = gfx_independent_replay
        host = self.root / "replay-host"
        rationale = json.dumps({
            "question": "Do replay CLI statuses retain their meaning?",
            "evidence": [str(self.record)],
            "gap": "CLI replay presentation was changed",
            "hypothesis": "The legacy and diagnostic outcomes stay independent",
            "action": "Run replay with fixture host output",
            "expected_information": "Legacy exit and both outcome fields",
            "cheaper_alternatives_checked": True,
        })

        def fake_run(command, **kwargs):
            output = Path(command[command.index("--out") + 1])
            if scenario != "missing_output":
                for artifact in self.fixture.metal.iterdir():
                    if artifact.name != "checkpoint-request.json":
                        shutil.copyfile(artifact, output / artifact.name)
                result_path = output / "replay.json"
                result = json.loads(result_path.read_text())
                result["shader_compile_options"] = {
                    "default_fast_math": True, "fast_math": True,
                }
                result_path.write_text(json.dumps(result))
                if scenario == "invalid_receipt":
                    (output / result["checkpoints"][0]["file"]).unlink()
            return subprocess.CompletedProcess(command, int(scenario == "host_failed"))

        for scenario, exit_code, status, execution, diagnostic, classification in (
            ("match", 0, "qualified", "ok", "complete", "qualified_match"),
            ("difference", 2, "confirmed_rendering_difference", "ok", "complete",
             "qualified_difference"),
            ("host_failed", 1, "invalid", "failed", "failed", "gpu_execution_failure"),
            ("missing_output", 1, "invalid", "failed", "failed", "gpu_execution_failure"),
            ("invalid_receipt", 1, "invalid", "ok", "invalid", "invalid_provenance"),
            ("fault", 3, "partial", "ok", "partial", "fault_experiment"),
        ):
            with self.subTest(scenario=scenario):
                if scenario == "difference":
                    self.fixture.alter_candidate()
                else:
                    self.fixture.write_metal()
                out = self.root / scenario
                args = ["--frame", str(self.frame), "--out", str(out),
                        "--shaders", str(self.fixture.shaders), "--rationale", rationale]
                if scenario == "fault":
                    args.append("--disable-render-target-vflip")
                stdout = io.StringIO()
                with mock.patch.object(gfx_prefix_check, "verify_trusted_evidence",
                                       wraps=verify) as checked, \
                        mock.patch.object(replay.gfx_frame, "build_replay_host"), \
                        mock.patch.object(replay.gfx_frame, "snapshot_replay_host",
                                          return_value=(host, {})), \
                        mock.patch.object(replay.subprocess, "run", side_effect=fake_run), \
                        contextlib.redirect_stdout(stdout):
                    self.assertEqual(replay.main(args), exit_code)
                checked.assert_called_once()
                result = json.loads(stdout.getvalue())
                report = json.loads((out / "replay-report.json").read_text())
                self.assertEqual(result["outcome"], report["outcome"])
                self.assertEqual(result["outcome"]["execution"], execution)
                self.assertEqual(result["status"], status)
                self.assertEqual(result["classification"], classification)
                self.assertIs(result["qualified"], scenario == "match")
                canonical = result["diagnostic_outcome"]
                self.assertEqual(canonical["execution"], "ok")
                self.assertEqual(canonical["diagnostic"], diagnostic)
                self.assertEqual(canonical["axes"]["capture_validity"], "valid")
                self.assertEqual(canonical["axes"]["gpu_execution"], execution)
                if scenario == "host_failed":
                    self.assertTrue((out / "metal-frame.bgra").is_file())
                if scenario == "invalid_receipt":
                    self.assertIn("invalid_checkpoint_binding", report["receipt_error"])
                    self.assertFalse((out / "producer-receipt.json").exists())

    def test_minimal_diagnoses_share_the_qualification_classification(self):
        for classification, validity in (
            ("invalid_provenance", "invalid"),
            ("incomplete_capture", "incomplete"),
            ("incomplete_diagnostics", "valid"),
            ("gpu_execution_failure", "valid"),
        ):
            with self.subTest(classification=classification):
                report = gfx_independent_replay._minimal_diagnosis(
                    self.frame, self.fixture.metal, message="original reason",
                    classification=classification)
                self.assertEqual(report["classification"], classification)
                self.assertEqual(report["qualification"]["classification"], classification)
                self.assertEqual(report["capture"], {"validity": validity,
                                                     "error": "original reason"})
                self.assertEqual(report["first_divergence"]["reason"], "original reason")

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
        cli_result = json.loads(cli.getvalue())
        self.assertEqual(cli_result["outcome"], cli_result["diagnostic_outcome"])
        for outcome in (core, cli_result["diagnostic_outcome"],
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


if __name__ == "__main__":
    unittest.main()
