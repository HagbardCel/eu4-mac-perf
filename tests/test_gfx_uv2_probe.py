"""Focused tests for the opt-in event-1 uv2 diagnostic probe.

Covers the four narrow contracts: output layout, event selection, readback
correctness and GL state restoration, plus the numerical comparison report.
No EU4 launch and no Metal game process is involved.
"""
from __future__ import annotations

import hashlib
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

WIDTH = 4
HEIGHT = 4
VERTEX_SHA = "v" * 64
FRAGMENT_SHA = "f" * 64
UNIFORM_SHA = "u" * 64
ATTRIBUTE_SHA = "a" * 64
INDEX_SHA = "i" * 64


def _probe_values() -> list[list[list[float]]]:
    return [
        [
            [(x + 0.5) / WIDTH, (y + 0.5) / HEIGHT, 0.0, 1.0]
            for x in range(WIDTH)
        ]
        for y in range(HEIGHT)
    ]


def _encode_values(values) -> bytes:
    body = bytearray()
    for row in values:
        for pixel in row:
            body.extend(struct.pack("<4f", *pixel))
    return bytes(body)


def _write_probe(
    root: Path,
    side: str,
    *,
    values=None,
    event: int = 1,
    program: int = 310,
    framebuffer: int = 6,
    texture: int = 433,
    control: bytes | None = None,
    uniform_sha: str = UNIFORM_SHA,
    attribute_sha: str = ATTRIBUTE_SHA,
    digest_ok: bool = True,
) -> dict:
    root.mkdir(parents=True, exist_ok=True)
    raw = _encode_values(values if values is not None else _probe_values())
    (root / "uv2-probe.rgba32f").write_bytes(raw)
    descriptor = {
        "schema": "gfx-uv2-probe/v1",
        "side": side,
        "status": "ok",
        "event": event,
        "program": program,
        "target": {"framebuffer": framebuffer, "texture": texture,
                   "width": WIDTH, "height": HEIGHT},
        "varying": "v_TEXCOORD1",
        "origin": "lower_left",
        "pixel_format": "RGBA32F",
        "width": WIDTH,
        "height": HEIGHT,
        "row_stride": WIDTH * 16,
        "raw_file": "uv2-probe.rgba32f",
        "raw_bytes": len(raw),
        "raw_sha256": hashlib.sha256(raw).hexdigest(),
        "sources": {"vertex_sha256": VERTEX_SHA,
                    "fragment_sha256": FRAGMENT_SHA},
        "attributes": [{"name": "in_POSITION", "location": 0,
                        "sha256": attribute_sha, "digest_ok": digest_ok}],
        "uniforms": [{"name": "UNIFORM0[0]", "sha256": uniform_sha},
                     {"name": "tex0", "kind": "sampler", "unit": 0}],
        "indices": {"count": 6, "sha256": INDEX_SHA},
        "draw": {"count": 6, "mode": 4, "indexed": True},
        "control": {"file": "uv2-control.bgra", "pixel_format": "BGRA8"},
    }
    if control is not None:
        (root / "uv2-control.bgra").write_bytes(control)
        descriptor["control"]["sha256"] = hashlib.sha256(control).hexdigest()
    (root / "uv2-probe.json").write_text(json.dumps(descriptor, indent=2))
    return descriptor


def _write_frame(root: Path, reference: bytes) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    (root / "blobs").mkdir(exist_ok=True)
    (root / "blobs" / "post.bin").write_bytes(reference)
    event = {
        "kind": "draw",
        "index": 1,
        "program": {
            "name": 310,
            "shaders": [
                {"stage": "vertex", "sha256": VERTEX_SHA},
                {"stage": "pixel", "sha256": FRAGMENT_SHA},
            ],
            "uniforms": [
                {"name": "UNIFORM0[0]", "type": "float", "sha256": UNIFORM_SHA},
                {"name": "tex0", "type": "sampler", "unit": 0},
            ],
        },
        "target": {
            "framebuffer": 6,
            "color_attachment": {"name": 433, "internal_format": "RGB8"},
            "width": WIDTH,
            "height": HEIGHT,
        },
        "draw": {"mode": 4, "count": 6, "indexed": True},
        "attributes": [{"name": "in_POSITION", "sha256": ATTRIBUTE_SHA}],
        "indices": {"count": 6, "sha256": INDEX_SHA},
    }
    lines = [
        {"kind": "clear", "before_first_draw": True},
        event,
    ]
    (root / "frame-events.jsonl").write_text(
        "\n".join(json.dumps(item) for item in lines) + "\n")
    observation = {
        "schema": "gfx-prefix-observation/v1",
        "event": 1,
        "timing": "post_event",
        "resource": "tex:433",
        "width": WIDTH,
        "height": HEIGHT,
        "pixel_format": "BGRA8",
        "origin": "lower_left",
        "color": {"file": "post.bin",
                  "sha256": hashlib.sha256(reference).hexdigest()},
    }
    (root / "prefix-observations.jsonl").write_text(json.dumps(observation) + "\n")
    return root


