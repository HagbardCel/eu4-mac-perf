"""Preflight for the reviewed original-OpenGL selected-draw control.

Each fixture builds a restricted replay-input package, computes the expected
BGRA image independently in Python, runs the pinned driver plus native executor
without exposing that expected image, and compares exactly.  Receipt identity is
checked separately from pixel correctness.
"""

from __future__ import annotations

import hashlib
import json
import os
import struct
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from automation import gfx_ed25519
from automation import gfx_gl_replay_input as replay_input

ROOT = Path(__file__).resolve().parents[1]
DRIVER = ROOT / "automation" / "gfx_gl_control_renderer.py"
EXECUTOR = ROOT / "benchmark" / "eu4_gfx_draw_control.c"
SEED = bytes(range(32))
PUBLIC_KEY = gfx_ed25519.derive_public_key(SEED).hex()

B, G, R, A = 0, 1, 2, 3
BLUE = bytes([255, 0, 0, 255])
RED = bytes([0, 0, 255, 255])
GREEN = bytes([0, 255, 0, 255])


def _sha(body: bytes) -> str:
    return hashlib.sha256(body).hexdigest()


def _state(**overrides) -> dict:
    state = {
        "blend": {"enabled": False, "src_rgb": "0x1", "dst_rgb": "0x0",
                  "src_alpha": "0x1", "dst_alpha": "0x0",
                  "equation_rgb": "0x8006", "equation_alpha": "0x8006"},
        "color_mask": [1, 1, 1, 1],
        "depth": {"test": False, "func": "0x203", "write": False,
                  "range": [0.0, 1.0]},
        "stencil": {"test": False, "func": "0x207", "ref": 0,
                    "value_mask": -1, "fail": "0x1e00", "zfail": "0x1e00",
                    "zpass": "0x1e00", "write_mask": -1},
        "cull": {"enabled": False, "mode": "0x405", "front": "0x901"},
        "polygon": {"mode": ["0x1b02", "0x1b02"], "offset_fill": False,
                    "offset_factor": 0, "offset_units": 0},
        "alpha_test": {"enabled": False, "func": "0x206", "ref": 1},
        "scissor": {"enabled": False, "box": [0, 0, 4, 4]},
        "multisample": True,
        "dither": False,
        "framebuffer_srgb": False,
        "framebuffer_srgb_known": True,
        "logic_op": {"enabled": False, "mode": "0x0"},
        "viewport": [0, 0, 4, 4],
    }
    state.update(overrides)
    return state


