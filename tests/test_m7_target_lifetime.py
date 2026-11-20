"""Native Metal regressions for independent colour/depth attachment lifetimes.

A synthetic frame (built here, not captured from EU4) is replayed through the
production Metal replay core on a real MTLDevice.  It contains a framebuffer
with a colour texture and a depth/stencil attachment, a draw that writes a
nontrivial depth, a colour-only clear after deleting just the colour texture,
and a depth-tested draw whose result depends on the retained depth contents.

The same event stream is executed twice: once as a correctly ordered reference
without any lifecycle transition and once with the lifecycle delete.  The two
outputs must agree, and the target registry must show a reset colour generation
with an unchanged depth generation.  The opposite case deletes only the depth
attachment and checks that the colour generation is untouched while the depth
generation resets.

These tests run only on macOS, where Metal is available.
"""
from __future__ import annotations

import hashlib
import json
import shutil
import struct
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BENCH = ROOT / "benchmark"
HOST = ROOT / "tests" / "gfx_metal_replay_abi_host.mm"
CORE = BENCH / "eu4_gfx_frame_replay.mm"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from automation import native_build  # noqa: E402

WIDTH = 64
HEIGHT = 64
RED = (0, 0, 255, 255)   # BGRA readback of opaque red
GREEN = (0, 255, 0, 255)
BLACK = (0, 0, 0, 255)
ZERO = (0, 0, 0, 0)

VERTEX = """#include <metal_stdlib>
using namespace metal;
struct In { float2 position [[attribute(0)]]; };
struct Out { float4 position [[position]]; };
vertex Out vs_main(In in [[stage_in]]) {
    Out out; out.position = float4(in.position, ZVALUE, 1.0); return out;
}
"""

FRAGMENT = """#include <metal_stdlib>
using namespace metal;
struct Out { float4 color [[color(0)]]; };
fragment Out fs_main() { Out out; out.color = float4(COLOR); return out; }
"""

PROGRAMS = (
    (7, 0.5, (1.0, 0.0, 0.0, 1.0), "depth-write"),
    (8, 0.25, (0.0, 1.0, 0.0, 1.0), "depth-tested"),
    (9, 0.0, (0.0, 0.0, 1.0, 1.0), "noop"),
)

TARGET = {
    "framebuffer": 6,
    "width": WIDTH,
    "height": HEIGHT,
    "color_attachment": {
        "type": "texture",
        "name": 433,
        "level": 0,
        "width": WIDTH,
        "height": HEIGHT,
    },
    "depth_attachment": {"type": "renderbuffer", "name": 3},
}

STATE = {
    "blend": {
        "enabled": False,
        "src_rgb": "0x1",
        "dst_rgb": "0x0",
        "src_alpha": "0x1",
        "dst_alpha": "0x0",
        "equation_rgb": "0x8006",
        "equation_alpha": "0x8006",
    },
    "color_mask": [1, 1, 1, 1],
    "depth": {"test": True, "func": "0x203", "write": True},
    "stencil": {
        "test": False,
        "func": "0x207",
        "ref": 0,
        "value_mask": -1,
        "fail": "0x1e00",
        "zfail": "0x1e00",
        "zpass": "0x1e00",
        "write_mask": -1,
    },
    "cull": {"enabled": False, "mode": "0x405", "front": "0x901"},
    "polygon": {
        "mode": ["0x1b02", "0x1b02"],
        "offset_fill": False,
        "offset_factor": 0,
        "offset_units": 0,
    },
    "alpha_test": {"enabled": False, "func": "0x206", "ref": 1},
    "scissor": {"enabled": False, "box": [0, 0, WIDTH, HEIGHT]},
    "multisample": False,
    "dither": False,
    "viewport": [0, 0, WIDTH, HEIGHT],
}

DEFAULT_TARGET = {**TARGET, "framebuffer": 0}

CLEAR_COLOR_DEPTH = {
    "kind": "clear",
    "flags": 3,
    "color": [0, 0, 0, 1],
    "depth": 1,
    "stencil": 0,
    "target": TARGET,
}

CLEAR_COLOR_ONLY = {
    "kind": "clear",
    "flags": 1,
    "color": [0, 0, 0, 1],
    "depth": 1,
    "stencil": 0,
    "target": TARGET,
}


