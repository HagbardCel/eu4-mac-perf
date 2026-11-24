"""Focused tests for the opt-in event-1 uv2 diagnostic probe.

Covers output layout, event selection, readback correctness, state
restoration, capture/replay binding, full draw-parameter validation, the
inconclusive zero-coverage rule and the numerical report.  No EU4 launch.
"""
from __future__ import annotations

import hashlib
import argparse
import io
import json
import os
import struct
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
BENCH = ROOT / "benchmark"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from automation import (gfx_independent_replay, gfx_uv2_probe, m6_frame,  # noqa: E402
                        native_build)

WIDTH = HEIGHT = 4
VERTEX_SHA = "v" * 64
FRAGMENT_SHA = "f" * 64
UNIFORM_SHA = "u" * 64
ATTRIBUTE_SHA = "a" * 64
INDEX_SHA = "i" * 64


def _values(covered: bool = True):
    return [[[(x + 0.5) / WIDTH, (y + 0.5) / HEIGHT, 0.0,
              1.0 if covered else 0.0] for x in range(WIDTH)]
            for y in range(HEIGHT)]


def _raw(values) -> bytes:
    body = bytearray()
    for row in values:
        for pixel in row:
            body.extend(struct.pack("<4f", *pixel))
    return bytes(body)


def _write_probe(root: Path, side: str, *, values=None, event: int = 1,
                 program: int = 310, framebuffer: int = 6, texture: int = 433,
                 control: bytes | None = None, sealed: bool = True,
                 session: str = "session", frame: int = 1,
                 capture: dict | None = None, normal_target: dict | None = None,
                 draw: dict | None = None, pid: int = 4242) -> dict:
    root.mkdir(parents=True, exist_ok=True)
    raw = _raw(values if values is not None else _values())
    (root / "uv2-probe.rgba32f").write_bytes(raw)
    descriptor = {
        "schema": "gfx-uv2-probe/v1", "side": side, "status": "ok",
        "sealed": sealed, "session_id": session, "capture_frame_id": frame,
        "pid": pid,
        "event": event, "program": program,
        "target": {"framebuffer": framebuffer, "texture": texture,
                   "width": WIDTH, "height": HEIGHT},
        "varying": "v_TEXCOORD1", "origin": "lower_left",
        "pixel_format": "RGBA32F", "width": WIDTH, "height": HEIGHT,
        "row_stride": WIDTH * 16, "raw_file": "uv2-probe.rgba32f",
        "raw_bytes": len(raw), "raw_sha256": hashlib.sha256(raw).hexdigest(),
        "sources": {"vertex_sha256": VERTEX_SHA,
                    "fragment_sha256": FRAGMENT_SHA},
        "attributes": [{"name": "in_POSITION", "location": 0,
                        "sha256": ATTRIBUTE_SHA, "digest_ok": True}],
        "uniforms": [{"name": "UNIFORM0[0]", "sha256": UNIFORM_SHA},
                     {"name": "tex0", "kind": "sampler", "unit": 0}],
        "indices": {"count": 6, "sha256": INDEX_SHA},
        "draw": draw or {"entry": "_GfxDrawIndexed", "mode": 4, "count": 6,
                         "first": 0, "instances": 1, "base_vertex": 0,
                         "indexed": True, "caller_rva": "0x10d4e50"},
        "control": {"file": "uv2-control.bgra", "pixel_format": "BGRA8"},
    }
    if side == "metal":
        descriptor["capture"] = capture or {
            "sealed": True, "session_id": session, "capture_frame_id": frame,
            "manifest_sha256": "m" * 64, "reference_sha256": "r" * 64}
        descriptor.pop("session_id", None)
        descriptor.pop("capture_frame_id", None)
        descriptor.pop("sealed", None)
        if normal_target is not None:
            descriptor["normal_target"] = normal_target
    if control is not None:
        (root / "uv2-control.bgra").write_bytes(control)
        descriptor["control"]["sha256"] = hashlib.sha256(control).hexdigest()
    (root / "uv2-probe.json").write_text(json.dumps(descriptor, indent=2))
    return descriptor


def _event(draw: dict | None = None) -> dict:
    return {
        "kind": "draw", "index": 1,
        "program": {"name": 310,
                    "shaders": [{"stage": "vertex", "sha256": VERTEX_SHA},
                                {"stage": "pixel", "sha256": FRAGMENT_SHA}],
                    "uniforms": [
                        {"name": "UNIFORM0[0]", "type": "float",
                         "sha256": UNIFORM_SHA},
                        {"name": "tex0", "type": "sampler", "unit": 0}]},
        "target": {"framebuffer": 6,
                   "color_attachment": {"name": 433,
                                        "internal_format": "RGB8"}},
        "draw": draw or {"entry": "_GfxDrawIndexed", "mode": 4, "count": 6,
                         "first": 0, "instance_count": 1, "base_vertex": 0,
                         "indexed": True, "caller_rva": "0x10d4e50"},
        "attributes": [{"name": "in_POSITION", "sha256": ATTRIBUTE_SHA}],
        "indices": {"count": 6, "sha256": INDEX_SHA},
    }


def _seal() -> dict:
    return {"sealed": True, "session_id": "session", "capture_frame_id": 1,
            "manifest_sha256": "m" * 64, "reference_sha256": "r" * 64,
            "pid": 4242}


