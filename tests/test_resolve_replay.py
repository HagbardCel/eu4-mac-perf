"""Native resolve/copy replay regression (synthetic tiny frame)."""
from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from automation import m6_frame  # noqa: E402

VERT = '''#include <metal_stdlib>
using namespace metal;
struct VOut { float4 position [[position]]; };
vertex VOut vmain(uint vid [[vertex_id]]) {
    float2 p[3] = { float2(-1,-1), float2(3,-1), float2(-1,3) };
    VOut o; o.position = float4(p[vid], 0.0, 1.0); return o;
}
'''
FRAG_RED = '''#include <metal_stdlib>
using namespace metal;
struct FOut { float4 color [[color(0)]]; };
fragment FOut fmain() { return FOut{float4(1.0,0.0,0.0,1.0)}; }
'''
FRAG_SAMPLE = '''#include <metal_stdlib>
using namespace metal;
struct FOut { float4 color [[color(0)]]; };
fragment FOut fmain(texture2d<float> tex0 [[texture(0)]], sampler s0 [[sampler(0)]]) {
    return FOut{tex0.sample(s0, float2(0.5,0.5))};
}
'''


@unittest.skipUnless(sys.platform == "darwin", "Metal replay host")
class ResolveReplayTests(unittest.TestCase):
    def test_copy_event_is_executed_and_sampled(self):
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            frame = base / "frame"
            (frame / "blobs").mkdir(parents=True)
            shaders = base / "shaders"
            shaders.mkdir()
            (shaders / "v.metal").write_text(VERT)
            (shaders / "f1.metal").write_text(FRAG_RED)
            (shaders / "f2.metal").write_text(FRAG_SAMPLE)

            def stage(file, entry, samplers=None):
                return {"msl_file": file, "msl_entry": entry,
                        "msl_uniform_buffer": None,
                        "msl_samplers": samplers or [], "attributes": [],
                        "uniforms": []}

            programs = [
                {"program": 1, "status": "ok", "stages": {
                    "vertex": stage("v.metal", "vmain"),
                    "pixel": stage("f1.metal", "fmain")}},
                {"program": 2, "status": "ok", "stages": {
                    "vertex": stage("v.metal", "vmain"),
                    "pixel": stage("f2.metal", "fmain", [{
                        "kind": "texture2d", "name": "tex0",
                        "texture_index": 0, "sampler_index": 0}])}},
            ]
            (shaders / "shaders.json").write_text(
                json.dumps({"programs": programs}))

            def draw(index, program, textures=None):
                return {
                    "kind": "draw", "index": index,
                    "draw": {"entry": "_GfxDraw", "mode": 4, "count": 3,
                             "base_vertex": 0, "instance_count": 1},
                    "state": {"viewport": [0, 0, 4, 4],
                              "depth": {"test": False, "write": False},
                              "blend": {"enabled": False},
                              "cull": {"enabled": False}},
                    "program": {"name": program, "uniforms": []},
                    "attributes": [], "textures": textures or [],
                    "target": {"framebuffer": 0, "width": 4, "height": 4},
                }

            events = [
                {"kind": "clear", "flags": 7, "color": [0, 0, 0, 1],
                 "depth": 1, "stencil": 0, "before_first_draw": True,
                 "target": {"framebuffer": 0, "width": 4, "height": 4}},
                draw(1, 1),
                {"kind": "resolve", "operation": "glCopyTexSubImage2D",
                 "source_framebuffer": 0, "source": "0", "source_level": 0,
                 "destination": "10", "destination_level": 0,
                 "source_rect": [0, 0, 4, 4],
                 "destination_offset": [0, 0], "width": 4, "height": 4,
                 "destination_width": 4, "destination_height": 4,
                 "internal_format": 0x8058},
                draw(2, 2, [{"name": 10, "sampler_name": "tex0", "unit": 0,
                             "width": 4, "height": 4,
                             "internal_format": "RGBA8", "levels": []}]),
            ]
            (frame / "frame-events.jsonl").write_text(
                "\n".join(json.dumps(event) for event in events) + "\n")
            red = bytes([0, 0, 255, 255]) * 16
            (frame / "frame-reference.bgra").write_bytes(red)
            (frame / "frame-trace.json").write_text(json.dumps({
                "schema": "gfx-frame-trace/v1", "complete": True,
                "reason": "scene_present", "events": len(events),
                "frame": {"width": 4, "height": 4, "origin": "lower_left",
                          "pixel_format": "BGRA8", "row_stride": 16,
                          "reference": "frame-reference.bgra",
                          "reference_bytes": len(red),
                          "reference_sha256": hashlib.sha256(red).hexdigest()},
                "seed": {"width": 4, "height": 4},
            }))
            out = base / "out"
            out.mkdir()
            host = m6_frame.build_replay_host()
            ran = subprocess.run(
                [str(host), "--frame", str(frame), "--shaders", str(shaders),
                 "--out", str(out)], capture_output=True, text=True, check=False)
            self.assertEqual(ran.returncode, 0, ran.stderr)
            report = json.loads((out / "replay.json").read_text())
            self.assertEqual(report["failed"], 0, report.get("failures"))
            self.assertEqual(report["encoded"], len(events))
            self.assertEqual((out / "metal-frame.bgra").read_bytes(), red)


if __name__ == "__main__":
    unittest.main()
