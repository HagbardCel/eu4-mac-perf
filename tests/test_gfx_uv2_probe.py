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

from automation import gfx_uv2_probe, m6_frame, native_build  # noqa: E402

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
                 draw: dict | None = None) -> dict:
    root.mkdir(parents=True, exist_ok=True)
    raw = _raw(values if values is not None else _values())
    (root / "uv2-probe.rgba32f").write_bytes(raw)
    descriptor = {
        "schema": "gfx-uv2-probe/v1", "side": side, "status": "ok",
        "sealed": sealed, "session_id": session, "capture_frame_id": frame,
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
            "manifest_sha256": "m" * 64, "reference_sha256": "r" * 64}


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
    def testBothProbesMustNameTheSealedFrame(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            gl_dir, metal_dir = Path(tmp) / "gl", Path(tmp) / "metal"
            _write_probe(gl_dir, "opengl")
            _write_probe(metal_dir, "metal")
            seal = _seal()
            binding = gfx_uv2_probe.capture_binding(
                gfx_uv2_probe.load_probe(gl_dir),
                gfx_uv2_probe.load_probe(metal_dir), seal)
            self.assertTrue(binding["match"])

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

    def testMatchingEventInputs(self) -> None:
        self.assertTrue(self._identity()["match"])

    def testAllDrawParametersAreValidated(self) -> None:
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

    def testUniformAttributeAndDigestFailures(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            descriptor = _write_probe(Path(tmp) / "p", "opengl")
            descriptor["attributes"][0]["digest_ok"] = False
            (Path(tmp) / "p" / "uv2-probe.json").write_text(
                json.dumps(descriptor))
            identity = gfx_uv2_probe.input_identity(
                gfx_uv2_probe.load_probe(Path(tmp) / "p"), _event())
            self.assertIn("attributes", identity["problems"])


class ComparisonTests(unittest.TestCase):
    def _probe(self, root: Path, side: str, values):
        _write_probe(root, side, values=values)
        return gfx_uv2_probe.load_probe(root)

    def _identity(self):
        return {"gl": {"match": True, "problems": [], "checks": {},
                       "observed": {}},
                "metal": {"match": True, "problems": [], "checks": {},
                          "observed": {}},
                "capture": {"match": True, "problems": []}}

    def testRawBitsValuesUlpsAndBitEquality(self):
        metal = _values()
        original = struct.unpack("<I", struct.pack("<f", metal[1][1][0]))[0]
        gl = _values()
        gl[1][1][0] = struct.unpack("<f", struct.pack("<I", original + 1))[0]
        with tempfile.TemporaryDirectory() as tmp:
            report = gfx_uv2_probe.compare(
                self._probe(Path(tmp) / "gl", "opengl", gl),
                self._probe(Path(tmp) / "metal", "metal", metal),
                identity=self._identity(), control={"match": True},
                mask=[(1, 1)], neighborhood=1)
        center = [e for e in report["pixels"]
                  if (e["x"], e["y"]) == (1, 1)][0]
        self.assertFalse(center["bit_equal"]["x"])
        self.assertEqual(center["ulp_differences"]["x"], 1)
        self.assertNotEqual(center["signed_differences"]["x"], 0.0)
        self.assertEqual(report["summary"]["max_ulp_difference_xy"], 1)
        self.assertEqual(len(report["pixels"]), 9)
        self.assertFalse(report["summary"]["bit_for_bit_equal_xy"])

    def testZeroCoverageIsInconclusiveNotEquivalent(self):
        with tempfile.TemporaryDirectory() as tmp:
            report = gfx_uv2_probe.compare(
                self._probe(Path(tmp) / "gl", "opengl", _values(False)),
                self._probe(Path(tmp) / "metal", "metal", _values(False)),
                identity=self._identity(), control={"match": True},
                mask=[(0, 0), (1, 1)], neighborhood=0)
        self.assertEqual(report["summary"]["mask_pixels_covered_by_both"], 0)
        self.assertFalse(report["summary"]["conclusive"])
        self.assertIsNone(report["summary"]["bit_for_bit_equal_xy"])
        self.assertIsNone(report["conclusion"]["xy_bit_for_bit_equal"])
        self.assertFalse(report["conclusion"]["conclusive"])

    def testEmptyMaskIsInconclusive(self):
        with tempfile.TemporaryDirectory() as tmp:
            report = gfx_uv2_probe.compare(
                self._probe(Path(tmp) / "gl", "opengl", _values()),
                self._probe(Path(tmp) / "metal", "metal", _values()),
                identity=self._identity(), control={"match": True},
                mask=[], neighborhood=1)
        self.assertEqual(report["summary"]["mask_pixels"], 0)
        self.assertFalse(report["summary"]["conclusive"])
        self.assertIsNone(report["summary"]["bit_for_bit_equal_xy"])


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
            for marker in ("probe=1", "layout=1", "readback=1", "control=1",
                           "restore=1"):
                self.assertIn(marker, ran.stdout)
            descriptor = json.loads(
                (output / "uv2-probe.json").read_text())
            self.assertEqual(descriptor["schema"], "gfx-uv2-probe/v1")
            self.assertEqual(descriptor["draw"]["entry"], "_GfxDrawIndexed")
            self.assertTrue(all(a["digest_ok"]
                                for a in descriptor["attributes"]))


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

    def testBoundComparisonReproducesTheEvent1Divergence(self):
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
            "side": "opengl", "sealed": True,
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
        self.assertTrue(report["summary"]["conclusive"])
        self.assertEqual(report["summary"]["max_ulp_difference_xy"], 1)
        self.assertTrue(report["identity"]["gl"]["match"])
        self.assertTrue(report["identity"]["metal"]["match"])
        self.assertTrue(report["identity"]["capture"]["match"])
        self.assertTrue(report["control"]["match"])
        self.assertTrue(report["conclusion"]["original_shader_control_match"])


if __name__ == "__main__":
    unittest.main()