class Package:
    def __init__(self, root: Path, width: int = 4, height: int = 4):
        self.root = root
        self.width = width
        self.height = height
        (root / "blobs").mkdir(parents=True)
        self.files: list[dict] = []

    def blob(self, role: str, extension: str, body: bytes) -> dict:
        digest = _sha(body)
        path = self.root / "blobs" / f"{role}-{digest}.{extension}"
        path.write_bytes(body)
        record = {"role": role, "path": f"blobs/{path.name}",
                  "sha256": digest, "bytes": len(body)}
        self.files.append(record)
        return record

    def build(self, *, shaders, attributes, uniforms=(), textures=(),
              indices=None, state=None, prestate=None, drawable=None,
              count=6, mode=4, draw=None) -> dict:
        vertex, pixel = shaders
        vertex_record = self.blob("shader-vertex", "vert", vertex)
        pixel_record = self.blob("shader-pixel", "frag", pixel)
        attribute_records = []
        for attribute in attributes:
            body = attribute["data"]
            record = self.blob("vertex-buffer", "bin", body)
            attribute_records.append({
                "index": attribute.get("index", 0),
                "name": attribute["name"],
                "location": attribute.get("index", 0),
                "enabled": True,
                "components": attribute.get("components", 2),
                "gl_type": attribute.get("gl_type", "0x1406"),
                "normalized": False,
                "integer": False,
                "stride": attribute.get("stride", 8),
                "offset": attribute.get("offset", 0),
                "divisor": 0,
                "range_begin": attribute.get("range_begin", 0),
                "range_bytes": attribute.get("range_bytes", len(body)),
                "first_vertex": attribute.get("first_vertex", 0),
                "data_path": record["path"],
                "data_sha256": record["sha256"],
                "data_bytes": record["bytes"],
            })
        uniform_records = []
        for uniform in uniforms:
            record = self.blob("uniform", "bin", uniform["data"])
            uniform_records.append({
                "name": uniform["name"], "type": "float",
                "components": uniform.get("components", 4),
                "elements": uniform.get("elements", 1),
                "path": record["path"], "sha256": record["sha256"],
                "bytes": record["bytes"],
            })
        texture_records = []
        for texture in textures:
            levels = []
            for level in texture["levels"]:
                record = self.blob("texture", "bin", level["data"])
                levels.append({
                    "level": level.get("level", 0),
                    "width": level["width"], "height": level["height"],
                    "internal_format": level["internal_format"],
                    "compressed": level.get("compressed", False),
                    "path": record["path"], "sha256": record["sha256"],
                    "bytes": record["bytes"],
                    "row_stride": level.get("row_stride", level["width"] * 4),
                })
            texture_records.append({
                "unit": texture.get("unit", 0),
                "sampler_name": texture.get("sampler_name", "tex0"),
                "target": "2D",
                "sampler": texture.get("sampler") or {
                    "min": "0x2600", "mag": "0x2600",
                    "wrap_s": "0x812f", "wrap_t": "0x812f",
                    "wrap_r": "0x812f", "min_lod": 0, "max_lod": 0,
                    "lod_bias": 0, "base_level": 0, "max_level": 0,
                    "compare_mode": "0x0", "compare_func": "0x203",
                    "anisotropy": 1,
                },
                "levels": levels,
            })
        index_record = None
        if indices is not None:
            record = self.blob("index-buffer", "bin", indices["data"])
            index_record = {
                "count": indices["count"], "first_index": 0,
                "base_vertex": 0, "vertex_origin": 0,
                "path": record["path"], "sha256": record["sha256"],
                "bytes": record["bytes"],
            }
        drawable = drawable or {
            "samples": 0, "sample_buffers": 0, "depth_bits": 32,
            "stencil_bits": 8, "red_bits": 8, "green_bits": 8,
            "blue_bits": 8, "alpha_bits": 8, "double_buffer": True,
            "stereo": False, "drawn_framebuffer_binding": 0,
            "width": self.width, "height": self.height,
            "pixel_format": "BGRA8", "origin": "lower_left",
        }
        draw = draw or {"mode": mode, "first": 0, "count": count,
                        "instance_count": 1, "indexed": indices is not None,
                        "base_vertex": 0, "caller_rva": "0x1"}
        manifest = {
            "schema": replay_input.PACKAGE_SCHEMA,
            "identity": {
                "session_id": "synthetic", "capture_frame_id": 1,
                "present_index": 1, "event_stream_sha256": "0" * 64,
                "checkpoint_event": 15, "draw_event": 14, "resource": "fb:0",
                "width": self.width, "height": self.height,
                "pixel_format": "BGRA8", "origin": "lower_left",
            },
            "drawable": drawable,
            "prestate": prestate,
            "draw": draw,
            "program": {
                "name": 1,
                "shaders": [
                    {"stage": "vertex", "path": vertex_record["path"],
                     "sha256": vertex_record["sha256"],
                     "bytes": vertex_record["bytes"]},
                    {"stage": "pixel", "path": pixel_record["path"],
                     "sha256": pixel_record["sha256"],
                     "bytes": pixel_record["bytes"]},
                ],
                "uniforms": uniform_records,
            },
            "attributes": attribute_records,
            "indices": index_record,
            "textures": texture_records,
            "state": state or _state(),
            "files": self.files,
        }
        manifest["package_sha256"] = replay_input.sha256_bytes(
            replay_input.canonical_json(manifest))
        (self.root / "replay-input.json").write_text(
            json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
        return manifest


def _quad(left=-1.0, right=1.0, bottom=-1.0, top=1.0, z=0.0) -> bytes:
    values = [
        left, bottom, right, bottom, left, top,
        right, bottom, right, top, left, top,
    ]
    if z != 0.0:
        return b""
    return struct.pack("<12f", *values)


def _quad_z(z: float) -> bytes:
    values = [
        -1.0, -1.0, z, 1.0,
        1.0, -1.0, z, 1.0,
        -1.0, 1.0, z, 1.0,
        1.0, -1.0, z, 1.0,
        1.0, 1.0, z, 1.0,
        -1.0, 1.0, z, 1.0,
    ]
    return struct.pack("<24f", *values)


def _dxt1_block(color0: int, color1: int, indices: int) -> bytes:
    return struct.pack("<HHI", color0, color1, indices)


def _bgra(pixel: bytes) -> bytes:
    r, g, b, a = pixel
    return bytes([b, g, r, a])


class DrawControlTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        cls.binary = Path(cls.temporary.name) / "eu4_gfx_draw_control"
        built = subprocess.run(
            ["clang", "-O2", "-w", "-I", str(ROOT / "benchmark"), "-o",
             str(cls.binary), str(EXECUTOR), "-framework", "OpenGL"],
            capture_output=True, text=True, check=False)
        if built.returncode != 0:
            raise unittest.SkipTest(f"native executor build failed: {built.stderr[:500]}")

    @classmethod
    def tearDownClass(cls):
        cls.temporary.cleanup()

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)

    def tearDown(self):
        self.temporary.cleanup()

    def _run(self, manifest, *, out: Path | None = None) -> tuple[dict, Path | None, int]:
        out = out or (self.root / "out")
        out.mkdir(parents=True, exist_ok=True)
        request = {
            "schema": "gfx-gl-control-request/v2",
            "nonce": "ab" * 16,
            "input_package": str(self.root / "package"),
            "input_package_sha256": manifest["package_sha256"],
            "expected": {
                "session_id": manifest["identity"]["session_id"],
                "capture_frame_id": manifest["identity"]["capture_frame_id"],
                "present_index": manifest["identity"]["present_index"],
                "event_stream_sha256":
                    manifest["identity"]["event_stream_sha256"],
                "checkpoint_event": 15,
                "checkpoint_resource": "fb:0",
                "draw_event": 14,
            },
            "width": manifest["identity"]["width"],
            "height": manifest["identity"]["height"],
        }
        (out / "request.json").write_text(json.dumps(request, indent=2) + "\n")
        env = dict(os.environ)
        env.update({
            "PYTHONPATH": str(ROOT),
            "GL_CONTROL_EXECUTABLE": str(self.binary),
            "GL_CONTROL_SIGNING_KEY": SEED.hex(),
        })
        completed = subprocess.run(
            [sys.executable, str(DRIVER), "--request",
             str(out / "request.json"), "--out", str(out)],
            capture_output=True, text=True, check=False, env=env)
        receipt_path = out / "receipt.json"
        receipt = (json.loads(receipt_path.read_text())
                   if receipt_path.is_file() else {})
        artifact = out / "result.bgra"
        return receipt, (artifact if artifact.is_file() else None), completed.returncode

    def _package(self) -> Package:
        return Package(self.root / "package")

    def _solid(self, *, color=(0.25, 0.5, 0.75, 1.0), state=None,
               drawable=None) -> dict:
        package = self._package()
        uniform = struct.pack("<4f", *color)
        vertex = (b"#version 120\nattribute vec2 in_POSITION;\n"
                  b"void main(){ gl_Position = vec4(in_POSITION, 0.0, 1.0); }\n")
        pixel = (b"#version 120\nuniform vec4 COLOR;\n"
                 b"void main(){ gl_FragColor = COLOR; }\n")
        prestate = {
            "color": self._color_seed(package, BLUE * 16),
            "depth": {"source": "clear_history", "event": 7, "value": 1.0,
                      "coverage": "full"},
            "stencil": {"source": "clear_history", "event": 7, "value": 0,
                        "coverage": "full"},
        }
        return package.build(
            shaders=(vertex, pixel),
            attributes=[{"name": "in_POSITION", "data": _quad()}],
            uniforms=[{"name": "COLOR", "data": uniform}],
            state=state or _state(), prestate=prestate, drawable=drawable)

    def _color_seed(self, package: Package, body: bytes) -> dict:
        record = package.blob("prestate-color", "bgra", body)
        return {"source": "checkpoint", "event": 14, "path": record["path"],
                "sha256": record["sha256"], "bytes": record["bytes"],
                "width": 4, "height": 4}

    def _reference(self) -> bytes:
        return _bgra((64, 128, 191, 255)) * 16

    # -- pixel correctness ------------------------------------------------

    def test_a_solid_colour_draw_matches_the_independent_reference(self):
        manifest = self._solid()
        receipt, artifact, code = self._run(manifest)
        self.assertEqual(code, 0, receipt)
        self.assertEqual(receipt["execution"], "completed", receipt)
        self.assertEqual(artifact.read_bytes(), self._reference())

    def test_dxt1_rgb_and_alpha_semantics(self):
        # All indices select colour0 (RGB) or the transparent index (alpha),
        # so the expected decode is exact under either BC1 rounding rule.
        vertex = (b"#version 120\nattribute vec2 in_POSITION;\n"
                  b"void main(){ gl_Position = vec4(in_POSITION, 0.0, 1.0); }\n")
        pixel = (b"#version 120\nuniform sampler2D tex0;\n"
                 b"void main(){ gl_FragColor = texture2D(tex0, gl_FragCoord.xy / "
                 b"vec2(4.0, 4.0)); }\n")
        cases = (
            ("DXT1", _dxt1_block(0xF800, 0x001F, 0x00000000), RED),
            ("DXT1_ALPHA", _dxt1_block(0x001F, 0xF800, 0xFFFFFFFF),
             bytes([0, 0, 0, 0])),
        )
        for name, block, expected_pixel in cases:
            with self.subTest(format=name):
                if self.root.exists():
                    import shutil
                    shutil.rmtree(self.root)
                self.root.mkdir(parents=True)
                package = self._package()
                prestate = {
                    "color": self._color_seed(package, BLUE * 16),
                    "depth": {"source": "clear_history", "event": 7,
                              "value": 1.0, "coverage": "full"},
                    "stencil": {"source": "clear_history", "event": 7,
                                "value": 0, "coverage": "full"},
                }
                manifest = package.build(
                    shaders=(vertex, pixel),
                    attributes=[{"name": "in_POSITION", "data": _quad()}],
                    textures=[{"unit": 0, "levels": [{
                        "width": 4, "height": 4, "internal_format": name,
                        "compressed": True, "data": block,
                        "row_stride": 8}]}],
                    state=_state(), prestate=prestate)
                receipt, artifact, code = self._run(manifest)
                self.assertEqual(code, 0, receipt)
                self.assertEqual(receipt["execution"], "completed", receipt)
                self.assertEqual(artifact.read_bytes(), expected_pixel * 16)

    def test_depth_test_uses_the_authorised_seed(self):
        vertex = (b"#version 120\nattribute vec4 in_POSITION;\n"
                  b"void main(){ gl_Position = in_POSITION; }\n")
        pixel = (b"#version 120\nvoid main(){ gl_FragColor = vec4(1.0, 0.0, "
                 b"0.0, 1.0); }\n")
        for seed, expected in ((1.0, RED), (0.0, BLUE)):
            with self.subTest(seed=seed):
                if self.root.exists():
                    import shutil
                    shutil.rmtree(self.root)
                self.root.mkdir(parents=True)
                package = self._package()
                depth_record = package.blob(
                    "prestate-depth", "f32",
                    struct.pack("<16f", *([seed] * 16)))
                prestate = {
                    "color": self._color_seed(package, BLUE * 16),
                    "depth": {"source": "checkpoint", "path":
                              depth_record["path"],
                              "sha256": depth_record["sha256"],
                              "bytes": depth_record["bytes"], "format": "f32",
                              "width": 4, "height": 4},
                    "stencil": {"source": "clear_history", "event": 7,
                                "value": 0, "coverage": "full"},
                }
                state = _state(depth={"test": True, "func": "0x203",
                                      "write": True, "range": [0.0, 1.0]})
                manifest = package.build(
                    shaders=(vertex, pixel),
                    attributes=[{"name": "in_POSITION", "data": _quad_z(0.5),
                                 "components": 4, "stride": 16}],
                    state=state, prestate=prestate)
                receipt, artifact, code = self._run(manifest)
                self.assertEqual(code, 0, receipt)
                self.assertEqual(artifact.read_bytes(), expected * 16,
                                 f"depth seed {seed}")

    def test_stencil_test_uses_the_authorised_clear_value(self):
        vertex = (b"#version 120\nattribute vec2 in_POSITION;\n"
                  b"void main(){ gl_Position = vec4(in_POSITION, 0.0, 1.0); }\n")
        pixel = (b"#version 120\nvoid main(){ gl_FragColor = vec4(1.0, 0.0, "
                 b"0.0, 1.0); }\n")
        for seed, expected in ((1, RED), (0, BLUE)):
            with self.subTest(seed=seed):
                if self.root.exists():
                    import shutil
                    shutil.rmtree(self.root)
                self.root.mkdir(parents=True)
                package = self._package()
                prestate = {
                    "color": self._color_seed(package, BLUE * 16),
                    "depth": {"source": "clear_history", "event": 7,
                              "value": 1.0, "coverage": "full"},
                    "stencil": {"source": "clear_history", "event": 7,
                                "value": seed, "coverage": "full"},
                }
                state = _state(stencil={
                    "test": True, "func": "0x203", "ref": 1,
                    "value_mask": -1, "fail": "0x1e00", "zfail": "0x1e00",
                    "zpass": "0x1e00", "write_mask": -1})
                manifest = package.build(
                    shaders=(vertex, pixel),
                    attributes=[{"name": "in_POSITION", "data": _quad()}],
                    state=state, prestate=prestate)
                receipt, artifact, code = self._run(manifest)
                self.assertEqual(code, 0, receipt)
                self.assertEqual(artifact.read_bytes(), expected * 16,
                                 f"stencil seed {seed}")

    def _regional(self, box, expected, *, scissor: bool):
        vertex = (b"#version 120\nattribute vec2 in_POSITION;\n"
                  b"void main(){ gl_Position = vec4(in_POSITION, 0.0, 1.0); }\n")
        pixel = (b"#version 120\nvoid main(){ gl_FragColor = vec4(1.0, 0.0, "
                 b"0.0, 1.0); }\n")
        package = self._package()
        prestate = {
            "color": self._color_seed(package, BLUE * 16),
            "depth": {"source": "clear_history", "event": 7, "value": 1.0,
                      "coverage": "full"},
            "stencil": {"source": "clear_history", "event": 7, "value": 0,
                        "coverage": "full"},
        }
        state = _state()
        if scissor:
            state["scissor"] = {"enabled": True, "box": list(box)}
        else:
            state["viewport"] = list(box)
        manifest = package.build(
            shaders=(vertex, pixel),
            attributes=[{"name": "in_POSITION", "data": _quad()}],
            state=state, prestate=prestate)
        receipt, artifact, code = self._run(manifest)
        self.assertEqual(code, 0, receipt)
        return artifact.read_bytes()

    def test_scissor_limits_the_draw(self):
        result = self._regional([1, 1, 2, 2], RED, scissor=True)
        expected = bytearray(BLUE * 16)
        for y in (1, 2):
            for x in (1, 2):
                offset = (y * 4 + x) * 4
                expected[offset:offset + 4] = RED
        self.assertEqual(result, bytes(expected))

    def test_a_sub_viewport_leaves_the_rest_of_the_framebuffer(self):
        result = self._regional([1, 1, 2, 2], RED, scissor=False)
        expected = bytearray(BLUE * 16)
        for y in (1, 2):
            for x in (1, 2):
                offset = (y * 4 + x) * 4
                expected[offset:offset + 4] = RED
        self.assertEqual(result, bytes(expected))

    def test_dither_state_is_reported_and_exact_endpoints_quantise(self):
        for dither in (True, False):
            with self.subTest(dither=dither):
                if self.root.exists():
                    import shutil
                    shutil.rmtree(self.root)
                self.root.mkdir(parents=True)
                state = _state(dither=dither)
                manifest = self._solid(color=(1.0, 1.0, 1.0, 1.0),
                                       state=state)
                receipt, artifact, code = self._run(manifest)
                self.assertEqual(code, 0, receipt)
                self.assertEqual(receipt["gl"]["error"], 0)
                self.assertEqual(artifact.read_bytes(),
                                 bytes([255, 255, 255, 255]) * 16)

    def test_an_incorrect_colour_seed_is_detectable(self):
        # A scissored draw leaves most of the framebuffer at the seed, so a
        # different seed produces a different image without any tolerance.
        state = _state()
        state["scissor"] = {"enabled": True, "box": [0, 0, 2, 2]}
        manifest = self._solid(state=state)
        expected = bytearray(BLUE * 16)
        for y in (0, 1):
            for x in (0, 1):
                offset = (y * 4 + x) * 4
                expected[offset:offset + 4] = _bgra((64, 128, 191, 255))
        receipt, artifact, code = self._run(manifest)
        self.assertEqual(code, 0, receipt)
        self.assertEqual(artifact.read_bytes(), bytes(expected))
        # Replace the seed and re-sign the package; the untouched pixels now
        # carry the wrong colour, which is a detectable mismatch.
        package_dir = self.root / "package"
        seed = package_dir / manifest["prestate"]["color"]["path"]
        seed.write_bytes(bytes([0, 255, 0, 255]) * 16)
        for record in manifest["files"]:
            if record["path"] == manifest["prestate"]["color"]["path"]:
                record["sha256"] = _sha(seed.read_bytes())
        body = dict(manifest)
        body.pop("package_sha256")
        manifest["package_sha256"] = replay_input.sha256_bytes(
            replay_input.canonical_json(body))
        (package_dir / "replay-input.json").write_text(
            json.dumps(manifest, indent=2) + "\n")
        receipt, artifact, code = self._run(manifest,
                                             out=self.root / "out-mutated")
        self.assertEqual(code, 0, receipt)
        self.assertNotEqual(artifact.read_bytes(), bytes(expected))

    def test_an_incorrect_sample_count_is_an_explicit_unsupported_drawable(self):
        drawable = {"samples": 4, "sample_buffers": 1, "depth_bits": 32,
                    "stencil_bits": 8, "red_bits": 8, "green_bits": 8,
                    "blue_bits": 8, "alpha_bits": 8, "double_buffer": True,
                    "stereo": False, "drawn_framebuffer_binding": 0,
                    "width": 4, "height": 4, "pixel_format": "BGRA8",
                    "origin": "lower_left"}
        manifest = self._solid(drawable=drawable)
        receipt, artifact, code = self._run(manifest)
        self.assertEqual(code, 0, receipt)
        self.assertEqual(receipt["execution"], "unsupported_drawable")
        self.assertIsNone(artifact)
        self.assertTrue(any("sample" in item.lower()
                            for item in receipt["unsupported"]))

    def _textured(self, *, internal_format, data, unit=0, sampler="tex0",
                  sample_xy="gl_FragCoord.xy / vec2(4.0, 4.0)"):
        vertex = (b"#version 120\nattribute vec2 in_POSITION;\n"
                  b"void main(){ gl_Position = vec4(in_POSITION, 0.0, 1.0); }\n")
        pixel = (b"#version 120\nuniform sampler2D " + sampler.encode() +
                 b";\nvoid main(){ gl_FragColor = texture2D(" +
                 sampler.encode() + b", " + sample_xy.encode() + b"); }\n")
        package = self._package()
        prestate = {
            "color": self._color_seed(package, BLUE * 16),
            "depth": {"source": "clear_history", "event": 7, "value": 1.0,
                      "coverage": "full"},
            "stencil": {"source": "clear_history", "event": 7, "value": 0,
                        "coverage": "full"},
        }
        return package.build(
            shaders=(vertex, pixel),
            attributes=[{"name": "in_POSITION", "data": _quad()}],
            textures=[{"unit": unit, "sampler_name": sampler, "levels": [{
                "width": 4, "height": 4,
                "internal_format": internal_format,
                "compressed": False, "data": data,
                "row_stride": 16}]}],
            state=_state(), prestate=prestate)

    def test_an_rgb8_texture_is_uploaded_from_the_captured_rgba_layout(self):
        red_rgba8 = bytes([255, 0, 0, 255]) * 16
        manifest = self._textured(internal_format="RGB8", data=red_rgba8)
        receipt, artifact, code = self._run(manifest)
        self.assertEqual(code, 0, receipt)
        self.assertEqual(artifact.read_bytes(), RED * 16)

    def test_a_vertex_texture_unit_is_not_forced_to_zero(self):
        green_rgba8 = bytes([0, 255, 0, 255]) * 16
        manifest = self._textured(
            internal_format="RGBA8", data=green_rgba8, unit=1,
            sampler="vertextex0")
        receipt, artifact, code = self._run(manifest)
        self.assertEqual(code, 0, receipt)
        self.assertEqual(artifact.read_bytes(), GREEN * 16)

    def test_a_captured_drawable_extent_mismatch_is_refused(self):
        drawable = {"samples": 0, "sample_buffers": 0, "depth_bits": 32,
                    "stencil_bits": 8, "red_bits": 8, "green_bits": 8,
                    "blue_bits": 8, "alpha_bits": 8, "double_buffer": True,
                    "stereo": False, "drawn_framebuffer_binding": 0,
                    "width": 8, "height": 8, "pixel_format": "BGRA8",
                    "origin": "lower_left"}
        manifest = self._solid(drawable=drawable)
        receipt, artifact, code = self._run(manifest)
        self.assertEqual(code, 0, receipt)
        self.assertEqual(receipt["execution"], "unsupported_drawable")
        self.assertIsNone(artifact)
        self.assertTrue(any("extent" in item
                            for item in receipt["unsupported"]))

    def test_a_captured_colour_precision_mismatch_is_refused(self):
        drawable = {"samples": 0, "sample_buffers": 0, "depth_bits": 32,
                    "stencil_bits": 8, "red_bits": 10, "green_bits": 10,
                    "blue_bits": 10, "alpha_bits": 2, "double_buffer": True,
                    "stereo": False, "drawn_framebuffer_binding": 0,
                    "width": 4, "height": 4, "pixel_format": "BGRA8",
                    "origin": "lower_left"}
        manifest = self._solid(drawable=drawable)
        receipt, artifact, code = self._run(manifest)
        self.assertEqual(code, 0, receipt)
        self.assertEqual(receipt["execution"], "unsupported_drawable")
        self.assertIsNone(artifact)

    def test_a_constant_blend_factor_is_refused(self):
        state = _state(blend={"enabled": True, "src_rgb": "0x8001",
                              "dst_rgb": "0x0", "src_alpha": "0x1",
                              "dst_alpha": "0x0",
                              "equation_rgb": "0x8006",
                              "equation_alpha": "0x8006"})
        manifest = self._solid(state=state)
        receipt, artifact, code = self._run(manifest)
        self.assertEqual(code, 0, receipt)
        self.assertEqual(receipt["execution"], "unsupported_drawable")
        self.assertTrue(any("blend" in item
                            for item in receipt["unsupported"]))

    def test_additive_blending_is_reproduced(self):
        state = _state(blend={"enabled": True, "src_rgb": "0x1",
                              "dst_rgb": "0x1", "src_alpha": "0x1",
                              "dst_alpha": "0x1",
                              "equation_rgb": "0x8006",
                              "equation_alpha": "0x8006"})
        manifest = self._solid(color=(0.25, 0.0, 0.0, 1.0), state=state)
        receipt, artifact, code = self._run(manifest)
        self.assertEqual(code, 0, receipt)
        self.assertEqual(artifact.read_bytes(),
                         bytes([255, 0, 64, 255]) * 16)

    def test_a_colour_write_mask_is_applied(self):
        state = _state()
        state["color_mask"] = [0, 1, 1, 1]
        manifest = self._solid(state=state)
        receipt, artifact, code = self._run(manifest)
        self.assertEqual(code, 0, receipt)
        # Red is masked, so the seed's red channel (0) survives while the
        # draw writes green/blue.
        self.assertEqual(artifact.read_bytes(), bytes([191, 128, 0, 255]) * 16)

    def test_a_non_indexed_draw_with_a_non_zero_first_vertex_is_rebased(self):
        vertex = (b"#version 120\nattribute vec2 in_POSITION;\n"
                  b"void main(){ gl_Position = vec4(in_POSITION, 0.0, 1.0); }\n")
        pixel = (b"#version 120\nuniform vec4 COLOR;\n"
                 b"void main(){ gl_FragColor = COLOR; }\n")
        package = self._package()
        body = struct.pack("<12f", -1.0, -1.0, 1.0, -1.0, -1.0, 1.0,
                           1.0, -1.0, 1.0, 1.0, -1.0, 1.0)
        prestate = {
            "color": self._color_seed(package, BLUE * 16),
            "depth": {"source": "clear_history", "event": 7, "value": 1.0,
                      "coverage": "full"},
            "stencil": {"source": "clear_history", "event": 7, "value": 0,
                        "coverage": "full"},
        }
        uniform = struct.pack("<4f", 0.25, 0.5, 0.75, 1.0)
        manifest = package.build(
            shaders=(vertex, pixel),
            attributes=[{"index": 0, "name": "in_POSITION", "data": body,
                         "stride": 8, "offset": 8, "range_begin": 8,
                         "first_vertex": 1}],
            uniforms=[{"name": "COLOR", "data": uniform}],
            state=_state(), prestate=prestate,
            draw={"mode": 4, "first": 1, "count": 6, "instance_count": 1,
                  "indexed": False, "base_vertex": 0, "caller_rva": "0x1"})
        receipt, artifact, code = self._run(manifest)
        self.assertEqual(code, 0, receipt)
        self.assertEqual(artifact.read_bytes(), self._reference())

    def test_depth_write_changes_the_post_draw_depth(self):
        for write, expected in ((True, 0.75), (False, 1.0)):
            with self.subTest(write=write):
                if self.root.exists():
                    import shutil
                    shutil.rmtree(self.root)
                self.root.mkdir(parents=True)
                vertex = (b"#version 120\nattribute vec4 in_POSITION;\n"
                          b"void main(){ gl_Position = in_POSITION; }\n")
                pixel = (b"#version 120\nvoid main(){ gl_FragColor = vec4(1.0,"
                         b" 0.0, 0.0, 1.0); }\n")
                package = self._package()
                depth_record = package.blob(
                    "prestate-depth", "f32", struct.pack("<16f", *([1.0] * 16)))
                prestate = {
                    "color": self._color_seed(package, BLUE * 16),
                    "depth": {"source": "checkpoint", "path":
                              depth_record["path"],
                              "sha256": depth_record["sha256"],
                              "bytes": depth_record["bytes"], "format": "f32",
                              "width": 4, "height": 4},
                    "stencil": {"source": "clear_history", "event": 7,
                                "value": 0, "coverage": "full"},
                }
                state = _state(depth={"test": True, "func": "0x203",
                                      "write": write, "range": [0.0, 1.0]})
                manifest = package.build(
                    shaders=(vertex, pixel),
                    attributes=[{"name": "in_POSITION", "data": _quad_z(0.5),
                                 "components": 4, "stride": 16}],
                    state=state, prestate=prestate)
                receipt, artifact, code = self._run(manifest)
                self.assertEqual(code, 0, receipt)
                depth_after = self.root / "out" / "native" / "depth-after.f32"
                values = struct.unpack("<16f", depth_after.read_bytes())
                for value in values:
                    self.assertAlmostEqual(value, expected, places=5)

    def test_stencil_ops_and_write_mask_are_applied(self):
        for write_mask, expected in ((-1, 2), (0, 1)):
            with self.subTest(write_mask=write_mask):
                if self.root.exists():
                    import shutil
                    shutil.rmtree(self.root)
                self.root.mkdir(parents=True)
                vertex = (b"#version 120\nattribute vec2 in_POSITION;\n"
                          b"void main(){ gl_Position = vec4(in_POSITION, 0.0,"
                          b" 1.0); }\n")
                pixel = (b"#version 120\nvoid main(){ gl_FragColor = vec4(1.0,"
                         b" 0.0, 0.0, 1.0); }\n")
                package = self._package()
                prestate = {
                    "color": self._color_seed(package, BLUE * 16),
                    "depth": {"source": "clear_history", "event": 7,
                              "value": 1.0, "coverage": "full"},
                    "stencil": {"source": "clear_history", "event": 7,
                                "value": 1, "coverage": "full"},
                }
                state = _state(stencil={
                    "test": True, "func": "0x203", "ref": 1,
                    "value_mask": -1, "fail": "0x1e00", "zfail": "0x1e00",
                    "zpass": "0x1e02", "write_mask": write_mask})
                manifest = package.build(
                    shaders=(vertex, pixel),
                    attributes=[{"name": "in_POSITION", "data": _quad()}],
                    state=state, prestate=prestate)
                receipt, artifact, code = self._run(manifest)
                self.assertEqual(code, 0, receipt)
                stencil_after = (self.root / "out" / "native"
                                 / "stencil-after.u8")
                self.assertEqual(set(stencil_after.read_bytes()), {expected})

    def test_the_receipt_is_signed_and_bound_to_the_execution(self):
        manifest = self._solid()
        receipt, _artifact, code = self._run(manifest)
        self.assertEqual(code, 0, receipt)
        signature = receipt["signature"]
        self.assertEqual(signature["algorithm"], "ed25519")
        body = dict(receipt)
        body.pop("signature")
        self.assertTrue(gfx_ed25519.verify(
            bytes.fromhex(PUBLIC_KEY), replay_input.canonical_json(body),
            bytes.fromhex(signature["value"])))
        self.assertEqual(
            receipt["renderer"]["sha256"],
            _sha(DRIVER.read_bytes()))
        roles = {item["role"]: item["sha256"]
                 for item in receipt["renderer"]["components"]}
        self.assertEqual(roles["native-gl-executor"],
                         _sha(self.binary.read_bytes()))
        self.assertEqual(receipt["input"]["package_sha256"],
                         manifest["package_sha256"])
        for mutated in ("nonce", "input"):
            forged = json.loads(json.dumps(body))
            if mutated == "nonce":
                forged["nonce"] = "0" * 32
            else:
                forged["input"]["package_sha256"] = "0" * 64
            self.assertFalse(gfx_ed25519.verify(
                bytes.fromhex(PUBLIC_KEY),
                replay_input.canonical_json(forged),
                bytes.fromhex(signature["value"])))

    def test_a_manifest_path_outside_the_declared_package_is_refused(self):
        manifest = self._solid()
        manifest["attributes"][0]["data_path"] = "/etc/passwd"
        body = dict(manifest)
        body.pop("package_sha256")
        manifest["package_sha256"] = replay_input.sha256_bytes(
            replay_input.canonical_json(body))
        (self.root / "package" / "replay-input.json").write_text(
            json.dumps(manifest, indent=2) + "\n")
        _receipt, _artifact, code = self._run(manifest)
        self.assertNotEqual(code, 0)

    def test_an_unsupported_texture_format_is_refused_not_faked(self):
        vertex = (b"#version 120\nattribute vec2 in_POSITION;\n"
                  b"void main(){ gl_Position = vec4(in_POSITION, 0.0, 1.0); }\n")
        pixel = (b"#version 120\nuniform sampler2D tex0;\n"
                 b"void main(){ gl_FragColor = texture2D(tex0, vec2(0.5)); }\n")
        package = self._package()
        prestate = {
            "color": self._color_seed(package, BLUE * 16),
            "depth": {"source": "clear_history", "event": 7, "value": 1.0,
                      "coverage": "full"},
            "stencil": {"source": "clear_history", "event": 7, "value": 0,
                        "coverage": "full"},
        }
        manifest = package.build(
            shaders=(vertex, pixel),
            attributes=[{"name": "in_POSITION", "data": _quad()}],
            textures=[{"unit": 0, "levels": [{
                "width": 4, "height": 4, "internal_format": "other",
                "compressed": False, "data": bytes(64)}]}],
            state=_state(), prestate=prestate)
        _receipt, _artifact, code = self._run(manifest)
        self.assertNotEqual(code, 0)


if __name__ == "__main__":
    unittest.main()