def _arguments(**overrides):
    import argparse

    values = {
        "gl": None,
        "metal": None,
        "frame": None,
        "mask_gl": None,
        "mask_metal": None,
        "mask_json": None,
        "neighborhood": 1,
        "out": None,
    }
    values.update(overrides)
    return argparse.Namespace(**values)


class ProbeLayoutTests(unittest.TestCase):
    def testValidDescriptorLoads(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp) / "probe"
            _write_probe(directory, "opengl")
            loaded = gfx_uv2_probe.load_probe(directory)
            self.assertEqual(loaded["width"], WIDTH)
            self.assertEqual(loaded["height"], HEIGHT)
            self.assertEqual(len(loaded["raw"]), WIDTH * HEIGHT * 16)

    def testRowStrideMismatchIsRejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp) / "probe"
            descriptor = _write_probe(directory, "opengl")
            descriptor["row_stride"] = WIDTH * 4
            (directory / "uv2-probe.json").write_text(json.dumps(descriptor))
            with self.assertRaises(gfx_uv2_probe.ProbeError):
                gfx_uv2_probe.load_probe(directory)

    def testPayloadDigestMismatchIsRejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp) / "probe"
            descriptor = _write_probe(directory, "opengl")
            descriptor["raw_sha256"] = "0" * 64
            (directory / "uv2-probe.json").write_text(json.dumps(descriptor))
            with self.assertRaises(gfx_uv2_probe.ProbeError):
                gfx_uv2_probe.load_probe(directory)

    def testFailedProbeIsRefused(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp) / "probe"
            descriptor = _write_probe(directory, "metal")
            descriptor["status"] = "identity_mismatch"
            (directory / "uv2-probe.json").write_text(json.dumps(descriptor))
            with self.assertRaises(gfx_uv2_probe.ProbeError):
                gfx_uv2_probe.load_probe(directory)


class MaskTests(unittest.TestCase):
    def _images(self, left: bytes, right: bytes):
        return (
            {"width": WIDTH, "height": HEIGHT, "body": left},
            {"width": WIDTH, "height": HEIGHT, "body": right},
        )

    def testRgbScopeIgnoresAlpha(self) -> None:
        left = bytes([1, 2, 3, 0]) * (WIDTH * HEIGHT)
        right = bytes([1, 2, 3, 255])
        right = right + bytes([1, 2, 3, 0]) * (WIDTH * HEIGHT - 1)
        pixels = gfx_uv2_probe.divergent_pixels(*self._images(left, right))
        self.assertEqual(pixels, [])

    def testDivergentCoordinates(self) -> None:
        left = bytes([10, 20, 30, 0]) * (WIDTH * HEIGHT)
        right = bytearray(left)
        right[5 * 4 + 1] = 21
        pixels = gfx_uv2_probe.divergent_pixels(
            *self._images(left, bytes(right)))
        self.assertEqual(len(pixels), 1)
        self.assertEqual((pixels[0]["x"], pixels[0]["y"]), (1, 1))
        self.assertEqual(pixels[0]["max_channel_error"], 1)


