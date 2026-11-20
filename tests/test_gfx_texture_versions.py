"""Captured texture versions and per-draw Metal consumption.

Two complementary regressions:

* the native capture host records a real GL texture mutation inside one frame
  and asserts the first draw keeps version A while the second draw carries the
  new version B (and an unchanged texture stays cached);
* the production Metal replay host consumes two different captured payloads of
  the same texture name in one frame and reads back the two rendered versions.

Only the existing trace and replay implementations are exercised.
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
BENCH = ROOT / "benchmark"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from automation import m6_frame, native_build  # noqa: E402

VERSION_A = bytes((index * 3) & 0xFF for index in range(64))
VERSION_B = bytes((255 - index * 3) & 0xFF for index in range(64))

VERT = """#include <metal_stdlib>
using namespace metal;
struct VOut { float4 position [[position]]; float2 uv; };
vertex VOut vmain(uint vid [[vertex_id]]) {
    float2 p[3] = { float2(-1,-1), float2(3,-1), float2(-1,3) };
    VOut o; o.position = float4(p[vid], 0.0, 1.0);
    o.uv = (p[vid] + 1.0) * 0.5; return o;
}
"""
FRAG = """#include <metal_stdlib>
using namespace metal;
struct VOut { float4 position [[position]]; float2 uv; };
struct FOut { float4 color [[color(0)]]; };
fragment FOut fsample(VOut in [[stage_in]], texture2d<float> tex0 [[texture(0)]],
                      sampler s0 [[sampler(0)]]) {
    return FOut{tex0.sample(s0, in.uv)};
}
"""


def sha(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


@unittest.skipUnless(sys.platform == "darwin", "native capture is macOS")
class CapturedTextureVersionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        cls.binary = Path(cls.temporary.name) / "gfx_texture_mutation_host"
        sources = [ROOT / "tests" / "gfx_texture_mutation_host.m",
                   BENCH / "eu4_gfx_frame_trace.c"]
        native_build.require_prerequisites(
            sources=sources, frameworks=("AppKit", "OpenGL"))
        native_build.build(
            [
                "clang", "-O1", "-Wall", "-Wextra", "-I", str(BENCH),
                "-o", str(cls.binary),
                *map(str, sources),
                "-framework", "AppKit", "-framework", "OpenGL",
            ],
            label="texture-mutation-host")

    @classmethod
    def tearDownClass(cls):
        cls.temporary.cleanup()

    def capture(self, scenario: str) -> tuple[dict, list[dict], Path]:
        directory = Path(self.temporary.name) / scenario
        directory.mkdir(parents=True, exist_ok=True)
        ran = subprocess.run(
            [str(self.binary), scenario, str(directory)],
            capture_output=True, text=True, check=False, timeout=120,
        )
        self.assertEqual(ran.returncode, 0, ran.stderr or ran.stdout)
        manifest = json.loads((directory / "frame-trace.json").read_text())
        events = [
            json.loads(line)
            for line in (directory / "frame-events.jsonl").read_text().splitlines()
            if line.strip()
        ]
        return manifest, events, directory

    @staticmethod
    def levels(event: dict) -> list[str]:
        return [
            level["sha256"]
            for texture in event.get("textures") or []
            for level in texture.get("levels") or []
        ]

    def test_subimage_mutation_captures_both_versions(self):
        manifest, events, directory = self.capture("subimage")
        self.assertTrue(manifest["complete"], manifest)
        self.assertTrue(manifest["stream_validated"], manifest)
        self.assertEqual([event["kind"] for event in events], ["draw", "draw"])
        self.assertEqual(self.levels(events[0]), [sha(VERSION_A)])
        self.assertEqual(self.levels(events[1]), [sha(VERSION_B)])
        self.assertNotEqual(self.levels(events[0]), self.levels(events[1]))
        self.assertEqual(manifest["texture_cache"]["invalidations"], 1)
        seal = json.loads((directory / "frame-seal.json").read_text())
        self.assertTrue(seal["sealed"], seal)
        self.assertEqual(len(seal["event_stream_sha256"]), 64)
        self.assertEqual(seal["capture_frame_id"], 1)

    def test_reallocation_captures_both_versions(self):
        manifest, events, _ = self.capture("realloc")
        self.assertTrue(manifest["complete"], manifest)
        self.assertEqual(self.levels(events[0]), [sha(VERSION_A)])
        self.assertEqual(self.levels(events[1]), [sha(VERSION_B)])

    def test_reused_name_does_not_keep_the_deleted_version(self):
        manifest, events, _ = self.capture("name-reuse")
        self.assertTrue(manifest["complete"], manifest)
        self.assertEqual(self.levels(events[0]), [sha(VERSION_A)])
        self.assertEqual(self.levels(events[1]), [sha(VERSION_B)])

    def test_update_before_the_first_sample_is_authoritative(self):
        manifest, events, _ = self.capture("presample")
        self.assertTrue(manifest["complete"], manifest)
        self.assertEqual(len(events), 1)
        self.assertEqual(self.levels(events[0]), [sha(VERSION_B)])

    def test_unchanged_texture_keeps_the_cached_payload(self):
        manifest, events, _ = self.capture("unchanged")
        self.assertTrue(manifest["complete"], manifest)
        self.assertEqual(self.levels(events[0]), [sha(VERSION_A)])
        self.assertEqual(self.levels(events[1]), [sha(VERSION_A)])
        self.assertEqual(manifest["texture_cache"]["invalidations"], 0)
        self.assertGreaterEqual(manifest["texture_cache"]["hits"], 1)


@unittest.skipUnless(sys.platform == "darwin", "Metal replay is macOS")
class MetalTextureVersionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        cls.base = Path(cls.temporary.name)
        cls.shaders = cls.base / "shaders"
        cls.shaders.mkdir()
        (cls.shaders / "v.metal").write_text(VERT)
        (cls.shaders / "f.metal").write_text(FRAG)
        stage = {
            "msl_file": "v.metal", "msl_entry": "vmain",
            "msl_uniform_buffer": None, "msl_samplers": [],
            "attributes": [], "uniforms": [],
        }
        pixel = {
            "msl_file": "f.metal", "msl_entry": "fsample",
            "msl_uniform_buffer": None,
            "msl_samplers": [{
                "kind": "texture2d", "name": "tex0",
                "texture_index": 0, "sampler_index": 0}],
            "attributes": [], "uniforms": [],
        }
        (cls.shaders / "shaders.json").write_text(json.dumps({
            "programs": [{"program": 1, "status": "ok",
                          "stages": {"vertex": stage, "pixel": pixel}}]}))
        native_build.require_prerequisites(
            frameworks=("Foundation", "Metal"))
        cls.host = m6_frame.build_replay_host()

    @classmethod
    def tearDownClass(cls):
        cls.temporary.cleanup()

    def sample(self, name: str, payload: bytes) -> dict:
        return {
            "name": 1, "sampler_name": name, "unit": 0,
            "width": 4, "height": 4, "internal_format": "RGBA8",
            "levels": [{
                "level": 0, "width": 4, "height": 4,
                "pixel_format": "RGBA8", "sha256": sha(payload),
                "file": f"{name}.bin",
            }],
        }

    def target(self, name: str) -> dict:
        return {
            "framebuffer": 1 if name == "10" else 2,
            "width": 4, "height": 4,
            "color_attachment": {
                "type": "texture", "name": name, "level": 0,
                "internal_format": "RGBA8", "width": 4, "height": 4,
            },
        }

    def draw(self, index: int, target: dict, payload: bytes) -> dict:
        return {
            "kind": "draw", "index": index,
            "draw": {"entry": "_GfxDraw", "mode": 4, "count": 3,
                     "base_vertex": 0, "instance_count": 1},
            "state": {
                "viewport": [0, 0, 4, 4],
                "depth": {"test": False, "write": False, "func": "0x0201"},
                "blend": {"enabled": False},
                "cull": {"enabled": False},
            },
            "program": {"name": 1, "uniforms": []},
            "attributes": [],
            "textures": [self.sample("tex0", payload)],
            "target": target,
        }

    def test_two_payloads_of_one_name_render_two_versions(self):
        base = self.base / "versions"
        frame = base / "frame"
        (frame / "blobs").mkdir(parents=True)
        out = base / "out"
        out.mkdir()
        (frame / "blobs" / "tex0-a.bin").write_bytes(VERSION_A)
        (frame / "blobs" / "tex0-b.bin").write_bytes(VERSION_B)
        first = self.draw(0, self.target("10"), VERSION_A)
        first["textures"][0]["levels"][0]["file"] = "tex0-a.bin"
        second = self.draw(1, self.target("11"), VERSION_B)
        second["textures"][0]["levels"][0]["file"] = "tex0-b.bin"
        # A trailing default-target clear creates an event boundary after both
        # target draws; checkpoints are evaluated at that boundary.
        events = [first, second, {
            "kind": "clear", "flags": 1, "color": [0, 0, 0, 1],
            "depth": 1, "stencil": 0,
            "target": {"framebuffer": 0, "width": 4, "height": 4},
        }]
        reference = bytes(64)
        (frame / "frame-events.jsonl").write_text(
            "\n".join(json.dumps(event) for event in events) + "\n")
        (frame / "frame-reference.bgra").write_bytes(reference)
        (frame / "frame-trace.json").write_text(json.dumps({
            "schema": "gfx-frame-trace/v1", "complete": True,
            "reason": "scene_present", "events": len(events),
            "frame": {"width": 4, "height": 4, "origin": "lower_left",
                      "pixel_format": "BGRA8", "row_stride": 16,
                      "reference": "frame-reference.bgra",
                      "reference_bytes": len(reference),
                      "reference_sha256": sha(reference)},
            "seed": {"width": 4, "height": 4},
        }))
        request = base / "checkpoints.json"
        request.write_text(json.dumps({"checkpoints": [
            {"event": 2, "target": "10", "file": "version-a.rgba",
             "reference_sha256": sha(VERSION_A)},
            {"event": 2, "target": "11", "file": "version-b.rgba",
             "reference_sha256": sha(VERSION_B)},
        ]}))
        ran = subprocess.run(
            [str(self.host), "--frame", str(frame), "--shaders",
             str(self.shaders), "--out", str(out),
             "--checkpoints", str(request)],
            capture_output=True, text=True, check=False, timeout=300,
        )
        self.assertEqual(ran.returncode, 0, ran.stderr[-1000:])
        report = json.loads((out / "replay.json").read_text())
        self.assertEqual(report["failed"], 0, report.get("failures"))
        version_a = (out / "version-a.rgba").read_bytes()
        version_b = (out / "version-b.rgba").read_bytes()
        self.assertEqual(len(version_a), 64)
        self.assertEqual(len(version_b), 64)
        self.assertNotEqual(version_a, version_b)
        self.assertEqual(sorted(version_a), sorted(VERSION_A))
        self.assertEqual(sorted(version_b), sorted(VERSION_B))
        self.assertEqual([item["generation"] for item in report["checkpoints"]],
                         [1, 1])


if __name__ == "__main__":
    unittest.main()