def _draw_on(target: dict, program: int, count: int = 6) -> dict:
    return {
        "kind": "draw",
        "state": STATE,
        "program": {"name": program, "uniforms": []},
        "attributes": [
            {
                "enabled": 1,
                "location": 0,
                "name": "in_POSITION",
                "components": 2,
                "gl_type": "0x1406",
                "normalized": 0,
                "stride": 8,
                "data": "quad.bin",
            }
        ],
        "draw": {
            "mode": 4,
            "count": count,
            "indexed": 0,
            "instance_count": 1,
        },
        "textures": [],
        "target": target,
    }


def _draw(program: int, count: int = 6) -> dict:
    return _draw_on(TARGET, program, count)


# 0 clear colour+depth (depth = 1)
# 1 draw at depth 0.5 (writes depth and red colour)
# 2 no-op draw: after a delete this recreates only the colour target
# 3 colour-only clear (does not touch depth)
# 4 depth-tested draw at 0.25: passes only when 0.5 is retained
# 5 no-op draw so the final state can be checkpointed
EVENTS = [
    CLEAR_COLOR_DEPTH,
    _draw(7),
    _draw(9, count=0),
    CLEAR_COLOR_ONLY,
    _draw(8),
    _draw(9, count=0),
]

# Same stream on the default framebuffer, with a final no-op so the last
# lifecycle event lands at the end-of-frame drain.
DEFAULT_EVENTS = [
    {**CLEAR_COLOR_DEPTH, "target": DEFAULT_TARGET},
    _draw_on(DEFAULT_TARGET, 7),
    _draw_on(DEFAULT_TARGET, 9, count=0),
    _draw_on(DEFAULT_TARGET, 9, count=0),
]
DEFAULT_COLOR_DELETE = {
    "kind": "delete",
    "event": len(DEFAULT_EVENTS),
    "lifecycle_sequence": 1,
    "names": ["433"],
}

COLOR_DELETE = {
    "kind": "delete",
    "event": 2,
    "lifecycle_sequence": 1,
    "names": ["433"],
}
DEPTH_DELETE = {
    "kind": "delete",
    "event": 2,
    "lifecycle_sequence": 1,
    "names": ["3"],
}


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