class LayoutTests(unittest.TestCase):
    def testValidDescriptorLoads(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            _write_probe(Path(tmp) / "p", "opengl")
            loaded = gfx_uv2_probe.load_probe(Path(tmp) / "p")
            self.assertEqual(len(loaded["raw"]), WIDTH * HEIGHT * 16)

    def testRowStrideDigestAndStatusAreValidated(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "p"
            descriptor = _write_probe(root, "opengl")
            for field, value in (("row_stride", WIDTH * 4),
                                 ("raw_sha256", "0" * 64),
                                 ("status", "identity_mismatch")):
                broken = dict(descriptor, **{field: value})
                (root / "uv2-probe.json").write_text(json.dumps(broken))
                with self.assertRaises(gfx_uv2_probe.ProbeError):
                    gfx_uv2_probe.load_probe(root)


class BindingTests(unittest.TestCase):
    def testProbesBindTheSealedFrameAndRejectForeignPid(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            gl_dir, metal_dir = Path(tmp) / "gl", Path(tmp) / "metal"
            _write_probe(gl_dir, "opengl")
            _write_probe(metal_dir, "metal")
            binding = gfx_uv2_probe.capture_binding(
                gfx_uv2_probe.load_probe(gl_dir),
                gfx_uv2_probe.load_probe(metal_dir), _seal())
            self.assertTrue(binding["match"])
        with tempfile.TemporaryDirectory() as tmp:
            gl_dir, metal_dir = Path(tmp) / "gl", Path(tmp) / "metal"
            _write_probe(gl_dir, "opengl", pid=999)
            _write_probe(metal_dir, "metal")
            binding = gfx_uv2_probe.capture_binding(
                gfx_uv2_probe.load_probe(gl_dir),
                gfx_uv2_probe.load_probe(metal_dir), _seal())
            self.assertFalse(binding["match"])
            self.assertIn("opengl pid", binding["problems"])

    def testForeignSessionAndUnsealedProbeAreRejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            gl_dir, metal_dir = Path(tmp) / "gl", Path(tmp) / "metal"
            _write_probe(gl_dir, "opengl", session="other")
            _write_probe(metal_dir, "metal", capture=dict(
                _seal(), manifest_sha256="x" * 64))
            binding = gfx_uv2_probe.capture_binding(
                gfx_uv2_probe.load_probe(gl_dir),
                gfx_uv2_probe.load_probe(metal_dir), _seal())
            self.assertFalse(binding["match"])
            self.assertIn("opengl session", binding["problems"])
            self.assertIn(
                "metal replay did not execute the sealed manifest",
                binding["problems"])


class IdentityTests(unittest.TestCase):
    def _identity(self, **overrides):
        with tempfile.TemporaryDirectory() as tmp:
            descriptor = _write_probe(Path(tmp) / "p", "opengl", **overrides)
            probe = gfx_uv2_probe.load_probe(Path(tmp) / "p")
        return gfx_uv2_probe.input_identity(probe, _event())

    def testAllDrawParametersAreValidated(self) -> None:
        self.assertTrue(self._identity()["match"])
        for change in ({"first": 1}, {"base_vertex": 2},
                       {"caller_rva": "0xdead"},
                       {"instance_count": 2}, {"entry": "_GfxDraw"}):
            draw = dict(_event()["draw"], **change)
            event = _event()
            event["draw"] = draw
            with tempfile.TemporaryDirectory() as tmp:
                _write_probe(Path(tmp) / "p", "opengl")
                probe = gfx_uv2_probe.load_probe(Path(tmp) / "p")
            identity = gfx_uv2_probe.input_identity(probe, event)
            self.assertFalse(identity["match"], change)
            self.assertIn("draw", identity["problems"])

class ComparisonTests(unittest.TestCase):
    def _probe(self, root: Path, side: str, values):
        _write_probe(root, side, values=values)
        return gfx_uv2_probe.load_probe(root)

    def _identity(self, *, capture=True, control=True):
        return {"gl": {"match": True, "problems": [], "checks": {},
                       "observed": {}},
                "metal": {"match": True, "problems": [], "checks": {},
                          "observed": {}},
                "capture": {"match": capture, "problems": []}}

    def _compare(self, tmp, gl_values, metal_values, mask, *,
                 authority="authentic", capture=True, control=True,
                 neighborhood=0, state=None):
        return gfx_uv2_probe.compare(
            self._probe(Path(tmp) / "gl", "opengl", gl_values),
            self._probe(Path(tmp) / "metal", "metal", metal_values),
            identity=self._identity(capture=capture),
            control={"match": control}, mask=mask, neighborhood=neighborhood,
            authority=authority, state=state)

    def testRawBitsValuesUlpsAndBitEquality(self):
        metal = _values()
        original = struct.unpack("<I", struct.pack("<f", metal[1][1][0]))[0]
        gl = _values()
        gl[1][1][0] = struct.unpack("<f", struct.pack("<I", original + 1))[0]
        with tempfile.TemporaryDirectory() as tmp:
            report = self._compare(tmp, gl, metal, [(1, 1)], neighborhood=1)
        center = [e for e in report["pixels"]
                  if (e["x"], e["y"]) == (1, 1)][0]
        self.assertFalse(center["bit_equal"]["x"])
        self.assertEqual(center["ulp_differences"]["x"], 1)
        self.assertNotEqual(center["signed_differences"]["x"], 0.0)
        self.assertEqual(report["summary"]["max_ulp_difference_xy"], 1)
        self.assertEqual(len(report["pixels"]), 9)
        self.assertTrue(report["summary"]["numerical_comparison_conclusive"])
        self.assertTrue(report["summary"]["observed_mismatch"])
        self.assertFalse(report["summary"]["all_selected_pixels_equal"])

    def testLowerLeftSamplingUsesTheCorrectRawRow(self):
        width, height = 2, 4
        # Construct in lower-left order: only (0, 3) differs.
        left = bytearray([10, 20, 30, 255] * (width * height))
        right = bytearray(left)
        right[3 * width * 4] = 11
        with tempfile.TemporaryDirectory() as tmp:
            a = Path(tmp) / "a.bgra"
            b = Path(tmp) / "b.bgra"
            a.write_bytes(bytes(left))
            b.write_bytes(bytes(right))
            left_image = gfx_uv2_probe._image(
                a, hashlib.sha256(bytes(left)).hexdigest(), width, height, "a")
            right_image = gfx_uv2_probe._image(
                b, hashlib.sha256(bytes(right)).hexdigest(), width, height, "b")
            mask = gfx_uv2_probe._differences(left_image, right_image)
            self.assertEqual(mask, [(0, 3)])
            # Distinct raw values at the lower-left row 3 and row 0.
            def raw(value):
                body = bytearray()
                for y in range(height):
                    for x in range(width):
                        body.extend(struct.pack("<4f", value, y, 0.0, 1.0))
                return body
            gl = {"width": width, "height": height, "raw": raw(0.0),
                  "directory": Path(tmp), "sha256": "0" * 64,
                  "descriptor": {"side": "opengl"}}
            metal = {"width": width, "height": height, "raw": raw(0.0),
                     "directory": Path(tmp), "sha256": "0" * 64,
                     "descriptor": {"side": "metal"}}
            # Perturb the physical lower-left row 3 pixel in Metal.
            offset = (3 * width + 0) * 16
            struct.pack_into("<f", metal["raw"], offset, 7.0)
            report = gfx_uv2_probe.compare(
                gl, metal, identity=self._identity(), control={"match": True},
                mask=mask, neighborhood=0, authority="authentic")
        entry = report["pixels"][0]
        self.assertEqual((entry["x"], entry["y"]), (0, 3))
        self.assertEqual(entry["gl_values"]["x"], 0.0)
        self.assertEqual(entry["metal_values"]["x"], 7.0)
        self.assertEqual(entry["signed_differences"]["x"], 7.0)
        self.assertTrue(entry["bit_equal"]["y"])

    def testEmptyAndZeroCoverageAreInconclusive(self):
        with tempfile.TemporaryDirectory() as tmp:
            empty = self._compare(tmp, _values(), _values(), [])
            zero = self._compare(
                tmp, _values(False), _values(False), [(0, 0), (1, 1)])
        self.assertEqual(empty["summary"]["status"], "inconclusive_no_mask")
        self.assertEqual(empty["summary"]["mask_pixels"], 0)
        self.assertIsNone(empty["summary"]["xy_bit_for_bit_equal"])
        self.assertEqual(zero["summary"]["status"], "inconclusive_uncovered")
        self.assertEqual(zero["summary"]["mask_pixels_covered_by_both"], 0)
        self.assertIsNone(zero["summary"]["all_selected_pixels_equal"])
        self.assertIsNone(zero["conclusion"]["uv2_results_equal"])
        for report in (empty, zero):
            self.assertFalse(
                report["summary"]["numerical_comparison_conclusive"])

    def testPartialCoverageIsInconclusiveAndRetainsMismatch(self):
        gl = _values()
        metal = _values()
        gl[1][1][3] = 0.0
        metal[1][1][3] = 0.0
        original = struct.unpack("<I", struct.pack("<f", gl[0][0][0]))[0]
        gl[0][0][0] = struct.unpack("<f", struct.pack("<I", original + 1))[0]
        with tempfile.TemporaryDirectory() as tmp:
            report = self._compare(tmp, gl, metal, [(0, 0), (1, 1)])
        self.assertEqual(report["summary"]["mask_pixels_covered_by_both"], 1)
        self.assertEqual(report["summary"]["status"],
                         "inconclusive_partial_coverage")
        self.assertFalse(report["summary"]["numerical_comparison_conclusive"])
        self.assertTrue(report["summary"]["observed_mismatch"])
        self.assertIsNone(report["summary"]["all_selected_pixels_equal"])
        self.assertTrue(report["pixels"][0]["ulp_differences"]["x"])

    def testFullCoverageIsConclusive(self):
        gl = _values()
        metal = _values()
        original = struct.unpack("<I", struct.pack("<f", gl[0][0][0]))[0]
        gl[0][0][0] = struct.unpack("<f", struct.pack("<I", original + 1))[0]
        with tempfile.TemporaryDirectory() as tmp:
            report = self._compare(tmp, gl, metal, [(0, 0), (1, 1)])
        self.assertTrue(report["summary"]["numerical_comparison_conclusive"])
        self.assertEqual(report["summary"]["status"], "conclusive_unequal")
        self.assertTrue(report["summary"]["observed_mismatch"])
        self.assertFalse(report["summary"]["all_selected_pixels_equal"])

    def testFragmentAttributionIsConservative(self):
        cases = (
            (None, "no captured draw state"),
            ({"blend": {"enabled": False}}, "depth state missing"),
            ({"blend": {"enabled": False}, "depth": {"test": False},
              "alpha_test": {"enabled": False}, "stencil": {"test": False}},
             "fragment-shader discard"),
            ({"blend": {"enabled": False}, "depth": {"test": True},
              "alpha_test": {"enabled": False}, "stencil": {"test": False}},
             "depth test"),
        )
        with tempfile.TemporaryDirectory() as tmp:
            for state, problem in cases:
                report = self._compare(tmp, _values(), _values(), [(0, 0)],
                                       state=state)
                self.assertTrue(
                    report["summary"]["numerical_comparison_conclusive"])
                self.assertFalse(report["fragment_attribution"]["verified"])
                self.assertIn(
                    problem,
                    " ".join(report["fragment_attribution"]["problems"]))
        self.assertFalse(
            report["conclusion"]["fragment_attribution_verified"])

    def testEvidenceFailureCannotBeConclusive(self):
        for overrides in ({"authority": "synthetic"}, {"capture": False},
                          {"control": False}):
            with tempfile.TemporaryDirectory() as tmp:
                report = self._compare(tmp, _values(), _values(), [(0, 0)],
                                       **overrides)
            self.assertFalse(report["summary"]["numerical_comparison_conclusive"], overrides)
            self.assertEqual(report["summary"]["status"],
                             "inconclusive_evidence")
            self.assertFalse(report["conclusion"]["numerical_comparison_conclusive"])
            self.assertIsNone(report["conclusion"]["uv2_results_equal"])

    def testNonFiniteFloatsProduceStrictJson(self):
        gl = _values()
        gl[0][0] = [float("nan"), 0.0, 0.0, 1.0]
        metal = _values()
        metal[0][0] = [float("inf"), float("-inf"), -0.0, 1.0]
        with tempfile.TemporaryDirectory() as tmp:
            report = self._compare(tmp, gl, metal, [(0, 0)])
        entry = report["pixels"][0]
        self.assertEqual(entry["gl_bits"]["x"], "0x7fc00000")
        self.assertEqual(entry["metal_bits"]["x"], "0x7f800000")
        self.assertIsNone(entry["gl_values"]["x"])
        self.assertIsNone(entry["metal_values"]["x"])
        self.assertIsNone(entry["signed_differences"]["x"])
        self.assertFalse(entry["all_components_bit_equal"])
        self.assertFalse(report["summary"]["all_selected_pixels_equal"])
        json.dumps(report, allow_nan=False)

    def testMaskBudgetIsRefusedWithTheActualCount(self):
        import numpy as np

        with tempfile.TemporaryDirectory() as tmp:
            gl = np.zeros((65, 65, 4), dtype=np.uint8)
            metal = np.full((65, 65, 4), 255, dtype=np.uint8)
            with self.assertRaises(gfx_uv2_probe.ProbeError) as caught:
                gfx_uv2_probe._differences(gl, metal)
        self.assertIn("4225", str(caught.exception))
        self.assertIn("4096", str(caught.exception))


class StageLocalizationTests(unittest.TestCase):
    def observations(self):
        values = {"fragment_coverage": {"covered": True, "primitive": 5},
                  "uv2": {"x": 0x3f800000, "y": 0x3f000000},
                  "texture_samples": {"tex0.r": 0x3f000000},
                  "pre_conversion": {"R": 0x3f000000, "G": 0, "B": 0},
                  "rgb8": {"R": 128, "G": 0, "B": 0}}
        return {stage: {"opengl": dict(data), "metal": dict(data)}
                for stage, data in values.items()}

    def compare(self, observations, *, verified=True, provenance=True, origin="lower_left"):
        return gfx_uv2_probe.compare_stages(
            observations, attribution={"verified": verified},
            provenance_valid=provenance, origin=origin,
            original_pipeline_stages=gfx_uv2_probe.FLOAT_STAGES)

    def test_first_observable_stage_distinguishes_numerical_mechanisms(self):
        for stage, channel in (("uv2", "x"), ("texture_samples", "tex0.r"),
                               ("pre_conversion", "R"), ("rgb8", "R")):
            with self.subTest(stage=stage):
                values = self.observations()
                values[stage]["metal"][channel] += 1
                values["rgb8"]["metal"]["R"] = 129
                result = self.compare(values)
                self.assertEqual(result["status"], "different")
                self.assertEqual(result["first_observed_difference"], stage)
                self.assertEqual(result["first_differing_stage"], stage)
                if stage != "rgb8":
                    row = result["stages"][stage]
                    self.assertEqual(row["ulp_differences"][channel], 1)
                    self.assertGreater(row["signed_differences"][channel], 0)
                    self.assertEqual(row["metal_bits"][channel],
                                     f"0x{values[stage]['metal'][channel]:08x}")

    def test_fragment_mismatch_precedes_numerical_stages(self):
        values = self.observations()
        values["fragment_coverage"]["metal"]["primitive"] = 6
        result = self.compare(values)
        self.assertEqual(result["first_differing_stage"], "fragment_coverage")
        self.assertEqual(result["stages"]["uv2"]["status"], "conditional")

    def test_ambiguous_fragments_leave_uv2_conditional(self):
        values = self.observations()
        values["uv2"]["metal"]["x"] += 1
        values["rgb8"]["metal"]["R"] += 1
        attribution = gfx_uv2_probe.fragment_attribution({"depth": {"test": True}})
        result = gfx_uv2_probe.compare_stages(
            values, attribution=attribution, provenance_valid=True)
        self.assertIn("discard", " ".join(attribution["problems"]))
        self.assertIn("depth test", " ".join(attribution["problems"]))
        self.assertEqual(result["status"], "inconclusive")
        self.assertEqual(result["first_observed_difference"], "rgb8")
        self.assertIsNone(result["first_differing_stage"])
        self.assertEqual(result["stages"]["uv2"]["status"], "conditional")

    def test_valid_exact_observed_agreement(self):
        result = self.compare(self.observations())
        self.assertEqual(result["status"], "exact_observed_stage_agreement")
        self.assertIsNone(result["first_observed_difference"])
        self.assertNotIn("qualified", result)

    def test_missing_original_uv2_does_not_promote_later_rgb8(self):
        values = self.observations()
        values["uv2"].pop("opengl")
        values["rgb8"]["metal"]["R"] += 1
        result = self.compare(values)
        self.assertEqual(result["status"], "missing_evidence")
        self.assertEqual(result["first_observed_difference"], "rgb8")
        self.assertEqual(result["first_unresolved_stage"], "uv2")
        self.assertEqual(result["stages"]["uv2"]["missing"], ["opengl"])
        self.assertIsNone(result["first_differing_stage"])

    def test_invalid_comparison_origin_fails_closed(self):
        result = self.compare(self.observations(), origin="upper_left")
        self.assertEqual(result, {"status": "invalid_provenance", "stages": {}})


class Event1ArtifactLocalizationTests(unittest.TestCase):
    def setUp(self):
        tmp = self.enterContext(tempfile.TemporaryDirectory())
        self.frame = Path(tmp) / "frame"
        self.replay = Path(tmp) / "replay"
        (self.frame / "blobs").mkdir(parents=True)
        self.replay.mkdir()
        self.renderer = "b" * 64
        self.seal = {**_seal(), "event_stream_sha256": "c" * 64}
        event = _event()
        event["target"]["color_attachment"].update(type="texture", level=0)
        event["target"]["depth_attachment"] = {"type": "renderbuffer", "name": 3}
        clear_target = {**event["target"], "viewport": [0, 0, 2, 2]}
        self.trace = {"_events": [{"kind": "clear", "target": clear_target}, event]}
        original = bytes([2, 10, 20, 255]) * (WIDTH * HEIGHT)
        candidate = bytearray(original)
        # B at x=1, lower-left y=2. Alpha-only changes remain outside RGB8.
        candidate[(2 * WIDTH + 1) * 4] += 1
        candidate[3] = 5
        name = "event1.bgra"
        (self.frame / "blobs" / name).write_bytes(original)
        (self.replay / name).write_bytes(candidate)
        (self.frame / "frame-seal.json").write_text(json.dumps(self.seal))
        self.post = {
            "event": 1, "resource": "tex:433", "timing": "post_event",
            "target": event["target"], "target_identity": {"internal_format": 0x8051},
            "origin": "lower_left",
            "pixel_format": "BGRA8", "width": WIDTH, "height": HEIGHT,
            "color": {"file": name, "sha256": hashlib.sha256(original).hexdigest()},
        }
        prior = bytearray(bytes([1, 1, 1, 255]) * (WIDTH * HEIGHT))
        # One matching neighbor keeps RGB unchanged across the draw. Its alpha differs.
        neighbor = (WIDTH + 0) * 4
        prior[neighbor:neighbor + 4] = bytes([*original[neighbor:neighbor + 3], 7])
        depth = struct.pack("<f", 1.0) * (WIDTH * HEIGHT)
        (self.frame / "blobs" / "pre.bgra").write_bytes(prior)
        (self.frame / "blobs" / "pre-depth.f32").write_bytes(depth)
        self.pre = {
            **self.post, "event": 0, "kind": "clear", "target": clear_target,
            "color": {"file": "pre.bgra", "sha256": hashlib.sha256(prior).hexdigest(),
                      "bytes": len(prior)},
            "depth": {"status": "captured", "file": "pre-depth.f32", "format": "f32",
                      "sha256": hashlib.sha256(depth).hexdigest(), "bytes": len(depth),
                      "width": WIDTH, "height": HEIGHT},
        }
        self.trace["_prefix"] = {"post_events": [self.pre, self.post]}
        self.result = {
            "status": "ok", "stage": "complete", "failed": 0, "partial": False,
            "timed_out": False, "encoded": 2, "events": 2, "readback": True,
            "command_status": 4, "checkpoints": [{
                "event": 1, "resource": "tex:433", "target": "433",
                "timing": "post_event", "file": name, "ok": True,
                "pixel_format": "BGRA8", "width": WIDTH, "height": HEIGHT,
                "reference_sha256": self.post["color"]["sha256"],
            }],
        }
        self.publish_candidate()
        self.enterContext(mock.patch.object(
            gfx_uv2_probe, "frame_context", return_value=(self.trace, self.seal, event)))

    def publish_candidate(self):
        result_path = self.replay / "replay.json"
        self.result["shader_compile_options"] = {"default_fast_math": True, "fast_math": False}
        result_path.write_text(json.dumps(self.result))
        gfx_independent_replay.write_producer_receipt(self.replay, self.frame, {
            "executable_sha256": self.renderer, "build_fingerprint": "a" * 64,
        }, result_path, env={"EU4_FRAME_REPLAY_FAST_MATH": "0"})

    def localize(self):
        return gfx_uv2_probe.localize(self.frame, self.replay, renderer_sha256=self.renderer)

    def setup_sparse(self):
        event = self.trace["_events"][1]
        event["program"]["shaders"] = [
            {"stage": stage, "sha256": digest}
            for stage, digest in gfx_uv2_probe.EVENT1_SHADER_SHA256.items()]
        event["indices"].update(first_index=0, range_valid=True, read_error=False,
                                bytes=12, sha256="a" * 64)
        event["state"] = {
            "color_mask": [1, 1, 1, 1],
            "depth": {"test": True, "write": True, "func": "0x203", "range": [0, 1]},
            "polygon": {"mode": ["0x1b02", "0x1b02"], "offset_fill": False},
            "blend": {"enabled": False}, "logic_op": {"enabled": False},
            "alpha_test": {"enabled": False}, "stencil": {"test": False},
            "scissor": {"enabled": False},
        }
        self.post["depth"] = {**self.pre["depth"], "file": "post-depth.f32"}
        values = struct.pack("<f", 0.5) * (WIDTH * HEIGHT)
        (self.frame / "blobs" / "post-depth.f32").write_bytes(values)
        self.post["depth"]["sha256"] = hashlib.sha256(values).hexdigest()
        (self.replay / "pre.bgra").write_bytes((self.frame / "blobs" / "pre.bgra").read_bytes())
        self.result["checkpoints"].append({
            **self.result["checkpoints"][0], "timing": "pre_event", "file": "pre.bgra",
            "reference_sha256": self.pre["color"]["sha256"],
        })
        self.gl_sample = {
            "x": 1, "y": 2, "sample": 0, "primitive": 1, "hits": 1,
            "hits_without_depth": 0, "uv2_bits": [0x3e800000, 0x3f000000],
            "survived": True, "control_rgb": [20, 10, 2], "pre_rgb": [1, 1, 1],
            "pre_depth_bits": 0x3f800000, "control_depth_bits": 0x3f000000,
        }
        self.metal_sample = {
            **self.gl_sample, "selected_hits": 1, "control_rgb": [20, 10, 3],
            "uv2_bits": [0x3e800001, 0x3f000000],
        }

    def publish_sparse(self, *, gl_fields=None, metal_fields=None):
        for side, folder, row, fields in (
                ("opengl", self.frame, self.gl_sample, gl_fields),
                ("metal", self.replay / "uv2-probe", self.metal_sample, metal_fields)):
            folder.mkdir(exist_ok=True)
            doc = {
                **gfx_uv2_probe.EVENT1, "level": 0, "capture": self.seal,
                "schema": gfx_uv2_probe.SPARSE_SCHEMA, "side": side, "status": "observed",
                "origin": "lower_left", "selection": [[row["x"], row["y"]]],
                "width": WIDTH, "height": HEIGHT, "samples": 1,
                "method": gfx_uv2_probe.SPARSE_METHODS[side], "pixels": [row],
                "index_sha256": self.trace["_events"][1]["indices"]["sha256"],
                **(fields or {}),
            }
            payload = json.dumps(doc).encode()
            (folder / gfx_uv2_probe.SPARSE_FILE).write_bytes(payload)
            binding = {"file": gfx_uv2_probe.SPARSE_FILE,
                       "sha256": hashlib.sha256(payload).hexdigest()}
            if side == "opengl":
                self.trace["uv2_fragments"] = binding
            else:
                self.result["uv2_probe"] = binding
        self.publish_candidate()

    def test_sparse_coverage_and_instrumented_disagreement_are_separate(self):
        self.setup_sparse()
        self.publish_sparse()
        report = self.localize()
        self.assertEqual(report["status"], "correspondence_verified", report.get("reason"))
        self.assertTrue(report["fragment_attribution_verified"])
        sparse = report["sparse_observations"]
        self.assertTrue(sparse["all_selected_coordinates_verified"])
        self.assertTrue(sparse["selection_covers_all_residuals"])
        self.assertEqual(sparse["fresh_residuals_outside_selection"], 0)
        pixel, = report["pixels"]
        self.assertEqual((pixel["x"], pixel["y"], pixel["selection"]), (1, 2, "residual"))
        self.assertEqual(pixel["first_observed_difference"], "rgb8")
        self.assertIsNone(pixel["first_differing_stage"])
        self.assertEqual(pixel["first_unresolved_stage"], "uv2")
        uv2 = pixel["stages"]["uv2"]
        self.assertEqual(uv2["ulp_differences"], {"x": 1, "y": 0})
        self.assertEqual(uv2["authority"], "instrumented_shader_variants")
        self.assertEqual(uv2["instrumented_comparison"], "different")
        self.assertFalse(uv2["original_pipeline_qualified"])
        self.assertTrue(pixel["fragment_correspondence"]["verified"])
        self.assertEqual(pixel["fragment_correspondence"]["decisions"],
                         {"opengl": "verified_writer", "metal": "verified_writer"})

    def set_candidate_blue(self, x, y, blue):
        path = self.replay / self.post["color"]["file"]
        data = bytearray(path.read_bytes())
        data[(y * WIDTH + x) * 4] = blue
        path.write_bytes(data)

    def test_new_residual_outside_a_fully_verified_selection_blocks_event_attribution(self):
        self.setup_sparse()
        self.set_candidate_blue(2, 3, 3)
        self.publish_sparse()
        report = self.localize()
        self.assertEqual(report["mask_pixels"], 2)
        self.assertEqual(report["status"], "inconclusive")
        self.assertFalse(report["fragment_attribution_verified"])
        self.assertTrue(report["pixels"][0]["fragment_correspondence"]["verified"])
        sparse = report["sparse_observations"]
        self.assertTrue(sparse["all_selected_coordinates_verified"])
        self.assertFalse(sparse["selection_covers_all_residuals"])
        self.assertEqual(sparse["fresh_residuals_outside_selection"], 1)
        self.assertIn("current_residuals_outside_selection", report["missing"])
        self.assertEqual(report["next_observation"],
                         "fragment_correspondence_at_unobserved_residuals")

    def test_large_residual_mask_keeps_sparse_results_and_counts_unselected_pixels(self):
        self.setup_sparse()
        self.set_candidate_blue(2, 3, 3)
        self.publish_sparse()
        with mock.patch.object(gfx_uv2_probe, "MAX_MASK_PIXELS", 1):
            report = self.localize()
        self.assertEqual(report["status"], "inconclusive")
        self.assertEqual(report["mask_pixels"], 2)
        self.assertEqual(report["sparse_observations"]["fresh_residuals_outside_selection"], 1)
        self.assertTrue(report["pixels"][0]["fragment_correspondence"]["verified"])

    def test_every_residual_in_a_multi_pixel_selection_can_verify(self):
        self.setup_sparse()
        self.set_candidate_blue(2, 3, 3)
        selection = [[1, 2], [2, 3]]
        self.publish_sparse(
            gl_fields={"selection": selection,
                       "pixels": [self.gl_sample, {**self.gl_sample, "x": 2, "y": 3}]},
            metal_fields={"selection": selection,
                          "pixels": [self.metal_sample, {**self.metal_sample, "x": 2, "y": 3}]})
        report = self.localize()
        self.assertEqual(report["status"], "correspondence_verified")
        self.assertEqual(report["mask_pixels"], 2)
        self.assertEqual(report["sparse_observations"]["verified"], 2)
        self.assertEqual(report["sparse_observations"]["fresh_residuals_outside_selection"], 0)
        self.assertTrue(report["sparse_observations"]["selection_covers_all_residuals"])

    def test_verified_rgb_control_alone_cannot_cover_residual(self):
        self.setup_sparse()
        for sample in (self.gl_sample, self.metal_sample):
            sample.update(x=2, y=2, control_rgb=[20, 10, 2])
        self.publish_sparse()
        report = self.localize()
        self.assertEqual(report["mask_pixels"], 1)
        self.assertEqual(report["sparse_observations"]["fresh_residuals_outside_selection"], 1)
        self.assertTrue(report["sparse_observations"]["all_selected_coordinates_verified"])
        self.assertFalse(report["sparse_observations"]["selection_covers_all_residuals"])
        self.assertEqual(report["status"], "inconclusive")
        self.assertEqual(report["pixels"][0]["selection"], "rgb_match_control")

    def test_matching_color_and_depth_controls_do_not_authenticate_uv2(self):
        self.setup_sparse()
        self.set_candidate_blue(1, 2, 2)
        self.metal_sample["control_rgb"] = self.gl_sample["control_rgb"]
        self.publish_sparse()
        report = self.localize()
        pixel, = report["pixels"]
        self.assertTrue(pixel["fragment_correspondence"]["verified"])
        self.assertEqual(pixel["original_gl_depth"]["float32_bits"]["post"], 0x3f000000)
        self.assertEqual(pixel["stages"]["rgb8"]["status"], "exact")
        uv2 = pixel["stages"]["uv2"]
        # GL's depth bits reproduce the sealed value; the Metal witness
        # re-passes at its own post-depth. Both color controls reproduced RGB8.
        self.assertEqual(uv2["controls"], {"opengl": "matched", "metal": "matched"})
        self.assertEqual(uv2["instrumented_comparison"], "different")
        self.assertEqual(uv2["ulp_differences"], {"x": 1, "y": 0})
        self.assertFalse(uv2["original_pipeline_qualified"])
        self.assertIsNone(pixel["first_observed_difference"])
        self.assertIsNone(pixel["first_differing_stage"])

    def test_failed_instrumentation_does_not_invalidate_the_two_normal_writers(self):
        self.setup_sparse()
        for side, change, expected in (
                ("opengl", {"survived": False}, "no_instrumented_write"),
                ("opengl", {"control_depth_bits": 0x3f000001}, "instrumented_depth_differs"),
                ("metal", {"control_rgb": [20, 10, 2]}, "instrumented_color_differs")):
            with self.subTest(side=side, change=change):
                target = self.gl_sample if side == "opengl" else self.metal_sample
                with mock.patch.dict(target, change):
                    self.publish_sparse()
                    pixel, = self.localize()["pixels"]
                self.assertTrue(pixel["fragment_correspondence"]["verified"])
                self.assertEqual(pixel["stages"]["uv2"]["controls"][side], expected)
                self.assertIsNone(pixel["first_differing_stage"])

    def test_sparse_requires_actual_write_coverage_and_same_fragment(self):
        self.setup_sparse()
        examples = (
            ("opengl", {"hits": 0}, "no_coverage_or_discard"),
            ("opengl", {"hits": 0, "hits_without_depth": 1}, "depth_rejected"),
            ("opengl", {"hits": 2}, "overlap"),
            ("opengl", {"pre_depth_bits": 0x3f000000}, "original_pre_depth_or_color_unbound"),
            ("metal", {"selected_hits": 0}, "gl_primitive_not_observed"),
            ("metal", {"hits": 2}, "overlap"),
            ("metal", {"hits": 0}, "no_passing_sample"),
            ("metal", {"sample": 1}, "unsupported_sample"),
            ("metal", {"primitive": 0}, "verified_writer"),
        )
        for side, change, expected in examples:
            with self.subTest(side=side, change=change):
                sample = self.gl_sample if side == "opengl" else self.metal_sample
                with mock.patch.dict(sample, change):
                    self.publish_sparse()
                    report = self.localize()
                self.assertEqual(report["status"], "inconclusive")
                sparse = report["sparse_observations"]
                self.assertFalse(sparse["all_selected_coordinates_verified"])
                self.assertTrue(sparse["selection_covers_all_residuals"])
                pixel, = report["pixels"]
                self.assertFalse(pixel["fragment_correspondence"]["verified"])
                self.assertEqual(pixel["fragment_correspondence"]["decisions"][side], expected)
                self.assertIsNone(pixel["first_differing_stage"])
        self.assertTrue(pixel["fragment_correspondence"]["different_primitive_or_sample"])

    def test_rgb_match_cannot_establish_overlap_or_a_color_preserving_write(self):
        self.setup_sparse()
        for sample in (self.gl_sample, self.metal_sample):
            sample.update(x=0, y=1, control_rgb=[20, 10, 2])
        self.publish_sparse()
        pixel, = self.localize()["pixels"]
        self.assertEqual(pixel["stages"]["rgb8"]["status"], "exact")
        self.assertEqual(pixel["fragment_correspondence"]["decisions"]["opengl"],
                         "normal_color_write_unproven")
        self.assertEqual(pixel["status"], "inconclusive")

    def test_sparse_rejects_wrong_seal_orientation_or_changed_payload(self):
        self.setup_sparse()
        for fields, message in (
                ({"capture": {**self.seal, "session_id": "another"}}, "different seal"),
                ({"origin": "upper_left"}, "orientation mismatch"),
                ({"index_sha256": "b" * 64}, "different index stream"),
                ({"method": "normal_pipeline_query_post_lequal"}, "unsupported method"),
                ({"selection": [[2, 1]]}, "sample record is invalid")):
            with self.subTest(fields=fields):
                self.publish_sparse(metal_fields=fields)
                report = self.localize()
                self.assertEqual(report["status"], "invalid_provenance")
                self.assertIn(message, report["reason"])
        self.publish_sparse()
        (self.frame / gfx_uv2_probe.SPARSE_FILE).write_bytes(b"{}")
        self.assertIn("digest mismatch", self.localize()["reason"])

    def test_negative_observation_and_unsupported_write_state_remain_unresolved(self):
        self.setup_sparse()
        self.publish_sparse(gl_fields={"status": "unresolved", "reason": "unsupported",
                                       "pixels": []})
        report = self.localize()
        self.assertEqual(report["status"], "inconclusive")
        self.assertEqual(report["sparse_observations"]["opengl"]["reason"], "unsupported")
        for state in ({"blend": {"enabled": True}}, {"color_mask": [0, 1, 1, 1]},
                      {"logic_op": {"enabled": True}}, {"alpha_test": {"enabled": True}},
                      {"depth": {"test": True, "write": False, "func": "0x203", "range": [0, 1]}},
                      {"depth": {"test": True, "write": True, "func": "0x201", "range": [0, 1]}},
                      {"depth": {"test": True, "write": True, "func": "0x203", "range": [1, 0]}}):
            with self.subTest(state=state), mock.patch.dict(self.trace["_events"][1]["state"], state):
                self.publish_sparse()
                pixel, = self.localize()["pixels"]
                self.assertEqual(pixel["fragment_correspondence"]["decisions"]["opengl"],
                                 "unsupported_write_or_draw_state")

    def test_sparse_replay_uses_only_sealed_selection_and_primitive(self):
        self.setup_sparse()
        self.publish_sparse()
        ordinals, checkpoints = gfx_uv2_probe.sparse_replay_inputs(self.frame, "1,2")
        self.assertEqual(ordinals, "1")
        self.assertEqual({row["timing"]: row["reference_sha256"] for row in checkpoints},
                         {"pre_event": self.pre["color"]["sha256"],
                          "post_event": self.post["color"]["sha256"]})
        with self.assertRaisesRegex(gfx_uv2_probe.ProbeError, "sealed selection"):
            gfx_uv2_probe.sparse_replay_inputs(self.frame, "2,1")

    def test_sparse_cli_pixels_reject_duplicates_shape_and_range(self):
        self.assertEqual(m6_frame._uv2_probe_pixels("0,1;7,2"), "0,1;7,2")
        for text in ("0,1;0,1", "0,1;", "-1,0", "0,8192", ""):
            with self.subTest(text=text), self.assertRaises(argparse.ArgumentTypeError):
                m6_frame._uv2_probe_pixels(text)

    def test_receipt_bound_rgb_and_controls_preserve_lower_left_order(self):
        report = self.localize()
        self.assertEqual(report["status"], "missing_evidence", report)
        self.assertEqual(report["mask_pixels"], 1)
        self.assertEqual(report["candidate"]["compile_mode_provenance"], "verified")
        self.assertEqual(report["opengl"]["predecessor"]["status"], "observed")
        self.assertEqual(report["opengl"]["pre_depth"], self.pre["depth"])
        self.assertEqual(report["opengl"]["pre_color"]["sha256"], self.pre["color"]["sha256"])
        controls = [row for row in report["pixels"] if row["selection"] == "rgb_match_control"]
        self.assertEqual(report["matched_control_pixels"], 7)
        self.assertEqual(len(controls), 7)
        self.assertNotIn((0, 1), {(row["x"], row["y"]) for row in controls})
        self.assertTrue(all(row["stages"]["rgb8"]["status"] == "exact" and not
                            row["stages"]["rgb8"]["fragment_correspondence_verified"]
                            for row in controls))
        pixel = report["pixels"][0]
        self.assertEqual((pixel["x"], pixel["y"], pixel["origin"]), (1, 2, "lower_left"))
        self.assertEqual(pixel["first_observed_difference"], "rgb8")
        self.assertIsNone(pixel["first_differing_stage"])
        self.assertEqual(pixel["stages"]["rgb8"]["signed_differences"],
                         {"R": 0, "G": 0, "B": 1})
        self.assertFalse(pixel["stages"]["rgb8"]["fragment_correspondence_verified"])
        self.assertEqual(self.localize()["pixels"], report["pixels"])

    def test_missing_predecessor_reports_missing_planes_and_keeps_rgb8(self):
        self.trace["_prefix"]["post_events"].remove(self.pre)
        report = self.localize()
        self.assertEqual(report["opengl"]["predecessor"]["status"], "missing_evidence")
        self.assertEqual(report["opengl"]["predecessor"]["missing"],
                         ["original_pre_color", "original_pre_depth"])
        self.assertEqual(report["matched_control_pixels"], 0)
        self.assertEqual(report["pixels"][0]["first_observed_difference"], "rgb8")
        self.assertIsNone(report["opengl"]["pre_depth"])

    def test_unbound_depth_does_not_hide_bound_pre_color(self):
        self.pre["target"] = {**self.pre["target"],
                              "depth_attachment": {"type": "texture", "name": 3}}
        report = self.localize()
        self.assertEqual(report["opengl"]["predecessor"]["missing"], ["original_pre_depth"])
        self.assertEqual(report["matched_control_pixels"], 7)

    def test_inconsistent_current_recipe_is_rejected(self):
        path = self.replay / "producer-receipt.json"
        original = path.read_text()
        for field, value in (("sha256", "0" * 64),
                             ("environment_sha256", "0" * 64),
                             ("diagnostic", False), ("effective_env", {})):
            with self.subTest(field=field):
                receipt = json.loads(original)
                receipt["recipe"][field] = value
                path.write_text(json.dumps(receipt))
                report = self.localize()
                self.assertEqual(report["status"], "invalid_provenance", report)
                self.assertEqual(report["pixels"], [])

    def test_self_consistent_recipe_conflicting_with_native_mode_is_rejected(self):
        path = self.replay / "producer-receipt.json"
        receipt = json.loads(path.read_text())
        receipt["recipe"] = gfx_independent_replay.replay_recipe(
            env={"EU4_FRAME_REPLAY_FAST_MATH": "1"})
        path.write_text(json.dumps(receipt))
        report = self.localize()
        self.assertEqual(report["status"], "invalid_provenance")
        self.assertIn("compile options disagree", report["reason"])

    def test_recipe_with_both_compile_switches_is_rejected(self):
        path = self.replay / "producer-receipt.json"
        receipt = json.loads(path.read_text())
        receipt["recipe"]["consumed_env"]["EU4_FRAME_REPLAY_PRECISE_MATH"] = "1"
        path.write_text(json.dumps(receipt))
        report = self.localize()
        self.assertEqual(report["status"], "invalid_provenance")
        self.assertIn("retired", report["reason"])

    def test_missing_recipe_is_only_accepted_for_historical_rgb8(self):
        path = self.replay / "producer-receipt.json"
        receipt = json.loads(path.read_text())
        receipt.pop("recipe")
        path.write_text(json.dumps(receipt))
        self.assertEqual(self.localize()["status"], "invalid_provenance")
        result_path = self.replay / "replay.json"
        result = json.loads(result_path.read_text())
        result.pop("shader_compile_options")
        result_path.write_text(json.dumps(result))
        receipt["result"]["replay_json_sha256"] = hashlib.sha256(
            result_path.read_bytes()).hexdigest()
        path.write_text(json.dumps(receipt))
        report = self.localize()
        self.assertEqual(report["candidate"]["compile_mode_provenance"], "unverified")
        self.assertEqual(report["pixels"][0]["first_observed_difference"], "rgb8")
        self.assertFalse(report["fragment_attribution"]["verified"])

    def test_missing_original_post_observation_is_reported(self):
        self.trace["_prefix"]["post_events"].clear()
        report = self.localize()
        self.assertEqual(report["status"], "missing_evidence", report)
        self.assertEqual(report["missing"], ["original_event1_rgb8"])

    def test_altered_seal_candidate_renderer_and_checkpoint_fail_closed(self):
        expected = gfx_uv2_probe.localize(self.frame, self.replay, renderer_sha256="a" * 64)
        self.assertEqual(expected["status"], "invalid_provenance")
        self.assertIn("renderer", expected["reason"])
        with mock.patch.dict(self.seal, {"manifest_sha256": "d" * 64}):
            report = self.localize()
            self.assertEqual(report["status"], "invalid_provenance")
            self.assertIn("sealed manifest", report["reason"])
        self.result["checkpoints"][0]["resource"] = "tex:432"
        self.publish_candidate()
        report = self.localize()
        self.assertEqual(report["status"], "invalid_provenance")
        self.assertEqual(report["pixels"], [])
        self.assertIn("tex:433", report["reason"])


@unittest.skipUnless(sys.platform == "darwin", "the native probe host is macOS")
class NativeHostTests(unittest.TestCase):
    def testReadbackLayoutJsonControlAndStateRestoration(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            binary = Path(tmp) / "gfx_uv2_probe_host"
            sources = [ROOT / "tests" / "gfx_uv2_probe_host.c",
                       BENCH / "eu4_gfx_frame_trace.c",
                       BENCH / "eu4_gfx_frame_observe.c"]
            native_build.require_build(
                ["clang", "-O1", "-Wall", "-Wextra", "-I", str(BENCH),
                 "-o", str(binary), *map(str, sources),
                 "-framework", "OpenGL", "-framework", "AppKit"],
                label="uv2-probe-host", sources=sources,
                frameworks=("OpenGL", "AppKit"), require_arm64=False)
            output = Path(tmp) / "out"
            output.mkdir()
            ran = subprocess.run([str(binary), str(output)],
                                 capture_output=True, text=True, check=False)
            self.assertEqual(ran.returncode, 0, ran.stderr)
            for marker in ("probe=1", "layout=1", "control=1",
                           "restore=1", "attribute_limit=1", "first_offset=1"):
                self.assertIn(marker, ran.stdout)
            descriptor = json.loads(
                (output / "uv2-probe.json").read_text())
            self.assertEqual(descriptor["schema"], "gfx-uv2-probe/v1")
            self.assertEqual(descriptor["draw"]["entry"], "_GfxDrawIndexed")
            self.assertTrue(all(a["digest_ok"]
                                for a in descriptor["attributes"]))
            observed = dict(line.split("=", 1) for line in ran.stdout.splitlines())
            for scenario in ("sparse-0x81a6", "sparse-0x81a5"):
                with self.subTest(scenario=scenario):
                    bits = int(observed[f"{scenario}_bits"])
                    failure = json.loads(
                        (output / scenario / "uv2-fragments.json").read_text())
                    self.assertEqual(failure["status"], "unsupported_sparse_draw")
                    self.assertEqual(failure["pixels"], [])
                    # Drivers may allocate greater precision than requested.
                    if bits == 24:
                        self.assertEqual(failure["reason"], "program")
                        self.assertEqual(failure["observed"], int(observed["program_name"]))
                        self.assertEqual(failure["required"], 310)
                    else:
                        self.assertEqual(failure["reason"], "depth_bits")
                        self.assertEqual((failure["observed"], failure["required"]), (bits, 24))
                        self.assertEqual(failure["depth_attachment"], {
                            "type": "renderbuffer",
                            "name": int(observed["renderbuffer_name"]),
                            "internal_format": int(observed[f"{scenario}_format"]),
                            "depth_bits": bits,
                            "component_type": int(observed[f"{scenario}_component"]),
                        })
            absent = json.loads(
                (output / "sparse-no-depth" / "uv2-fragments.json").read_text())
            self.assertEqual(absent["status"], "unsupported_sparse_draw")
            self.assertEqual(absent["reason"], "depth_bits")
            self.assertEqual((absent["observed"], absent["required"]), (0, 24))
            self.assertEqual(absent["pixels"], [])
            self.assertNotIn("depth_attachment", absent)


@unittest.skipUnless(sys.platform == "darwin", "the Metal replay is macOS")
class MetalProbeTests(unittest.TestCase):
    FIXTURE = ROOT / ".local" / "m6" / "frames" / "menu-occ300"
    SEAL = ROOT / ".local" / "m7" / "event20" / "trusted-copy" / "47590918"

    @classmethod
    def setUpClass(cls):
        if not (cls.FIXTURE / "frame" / "frame-trace.json").is_file():
            raise unittest.SkipTest("menu-occ300 fixture is unavailable")
        cls.temporary = tempfile.TemporaryDirectory()
        cls.cli = Path(cls.temporary.name) / "gfx_frame_replay"
        sources = [BENCH / "eu4_gfx_frame_replay.mm",
                   BENCH / "eu4_gfx_frame_replay_cli.mm"]
        native_build.require_prerequisites(
            sources=sources, frameworks=("Foundation", "Metal"),
            require_arm64=True)
        native_build.build(
            ["clang", "-arch", "arm64", "-O2", "-fobjc-arc", "-lc++",
             "-framework", "Foundation", "-framework", "Metal",
             "-I", str(BENCH), "-o", str(cls.cli), *map(str, sources)],
            label="uv2-probe-metal", timeout=300)

    @classmethod
    def tearDownClass(cls):
        cls.temporary.cleanup()

    def _replay(self, output: Path, env_extra: dict | None = None,
                checkpoint: Path | None = None):
        env = dict(os.environ)
        env.pop("EU4_FRAME_REPLAY_UV2_PROBE", None)
        env.update(env_extra or {})
        command = [str(self.cli), "--frame",
                   str(self.FIXTURE / "frame"), "--shaders",
                   str(self.FIXTURE / "translated"), "--out", str(output)]
        if checkpoint is not None:
            command += ["--checkpoints", str(checkpoint)]
        return subprocess.run(command, capture_output=True, text=True,
                              check=False, timeout=300, env=env)

    def testProbeLayoutAndNormalFrameIdentity(self) -> None:
        base = Path(self.temporary.name) / "base"
        probe = Path(self.temporary.name) / "probe"
        request = Path(self.temporary.name) / "request.json"
        request.write_text(json.dumps({
            "schema": "gfx-frame-checkpoint-request/v1",
            "checkpoints": [{"event": 1, "target": "433", "resource": "tex:433",
                             "file": "uv2-color.bgra", "timing": "post_event"}]}))
        base_run = self._replay(base)
        self.assertEqual(base_run.returncode, 0, base_run.stderr)
        probe_run = self._replay(
            probe,
            {"EU4_FRAME_REPLAY_UV2_PROBE": "1",
             "EU4_FRAME_REPLAY_UV2_PROBE_EVENT": "1",
             "EU4_FRAME_REPLAY_UV2_PROBE_PROGRAM": "310",
             "EU4_FRAME_REPLAY_UV2_PROBE_FRAMEBUFFER": "6",
             "EU4_FRAME_REPLAY_UV2_PROBE_TEXTURE": "433",
             "EU4_FRAME_REPLAY_UV2_PROBE_DIR": str(probe / "uv2-probe")},
            checkpoint=request)
        self.assertEqual(probe_run.returncode, 0, probe_run.stderr)
        self.assertEqual((base / "metal-frame.bgra").read_bytes(),
                         (probe / "metal-frame.bgra").read_bytes())
        descriptor = json.loads(
            (probe / "uv2-probe" / "uv2-probe.json").read_text())
        self.assertEqual(descriptor["status"], "ok")
        self.assertEqual(descriptor["draw"]["entry"], "_GfxDrawIndexed")
        self.assertEqual(descriptor["normal_target"]["file"], "uv2-color.bgra")
        self.assertEqual(descriptor["normal_target"]["width"], 1728)
        self.assertEqual(
            (probe / "uv2-color.bgra").stat().st_size, 1728 * 1117 * 4)
        self.assertEqual(descriptor["capture"]["sealed"], False)

    def testInconsistentCheckpointRequestIsRefused(self) -> None:
        cases = {
            "resource": {"resource": "tex:999"},
            "target": {"target": "999", "resource": "tex:999"},
            "timing": {"timing": "pre_event"},
            "file": {"file": "other.bgra"},
        }
        for label, overrides in cases.items():
            with self.subTest(case=label):
                output = Path(self.temporary.name) / f"bad-{label}"
                checkpoint = output / "request.json"
                output.mkdir()
                checkpoint.write_text(json.dumps({
                    "schema": "gfx-frame-checkpoint-request/v1",
                    "checkpoints": [dict({
                        "event": 1, "target": "433", "resource": "tex:433",
                        "file": "uv2-color.bgra", "timing": "post_event",
                    }, **overrides)]}))
                ran = self._replay(
                    output,
                    {"EU4_FRAME_REPLAY_UV2_PROBE": "1",
                     "EU4_FRAME_REPLAY_UV2_PROBE_EVENT": "1",
                     "EU4_FRAME_REPLAY_UV2_PROBE_PROGRAM": "310",
                     "EU4_FRAME_REPLAY_UV2_PROBE_FRAMEBUFFER": "6",
                     "EU4_FRAME_REPLAY_UV2_PROBE_TEXTURE": "433",
                     "EU4_FRAME_REPLAY_UV2_PROBE_DIR": str(output / "uv2-probe")},
                    checkpoint=checkpoint)
                replay = json.loads((output / "replay.json").read_text())
                if label == "target":
                    # The existing checkpoint path refuses a missing target
                    # before the probe can publish a diagnostic.
                    self.assertEqual(replay["status"], "failed")
                    self.assertEqual(
                        replay["failures"][0]["stage"], "checkpoint_readback")
                else:
                    self.assertEqual(ran.returncode, 0, ran.stderr)
                    self.assertEqual(replay["status"], "ok")
                    self.assertEqual(replay["uv2_probe"]["status"], "error")
                    self.assertIn("no post_event checkpoint matches",
                                  replay["uv2_probe"]["reason"])
                descriptor = json.loads(
                    (output / "uv2-probe" / "uv2-probe.json").read_text())
                self.assertEqual(descriptor["status"], "error")
                self.assertIn("no post_event checkpoint matches",
                              descriptor["reason"])

    def testNonSelectedEventWritesNoProbe(self) -> None:
        output = Path(self.temporary.name) / "skipped"
        ran = self._replay(output, {
            "EU4_FRAME_REPLAY_UV2_PROBE": "1",
            "EU4_FRAME_REPLAY_UV2_PROBE_EVENT": "0",
            "EU4_FRAME_REPLAY_UV2_PROBE_DIR": str(output / "uv2-probe")})
        self.assertEqual(ran.returncode, 0, ran.stderr)
        self.assertFalse((output / "uv2-probe" / "uv2-probe.json").exists())
        replay = json.loads((output / "replay.json").read_text())
        self.assertEqual(replay["uv2_probe"]["status"], "skipped")


@unittest.skipUnless(sys.platform == "darwin", "the Metal replay is macOS")
class SealComparisonTests(unittest.TestCase):
    """Same-frame binding and the existing event-1 divergent-pixel set."""

    SEAL = ROOT / ".local" / "m7" / "event20" / "trusted-copy" / "47590918"
    SHADERS = ROOT / ".local" / "m7" / "event20" / "candidate-run" / "baseline" / "shaders"

    @classmethod
    def setUpClass(cls):
        if not (cls.SEAL / "frame" / "frame-trace.json").is_file() or not (
                cls.SHADERS / "shaders.json").is_file():
            raise unittest.SkipTest("the sealed event-1 frame is unavailable")
        cls.temporary = tempfile.TemporaryDirectory()
        cls.cli = Path(cls.temporary.name) / "gfx_frame_replay"
        sources = [BENCH / "eu4_gfx_frame_replay.mm",
                   BENCH / "eu4_gfx_frame_replay_cli.mm"]
        native_build.require_prerequisites(
            sources=sources, frameworks=("Foundation", "Metal"),
            require_arm64=True)
        native_build.build(
            ["clang", "-arch", "arm64", "-O2", "-fobjc-arc", "-lc++",
             "-framework", "Foundation", "-framework", "Metal",
             "-I", str(BENCH), "-o", str(cls.cli), *map(str, sources)],
            label="uv2-probe-seal", timeout=300)

    @classmethod
    def tearDownClass(cls):
        cls.temporary.cleanup()

    def testConditionalComparisonOnASyntheticGlsSidecar(self):
        """The GL sidecar is fabricated; the frame is authentic."""
        metal = Path(self.temporary.name) / "metal"
        request = Path(self.temporary.name) / "request.json"
        request.write_text(json.dumps({
            "schema": "gfx-frame-checkpoint-request/v1",
            "checkpoints": [{"event": 1, "target": "433",
                             "resource": "tex:433", "file": "uv2-color.bgra",
                             "timing": "post_event"}]}))
        env = dict(os.environ)
        env.update({
            "EU4_FRAME_REPLAY_UV2_PROBE": "1",
            "EU4_FRAME_REPLAY_UV2_PROBE_EVENT": "1",
            "EU4_FRAME_REPLAY_UV2_PROBE_PROGRAM": "310",
            "EU4_FRAME_REPLAY_UV2_PROBE_FRAMEBUFFER": "6",
            "EU4_FRAME_REPLAY_UV2_PROBE_TEXTURE": "433",
            "EU4_FRAME_REPLAY_UV2_PROBE_DIR": str(metal / "uv2-probe")})
        ran = subprocess.run(
            [str(self.cli), "--frame", str(self.SEAL / "frame"),
             "--shaders", str(self.SHADERS), "--out", str(metal),
             "--checkpoints", str(request)],
            capture_output=True, text=True, check=False, timeout=300, env=env)
        self.assertEqual(ran.returncode, 0, ran.stderr)
        metal_dir = metal / "uv2-probe"
        metal_probe = json.loads((metal_dir / "uv2-probe.json").read_text())
        self.assertEqual(metal_probe["status"], "ok")

        trace, seal, _ = gfx_uv2_probe.frame_context(self.SEAL / "frame", 1)
        observation = gfx_uv2_probe.post_observation(trace, 1, "tex:433")
        reference = gfx_uv2_probe.observation_image(
            self.SEAL / "frame", observation)
        left = gfx_uv2_probe._image(
            reference["path"], reference["sha256"], 1728, 1117, "seal")
        right = gfx_uv2_probe._image(
            metal / "uv2-color.bgra",
            hashlib.sha256((metal / "uv2-color.bgra").read_bytes()).hexdigest(),
            1728, 1117, "metal")
        mask = gfx_uv2_probe._differences(left, right)
        self.assertEqual(len(mask), 23)

        gl_dir = Path(self.temporary.name) / "gl"
        gl_dir.mkdir()
        raw = bytearray((metal_dir / "uv2-probe.rgba32f").read_bytes())
        x, y = mask[0]
        offset = (y * 1728 + x) * 16
        bits = struct.unpack_from("<I", raw, offset)[0]
        struct.pack_into("<I", raw, offset, bits + 1)
        (gl_dir / "uv2-probe.rgba32f").write_bytes(bytes(raw))
        (gl_dir / "uv2-control.bgra").write_bytes(
            (self.SEAL / "frame" / "blobs" / observation["color"]["file"]).read_bytes())
        gl_probe = dict(metal_probe)
        gl_probe.pop("capture", None)
        gl_probe.pop("normal_target", None)
        gl_probe.update({
            "side": "opengl", "sealed": True, "pid": seal["pid"],
            "session_id": seal["session_id"],
            "capture_frame_id": seal["capture_frame_id"],
            "raw_bytes": len(raw),
            "raw_sha256": hashlib.sha256(bytes(raw)).hexdigest(),
            "control": {"file": "uv2-control.bgra", "pixel_format": "BGRA8",
                        "sha256": reference["sha256"]},
        })
        (gl_dir / "uv2-probe.json").write_text(json.dumps(gl_probe, indent=2))

        report = gfx_uv2_probe.run(argparse.Namespace(
            gl=str(gl_dir), metal=str(metal_dir),
            frame=str(self.SEAL / "frame"), neighborhood=1,
            out=str(Path(self.temporary.name) / "out")))
        self.assertEqual(report["summary"]["mask_pixels"], 23)
        self.assertTrue(report["summary"]["numerical_comparison_conclusive"])
        self.assertEqual(report["summary"]["max_ulp_difference_xy"], 1)
        self.assertTrue(report["identity"]["gl"]["match"])
        self.assertTrue(report["identity"]["metal"]["match"])
        self.assertTrue(report["identity"]["capture"]["match"])
        self.assertTrue(report["control"]["match"])
        self.assertTrue(report["conclusion"]["original_shader_control_match"])
        self.assertEqual(report["provenance"]["frame"]["authority"], "authentic")
        sidecar = report["provenance"]["gl_sidecar"]
        self.assertEqual(sidecar["kind"], "descriptor_self_asserted")
        self.assertFalse(sidecar["authenticated"])
        self.assertTrue(sidecar["identity_checks"]["pid"])
        self.assertFalse(
            report["conclusion"]["authenticated_original_gl_observation"])
        self.assertFalse(report["fragment_attribution"]["verified"])
        self.assertFalse(
            report["conclusion"]["fragment_attribution_verified"])
        self.assertEqual(report["conclusion"]["classification"],
                         "conditional_numerical_comparison")


if __name__ == "__main__":
    unittest.main()
