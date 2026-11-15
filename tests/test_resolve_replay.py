"""Native resolve/copy and clear replay regressions (asymmetric 4x4 frames).

These tests execute the production Metal replay host and assert actual pixels,
generations and checkpoint contents.  No EU4 launch is involved.
"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from automation import m6_frame  # noqa: E402

WIDTH = HEIGHT = 4
CLEAR_DEST = (0.0, 0.5, 0.0, 0.5)

VERT = '''#include <metal_stdlib>
using namespace metal;
struct VOut { float4 position [[position]]; float2 uv; };
vertex VOut vmain(uint vid [[vertex_id]]) {
    float2 p[3] = { float2(-1,-1), float2(3,-1), float2(-1,3) };
    VOut o; o.position = float4(p[vid], 0.0, 1.0);
    o.uv = (p[vid] + 1.0) * 0.5; return o;
}
vertex VOut vdepth05(uint vid [[vertex_id]]) {
    float2 p[3] = { float2(-1,-1), float2(3,-1), float2(-1,3) };
    VOut o; o.position = float4(p[vid], 0.5, 1.0);
    o.uv = (p[vid] + 1.0) * 0.5; return o;
}
vertex VOut vdepth075(uint vid [[vertex_id]]) {
    float2 p[3] = { float2(-1,-1), float2(3,-1), float2(-1,3) };
    VOut o; o.position = float4(p[vid], 0.75, 1.0);
    o.uv = (p[vid] + 1.0) * 0.5; return o;
}
'''
# The copy/clear test uses an exact 0/1 palette derived from the GL row, so a
# row-order error changes actual bytes rather than merely the encoding path.
FRAG_PATTERN = '''#include <metal_stdlib>
using namespace metal;
struct FOut { float4 color [[color(0)]]; };
fragment FOut fpattern(float4 pos [[position]]) {
    int x = int(pos.x);
    int glrow = 3 - int(pos.y);
    float r = (x == 0 || x == 3) ? 1.0 : 0.0;
    float g = (glrow == 0 || glrow == 3) ? 1.0 : 0.0;
    float b = (x == glrow) ? 1.0 : 0.0;
    return FOut{float4(r, g, b, 1.0)};
}
'''
FRAG_SAMPLE = '''#include <metal_stdlib>
using namespace metal;
struct VOut { float4 position [[position]]; float2 uv; };
struct FOut { float4 color [[color(0)]]; };
fragment FOut fsample(VOut in [[stage_in]], texture2d<float> tex0 [[texture(0)]],
                      sampler s0 [[sampler(0)]]) {
    return FOut{tex0.sample(s0, in.uv)};
}
'''
FRAG_RED = '''#include <metal_stdlib>
using namespace metal;
struct FOut { float4 color [[color(0)]]; };
fragment FOut fred() { return FOut{float4(1.0, 0.0, 0.0, 1.0)}; }
'''
FRAG_GREEN = '''#include <metal_stdlib>
using namespace metal;
struct FOut { float4 color [[color(0)]]; };
fragment FOut fgreen() { return FOut{float4(0.0, 1.0, 0.0, 1.0)}; }
'''
FRAG_BLUE = '''#include <metal_stdlib>
using namespace metal;
struct FOut { float4 color [[color(0)]]; };
fragment FOut fblue() { return FOut{float4(0.0, 0.0, 1.0, 1.0)}; }
'''


def byte(value: float) -> int:
    return int(round(value * 255.0))


def rgba(color: tuple[float, float, float, float]) -> bytes:
    return bytes(byte(value) for value in color)


def bgra(color: tuple[float, float, float, float]) -> bytes:
    r, g, b, a = color
    return bytes([byte(b), byte(g), byte(r), byte(a)])


def source_color(x: int, glrow: int) -> tuple[float, float, float, float]:
    return (
        1.0 if x in (0, 3) else 0.0,
        1.0 if glrow in (0, 3) else 0.0,
        1.0 if x == glrow else 0.0,
        1.0,
    )


def dest_gl_image() -> bytes:
    rows = []
    for glrow in range(HEIGHT):
        row = bytearray()
        for x in range(WIDTH):
            row += rgba(CLEAR_DEST)
        rows.append(row)
    for k in range(2):
        glrow = 1 + k
        for j in range(2):
            x = 1 + j
            rows[glrow][x * 4:(x + 1) * 4] = rgba(source_color(x, glrow))
    return b"".join(rows)


def source_gl_image() -> bytes:
    rows = []
    for glrow in range(HEIGHT):
        row = bytearray()
        for x in range(WIDTH):
            row += rgba(source_color(x, glrow))
        rows.append(row)
    return b"".join(rows)


def manifest(events: list[dict], reference: bytes) -> dict:
    return {
        "schema": "gfx-frame-trace/v1",
        "complete": True,
        "reason": "scene_present",
        "events": len(events),
        "frame": {
            "width": WIDTH, "height": HEIGHT, "origin": "lower_left",
            "pixel_format": "BGRA8", "row_stride": WIDTH * 4,
            "reference": "frame-reference.bgra",
            "reference_bytes": len(reference),
            "reference_sha256": hashlib.sha256(reference).hexdigest(),
        },
        "seed": {"width": WIDTH, "height": HEIGHT},
    }


@unittest.skipUnless(sys.platform == "darwin", "Metal replay host")
class ResolveReplayTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        cls.base = Path(cls.temporary.name)
        cls.shaders = cls.base / "shaders"
        cls.shaders.mkdir()

        def stage(file, entry, samplers=None):
            return {
                "msl_file": file,
                "msl_entry": entry,
                "msl_uniform_buffer": None,
                "msl_samplers": samplers or [],
                "attributes": [],
                "uniforms": [],
            }

        programs = [
            {"program": 1, "status": "ok", "stages": {
                "vertex": stage("v.metal", "vmain"),
                "pixel": stage("f_pattern.metal", "fpattern")}},
            {"program": 2, "status": "ok", "stages": {
                "vertex": stage("v.metal", "vmain"),
                "pixel": stage("f_sample.metal", "fsample", [{
                    "kind": "texture2d", "name": "tex0",
                    "texture_index": 0, "sampler_index": 0}])}},
            {"program": 3, "status": "ok", "stages": {
                "vertex": stage("v.metal", "vdepth05"),
                "pixel": stage("f_red.metal", "fred")}},
            {"program": 4, "status": "ok", "stages": {
                "vertex": stage("v.metal", "vdepth075"),
                "pixel": stage("f_blue.metal", "fblue")}},
            {"program": 5, "status": "ok", "stages": {
                "vertex": stage("v.metal", "vmain"),
                "pixel": stage("f_green.metal", "fgreen")}},
            {"program": 6, "status": "ok", "stages": {
                "vertex": stage("v.metal", "vdepth075"),
                "pixel": stage("f_green.metal", "fgreen")}},
        ]
        (cls.shaders / "shaders.json").write_text(json.dumps({"programs": programs}))
        (cls.shaders / "v.metal").write_text(VERT)
        (cls.shaders / "f_pattern.metal").write_text(FRAG_PATTERN)
        (cls.shaders / "f_sample.metal").write_text(FRAG_SAMPLE)
        (cls.shaders / "f_red.metal").write_text(FRAG_RED)
        (cls.shaders / "f_green.metal").write_text(FRAG_GREEN)
        (cls.shaders / "f_blue.metal").write_text(FRAG_BLUE)
        cls.host = m6_frame.build_replay_host()

    @classmethod
    def tearDownClass(cls):
        cls.temporary.cleanup()

    def run_frame(self, name, events, reference, checkpoints=None, env=None):
        base = self.base / name
        frame = base / "frame"
        (frame / "blobs").mkdir(parents=True)
        out = base / "out"
        out.mkdir()
        (frame / "frame-events.jsonl").write_text(
            "\n".join(json.dumps(event) for event in events) + "\n")
        (frame / "frame-reference.bgra").write_bytes(reference)
        (frame / "frame-trace.json").write_text(json.dumps(manifest(events, reference)))
        command = [
            str(self.host), "--frame", str(frame),
            "--shaders", str(self.shaders), "--out", str(out),
        ]
        if checkpoints:
            request = base / "checkpoints.json"
            request.write_text(json.dumps({"checkpoints": checkpoints}))
            command += ["--checkpoints", str(request)]
        runs = dict(env or {})
        ran = subprocess.run(command, capture_output=True, text=True,
                             check=False, env={**os.environ, **runs})
        report = None
        if (out / "replay.json").is_file():
            report = json.loads((out / "replay.json").read_text())
        return ran, report, out

    def default_target(self):
        return {"framebuffer": 0, "width": WIDTH, "height": HEIGHT}

    def rt_target(self, name, depth=False):
        target = {
            "framebuffer": 1 if name == "10" else 2,
            "width": WIDTH, "height": HEIGHT,
            "color_attachment": {
                "type": "texture", "name": name, "level": 0,
                "internal_format": "RGBA8", "width": WIDTH, "height": HEIGHT,
            },
        }
        if depth:
            target["depth_attachment"] = {
                "internal_format": "DEPTH24_STENCIL8",
                "width": WIDTH, "height": HEIGHT,
            }
        return target

    def clear(self, target, flags, color=(0, 0, 0, 1), depth=1, stencil=0):
        return {
            "kind": "clear", "flags": flags, "color": list(color),
            "depth": depth, "stencil": stencil, "target": target,
        }

    def draw(self, index, program, target=None, textures=None,
             depth_test=False, depth_write=False):
        return {
            "kind": "draw", "index": index,
            "draw": {"entry": "_GfxDraw", "mode": 4, "count": 3,
                     "base_vertex": 0, "instance_count": 1},
            "state": {
                "viewport": [0, 0, WIDTH, HEIGHT],
                "depth": {"test": depth_test, "write": depth_write,
                          "func": "0x0201"},
                "blend": {"enabled": False},
                "cull": {"enabled": False},
            },
            "program": {"name": program, "uniforms": []},
            "attributes": [], "textures": textures or [],
            "target": target or self.default_target(),
        }

    def copy(self, destination, *, source="0", source_framebuffer=0,
             read_buffer=0x0405, source_level=0, destination_level=0,
             rect=(0, 0, WIDTH, HEIGHT), offset=(0, 0),
             operation="glCopyTexSubImage2D", source_resolved=True,
             destination_resolved=True):
        return {
            "kind": "resolve", "operation": operation,
            "is_blit": False,
            "source_framebuffer": source_framebuffer,
            "read_buffer": read_buffer,
            "draw_buffer": read_buffer,
            "double_buffered": 1,
            "source": source, "source_level": source_level,
            "source_attachment": read_buffer if source_framebuffer else 0,
            "source_resolved": source_resolved,
            "source_width": WIDTH, "source_height": HEIGHT,
            "source_internal_format": 0x8058,
            "destination": destination, "destination_level": destination_level,
            "destination_resolved": destination_resolved,
            "source_rect": list(rect), "destination_offset": list(offset),
            "width": rect[2], "height": rect[3],
            "destination_width": WIDTH, "destination_height": HEIGHT,
            "internal_format": 0x8058,
        }

    def test_partial_copy_preserves_destination_and_row_order(self):
        reference = dest_gl_image()
        events = [
            self.clear(self.rt_target("10"), 1, CLEAR_DEST),
            self.clear(self.default_target(), 7),
            self.draw(1, 1),
            self.copy("10", rect=(1, 1, 2, 2), offset=(1, 1)),
            self.draw(2, 2, textures=[{
                "name": 10, "sampler_name": "tex0", "unit": 0,
                "width": WIDTH, "height": HEIGHT,
                "internal_format": "RGBA8", "levels": []}]),
        ]
        checkpoints = [{
            "event": 4, "target": "10", "file": "dest-after-copy.rgba",
            "reference_sha256": hashlib.sha256(dest_gl_image()).hexdigest(),
        }]
        ran, report, out = self.run_frame(
            "partial", events, reference, checkpoints)
        self.assertEqual(ran.returncode, 0, ran.stderr)
        self.assertEqual(report["failed"], 0, report.get("failures"))
        self.assertEqual((out / "dest-after-copy.rgba").read_bytes(),
                         dest_gl_image())
        # The final image is the sampled GL-oriented destination.
        expected = bytearray()
        for glrow in range(HEIGHT):
            for x in range(WIDTH):
                offset = (glrow * WIDTH + x) * 4
                rgba_pixel = dest_gl_image()[offset:offset + 4]
                expected += bytes([rgba_pixel[2], rgba_pixel[1],
                                   rgba_pixel[0], rgba_pixel[3]])
        self.assertEqual((out / "metal-frame.bgra").read_bytes(), bytes(expected))
        checkpoint = report["checkpoints"][0]
        self.assertEqual(checkpoint["generation"], 2)

    def test_complete_copy_and_top_bottom_orientation(self):
        reference = source_gl_image()
        events = [
            self.clear(self.default_target(), 7),
            self.draw(1, 1),
            self.copy("10", rect=(0, 0, WIDTH, HEIGHT), offset=(0, 0)),
            self.draw(2, 2, textures=[{
                "name": 10, "sampler_name": "tex0", "unit": 0,
                "width": WIDTH, "height": HEIGHT,
                "internal_format": "RGBA8", "levels": []}]),
        ]
        checkpoints = [{
            "event": 3, "target": "10", "file": "dest-full.rgba",
            "reference_sha256": hashlib.sha256(source_gl_image()).hexdigest(),
        }]
        ran, report, out = self.run_frame(
            "complete", events, reference, checkpoints)
        self.assertEqual(ran.returncode, 0, ran.stderr)
        self.assertEqual(report["failed"], 0, report.get("failures"))
        self.assertEqual((out / "dest-full.rgba").read_bytes(),
                         source_gl_image())
        expected = bytearray()
        for glrow in range(HEIGHT):
            for x in range(WIDTH):
                expected += bgra(source_color(x, glrow))
        self.assertEqual((out / "metal-frame.bgra").read_bytes(), bytes(expected))
        # A row-order defect would swap the visually distinct first and last
        # rows (both copied here).
        expected_top = bgra(source_color(0, HEIGHT - 1))
        expected_bottom = bgra(source_color(0, 0))
        self.assertNotEqual(expected_top, expected_bottom)
        self.assertEqual(bytes(expected[:4]), expected_bottom)

    def test_missing_copy_metadata_is_rejected(self):
        event = self.copy("10")
        event.pop("source_width")
        events = [
            self.clear(self.default_target(), 7),
            self.draw(1, 1),
            event,
        ]
        ran, report, _ = self.run_frame("missing-metadata", events, bytes(64))
        self.assertNotEqual(ran.returncode, 0)
        self.assertEqual(report["failed"], 1, report.get("failures"))
        self.assertIn("missing source_width", report["failures"][0]["error"])

    def test_unknown_copy_variant_is_rejected(self):
        events = [
            self.clear(self.default_target(), 7),
            self.draw(1, 1),
            self.copy("10", operation="glBlitFramebuffer"),
        ]
        ran, report, _ = self.run_frame("variant", events, bytes(4 * 4 * 4))
        self.assertNotEqual(ran.returncode, 0)
        self.assertEqual(report["failed"], 1, report.get("failures"))
        self.assertIn("unsupported resolve/copy variant",
                      report["failures"][0]["error"])

    def test_unsupported_mip_level_is_rejected(self):
        for field, value in (("destination_level", 1), ("source_level", 1)):
            with self.subTest(field=field):
                events = [
                    self.clear(self.default_target(), 7),
                    self.draw(1, 1),
                    self.copy("10", **{field: value}),
                ]
                ran, report, _ = self.run_frame(
                    f"mip-{field}", events, bytes(4 * 4 * 4))
                self.assertNotEqual(ran.returncode, 0)
                self.assertEqual(report["failed"], 1, report.get("failures"))

    def test_missing_or_incorrect_source_is_rejected(self):
        events = [
            self.clear(self.default_target(), 7),
            self.draw(1, 1),
            self.copy("10", source="999", source_framebuffer=1),
        ]
        ran, report, _ = self.run_frame("missing-source", events, bytes(64))
        self.assertNotEqual(ran.returncode, 0)
        self.assertEqual(report["failed"], 1, report.get("failures"))

        events = [
            self.clear(self.default_target(), 7),
            self.draw(1, 1),
            self.copy("10", read_buffer=0x0404),
        ]
        ran, report, _ = self.run_frame("front-buffer", events, bytes(64))
        self.assertNotEqual(ran.returncode, 0)
        self.assertEqual(report["failed"], 1, report.get("failures"))

    def test_invalid_rectangles_are_rejected(self):
        for rect in ((0, 0, 0, 1), (3, 3, 2, 2), (-1, 0, 2, 2)):
            with self.subTest(rect=rect):
                events = [
                    self.clear(self.default_target(), 7),
                    self.draw(1, 1),
                    self.copy("10", rect=rect),
                ]
                ran, report, _ = self.run_frame(
                    f"rect-{rect[0]}-{rect[2]}", events, bytes(64))
                self.assertNotEqual(ran.returncode, 0)
                self.assertEqual(report["failed"], 1, report.get("failures"))

    def test_command_buffer_failure_is_reported(self):
        events = [
            self.clear(self.default_target(), 7),
            self.draw(1, 1),
            self.copy("10"),
        ]
        ran, report, _ = self.run_frame(
            "command-failure", events, bytes(64),
            env={"EU4_FRAME_REPLAY_COPY_FAULT": "command"})
        self.assertNotEqual(ran.returncode, 0)
        self.assertEqual(report["failed"], 1, report.get("failures"))
        self.assertIn("resolve copy command failed",
                      report["failures"][0]["error"])

    def test_color_only_clear_preserves_depth(self):
        events = [
            self.clear(self.default_target(), 7),
            self.draw(1, 3, depth_test=True, depth_write=True),
            self.clear(self.default_target(), 1, (0, 1, 0, 1)),
            self.draw(2, 4, depth_test=True, depth_write=True),
        ]
        expected = bgra((0, 1, 0, 1)) * (WIDTH * HEIGHT)
        ran, report, out = self.run_frame("clear-color", events, expected)
        self.assertEqual(ran.returncode, 0, ran.stderr)
        self.assertEqual((out / "metal-frame.bgra").read_bytes(), expected)

    def test_depth_only_clear_preserves_color(self):
        events = [
            self.clear(self.default_target(), 7),
            self.draw(1, 3, depth_test=True, depth_write=True),
            self.clear(self.default_target(), 2),
        ]
        expected = bgra((1, 0, 0, 1)) * (WIDTH * HEIGHT)
        ran, report, out = self.run_frame("clear-depth", events, expected)
        self.assertEqual(ran.returncode, 0, ran.stderr)
        self.assertEqual((out / "metal-frame.bgra").read_bytes(), expected)

    def test_stencil_only_clear_preserves_color_and_depth(self):
        events = [
            self.clear(self.default_target(), 7),
            self.draw(1, 3, depth_test=True, depth_write=True),
            self.clear(self.default_target(), 4),
            self.draw(2, 4, depth_test=True, depth_write=True),
        ]
        expected = bgra((1, 0, 0, 1)) * (WIDTH * HEIGHT)
        ran, report, out = self.run_frame("clear-stencil", events, expected)
        self.assertEqual(ran.returncode, 0, ran.stderr)
        self.assertEqual((out / "metal-frame.bgra").read_bytes(), expected)

    def test_combined_clear_clears_color_and_depth(self):
        events = [
            self.clear(self.default_target(), 7),
            self.draw(1, 3, depth_test=True, depth_write=True),
            self.clear(self.default_target(), 7, (0, 1, 0, 1)),
            self.draw(2, 4, depth_test=True, depth_write=True),
        ]
        expected = bgra((0, 0, 1, 1)) * (WIDTH * HEIGHT)
        ran, report, out = self.run_frame("clear-combined", events, expected)
        self.assertEqual(ran.returncode, 0, ran.stderr)
        self.assertEqual((out / "metal-frame.bgra").read_bytes(), expected)

    def test_depth_only_clear_does_not_advance_color_generation(self):
        events = [
            self.clear(self.default_target(), 7),
            self.clear(self.rt_target("10", depth=True), 1, CLEAR_DEST),
            self.clear(self.rt_target("10", depth=True), 2),
            self.draw(1, 5),
        ]
        checkpoints = [{
            "event": 3, "target": "10", "file": "color-after-depth.rgba",
            "reference_sha256": hashlib.sha256(rgba(CLEAR_DEST)).hexdigest(),
        }]
        ran, report, out = self.run_frame(
            "clear-generation", events,
            bgra((0, 1, 0, 1)) * (WIDTH * HEIGHT), checkpoints)
        self.assertEqual(ran.returncode, 0, ran.stderr)
        self.assertEqual(report["failed"], 0, report.get("failures"))
        checkpoint = report["checkpoints"][0]
        self.assertEqual(checkpoint["generation"], 1, checkpoint)
        self.assertEqual((out / "color-after-depth.rgba").read_bytes(),
                         rgba(CLEAR_DEST) * (WIDTH * HEIGHT))


if __name__ == "__main__":
    unittest.main()