class ComparisonTests(unittest.TestCase):
    def _identity(self, match: bool = True):
        return {"match": match, "problems": [] if match else ["program"],
                "checks": {}, "observed": {}}

    def testRawBitsValuesAndUlps(self) -> None:
        values = _probe_values()
        original_bits = struct.unpack("<I", struct.pack("<f", values[1][1][0]))[0]
        values[1][1][0] = struct.unpack(
            "<f", struct.pack("<I", original_bits + 1))[0]
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            gl_dir = root / "gl"
            metal_dir = root / "metal"
            _write_probe(gl_dir, "opengl")
            _write_probe(metal_dir, "metal", values=values)
            gl = gfx_uv2_probe.load_probe(gl_dir)
            metal = gfx_uv2_probe.load_probe(metal_dir)
            mask = [{"x": 1, "y": 1}]
            report = gfx_uv2_probe.compare(
                gl,
                metal,
                gl_identity=self._identity(),
                metal_identity=self._identity(),
                control={"match": True},
                mask_pixels=mask,
                neighborhood=1,
            )
            center = [
                entry for entry in report["pixels"]
                if entry["x"] == 1 and entry["y"] == 1
            ][0]
            self.assertFalse(center["bit_equal"]["x"])
            self.assertEqual(center["ulp_differences"]["x"], 1)
            self.assertGreater(center["signed_differences"]["x"], 0.0)
            self.assertTrue(center["bit_equal"]["y"])
            self.assertEqual(report["summary"]["max_ulp_difference_xy"], 1)
            self.assertEqual(len(report["pixels"]), 9)
            self.assertFalse(report["summary"]["bit_for_bit_equal_xy"])

    def testBitExactComparison(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            gl_dir = root / "gl"
            metal_dir = root / "metal"
            _write_probe(gl_dir, "opengl")
            _write_probe(metal_dir, "metal")
            report = gfx_uv2_probe.compare(
                gfx_uv2_probe.load_probe(gl_dir),
                gfx_uv2_probe.load_probe(metal_dir),
                gl_identity=self._identity(),
                metal_identity=self._identity(),
                control={"match": True},
                mask_pixels=[{"x": 2, "y": 2}],
                neighborhood=0,
            )
            self.assertEqual(len(report["pixels"]), 1)
            self.assertTrue(report["pixels"][0]["all_components_bit_equal"])
            self.assertTrue(report["summary"]["bit_for_bit_equal_xy"])


class ControlTests(unittest.TestCase):
    def testExactControlMatches(self) -> None:
        body = bytes([1, 2, 3, 255]) * (WIDTH * HEIGHT)
        image = {"width": WIDTH, "height": HEIGHT, "body": body,
                 "path": "c", "sha256": "0", "pixel_format": "BGRA8",
                 "origin": "lower_left"}
        self.assertTrue(
            gfx_uv2_probe.control_correspondence(image, image)["match"])

    def testOneLsbControlMismatchIsRecorded(self) -> None:
        left = bytes([1, 2, 3, 255]) * (WIDTH * HEIGHT)
        right = bytearray(left)
        right[2 * 4] = 2
        control = {"width": WIDTH, "height": HEIGHT, "body": left,
                   "path": "c", "sha256": "0", "pixel_format": "BGRA8",
                   "origin": "lower_left"}
        reference = dict(control, body=bytes(right))
        result = gfx_uv2_probe.control_correspondence(control, reference)
        self.assertFalse(result["match"])
        self.assertEqual(result["mismatched_pixels"], 1)
        self.assertEqual(result["max_channel_error"], 1)


class IdentityTests(unittest.TestCase):
    def _frame_and_probe(self, root: Path, **overrides):
        reference = bytes([0, 0, 0, 255]) * (WIDTH * HEIGHT)
        frame = _write_frame(root / "frame", reference)
        probe_dir = root / "probe"
        _write_probe(probe_dir, "opengl", **overrides)
        probe = gfx_uv2_probe.load_probe(probe_dir)
        event = gfx_uv2_probe._load_event(frame, 1)
        return probe, event

    def testMatchingInputs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            probe, event = self._frame_and_probe(Path(tmp))
            identity = gfx_uv2_probe.input_identity(probe, event)
            self.assertTrue(identity["match"])
            self.assertEqual(identity["problems"], [])

    def testProgramMismatch(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            probe, event = self._frame_and_probe(Path(tmp), program=999)
            identity = gfx_uv2_probe.input_identity(probe, event)
            self.assertFalse(identity["match"])
            self.assertIn("program", identity["problems"])

    def testUniformMismatch(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            probe, event = self._frame_and_probe(Path(tmp), uniform_sha="0" * 64)
            identity = gfx_uv2_probe.input_identity(probe, event)
            self.assertFalse(identity["match"])
            self.assertIn("uniforms", identity["problems"])

    def testFailedAttributeDigestIsReported(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            probe, event = self._frame_and_probe(Path(tmp), digest_ok=False)
            identity = gfx_uv2_probe.input_identity(probe, event)
            self.assertFalse(identity["match"])
            self.assertIn("attributes", identity["problems"])


class CommandTests(unittest.TestCase):
    def testEndToEndComparisonWritesReport(self) -> None:
        reference = bytes([7, 8, 9, 255]) * (WIDTH * HEIGHT)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            frame = _write_frame(root / "frame", reference)
            gl_dir = root / "gl"
            metal_dir = root / "metal"
            _write_probe(gl_dir, "opengl", control=reference)
            _write_probe(metal_dir, "metal")
            mask_gl = root / "mask-gl.bgra"
            mask_metal = root / "mask-metal.bgra"
            mask_gl.write_bytes(reference)
            altered = bytearray(reference)
            altered[3 * 4 + 2] += 1
            mask_metal.write_bytes(bytes(altered))
            out = root / "out"
            with redirect_stdout(io.StringIO()):
                code = gfx_uv2_probe.main(
                    ["compare", "--gl", str(gl_dir), "--metal", str(metal_dir),
                     "--frame", str(frame), "--mask-gl", str(mask_gl),
                     "--mask-metal", str(mask_metal), "--out", str(out)])
            self.assertEqual(code, 0)
            report = json.loads((out / "comparison.json").read_text())
            self.assertEqual(report["schema"], gfx_uv2_probe.COMPARISON_SCHEMA)
            self.assertEqual(report["summary"]["mask_pixels"], 1)
            self.assertTrue(report["conclusion"]["input_identity_match"])
            self.assertTrue(report["conclusion"]["original_shader_control_match"])
            self.assertIn("pixels", report)


class CaptureWiringTests(unittest.TestCase):
    def testProbeEnvironmentIsForwarded(self) -> None:
        import eu4_gfx_startup_trace as startup

        with tempfile.TemporaryDirectory() as temporary:
            output = str(Path(temporary) / "capture")
            args = m6_frame._parser().parse_args(
                ["capture", "--output", output, "--prefix-end", "1",
                 "--uv2-probe", "--uv2-probe-event", "1"])
            seen = {}

            def fake_run_ops(**kwargs):
                seen.update(kwargs)
                raise RuntimeError("stop after environment capture")

            with mock.patch.object(m6_frame.campaign_guard, "from_env",
                                   return_value=None), \
                 mock.patch.object(startup, "preflight_ops",
                                   return_value={}), \
                 mock.patch.object(startup, "run_ops",
                                   side_effect=fake_run_ops):
                status = m6_frame.capture(args)
            self.assertEqual(status, 4)
            env = seen["env_extra"]
            self.assertEqual(env["EU4_GFX_UV2_PROBE"], "1")
            self.assertEqual(env["EU4_GFX_UV2_PROBE_EVENT"], "1")
            self.assertEqual(env["EU4_GFX_UV2_PROBE_PROGRAM"], "310")
            self.assertEqual(env["EU4_GFX_UV2_PROBE_FRAMEBUFFER"], "6")
            self.assertEqual(env["EU4_GFX_UV2_PROBE_TEXTURE"], "433")
            self.assertTrue(
                env["EU4_GFX_UV2_PROBE_DIR"].endswith("/uv2-probe"))

    def testProbeRequiresPrefixObservation(self) -> None:
        import eu4_gfx_startup_trace as startup

        with tempfile.TemporaryDirectory() as temporary:
            output = str(Path(temporary) / "capture")
            args = m6_frame._parser().parse_args(
                ["capture", "--output", output, "--uv2-probe"])
            with mock.patch.object(m6_frame.campaign_guard, "from_env",
                                   return_value=None), \
                 mock.patch.object(startup, "preflight_ops",
                                   return_value={}):
                stderr = io.StringIO()
                with mock.patch.object(sys, "stderr", stderr):
                    status = m6_frame.capture(args)
            self.assertEqual(status, 2)
            self.assertIn("--prefix-end", stderr.getvalue())

    def testReplayEnvironmentSelection(self) -> None:
        args = m6_frame._parser().parse_args(
            ["replay", "--frame", "frame", "--out", "out",
             "--uv2-probe", "--uv2-probe-event", "3"])
        env = m6_frame.uv2_probe_replay_env(Path("out"), args)
        self.assertEqual(env["EU4_FRAME_REPLAY_UV2_PROBE"], "1")
        self.assertEqual(env["EU4_FRAME_REPLAY_UV2_PROBE_EVENT"], "3")
        self.assertEqual(env["EU4_FRAME_REPLAY_UV2_PROBE_PROGRAM"], "310")
        self.assertEqual(
            env["EU4_FRAME_REPLAY_UV2_PROBE_DIR"], str(Path("out") /
                                                      "uv2-probe"))
        negative = m6_frame._parser().parse_args(
            ["replay", "--frame", "frame", "--out", "out",
             "--uv2-probe", "--uv2-probe-event", "-1"])
        self.assertIsNone(m6_frame.uv2_probe_replay_env(Path("out"),
                                                        negative))
        disabled = m6_frame._parser().parse_args(
            ["replay", "--frame", "frame", "--out", "out"])
        self.assertNotIn("EU4_FRAME_REPLAY_UV2_PROBE",
                         m6_frame.uv2_probe_replay_env(Path("out"), disabled))


@unittest.skipUnless(sys.platform == "darwin", "the native probe host is macOS")
class NativeProbeHostTests(unittest.TestCase):
    def testReadbackLayoutControlAndStateRestoration(self) -> None:
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
            for marker in ("probe=1", "layout=1", "readback=1",
                           "control=1", "restore=1", "selection=1"):
                self.assertIn(marker, ran.stdout)


@unittest.skipUnless(sys.platform == "darwin", "the Metal replay is macOS")
class MetalProbeTests(unittest.TestCase):
    FIXTURE = ROOT / ".local" / "m6" / "frames" / "menu-occ300"

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

    def _replay(self, output: Path, probe: bool) -> subprocess.CompletedProcess:
        env = dict(os.environ)
        env.pop("EU4_FRAME_REPLAY_UV2_PROBE", None)
        if probe:
            env.update({
                "EU4_FRAME_REPLAY_UV2_PROBE": "1",
                "EU4_FRAME_REPLAY_UV2_PROBE_EVENT": "1",
                "EU4_FRAME_REPLAY_UV2_PROBE_PROGRAM": "310",
                "EU4_FRAME_REPLAY_UV2_PROBE_FRAMEBUFFER": "6",
                "EU4_FRAME_REPLAY_UV2_PROBE_TEXTURE": "433",
                "EU4_FRAME_REPLAY_UV2_PROBE_DIR": str(output / "uv2-probe"),
            })
        return subprocess.run(
            [str(self.cli), "--frame", str(self.FIXTURE / "frame"),
             "--shaders", str(self.FIXTURE / "translated"),
             "--out", str(output)],
            capture_output=True, text=True, check=False, timeout=300,
            env=env)

    def testProbeLayoutAndNormalPassUnchanged(self) -> None:
        base = Path(self.temporary.name) / "baseline"
        probe = Path(self.temporary.name) / "probe"
        base_run = self._replay(base, probe=False)
        self.assertEqual(base_run.returncode, 0, base_run.stderr)
        probe_run = self._replay(probe, probe=True)
        self.assertEqual(probe_run.returncode, 0, probe_run.stderr)
        base_frame = (base / "metal-frame.bgra").read_bytes()
        probe_frame = (probe / "metal-frame.bgra").read_bytes()
        self.assertEqual(base_frame, probe_frame)
        replay = json.loads((probe / "replay.json").read_text())
        self.assertEqual(replay["status"], "ok")
        self.assertEqual(replay["uv2_probe"]["status"], "ok")
        descriptor = json.loads(
            (probe / "uv2-probe" / "uv2-probe.json").read_text())
        self.assertEqual(descriptor["schema"], "gfx-uv2-probe/v1")
        self.assertEqual(descriptor["side"], "metal")
        self.assertEqual(descriptor["event"], 1)
        self.assertEqual(descriptor["program"], 310)
        self.assertEqual(descriptor["pixel_format"], "RGBA32F")
        self.assertEqual(descriptor["origin"], "lower_left")
        self.assertEqual(descriptor["row_stride"], 1728 * 16)
        raw = (probe / "uv2-probe" / "uv2-probe.rgba32f").read_bytes()
        self.assertEqual(len(raw), 1728 * 1117 * 16)
        self.assertEqual(descriptor["raw_bytes"], len(raw))
        # The probe reuses the normal pass's compiled vertex stage.
        self.assertEqual(descriptor["sources"]["vertex_sha256"],
                         "2e8842a26b765c70f06594103eff681f8a149a49966c34b25d8648058f7a4b98")
        self.assertEqual(descriptor["varying_location"], 1)

    def testNonSelectedEventWritesNoProbe(self) -> None:
        output = Path(self.temporary.name) / "skipped"
        env = dict(os.environ)
        env.update({
            "EU4_FRAME_REPLAY_UV2_PROBE": "1",
            "EU4_FRAME_REPLAY_UV2_PROBE_EVENT": "0",
            "EU4_FRAME_REPLAY_UV2_PROBE_DIR": str(output / "uv2-probe"),
        })
        ran = subprocess.run(
            [str(self.cli), "--frame", str(self.FIXTURE / "frame"),
             "--shaders", str(self.FIXTURE / "translated"),
             "--out", str(output)],
            capture_output=True, text=True, check=False, timeout=300,
            env=env)
        self.assertEqual(ran.returncode, 0, ran.stderr)
        self.assertFalse((output / "uv2-probe" / "uv2-probe.json").exists())
        replay = json.loads((output / "replay.json").read_text())
        self.assertEqual(replay["uv2_probe"]["status"], "skipped")


if __name__ == "__main__":
    unittest.main()