@unittest.skipUnless(sys.platform == "darwin", "Metal replay runs on macOS")
class TargetAttachmentLifetimeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        cls.base = Path(cls.temporary.name)
        cls.binary = cls.base / "gfx_metal_replay_abi"
        native_build.require_build(
            [
                "clang",
                "-arch",
                "arm64",
                "-O2",
                "-fobjc-arc",
                "-lc++",
                "-I",
                str(BENCH),
                "-framework",
                "Foundation",
                "-framework",
                "Metal",
                "-o",
                str(cls.binary),
                str(HOST),
                str(CORE),
            ],
            label="target-lifetime-abi", sources=[HOST, CORE],
            frameworks=("Foundation", "Metal"), require_arm64=True,
            timeout=300)
        cls.shaders = cls.base / "shaders"
        cls._write_shaders(cls.shaders)
        cls.checkpoints = cls.base / "checkpoints.json"
        cls.checkpoints.write_text(
            json.dumps(
                {
                    "checkpoints": [
                        {"event": 3, "target": "433", "file": "recreated.bgra"},
                        {"event": 5, "target": "433", "file": "end.bgra"},
                    ]
                }
            )
        )

    @classmethod
    def tearDownClass(cls):
        cls.temporary.cleanup()

    @staticmethod
    def _write_shaders(directory: Path) -> None:
        directory.mkdir(parents=True, exist_ok=True)
        report = {
            "schema": "gfx-shader-report/v1",
            "frame": 1,
            "ok": True,
            "programs": [],
        }
        for program, depth, color, tag in PROGRAMS:
            (directory / f"{tag}.vert.metal").write_text(
                VERTEX.replace("ZVALUE", str(depth))
            )
            (directory / f"{tag}.frag.metal").write_text(
                FRAGMENT.replace("COLOR", ",".join(str(value) for value in color))
            )
            report["programs"].append(
                {
                    "program": program,
                    "draws": 1,
                    "status": "ok",
                    "stages": {
                        "vertex": {
                            "stage": "vertex",
                            "msl_file": f"{tag}.vert.metal",
                            "msl_entry": "vs_main",
                            "msl_uniform_buffer": None,
                            "msl_samplers": [],
                            "uniforms": [],
                            "attributes": [{"name": "in_POSITION", "type": "float2"}],
                            "attribute_locations": {"in_POSITION": 0},
                        },
                        "pixel": {
                            "stage": "pixel",
                            "msl_file": f"{tag}.frag.metal",
                            "msl_entry": "fs_main",
                            "msl_uniform_buffer": None,
                            "msl_samplers": [],
                            "uniforms": [],
                            "attributes": [],
                            "attribute_locations": {},
                        },
                    },
                }
            )
        (directory / "shaders.json").write_text(json.dumps(report))

    def _make_frame(self, name: str, lifecycle, events=None) -> Path:
        frame = self.base / name
        if frame.exists():
            shutil.rmtree(frame)
        (frame / "blobs").mkdir(parents=True)
        vertices = [(-1, -1), (1, -1), (-1, 1), (-1, 1), (1, -1), (1, 1)]
        (frame / "blobs" / "quad.bin").write_bytes(
            b"".join(struct.pack("<ff", x, y) for x, y in vertices)
        )
        events = EVENTS if events is None else events
        (frame / "frame-events.jsonl").write_text(
            "\n".join(json.dumps(event) for event in events) + "\n"
        )
        reference = bytes(WIDTH * HEIGHT * 4)
        (frame / "frame-reference.bgra").write_bytes(reference)
        manifest = {
            "schema": "gfx-frame-trace/v1",
            "complete": True,
            "reason": "",
            "truncated": False,
            "events": len(events),
            "frame": {
                "width": WIDTH,
                "height": HEIGHT,
                "origin": "lower_left",
                "pixel_format": "BGRA8",
                "row_stride": WIDTH * 4,
                "reference": "frame-reference.bgra",
                "reference_bytes": len(reference),
                "reference_sha256": hashlib.sha256(reference).hexdigest(),
            },
            "blobs": {},
        }
        (frame / "frame-trace.json").write_text(json.dumps(manifest))
        sequence = {
            "schema": "gfx-frame-sequence/v1",
            "session_id": "synthetic-target-lifetime",
            "pid": 1,
            "sequence_index": 1,
            "campaign_frames": 1,
            "capture_frame_id": 1,
            "complete": True,
            "boundary": "scene",
            "reason": "scene_present",
            "previous": None,
            "render_targets": [],
            "carried_renderer_outputs": [],
            "lifecycle": lifecycle,
            "reference_sha256": _sha(frame / "frame-reference.bgra"),
            "manifest_sha256": _sha(frame / "frame-trace.json"),
            "event_stream_sha256": _sha(frame / "frame-events.jsonl"),
            "operations": len(events),
            "draws": 1,
            "clears": 2,
            "copies": 0,
        }
        (frame / "frame-sequence.json").write_text(json.dumps(sequence))
        return frame

    def _run_frame(self, frame: Path, name: str, *, checkpoints=True):
        output = self.base / "out" / name
        if output.exists():
            shutil.rmtree(output)
        command = [
            str(self.binary),
            "--sequence",
            str(frame),
            "--shaders",
            str(self.shaders),
            "--out",
            str(output),
        ]
        if checkpoints:
            command.extend(["--checkpoints", str(self.checkpoints)])
        ran = subprocess.run(
            command, capture_output=True, text=True, check=False, timeout=300)
        return ran, output / "out-001"

    def _replay(self, frame: Path, name: str) -> tuple[dict, Path]:
        ran, out = self._run_frame(frame, name)
        self.assertEqual(ran.returncode, 0, ran.stderr[-2000:] or ran.stdout[-2000:])
        result = json.loads((out / "replay.json").read_text())
        self.assertEqual(result.get("status"), "ok", result)
        return result, out

    @staticmethod
    def _pixels(path: Path) -> set[tuple[int, int, int, int]]:
        data = path.read_bytes()
        return {tuple(data[index:index + 4]) for index in range(0, len(data), 4)}

    @staticmethod
    def _target(result: dict, framebuffer: int) -> dict:
        registry = result.get("targets") or {}
        for entry in registry.get("targets", []):
            if entry.get("framebuffer") == framebuffer:
                return entry
        raise AssertionError(f"framebuffer {framebuffer} not in {registry}")

    def test_colour_delete_keeps_the_depth_attachment_and_resets_colour(self):
        reference_frame = self._make_frame("reference", [])
        reference, reference_out = self._replay(reference_frame, "reference")
        self.assertEqual(self._pixels(reference_out / "end.bgra"), {GREEN})

        colour_frame = self._make_frame("colour-delete", [COLOR_DELETE])
        result, out = self._replay(colour_frame, "colour-delete")

        # The retained depth at 0.5 blocks the 0.25 draw exactly as in the
        # correctly ordered reference execution.
        self.assertEqual(self._pixels(out / "end.bgra"), {GREEN})
        self.assertEqual(self._pixels(out / "end.bgra"),
                         self._pixels(reference_out / "end.bgra"))

        # The recreated colour target never exposes the deleted red contents.
        self.assertEqual(self._pixels(out / "recreated.bgra"), {ZERO})
        self.assertEqual(self._pixels(reference_out / "recreated.bgra"), {RED})

        reference_target = self._target(reference, 6)
        target = self._target(result, 6)
        self.assertEqual(target["color_name"], "433")
        self.assertEqual(target["color_generation"], 4)
        self.assertLess(target["color_generation"],
                        reference_target["color_generation"])
        self.assertEqual(target["depth_generation"],
                         reference_target["depth_generation"])
        self.assertEqual(target["depth_generation"], 5)
        self.assertTrue(target["depth_live"])
        self.assertEqual(target["depth_name"], "3")

    def test_depth_delete_is_independent_of_the_colour_attachment(self):
        reference_frame = self._make_frame("depth-reference", [])
        reference, reference_out = self._replay(reference_frame, "depth-reference")
        self.assertEqual(self._pixels(reference_out / "end.bgra"), {GREEN})

        depth_frame = self._make_frame("depth-delete", [DEPTH_DELETE])
        result, out = self._replay(depth_frame, "depth-delete")

        # The depth contents were invalidated: the 0.25 draw now sees a fresh
        # attachment and fails against the cleared value, unlike the reference.
        self.assertEqual(self._pixels(out / "end.bgra"), {BLACK})
        self.assertNotEqual(self._pixels(out / "end.bgra"),
                            self._pixels(reference_out / "end.bgra"))
        # The colour attachment was never deleted.
        self.assertEqual(self._pixels(out / "recreated.bgra"), {RED})

        reference_target = self._target(reference, 6)
        target = self._target(result, 6)
        self.assertEqual(target["color_generation"],
                         reference_target["color_generation"])
        self.assertEqual(target["color_generation"], 6)
        self.assertLess(target["depth_generation"],
                        reference_target["depth_generation"])
        self.assertEqual(target["depth_generation"], 3)
        self.assertTrue(target["depth_live"])
        self.assertEqual(target["depth_name"], "3")


    def test_default_colour_delete_at_the_end_fails_closed(self):
        # The session's readback pointer is unretained.  Deleting the default
        # target's colour texture at the end-of-frame lifecycle drain must not
        # leave a dangling pointer or silently read back a deleted resource.
        frame = self._make_frame("default-delete", [DEFAULT_COLOR_DELETE],
                                 events=DEFAULT_EVENTS)
        ran, out = self._run_frame(frame, "default-delete", checkpoints=False)
        self.assertNotEqual(ran.returncode, 0,
                            ran.stdout[-2000:] or ran.stderr[-2000:])
        self.assertNotIn("Segmentation", ran.stderr)
        result = json.loads((out / "replay.json").read_text())
        self.assertNotEqual(result.get("status"), "ok", result)
        self.assertTrue(any(
            failure.get("stage") == "frame_readback" and
            "missing" in str(failure.get("error", ""))
            for failure in result.get("failures") or []), result)


if __name__ == "__main__":
    unittest.main()
