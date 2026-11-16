"""Portable cross-frame reproduction cases.

The fixture is a deterministic four-frame Metal sequence: frame 2 renders
texture 500, frame 3 leaves it unchanged, frame 4 samples it.  Frame 1 is an
unrelated successful frame that the minimal prefix must exclude.  No EU4
process and no original campaign directory are used after packaging.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BENCH = ROOT / "benchmark"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from automation import gfx_frame_graph, gfx_replay_case, m6_frame, m7_continuous  # noqa: E402

WIDTH = HEIGHT = 4
SESSION = "synthetic-cross-frame"

VERT = """#include <metal_stdlib>
using namespace metal;
struct VOut { float4 position [[position]]; float2 uv; };
vertex VOut vmain(uint vid [[vertex_id]]) {
    float2 p[3] = { float2(-1,-1), float2(3,-1), float2(-1,3) };
    VOut o; o.position = float4(p[vid], 0.0, 1.0);
    o.uv = (p[vid] + 1.0) * 0.5; return o;
}
"""
FRAG_PATTERN = """#include <metal_stdlib>
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
"""
FRAG_SAMPLE = """#include <metal_stdlib>
using namespace metal;
struct VOut { float4 position [[position]]; float2 uv; };
struct FOut { float4 color [[color(0)]]; };
fragment FOut fsample(VOut in [[stage_in]], texture2d<float> tex0 [[texture(0)]],
                      sampler s0 [[sampler(0)]]) {
    return FOut{tex0.sample(s0, in.uv)};
}
"""
FRAG_BLACK = """#include <metal_stdlib>
using namespace metal;
struct FOut { float4 color [[color(0)]]; };
fragment FOut fblack() { return FOut{float4(0.0, 0.0, 0.0, 1.0)}; }
"""


def sha(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def byte(value: float) -> int:
    return int(round(value * 255.0))


def pattern_bgra(x: int, glrow: int) -> bytes:
    r = 1.0 if x in (0, 3) else 0.0
    g = 1.0 if glrow in (0, 3) else 0.0
    b = 1.0 if x == glrow else 0.0
    return bytes([byte(b), byte(g), byte(r), 255])


def expected_image() -> bytes:
    return b"".join(pattern_bgra(x, glrow)
                    for glrow in range(HEIGHT) for x in range(WIDTH))


def target(name=None, framebuffer=1):
    if name is None:
        return {"framebuffer": 0, "width": WIDTH, "height": HEIGHT}
    return {
        "framebuffer": framebuffer, "width": WIDTH, "height": HEIGHT,
        "color_attachment": {
            "type": "texture", "name": name, "level": 0,
            "internal_format": "RGB8", "width": WIDTH, "height": HEIGHT,
        },
    }


def draw(program, target_value, textures=(), index=0, shaders=(), attributes=()):
    return {
        "kind": "draw", "index": index,
        "draw": {"entry": "_GfxDraw", "mode": 4, "count": 3,
                 "base_vertex": 0, "instance_count": 1},
        "state": {
            "viewport": [0, 0, WIDTH, HEIGHT],
            "depth": {"test": False, "write": False, "func": "0x0201"},
            "blend": {"enabled": False},
            "cull": {"enabled": False},
        },
        "program": {"name": program, "uniforms": [],
                    "shaders": list(shaders)},
        "attributes": list(attributes), "textures": list(textures),
        "target": target_value,
    }


def manifest(events, reference):
    return {
        "schema": "gfx-frame-trace/v1", "complete": True,
        "reason": "scene_present", "events": len(events),
        "frame": {"width": WIDTH, "height": HEIGHT, "origin": "lower_left",
                  "pixel_format": "BGRA8", "row_stride": WIDTH * 4,
                  "reference": "frame-reference.bgra",
                  "reference_bytes": len(reference),
                  "reference_sha256": sha(reference)},
        "seed": {"width": WIDTH, "height": HEIGHT},
    }


class SequenceFixture:
    def __init__(self, base: Path, host: Path):
        self.base = base
        self.host = host
        self.shaders = base / "shaders"
        self.shaders.mkdir(parents=True)
        (self.shaders / "v.metal").write_text(VERT)
        (self.shaders / "f_pattern.metal").write_text(FRAG_PATTERN)
        (self.shaders / "f_sample.metal").write_text(FRAG_SAMPLE)
        (self.shaders / "f_black.metal").write_text(FRAG_BLACK)
        stage = {
            "msl_file": "v.metal", "msl_entry": "vmain",
            "msl_uniform_buffer": None, "msl_samplers": [],
            "attributes": [], "uniforms": [],
            "source_sha256": sha(b"vertex"),
        }

        def pixel(file, entry, samplers=()):
            return {
                "msl_file": file, "msl_entry": entry,
                "msl_uniform_buffer": None,
                "msl_samplers": list(samplers),
                "attributes": [], "uniforms": [],
                "source_sha256": sha({
                    "f_pattern.metal": b"pattern",
                    "f_sample.metal": b"sample",
                    "f_black.metal": b"black",
                }[file]),
            }

        programs = [
            {"program": 1, "status": "ok", "stages": {
                "vertex": stage,
                "pixel": pixel("f_pattern.metal", "fpattern")}},
            {"program": 2, "status": "ok", "stages": {
                "vertex": stage,
                "pixel": pixel("f_sample.metal", "fsample", [{
                    "kind": "texture2d", "name": "tex0",
                    "texture_index": 0, "sampler_index": 0}])}},
            {"program": 3, "status": "ok", "stages": {
                "vertex": stage,
                "pixel": pixel("f_black.metal", "fblack")}},
        ]
        (self.shaders / "shaders.json").write_text(
            json.dumps({"programs": programs}))
        self.frames: list[Path] = []

    def frame(self, index: int, events: list[dict], reference: bytes, *,
              previous: Path | None = None, render_targets=(),
              carried=(), lifecycle=()) -> Path:
        frame = self.base / "frames" / f"frame-{index:04d}"
        (frame / "blobs").mkdir(parents=True)
        (frame / "blobs" / "v.metal").write_bytes(b"vertex")
        (frame / "blobs" / "f_pattern.metal").write_bytes(b"pattern")
        (frame / "blobs" / "f_sample.metal").write_bytes(b"sample")
        (frame / "blobs" / "f_black.metal").write_bytes(b"black")
        (frame / "blobs" / "verts.bin").write_bytes(b"verts!")
        (frame / "blobs" / "tex500.bin").write_bytes(bytes(64))
        shader_stages = [
            {"stage": "vertex", "file": "v.metal",
             "sha256": sha(b"vertex")},
        ]
        attribute = {
            "location": 0, "name": "in_POSITION", "components": 2,
            "gl_type": "0x1406", "stride": 8, "data": "verts.bin",
            "sha256": sha(b"verts!"),
        }
        for event in events:
            fragment = {1: "f_pattern.metal", 2: "f_sample.metal",
                        3: "f_black.metal"}[event["program"]["name"]]
            event["program"]["shaders"] = shader_stages + [{
                "stage": "pixel", "file": fragment,
                "sha256": sha(Path(frame / "blobs" / fragment).read_bytes()),
            }]
            event["attributes"] = [attribute]
            for texture in event.get("textures") or []:
                if texture.get("name") == 500 and not texture.get("levels"):
                    texture["levels"] = [{
                        "level": 0, "width": WIDTH, "height": HEIGHT,
                        "pixel_format": "RGB8", "sha256": sha(bytes(64)),
                        "file": "tex500.bin",
                    }]
        (frame / "frame-events.jsonl").write_text(
            "\n".join(json.dumps(event) for event in events) + "\n")
        (frame / "frame-reference.bgra").write_bytes(reference)
        (frame / "frame-trace.json").write_text(
            json.dumps(manifest(events, reference)))
        sequence = {
            "schema": "gfx-frame-sequence/v1", "session_id": SESSION,
            "pid": 1, "sequence_index": index, "campaign_frames": 4,
            "capture_frame_id": index, "complete": True,
            "boundary": "scene", "reason": "scene_present",
            "previous": None,
            "render_targets": list(render_targets),
            "carried_renderer_outputs": list(carried),
            "lifecycle": list(lifecycle),
            "reference_sha256": sha(reference),
            "manifest_sha256": sha((frame / "frame-trace.json").read_bytes()),
            "event_stream_sha256": sha(
                (frame / "frame-events.jsonl").read_bytes()),
            "operations": len(events), "draws": len(events),
            "clears": 0, "copies": 0,
        }
        if previous is not None:
            sequence["previous"] = {
                "capture_frame_id": index - 1,
                "dir": str(previous),
                "manifest_sha256": sha(
                    (previous / "frame-trace.json").read_bytes()),
                "event_stream_sha256": sha(
                    (previous / "frame-events.jsonl").read_bytes()),
            }
        (frame / "frame-sequence.json").write_text(json.dumps(sequence))
        (frame / "frame-seal.json").write_text(json.dumps({
            "schema": "gfx-frame-seal/v1", "sealed": True, "complete": True,
            "struct_size": 2256, "abi_version": 2,
            "trace_complete": True, "trace_integrity": True,
            "readback_ok": True, "event_stream_verified": True,
            "capture_frame_id": index, "pid": 1, "session_id": SESSION,
            "capture_dir": str(frame),
            "manifest_file": "frame-trace.json",
            "manifest_sha256": sequence["manifest_sha256"],
            "reference_file": "frame-reference.bgra",
            "reference_bytes": len(reference),
            "reference_sha256": sequence["reference_sha256"],
            "event_stream_file": "frame-events.jsonl",
            "event_stream_bytes": (frame / "frame-events.jsonl").stat().st_size,
            "event_stream_sha256": sequence["event_stream_sha256"],
            "operation_count": len(events), "draw_count": len(events),
            "clear_count": 0, "copy_count": 0,
        }))
        self.frames.append(frame)
        return frame

    def build(self):
        black = bytes([0, 0, 0, 255]) * (WIDTH * HEIGHT)
        self.frame(1, [draw(3, target())], black)
        self.frame(2, [draw(1, target("500")), draw(3, target())], black,
                   previous=self.frames[-1], render_targets=("500",))
        self.frame(3, [draw(3, target())], black,
                   previous=self.frames[-1], carried=("500",))
        sampled = {
            "name": 500, "sampler_name": "tex0", "unit": 0,
            "width": WIDTH, "height": HEIGHT, "internal_format": "RGB8",
            "levels": [],
        }
        self.frame(4, [draw(2, target(), textures=[sampled])],
                   expected_image(), previous=self.frames[-1],
                   carried=("500",))
        return self.frames

    def build_transitive(self):
        """Frame 3 carries 600 from frame 2 while producing 500 for frame 5."""
        black = bytes([0, 0, 0, 255]) * (WIDTH * HEIGHT)
        self.frame(1, [draw(3, target())], black)
        self.frame(2, [draw(1, target("600", 1)), draw(3, target())], black,
                   previous=self.frames[-1], render_targets=("600",))
        self.frame(3, [draw(1, target("500", 2)), draw(3, target())], black,
                   previous=self.frames[-1], carried=("600",),
                   render_targets=("500",))
        # Native capture snapshots the cumulative carried set at frame start,
        # so the frame that deletes 600 still declares it as carried.
        self.frame(4, [draw(3, target())], black, previous=self.frames[-1],
                   carried=("500", "600"),
                   lifecycle=({"kind": "delete", "event": 0,
                               "names": ("600",)},))
        sampled = {
            "name": 500, "sampler_name": "tex0", "unit": 0,
            "width": WIDTH, "height": HEIGHT, "internal_format": "RGB8",
            "levels": [],
        }
        self.frame(5, [draw(2, target(), textures=[sampled])],
                   expected_image(), previous=self.frames[-1],
                   carried=("500",))
        return self.frames


class HeterogeneousShaderFixture:
    """Three frames whose required translations live in distinct frame dirs.

    Frame 1 renders texture 600 with program 1 (pattern).  Frame 2 retains it
    with program 3 (black).  Frame 3 samples it with program 2 (sample).  No
    directory contains the union of P1 and P2.
    """

    def __init__(self, base: Path, host: Path):
        self.base = base
        self.host = host
        self.frames: list[Path] = []
        self.frame_shaders: dict[int, Path] = {}
        self.source_bytes: dict[int, bytes] = {}

    def _stage(self, msl_file: str, entry: str, source: bytes) -> dict:
        return {
            "stage": "vertex" if entry == "vmain" else "pixel",
            "msl_file": msl_file, "msl_entry": entry,
            "msl_uniform_buffer": None, "msl_samplers": [],
            "uniforms": [], "attributes": [], "attribute_locations": {},
            "source_sha256": sha(source),
        }

    def _program(self, number: int, vertex_file: str, pixel_file: str,
                 vertex_entry: str, pixel_entry: str,
                 vertex_source: bytes, pixel_source: bytes,
                 samplers=()) -> dict:
        vertex = self._stage(vertex_file, vertex_entry, vertex_source)
        pixel = self._stage(pixel_file, pixel_entry, pixel_source)
        pixel["msl_samplers"] = list(samplers)
        return {"program": number, "status": "ok", "stages": {
            "vertex": vertex, "pixel": pixel}}

    def _write_shaders(self, index: int, programs: list[dict],
                       msl: dict[str, str]) -> Path:
        directory = self.base / "campaign" / f"frame-{index}" / "shaders"
        directory.mkdir(parents=True, exist_ok=True)
        for name, source in msl.items():
            (directory / name).write_text(source)
        report = {"schema": "gfx-shader-report/v1", "frame": index,
                  "ok": True, "programs": programs}
        (directory / "shaders.json").write_text(json.dumps(report))
        self.frame_shaders[index] = directory
        return directory

    def build(self):
        black = bytes([0, 0, 0, 255]) * (WIDTH * HEIGHT)
        for index in (1, 2, 3):
            frame = self.base / "campaign" / f"frame-{index}"
            (frame / "blobs").mkdir(parents=True)
            (frame / "blobs" / "verts.bin").write_bytes(b"verts!")
        # Frame 1: P1 pattern -> texture 600.
        v1, p1 = b"vertex-1", b"pattern-1"
        (self.base / "campaign" / "frame-1" / "blobs" / "v1.bin").write_bytes(v1)
        (self.base / "campaign" / "frame-1" / "blobs" / "p1.bin").write_bytes(p1)
        self._write_shaders(1, [self._program(
            1, "v.metal", "f_pattern.metal", "vmain", "fpattern", v1, p1)],
            {"v.metal": VERT, "f_pattern.metal": FRAG_PATTERN})
        self.source_bytes[1] = v1
        self.frame(1, [draw(1, target("600", 1),
                            shaders=[{"stage": "vertex", "file": "v1.bin",
                                      "sha256": sha(v1)},
                                     {"stage": "pixel", "file": "p1.bin",
                                      "sha256": sha(p1)}])],
                   black, render_targets=("600",))
        # Frame 2: P3 black, retains texture 600 without writing it.
        v2, p2 = b"vertex-2", b"black-2"
        (self.base / "campaign" / "frame-2" / "blobs" / "v2.bin").write_bytes(v2)
        (self.base / "campaign" / "frame-2" / "blobs" / "p2.bin").write_bytes(p2)
        self._write_shaders(2, [self._program(
            3, "v.metal", "f_black.metal", "vmain", "fblack", v2, p2)],
            {"v.metal": VERT, "f_black.metal": FRAG_BLACK})
        self.source_bytes[2] = v2
        self.frame(2, [draw(3, target(),
                            shaders=[{"stage": "vertex", "file": "v2.bin",
                                      "sha256": sha(v2)},
                                     {"stage": "pixel", "file": "p2.bin",
                                      "sha256": sha(p2)}])],
                   black, previous=self.frames[-1], carried=("600",))
        # Frame 3: P2 samples texture 600.
        v3, p3 = b"vertex-3", b"sample-3"
        (self.base / "campaign" / "frame-3" / "blobs" / "v3.bin").write_bytes(v3)
        (self.base / "campaign" / "frame-3" / "blobs" / "p3.bin").write_bytes(p3)
        self._write_shaders(3, [self._program(
            2, "v.metal", "f_sample.metal", "vmain", "fsample", v3, p3,
            [{"kind": "texture2d", "name": "tex0",
              "texture_index": 0, "sampler_index": 0}])],
            {"v.metal": VERT, "f_sample.metal": FRAG_SAMPLE})
        self.source_bytes[3] = v3
        sampled = {
            "name": 600, "sampler_name": "tex0", "unit": 0,
            "width": WIDTH, "height": HEIGHT, "internal_format": "RGB8",
            "levels": [],
        }
        self.frame(3, [draw(2, target(), textures=[sampled],
                            shaders=[{"stage": "vertex", "file": "v3.bin",
                                      "sha256": sha(v3)},
                                     {"stage": "pixel", "file": "p3.bin",
                                      "sha256": sha(p3)}])],
                   expected_image(), previous=self.frames[-1],
                   carried=("600",))
        return self.frames

    def frame(self, index: int, events: list[dict], reference: bytes, *,
              previous: Path | None = None, render_targets=(),
              carried=(), lifecycle=()) -> Path:
        frame = self.base / "campaign" / f"frame-{index}"
        attribute = {
            "location": 0, "name": "in_POSITION", "components": 2,
            "gl_type": "0x1406", "stride": 8, "data": "verts.bin",
            "sha256": sha(b"verts!"),
        }
        for event in events:
            if event.get("kind") == "draw":
                event["attributes"] = [attribute]
            for texture in event.get("textures") or []:
                if texture.get("name") == 600 and not texture.get("levels"):
                    texture["levels"] = [{
                        "level": 0, "width": WIDTH, "height": HEIGHT,
                        "pixel_format": "RGB8",
                        "sha256": sha(bytes(64)), "file": "tex600.bin",
                    }]
                    (frame / "blobs" / "tex600.bin").write_bytes(bytes(64))
        (frame / "frame-events.jsonl").write_text(
            "\n".join(json.dumps(event) for event in events) + "\n")
        (frame / "frame-reference.bgra").write_bytes(reference)
        (frame / "frame-trace.json").write_text(json.dumps(
            manifest(events, reference)))
        sequence = {
            "schema": "gfx-frame-sequence/v1", "session_id": SESSION,
            "pid": 1, "sequence_index": index, "campaign_frames": 3,
            "capture_frame_id": index, "complete": True,
            "boundary": "scene", "reason": "scene_present",
            "previous": None, "render_targets": list(render_targets),
            "carried_renderer_outputs": list(carried),
            "lifecycle": list(lifecycle),
            "reference_sha256": sha(reference),
            "manifest_sha256": sha((frame / "frame-trace.json").read_bytes()),
            "event_stream_sha256": sha(
                (frame / "frame-events.jsonl").read_bytes()),
            "operations": len(events), "draws": len(events),
            "clears": 0, "copies": 0,
        }
        if previous is not None:
            sequence["previous"] = {
                "capture_frame_id": index - 1,
                "dir": str(previous),
                "manifest_sha256": sha(
                    (previous / "frame-trace.json").read_bytes()),
                "event_stream_sha256": sha(
                    (previous / "frame-events.jsonl").read_bytes()),
            }
        (frame / "frame-sequence.json").write_text(json.dumps(sequence))
        (frame / "frame-seal.json").write_text(json.dumps({
            "schema": "gfx-frame-seal/v1", "sealed": True, "complete": True,
            "struct_size": 2256, "abi_version": 2,
            "trace_complete": True, "trace_integrity": True,
            "readback_ok": True, "event_stream_verified": True,
            "capture_frame_id": index, "pid": 1, "session_id": SESSION,
            "capture_dir": str(frame),
            "manifest_file": "frame-trace.json",
            "manifest_sha256": sequence["manifest_sha256"],
            "reference_file": "frame-reference.bgra",
            "reference_bytes": len(reference),
            "reference_sha256": sequence["reference_sha256"],
            "event_stream_file": "frame-events.jsonl",
            "event_stream_bytes": (frame / "frame-events.jsonl").stat().st_size,
            "event_stream_sha256": sequence["event_stream_sha256"],
            "operation_count": len(events), "draw_count": len(events),
            "clear_count": 0, "copy_count": 0,
        }))
        self.frames.append(frame)
        return frame


def canonical_fixture():
    candidates = [ROOT, ROOT.parent / "eu4-mac-perf", ROOT.parent / "wt_infra"]
    candidates.extend(Path(item) for item in
                      os.environ.get("EU4_EVIDENCE_ROOT", "").split(os.pathsep)
                      if item)
    for root in candidates:
        base = root / ".local" / "m6" / "frames" / "menu-occ300"
        frame = base / "frame"
        shaders = base / "translated"
        if (frame / "frame-trace.json").is_file() and (
                shaders / "shaders.json").is_file():
            return frame, shaders
    return None, None


@unittest.skipUnless(sys.platform == "darwin", "Metal replay is macOS")
class AuthenticCanonicalPrefixTests(unittest.TestCase):
    """Relocate an ordered prefix built from the canonical authentic capture."""

    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        cls.base = Path(cls.temporary.name)
        cls.source, cls.shaders = canonical_fixture()
        if cls.source is None:
            raise unittest.SkipTest("canonical frame fixture is not available")
        try:
            cls.host = m6_frame.build_replay_host()
        except Exception as exc:  # noqa: BLE001 - explicit skip
            raise unittest.SkipTest(str(exc))

    @classmethod
    def tearDownClass(cls):
        cls.temporary.cleanup()

    def make_frame(self, name, index, previous, carried, render_targets,
                   remove=()):
        frame = self.base / "campaign" / name
        (frame / "blobs").mkdir(parents=True)
        for file in ("frame-trace.json", "frame-events.jsonl",
                     "frame-reference.bgra"):
            shutil.copy2(self.source / file, frame / file)
        for blob in (self.source / "blobs").iterdir():
            try:
                os.link(blob, frame / "blobs" / blob.name)
            except OSError:
                shutil.copy2(blob, frame / "blobs" / blob.name)
        if remove:
            lines = (frame / "frame-events.jsonl").read_text().splitlines()
            kept = [line for line_index, line in enumerate(lines)
                    if line_index not in remove]
            (frame / "frame-events.jsonl").write_text(
                "\n".join(kept) + "\n")
            manifest = json.loads((frame / "frame-trace.json").read_text())
            manifest["events"] = len(kept)
            (frame / "frame-trace.json").write_text(json.dumps(manifest))
        manifest = json.loads((frame / "frame-trace.json").read_text())
        sequence = {
            "schema": "gfx-frame-sequence/v1", "session_id": "canonical-prefix",
            "pid": 1, "sequence_index": index, "campaign_frames": 3,
            "capture_frame_id": index, "complete": True,
            "boundary": "scene", "reason": "scene_present",
            "previous": None,
            "render_targets": list(render_targets),
            "carried_renderer_outputs": list(carried),
            "lifecycle": [],
            "reference_sha256": sha((frame / "frame-reference.bgra").read_bytes()),
            "manifest_sha256": sha((frame / "frame-trace.json").read_bytes()),
            "event_stream_sha256": sha(
                (frame / "frame-events.jsonl").read_bytes()),
            "operations": manifest["events"], "draws": 0, "clears": 0,
            "copies": 0,
        }
        if previous is not None:
            sequence["previous"] = {
                "capture_frame_id": index - 1,
                "dir": str(previous),
                "manifest_sha256": sha(
                    (previous / "frame-trace.json").read_bytes()),
                "event_stream_sha256": sha(
                    (previous / "frame-events.jsonl").read_bytes()),
            }
        (frame / "frame-sequence.json").write_text(json.dumps(sequence))
        return frame

    def test_canonical_prefix_relocates_and_keeps_carried_metal_history(self):
        campaign = self.base / "campaign"
        frame1 = self.make_frame("frame-1", 1, None, (), ("443",))
        frame2 = self.make_frame("frame-2", 2, frame1, ("443",), (),
                                 remove=(288, 289))
        frame3 = self.make_frame("frame-3", 3, frame2, ("443",), (),
                                 remove=(288, 289))
        frames = [frame1, frame2, frame3]
        out = self.base / "out"
        report = m7_continuous.run_campaign(
            frames, out, shaders=self.shaders, stop_on_difference=False)
        self.assertEqual(report["status"], "complete", report)
        self.assertGreaterEqual(report["frames"][1]["carried_bindings"], 1)
        diagnosis = json.loads(
            (out / "out-003" / "diagnostic-report.json").read_text())
        case = self.base / "case"
        gfx_replay_case.package_sequence_diagnosis(
            frames, out / "out-003", case, diagnosis,
            shaders=self.shaders, case_id="canonical-prefix", max_bytes=0)
        moved = self.base / "relocated"
        shutil.copytree(case, moved)
        shutil.rmtree(campaign)
        shutil.rmtree(out)
        verified = gfx_replay_case.verify(moved)
        self.assertTrue(verified["ok"], verified["problems"])
        replay = gfx_replay_case.replay_case(moved, self.base / "replay")
        self.assertEqual(replay["campaign"]["target_sequence"], 3)
        self.assertEqual(replay["classification"],
                         diagnosis["classification"])
        self.assertGreaterEqual(
            replay["provenance"]["metal_render_target_bindings"], 1)


@unittest.skipUnless(sys.platform == "darwin", "Metal replay is macOS")
class PortableSequenceCaseTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        cls.base = Path(cls.temporary.name)
        try:
            cls.host = m6_frame.build_replay_host()
        except Exception as exc:  # noqa: BLE001 - explicit skip
            raise unittest.SkipTest(str(exc))

    @classmethod
    def tearDownClass(cls):
        cls.temporary.cleanup()

    def setUp(self):
        self.fixture = SequenceFixture(self.base / self.id().split(".")[-1],
                                       self.host)
        self.frames = self.fixture.build()
        self.shaders = self.fixture.shaders

    def baseline(self):
        out = self.fixture.base / "baseline"
        report = m7_continuous.run_campaign(
            self.frames, out, shaders=self.shaders,
            stop_on_difference=False)
        self.assertEqual(report["status"], "complete", report)
        diagnosis = json.loads(
            (out / "out-004" / "diagnostic-report.json").read_text())
        return out, diagnosis

    def package(self, out, diagnosis, case=None):
        case = case or (self.fixture.base / "case")
        manifest = gfx_replay_case.package_sequence_diagnosis(
            self.frames, out / "out-004", case, diagnosis,
            shaders=self.shaders, case_id="cross-frame")
        return case, manifest

    def refresh_manifest_digest(self, case: Path, relative: str):
        manifest = json.loads((case / "case.json").read_text())
        path = case / relative
        for item in manifest["files"]:
            if item["path"] == relative:
                item["bytes"] = path.stat().st_size
                item["sha256"] = sha(path.read_bytes())
                break
        (case / "case.json").write_text(json.dumps(manifest, indent=2) + "\n")

    def test_minimal_prefix_is_relocatable_and_reproduces_the_fault(self):
        out, diagnosis = self.baseline()
        self.assertEqual(diagnosis["classification"], "qualified_match",
                         diagnosis)
        case, manifest = self.package(out, diagnosis)
        sequence = json.loads((case / "sequence.json").read_text())
        self.assertEqual(sequence["target_index"], 3, sequence)
        self.assertTrue(sequence["minimal_prefix"], sequence)
        self.assertEqual(sequence["excluded_frames"], 1, sequence)
        self.assertEqual([entry["original_sequence_index"]
                          for entry in sequence["frames"]], [2, 3, 4])
        prefix_bytes = sum(
            path.stat().st_size for path in (case / "frames").rglob("*")
            if path.is_file() and "shaders" not in
            path.relative_to(case / "frames").parts)
        self.assertLess(sequence["source_prefix_bytes"],
                        sequence["full_source_bytes"])
        self.assertGreater(sequence["excluded_source_bytes"], 0)
        self.assertLess(prefix_bytes, sequence["full_source_bytes"])
        moved = self.fixture.base / "relocated"
        shutil.copytree(case, moved)
        shutil.rmtree(self.fixture.base / "frames")
        shutil.rmtree(out)
        verified = gfx_replay_case.verify(moved)
        self.assertTrue(verified["ok"], verified["problems"])
        self.assertEqual(verified["sequence"]["frames"], 3)
        replay_out = self.fixture.base / "replay-ok"
        replay = gfx_replay_case.replay_case(moved, replay_out)
        self.assertEqual(replay["final"]["status"], "qualified_match",
                         replay)
        self.assertEqual(replay["campaign"]["target_sequence"], 3)
        fault_out = self.fixture.base / "replay-fault"
        fault = gfx_replay_case.replay_case(
            moved, fault_out,
            faults={"disable_render_target_vflip": True})
        self.assertNotEqual(fault["final"]["status"], "qualified_match",
                            fault)
        self.assertGreater(fault["pixels"]["statistics"]["mismatched_pixels"],
                           0)
        self.assertEqual(fault["campaign"]["target_sequence"], 3)
        self.assertEqual(
            fault["reproduction"]["earliest_affected_frame"], 3)
        # The captured diagnosis does not attribute the first divergence to a
        # specific operation for this controlled fault; do not invent one.
        self.assertIsNone(
            fault["reproduction"]["earliest_affected_operation"])

    def test_verifier_detects_missing_predecessor(self):
        out, diagnosis = self.baseline()
        case, _ = self.package(out, diagnosis)
        sequence = json.loads((case / "sequence.json").read_text())
        sequence["frames"] = sequence["frames"][1:]
        for index, entry in enumerate(sequence["frames"], start=1):
            entry["index"] = index
            entry["dir"] = f"frames/{entry['dir'].split('/')[-1]}"
        sequence["target_index"] = len(sequence["frames"])
        (case / "sequence.json").write_text(json.dumps(sequence))
        self.refresh_manifest_digest(case, "sequence.json")
        verified = gfx_replay_case.verify(case)
        self.assertFalse(verified["ok"], verified)
        self.assertTrue(any("predecessor" in problem
                            for problem in verified["problems"]), verified)

    def test_verifier_detects_wrong_session_and_missing_lifecycle(self):
        out, diagnosis = self.baseline()
        case, _ = self.package(out, diagnosis)
        frame = case / "frames" / "000002" / "frame-sequence.json"
        document = json.loads(frame.read_text())
        document["session_id"] = "another-session"
        document.pop("lifecycle")
        frame.write_text(json.dumps(document))
        self.refresh_manifest_digest(case, "frames/000002/frame-sequence.json")
        verified = gfx_replay_case.verify(case)
        self.assertFalse(verified["ok"], verified)
        joined = "\n".join(verified["problems"])
        self.assertIn("another session", joined)
        self.assertIn("lifecycle mutation record", joined)

    def test_verifier_detects_reordered_and_stale_checkpoint(self):
        out, diagnosis = self.baseline()
        case, _ = self.package(out, diagnosis)
        sequence = json.loads((case / "sequence.json").read_text())
        sequence["frames"][0], sequence["frames"][1] = (
            sequence["frames"][1], sequence["frames"][0])
        (case / "sequence.json").write_text(json.dumps(sequence))
        self.refresh_manifest_digest(case, "sequence.json")
        verified = gfx_replay_case.verify(case)
        self.assertTrue(any("reordered" in problem
                            for problem in verified["problems"]), verified)
        checkpoint = case / "checkpoints" / "frame-002" / (
            "checkpoint-request.json")
        checkpoint.write_text(json.dumps({"checkpoints": []}))
        self.refresh_manifest_digest(
            case, "checkpoints/frame-002/checkpoint-request.json")
        verified = gfx_replay_case.verify(case)
        self.assertTrue(any("stale or substituted" in problem
                            for problem in verified["problems"]), verified)

    def test_verifier_detects_corrupt_payload_and_wrong_generation(self):
        out, diagnosis = self.baseline()
        case, _ = self.package(out, diagnosis)
        blob = next((case / "frames" / "000002" / "blobs").iterdir())
        original = blob.read_bytes()
        tampered = bytearray(original)
        tampered[0] ^= 0xFF
        blob.write_bytes(bytes(tampered))
        verified = gfx_replay_case.verify(case)
        self.assertTrue(any("digest drift" in problem
                            for problem in verified["problems"]), verified)
        blob.write_bytes(original)
        frame = case / "frames" / "000001" / "frame-sequence.json"
        document = json.loads(frame.read_text())
        document["render_targets"] = []
        frame.write_text(json.dumps(document))
        self.refresh_manifest_digest(
            case, "frames/000001/frame-sequence.json")
        verified = gfx_replay_case.verify(case)
        self.assertTrue(any("no included predecessor" in problem
                            for problem in verified["problems"]), verified)

    def test_supervisor_packages_the_prefix_when_it_stops(self):
        target = self.frames[-1]
        wrong = bytes([0, 255, 0, 255]) * (WIDTH * HEIGHT)
        (target / "frame-reference.bgra").write_bytes(wrong)
        manifest = json.loads((target / "frame-trace.json").read_text())
        manifest["frame"]["reference_sha256"] = sha(wrong)
        (target / "frame-trace.json").write_text(json.dumps(manifest))
        sequence = json.loads((target / "frame-sequence.json").read_text())
        sequence["manifest_sha256"] = sha(
            (target / "frame-trace.json").read_bytes())
        sequence["reference_sha256"] = sha(wrong)
        (target / "frame-sequence.json").write_text(json.dumps(sequence))
        seal = json.loads((target / "frame-seal.json").read_text())
        seal["manifest_sha256"] = sequence["manifest_sha256"]
        seal["reference_sha256"] = sha(wrong)
        (target / "frame-seal.json").write_text(json.dumps(seal))
        out = self.fixture.base / "supervisor"
        report = m7_continuous.run_campaign(
            self.frames, out, shaders=self.shaders, stop_on_difference=True)
        self.assertEqual(report["status"], "stopped", report)
        entry = report["frames"][-1]
        self.assertEqual(entry["sequence"], 4)
        self.assertTrue(entry.get("case"), entry)
        case = out / "case"
        manifest = json.loads((case / "case.json").read_text())
        self.assertEqual(manifest["schema"], gfx_replay_case.SEQUENCE_SCHEMA)
        sequence = json.loads((case / "sequence.json").read_text())
        self.assertEqual(sequence["target_index"], 3)
        self.assertEqual(sequence["excluded_frames"], 1)

    def test_prefix_is_transitively_closed_over_predecessor_carried_outputs(self):
        self.fixture = SequenceFixture(
            self.base / "transitive", self.host)
        frames = self.fixture.build_transitive()
        out = self.fixture.base / "out"
        report = m7_continuous.run_campaign(
            frames, out, shaders=self.fixture.shaders,
            stop_on_difference=False)
        self.assertEqual(report["status"], "complete", report)
        diagnosis = json.loads(
            (out / "out-005" / "diagnostic-report.json").read_text())
        case = self.fixture.base / "case"
        manifest = gfx_replay_case.package_sequence_diagnosis(
            frames, out / "out-005", case, diagnosis,
            shaders=self.fixture.shaders, case_id="transitive")
        sequence = json.loads((case / "sequence.json").read_text())
        self.assertEqual([entry["original_sequence_index"]
                          for entry in sequence["frames"]], [2, 3, 4, 5])
        self.assertEqual(sequence["target_index"], 4)
        verified = gfx_replay_case.verify(case)
        self.assertTrue(verified["ok"], verified["problems"])
        replay = gfx_replay_case.replay_case(
            case, self.fixture.base / "transitive-replay")
        self.assertEqual(replay["final"]["status"], "qualified_match", replay)
        self.assertEqual((replay.get("history") or {}).get("carried_bindings"),
                         1)

    def test_verifier_detects_delete_of_a_carried_resource(self):
        out, diagnosis = self.baseline()
        case, _ = self.package(out, diagnosis)
        frame = case / "frames" / "000002" / "frame-sequence.json"
        document = json.loads(frame.read_text())
        document["lifecycle"] = [{"kind": "delete", "event": 0,
                                  "names": ["500"]}]
        frame.write_text(json.dumps(document))
        self.refresh_manifest_digest(case, "frames/000002/frame-sequence.json")
        verified = gfx_replay_case.verify(case)
        self.assertFalse(verified["ok"], verified)
        self.assertTrue(any("deleted without a later" in problem
                            for problem in verified["problems"]), verified)

    def test_packaging_refuses_missing_producer_and_unreproduced_mutation(self):
        out, diagnosis = self.baseline()
        missing = json.loads(
            (self.frames[-1] / "frame-sequence.json").read_text())
        original = json.loads(json.dumps(missing))
        missing["carried_renderer_outputs"] = ["999"]
        (self.frames[-1] / "frame-sequence.json").write_text(
            json.dumps(missing))
        try:
            with self.assertRaises(gfx_replay_case.CaseError) as caught:
                gfx_replay_case.package_sequence_diagnosis(
                    self.frames, out / "out-004", self.fixture.base / "missing",
                    diagnosis, shaders=self.shaders)
            self.assertEqual(caught.exception.kind, "missing_predecessor")
        finally:
            (self.frames[-1] / "frame-sequence.json").write_text(
                json.dumps(original))
        mutated = json.loads(
            (self.frames[1] / "frame-sequence.json").read_text())
        original_mutated = json.loads(json.dumps(mutated))
        mutated["lifecycle"] = [{"kind": "mutation", "event": 0,
                                 "operation": "glTexSubImage2D",
                                 "names": ["500"]}]
        (self.frames[1] / "frame-sequence.json").write_text(
            json.dumps(mutated))
        try:
            with self.assertRaises(gfx_replay_case.CaseError) as caught:
                gfx_replay_case.package_sequence_diagnosis(
                    self.frames, out / "out-004", self.fixture.base / "mutated",
                    diagnosis, shaders=self.shaders)
            self.assertEqual(caught.exception.kind,
                             "missing_mutation_record")
        finally:
            (self.frames[1] / "frame-sequence.json").write_text(
                json.dumps(original_mutated))

    @staticmethod
    def _case_bytes(root: Path) -> int:
        return sum(path.stat().st_size for path in root.rglob("*")
                   if path.is_file())

    def _full_case(self, out, diagnosis, name="full-case"):
        case = self.fixture.base / name
        gfx_replay_case.package_sequence_diagnosis(
            self.frames, out / "out-004", case, diagnosis,
            shaders=self.shaders, case_id="budget", max_bytes=0)
        return case, self._case_bytes(case)

    def test_budget_below_the_minimum_summary_fails_explicitly(self):
        out, diagnosis = self.baseline()
        case = self.fixture.base / "budget-minimum"
        with self.assertRaises(gfx_replay_case.CaseError) as caught:
            gfx_replay_case.package_sequence_diagnosis(
                self.frames, out / "out-004", case, diagnosis,
                shaders=self.shaders, case_id="budget", max_bytes=1)
        self.assertEqual(caught.exception.kind, "case_budget", caught.exception)
        self.assertFalse(case.exists(), "an over-budget case must not be published")
        self.assertEqual(
            list(self.fixture.base.glob(f".{case.name}-staging-*")), [],
            "staging output must be removed")

    def test_budget_just_below_the_complete_package_is_incomplete(self):
        out, diagnosis = self.baseline()
        _, size = self._full_case(out, diagnosis)
        case = self.fixture.base / "budget-below"
        manifest = gfx_replay_case.package_sequence_diagnosis(
            self.frames, out / "out-004", case, diagnosis,
            shaders=self.shaders, case_id="budget", max_bytes=size - 1)
        self.assertFalse(manifest["reproduction"]["independently_reproducible"])
        self.assertEqual(manifest["reproduction"]["status"], "incomplete")
        missing = manifest["reproduction"]["missing"][0]
        self.assertEqual(missing["kind"], "artifact_budget")
        self.assertEqual(missing["reason"], "artifact_budget")
        self.assertEqual(missing["max_bytes"], size - 1)
        self.assertGreater(missing["required_bytes"], size - 1)
        self.assertEqual(manifest["replay_command"], [])
        self.assertTrue((case / "reproduction-incomplete.json").is_file())
        self.assertLessEqual(self._case_bytes(case), size - 1)
        readme = (case / "README.md").read_text()
        self.assertIn("Reproduction incomplete", readme)
        self.assertNotIn("## Replay", readme)
        self.assertNotIn("captured", readme.lower())
        verified = gfx_replay_case.verify(case)
        self.assertFalse(verified["ok"])
        self.assertIn("reproduction is explicitly incomplete",
                      verified["problems"])
        with self.assertRaises(gfx_replay_case.CaseError) as caught:
            gfx_replay_case.replay_case(case, self.fixture.base / "budget-out")
        self.assertEqual(caught.exception.kind, "verification")

    def test_budget_at_the_complete_package_size_publishes_a_valid_case(self):
        out, diagnosis = self.baseline()
        _, size = self._full_case(out, diagnosis)
        case = self.fixture.base / "budget-exact"
        manifest = gfx_replay_case.package_sequence_diagnosis(
            self.frames, out / "out-004", case, diagnosis,
            shaders=self.shaders, case_id="budget", max_bytes=size)
        self.assertTrue(manifest["reproduction"]["independently_reproducible"])
        self.assertEqual(manifest["reproduction"]["status"], "complete")
        self.assertGreater(len(manifest["replay_command"]), 0)
        self.assertLessEqual(self._case_bytes(case), size)
        self.assertTrue(gfx_replay_case.verify(case)["ok"])

    def test_budget_does_not_modify_source_inputs(self):
        out, diagnosis = self.baseline()
        _, size = self._full_case(out, diagnosis)
        before = {
            path: (path.stat().st_size, sha(path.read_bytes()))
            for frame in self.frames for path in frame.rglob("*")
            if path.is_file()
        }
        case = self.fixture.base / "budget-sources"
        gfx_replay_case.package_sequence_diagnosis(
            self.frames, out / "out-004", case, diagnosis,
            shaders=self.shaders, case_id="budget", max_bytes=size - 1)
        after = {
            path: (path.stat().st_size, sha(path.read_bytes()))
            for frame in self.frames for path in frame.rglob("*")
            if path.is_file()
        }
        self.assertEqual(before, after)
        self.assertTrue(all(path.is_file() for path in before))

    def _edit_manifest(self, case: Path, edit) -> dict:
        manifest = json.loads((case / "case.json").read_text())
        edit(manifest)
        (case / "case.json").write_text(json.dumps(manifest, indent=2) + "\n")
        return manifest

    def test_verifier_requires_every_required_payload_in_the_inventory(self):
        out, diagnosis = self.baseline()
        case, _ = self.package(out, diagnosis)
        shaders = case / "shaders" / "shaders.json"
        shaders.write_text(shaders.read_text().replace("program", "PROGRAM"))
        def edit(manifest):
            manifest["files"] = [item for item in manifest["files"]
                                 if item["path"] != "shaders/shaders.json"]
        self._edit_manifest(case, edit)
        verified = gfx_replay_case.verify(case)
        self.assertFalse(verified["ok"], verified)
        self.assertIn("required payload is not declared: shaders/shaders.json",
                      verified["problems"])
        self.assertTrue(any("not declared in the inventory" in problem
                            for problem in verified["problems"]), verified)

    def test_verifier_requires_an_unlisted_checkpoint_request(self):
        out, diagnosis = self.baseline()
        case, _ = self.package(out, diagnosis)
        relative = "checkpoints/frame-001/checkpoint-request.json"
        self.assertTrue((case / relative).is_file())
        def edit(manifest):
            manifest["files"] = [item for item in manifest["files"]
                                 if item["path"] != relative]
        self._edit_manifest(case, edit)
        verified = gfx_replay_case.verify(case)
        self.assertFalse(verified["ok"], verified)
        self.assertIn(f"required payload is not declared: {relative}",
                      verified["problems"])

    def test_verifier_rejects_duplicate_manifest_entries(self):
        out, diagnosis = self.baseline()
        case, _ = self.package(out, diagnosis)
        def edit(manifest):
            manifest["files"].append(dict(manifest["files"][0]))
        self._edit_manifest(case, edit)
        verified = gfx_replay_case.verify(case)
        self.assertFalse(verified["ok"], verified)
        self.assertTrue(any("duplicate manifest path" in problem
                            for problem in verified["problems"]), verified)

    def test_verifier_rejects_a_reference_escaping_the_case(self):
        out, diagnosis = self.baseline()
        case, _ = self.package(out, diagnosis)
        outside = self.fixture.base / "outside.bin"
        outside.write_bytes(b"outside")
        payload = outside.read_bytes()
        def edit(manifest):
            manifest["files"].append({
                "path": "../../outside.bin", "bytes": len(payload),
                "sha256": sha(payload)})
        self._edit_manifest(case, edit)
        verified = gfx_replay_case.verify(case)
        self.assertFalse(verified["ok"], verified)
        self.assertTrue(any("nonportable manifest path" in problem
                            for problem in verified["problems"]), verified)

    def test_verifier_rejects_a_symlink_outside_the_case(self):
        out, diagnosis = self.baseline()
        case, _ = self.package(out, diagnosis)
        outside = self.fixture.base / "outside-symlink.bin"
        outside.write_bytes(b"outside")
        link = case / "escape.bin"
        link.symlink_to(outside)
        payload = outside.read_bytes()
        def edit(manifest):
            manifest["files"].append({
                "path": "escape.bin", "bytes": len(payload),
                "sha256": sha(payload)})
        self._edit_manifest(case, edit)
        verified = gfx_replay_case.verify(case)
        self.assertFalse(verified["ok"], verified)
        self.assertTrue(any("escapes the case directory" in problem
                            for problem in verified["problems"]), verified)

    def test_verifier_detects_a_missing_predecessor_resource_blob(self):
        out, diagnosis = self.baseline()
        case, _ = self.package(out, diagnosis)
        relative = "frames/000003/blobs/verts.bin"
        self.assertTrue((case / relative).is_file())
        (case / relative).unlink()
        def edit(manifest):
            manifest["files"] = [item for item in manifest["files"]
                                 if item["path"] != relative]
        self._edit_manifest(case, edit)
        verified = gfx_replay_case.verify(case)
        self.assertFalse(verified["ok"], verified)
        self.assertTrue(any("missing blob" in problem
                            for problem in verified["problems"]), verified)

    def test_documented_replay_into_the_case_does_not_invalidate_it(self):
        out, diagnosis = self.baseline()
        case, _ = self.package(out, diagnosis)
        self.assertTrue(gfx_replay_case.verify(case)["ok"])
        # The documented complete-v2 command runs from inside the case and
        # writes replay-rerun/ there.  That scratch output is not reproduction
        # evidence and must not make the immutable package unverifiable.
        replay = gfx_replay_case.replay_case(case, case / "replay-rerun")
        self.assertEqual(replay["final"]["status"], "qualified_match", replay)
        verified = gfx_replay_case.verify(case)
        self.assertTrue(verified["ok"], verified["problems"])
        again = gfx_replay_case.replay_case(case, case / "replay-rerun-2")
        self.assertEqual(again["final"]["status"], "qualified_match", again)

    def test_target_frame_cleanup_delete_is_refused(self):
        out, diagnosis = self.baseline()
        target = self.frames[-1]
        path = target / "frame-sequence.json"
        original = json.loads(path.read_text())
        events = json.loads((target / "frame-trace.json").read_text())["events"]
        mutated = json.loads(json.dumps(original))
        mutated["lifecycle"] = [{
            "kind": "delete", "event": events, "names": ["500"],
        }]
        path.write_text(json.dumps(mutated))
        try:
            case = self.fixture.base / "target-cleanup"
            with self.assertRaises(gfx_replay_case.CaseError) as caught:
                gfx_replay_case.package_sequence_diagnosis(
                    self.frames, out / "out-004", case, diagnosis,
                    shaders=self.shaders, case_id="target-cleanup")
            self.assertEqual(caught.exception.kind, "missing_mutation_record")
            self.assertIn("target frame", str(caught.exception))
            self.assertFalse(case.exists())
        finally:
            path.write_text(json.dumps(original))

    def test_verifier_rejects_an_external_sequence_reference(self):
        out, diagnosis = self.baseline()
        case, _ = self.package(out, diagnosis)
        external = self.fixture.base / "external-sequence.json"
        external.write_text((case / "sequence.json").read_text())
        def edit(manifest):
            manifest["sequence"] = "../../external-sequence.json"
        self._edit_manifest(case, edit)
        verified = gfx_replay_case.verify(case)
        self.assertFalse(verified["ok"], verified)
        self.assertTrue(any("sequence manifest path is invalid" in problem or
                            "must be sequence.json" in problem
                            for problem in verified["problems"]), verified)

    def test_complete_case_evidence_is_path_independent(self):
        out, diagnosis = self.baseline()
        # A second capture at a different path length must produce the same
        # case digest: copied replay metadata and source-byte metrics are
        # normalized, not measured from the capture-machine paths.
        remote = SequenceFixture(
            self.fixture.base.parent / "a-much-longer-capture-path" / "fixture",
            self.host)
        remote_frames = remote.build()
        second_out = remote.base / "baseline"
        report = m7_continuous.run_campaign(
            remote_frames, second_out, shaders=remote.shaders,
            stop_on_difference=False)
        self.assertEqual(report["status"], "complete", report)
        second_diagnosis = json.loads(
            (second_out / "out-004" / "diagnostic-report.json").read_text())
        relocated = remote.base / "deterministic-remote"
        gfx_replay_case.package_sequence_diagnosis(
            remote_frames, second_out / "out-004", relocated,
            second_diagnosis, shaders=remote.shaders, case_id="cross-frame")
        first, _ = self.package(out, diagnosis,
                                self.fixture.base / "deterministic-a")
        for name in ("dependency-graph.json", "diagnostic-report.json",
                     "sequence.json"):
            self.assertNotIn(str(self.fixture.base),
                             (first / name).read_text(), name)
            self.assertNotIn(str(self.fixture.base),
                             (first / "frames" / "000001" /
                              "frame-sequence.json").read_text(), name)
        local, _ = self.package(out, diagnosis,
                                self.fixture.base / "deterministic-b")
        self.assertEqual(sha((first / "case.json").read_bytes()),
                         sha((local / "case.json").read_bytes()))
        self.assertEqual(sha((first / "case.json").read_bytes()),
                         sha((relocated / "case.json").read_bytes()))
        self.assertEqual(sha((first / "dependency-graph.json").read_bytes()),
                         sha((local / "dependency-graph.json").read_bytes()))

    def test_single_frame_budget_fails_below_the_minimum_summary(self):
        out, diagnosis = self.baseline()
        case = self.fixture.base / "single-budget"
        with self.assertRaises(gfx_replay_case.CaseError) as caught:
            gfx_replay_case.package_diagnosis(
                self.frames[-1], out / "out-004", case, diagnosis,
                shaders=self.shaders, case_id="budget", max_bytes=1)
        self.assertEqual(caught.exception.kind, "case_budget", caught.exception)
        self.assertFalse(case.exists())



@unittest.skipUnless(sys.platform == "darwin", "Metal replay is macOS")
class HeterogeneousShaderSequenceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        cls.base = Path(cls.temporary.name)
        try:
            cls.host = m6_frame.build_replay_host()
        except Exception as exc:  # noqa: BLE001 - explicit skip
            raise unittest.SkipTest(str(exc))

    @classmethod
    def tearDownClass(cls):
        cls.temporary.cleanup()

    def setUp(self):
        self.fixture = HeterogeneousShaderFixture(
            self.base / self.id().split(".")[-1], self.host)
        self.frames = self.fixture.build()
        self.out = self.fixture.base / "out"
        # The coordinator may pass a fallback shader directory; the sequence
        # CLI must still use each frame's own <frame>/shaders translation.
        report = m7_continuous.run_campaign(
            self.frames, self.out, shaders=self.fixture.frame_shaders[3],
            stop_on_difference=False)
        self.assertEqual(report["status"], "complete", report)
        self.diagnosis = json.loads(
            (self.out / "out-003" / "diagnostic-report.json").read_text())

    def package(self, case: Path):
        return gfx_replay_case.package_sequence_diagnosis(
            self.frames, self.out / "out-003", case, self.diagnosis,
            shaders=None, case_id="heterogeneous-shaders")

    def refresh(self, case: Path, relative: str, *,
                update_sequence_entry: bool = False):
        manifest = json.loads((case / "case.json").read_text())
        path = case / relative
        for item in manifest["files"]:
            if item["path"] == relative:
                item["bytes"] = path.stat().st_size
                item["sha256"] = sha(path.read_bytes())
                break
        else:
            manifest["files"].append({
                "path": relative, "bytes": path.stat().st_size,
                "sha256": sha(path.read_bytes())})
        if update_sequence_entry:
            entry_index = int(Path(relative).parts[1]) - 1
            sequence = json.loads((case / "sequence.json").read_text())
            sequence["frames"][entry_index]["shader_report_sha256"] = sha(
                path.read_bytes())
            (case / "sequence.json").write_text(json.dumps(sequence))
            self.refresh(case, "sequence.json")
            manifest = json.loads((case / "case.json").read_text())
        (case / "case.json").write_text(json.dumps(manifest, indent=2) + "\n")

    def test_per_frame_shaders_relocate_and_replay(self):
        case = self.fixture.base / "case"
        manifest = self.package(case)
        self.assertTrue(manifest["reproduction"]["independently_reproducible"])
        sequence = json.loads((case / "sequence.json").read_text())
        programs = []
        for entry in sequence["frames"]:
            report = json.loads(
                (case / entry["shader_dir"] / "shaders.json").read_text())
            programs.append(sorted(
                item["program"] for item in report["programs"]))
        self.assertEqual(programs, [[1], [3], [2]], programs)
        self.assertEqual([entry["shader_programs"] for entry in
                          sequence["frames"]], [[1], [3], [2]])
        # No shared union: the case-level fallback is the target frame's own
        # translation directory, not a merged report.
        fallback = json.loads(
            (case / "shaders" / "shaders.json").read_text())
        self.assertEqual(sorted(item["program"] for item in
                                fallback["programs"]), [2])
        moved = self.fixture.base / "relocated"
        shutil.copytree(case, moved)
        shutil.rmtree(self.fixture.base / "campaign")
        shutil.rmtree(self.out)
        verified = gfx_replay_case.verify(moved)
        self.assertTrue(verified["ok"], verified["problems"])
        replay = gfx_replay_case.replay_case(
            moved, self.fixture.base / "replay")
        self.assertEqual(replay["final"]["status"], "qualified_match",
                         replay)
        self.assertEqual(replay["campaign"]["target_sequence"], 3)
        self.assertEqual(replay["campaign"]["frames"], 3)
        self.assertEqual((replay.get("history") or {}).get("status"), "ok")
        self.assertGreaterEqual(
            (replay.get("history") or {}).get("carried_bindings") or 0, 1)
        provenance = replay.get("provenance") or {}
        self.assertGreaterEqual(
            provenance.get("metal_render_target_bindings") or 0, 1)
        rows = [json.loads(line) for line in
                (self.fixture.base / "replay" / "out-003" /
                 "replay-provenance.jsonl").read_text().splitlines()
                if line.strip()]
        sampled = [row for row in rows if str(row.get("texture")) == "600"]
        self.assertTrue(sampled, rows)
        self.assertTrue(all(row.get("source") == "metal_render_target"
                            for row in sampled), sampled)
        self.assertFalse(any(row.get("source") == "external_capture"
                             for row in sampled), sampled)
        self.assertEqual(replay["pixels"]["statistics"]["mismatched_pixels"],
                         0)

    def test_verifier_detects_missing_frame_shader_artifact(self):
        case = self.fixture.base / "case-missing"
        self.package(case)
        relative = "frames/000003/shaders/f_sample.metal"
        (case / relative).unlink()
        manifest = json.loads((case / "case.json").read_text())
        manifest["files"] = [item for item in manifest["files"]
                             if item["path"] != relative]
        (case / "case.json").write_text(json.dumps(manifest, indent=2) + "\n")
        verified = gfx_replay_case.verify(case)
        self.assertFalse(verified["ok"], verified)
        self.assertTrue(any("f_sample.metal" in problem
                            for problem in verified["problems"]), verified)

    def test_verifier_requires_every_captured_program(self):
        case = self.fixture.base / "case-program"
        self.package(case)
        relative = "frames/000003/shaders/shaders.json"
        report = json.loads((case / relative).read_text())
        report["programs"] = [item for item in report["programs"]
                              if item["program"] != 2]
        (case / relative).write_text(json.dumps(report))
        self.refresh(case, relative, update_sequence_entry=True)
        verified = gfx_replay_case.verify(case)
        self.assertFalse(verified["ok"], verified)
        self.assertTrue(any("program 2 has no translated shader entry"
                            in problem for problem in verified["problems"]),
                        verified)

    def test_verifier_detects_shader_identity_mismatch(self):
        case = self.fixture.base / "case-identity"
        self.package(case)
        relative = "frames/000001/shaders/shaders.json"
        report = json.loads((case / relative).read_text())
        report["programs"][0]["stages"]["pixel"]["source_sha256"] = "0" * 64
        (case / relative).write_text(json.dumps(report))
        self.refresh(case, relative, update_sequence_entry=True)
        verified = gfx_replay_case.verify(case)
        self.assertFalse(verified["ok"], verified)
        self.assertTrue(any("shader identity disagrees" in problem
                            for problem in verified["problems"]), verified)

    def test_unusable_frame_translation_never_claims_complete(self):
        report_path = (self.fixture.base / "campaign" / "frame-1" /
                       "shaders" / "shaders.json")
        original = report_path.read_bytes()
        variants = {
            "corrupt": b"{not-json",
            "status": json.dumps({
                "schema": "gfx-shader-report/v1",
                "programs": [{"program": 1, "status": "error",
                              "stages": {}}]}).encode(),
        }
        for name, payload in variants.items():
            with self.subTest(variant=name):
                report_path.write_bytes(payload)
                try:
                    case = self.fixture.base / f"unusable-{name}"
                    manifest = self.package(case)
                    self.assertFalse(
                        manifest["reproduction"]["independently_reproducible"])
                    self.assertEqual(manifest["reproduction"]["status"],
                                     "incomplete")
                    self.assertEqual(
                        manifest["reproduction"]["reason"],
                        "shader_artifact_unavailable")
                    self.assertEqual(manifest["replay_command"], [])
                finally:
                    report_path.write_bytes(original)
        # A missing MSL payload is also unusable at packaging time.
        msl = (self.fixture.base / "campaign" / "frame-1" / "shaders" /
               "f_pattern.metal")
        original_msl = msl.read_bytes()
        msl.unlink()
        try:
            case = self.fixture.base / "unusable-msl"
            manifest = self.package(case)
            self.assertEqual(manifest["reproduction"]["status"], "incomplete")
            self.assertEqual(manifest["reproduction"]["reason"],
                             "shader_artifact_unavailable")
            self.assertFalse(
                manifest["reproduction"]["independently_reproducible"])
        finally:
            msl.write_bytes(original_msl)

    def test_identity_must_be_declared(self):
        case = self.fixture.base / "case-no-identity"
        self.package(case)
        relative = "frames/000001/shaders/shaders.json"
        report = json.loads((case / relative).read_text())
        report["programs"][0]["stages"]["pixel"].pop("source_sha256", None)
        (case / relative).write_text(json.dumps(report))
        self.refresh(case, relative, update_sequence_entry=True)
        verified = gfx_replay_case.verify(case)
        self.assertFalse(verified["ok"], verified)
        self.assertTrue(any("does not declare the captured stage identity"
                            in problem for problem in verified["problems"]),
                        verified)

    def test_budget_at_actual_size_with_per_frame_shaders(self):
        unlimited = self.fixture.base / "case-size"
        self.package(unlimited)
        size = sum(path.stat().st_size for path in unlimited.rglob("*")
                   if path.is_file())
        case = self.fixture.base / "case-exact-budget"
        manifest = gfx_replay_case.package_sequence_diagnosis(
            self.frames, self.out / "out-003", case, self.diagnosis,
            shaders=None, case_id="heterogeneous-shaders", max_bytes=size)
        self.assertEqual(manifest["reproduction"]["status"], "complete",
                         manifest["reproduction"])
        self.assertLessEqual(
            sum(path.stat().st_size for path in case.rglob("*")
                if path.is_file()), size)
        self.assertTrue(gfx_replay_case.verify(case)["ok"])

    def test_incomplete_summary_cannot_write_inside_a_source_frame(self):
        frame = self.frames[0]
        before = {path: sha(path.read_bytes()) for path in frame.rglob("*")
                  if path.is_file()}
        destination = frame / "summary-inside-source"
        with self.assertRaises(gfx_replay_case.CaseError) as caught:
            gfx_replay_case.package_sequence_diagnosis(
                self.frames, self.out / "out-003", destination,
                self.diagnosis, shaders=None,
                case_id="heterogeneous-shaders", max_bytes=1)
        self.assertEqual(caught.exception.kind, "output_overlap")
        self.assertFalse(destination.exists())
        after = {path: sha(path.read_bytes()) for path in frame.rglob("*")
                 if path.is_file()}
        self.assertEqual(before, after)

    def test_unavailable_frame_translation_is_incomplete(self):
        shutil.rmtree(self.fixture.base / "campaign" / "frame-2" / "shaders")
        case = self.fixture.base / "case-incomplete"
        manifest = self.package(case)
        self.assertFalse(manifest["reproduction"]["independently_reproducible"])
        self.assertEqual(manifest["reproduction"]["status"], "incomplete")
        self.assertEqual(manifest["reproduction"]["reason"],
                         "shader_artifact_unavailable")
        missing = manifest["reproduction"]["missing"]
        self.assertEqual(missing[0]["kind"], "shader_artifact")
        self.assertEqual(missing[0]["frame"], 2)
        self.assertEqual(manifest["replay_command"], [])
        self.assertFalse(gfx_replay_case.verify(case)["ok"])


if __name__ == "__main__":
    unittest.main()
