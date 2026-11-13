"""M6 frame capture, translation, replay and comparison semantics.

These tests are offline and synthetic where a live frame is not required. They
pin the fail-closed behavior of the new tooling: a trace is only usable when its
manifest, payload digests, events and coverage agree, and a differential only
passes when both sides' provenance validates and every pixel matches the
declared policy.
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
sys.path.insert(0, str(ROOT / "benchmark"))

from automation import gfx_shader_translate as translate  # noqa: E402
from automation import m6_compare, m6_frame  # noqa: E402

WIDTH = 4
HEIGHT = 3


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _write_blob(frame: Path, name: str, data: bytes) -> dict:
    path = frame / "blobs" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return {
        "file": name,
        "bytes": len(data),
        "sha256": _sha(data),
    }


def build_synthetic_trace(
    frame: Path,
    *,
    width: int = WIDTH,
    height: int = HEIGHT,
    reference: bytes | None = None,
    tamper_blob: bool = False,
) -> dict:
    """Builds one minimal but complete frame trace the loader must accept."""
    frame.mkdir(parents=True, exist_ok=True)
    if reference is None:
        reference = bytes([0, 0, 0, 255] * (width * height))
    reference_path = frame / "frame-reference.bgra"
    reference_path.write_bytes(reference)
    seed = bytes([255, 0, 0, 255] * (width * height))
    seed_member = _write_blob(frame, "seed-" + _sha(seed)[:8] + ".bin", seed)
    vertex = bytes(range(12))
    vertex_member = _write_blob(frame, "vb-test.bin", vertex)
    if tamper_blob:
        (frame / "blobs" / vertex_member["file"]).write_bytes(b"tampered")
    uniform = bytes(8)
    uniform_member = _write_blob(frame, "uniform-test.bin", uniform)
    shader = b"void main() {}\n"
    shader_member = _write_blob(frame, "shader-test.bin", shader)
    texture = bytes([0, 0, 0, 255] * 1)
    texture_member = _write_blob(frame, "tex-test.bin", texture)
    index = bytes(4)
    index_member = _write_blob(frame, "ib-test.bin", index)
    events = [
        {
            "kind": "clear",
            "before_first_draw": True,
            "flags": 3,
            "color": [0, 0, 0, 0],
            "depth": 1,
            "stencil": 0,
            "target": {
                "framebuffer": 1,
                "width": width,
                "height": height,
                "color_attachment": {"type": "texture", "name": 9},
            },
        },
        {
            "kind": "draw",
            "index": 0,
            "seed": {
                "width": width,
                "height": height,
                "file": seed_member["file"],
                "sha256": seed_member["sha256"],
            },
            "state": {
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
                "depth": {"test": False, "func": "0x203", "write": False},
                "stencil": {"test": False},
                "cull": {"enabled": False, "mode": "0x405", "front": "0x901"},
                "scissor": {"enabled": False, "box": [0, 0, width, height]},
                "viewport": [0, 0, width, height],
            },
            "program_diagnostics": {
                "active_attributes": 1,
                "active_uniforms": 1,
                "link": True,
                "enabled_bits": [1] + [0] * 15,
            },
            "program": {
                "name": 7,
                "shaders": [
                    {
                        "stage": "vertex",
                        "compile": True,
                        "bytes": shader_member["bytes"],
                        "file": shader_member["file"],
                        "sha256": shader_member["sha256"],
                    },
                    {
                        "stage": "pixel",
                        "compile": True,
                        "bytes": shader_member["bytes"],
                        "file": shader_member["file"],
                        "sha256": shader_member["sha256"],
                    },
                ],
                "uniforms": [
                    {
                        "name": "UNIFORM0[0]",
                        "type": "float",
                        "components": 4,
                        "elements": 2,
                        "bytes": uniform_member["bytes"],
                        "file": uniform_member["file"],
                        "sha256": uniform_member["sha256"],
                    }
                ],
            },
            "textures": [
                {
                    "unit": 0,
                    "sampler_name": "tex0",
                    "target": "2D",
                    "name": 5,
                    "width": 1,
                    "height": 1,
                    "internal_format": "RGBA8",
                    "sampler": {
                        "min": "0x2601",
                        "mag": "0x2601",
                        "wrap_s": "0x812f",
                        "wrap_t": "0x812f",
                    },
                    "levels": [
                        {
                            "level": 0,
                            "width": 1,
                            "height": 1,
                            "pixel_format": "RGBA8",
                            "bytes": texture_member["bytes"],
                            "file": texture_member["file"],
                            "sha256": texture_member["sha256"],
                        }
                    ],
                }
            ],
            "target": {
                "framebuffer": 0,
                "width": width,
                "height": height,
                "viewport": [0, 0, width, height],
            },
            "attributes": [
                {
                    "index": 0,
                    "name": "in_POSITION",
                    "location": 0,
                    "enabled": True,
                    "components": 3,
                    "gl_type": "0x1406",
                    "normalized": False,
                    "stride": 12,
                    "offset": 0,
                    "divisor": 0,
                    "buffer": 3,
                    "range_bytes": vertex_member["bytes"],
                    "data": vertex_member["file"],
                    "sha256": vertex_member["sha256"],
                }
            ],
            "indices": {
                "buffer": 4,
                "bytes": index_member["bytes"],
                "count": 1,
                "instance_count": 1,
                "file": index_member["file"],
                "sha256": index_member["sha256"],
            },
            "draw": {
                "entry": "_GfxDrawIndexed",
                "mode": 4,
                "first": 0,
                "count": 1,
                "instance_count": 1,
                "indexed": True,
                "base_vertex": 0,
                "caller_rva": "0x100",
            },
        },
    ]
    manifest = {
        "schema": m6_frame.TRACE_SCHEMA,
        "complete": True,
        "reason": "present",
        "truncated": False,
        "pid": 1234,
        "present_index": 12,
        "events": len(events),
        "gl": {"vendor": "Apple", "renderer": "test", "version": "2.1"},
        "frame": {
            "width": width,
            "height": height,
            "origin": "lower_left",
            "pixel_format": "BGRA8",
            "row_stride": width * 4,
            "reference": "frame-reference.bgra",
            "reference_bytes": len(reference),
            "reference_sha256": _sha(reference),
        },
        "selection": {
            "callers": ["0x100"],
            "min_draws": 1,
            "max_frame_draws": 0,
            "after_ms": 0,
            "occurrence": 1,
            "required": True,
        },
        "seed": {"width": width, "height": height},
        "blobs": 5,
    }
    (frame / "frame-trace.json").write_text(json.dumps(manifest))
    (frame / "frame-events.jsonl").write_text(
        "\n".join(json.dumps(event) for event in events) + "\n"
    )
    return manifest


class FrameTraceTests(unittest.TestCase):
    def test_valid_trace_loads_and_coverage_is_complete(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            frame = Path(tmp) / "frame"
            build_synthetic_trace(frame)
            trace = m6_frame.load_trace(frame)
            coverage = m6_frame.coverage_report(trace)
            self.assertTrue(coverage["ok"], coverage["missing"])
            self.assertEqual(coverage["draw_count"], 1)
            self.assertEqual(coverage["shader_pairs"], 2)
            self.assertEqual(coverage["texture_count"], 1)
            self.assertEqual(coverage["seed"]["width"], WIDTH)

    def test_tampered_blob_is_refused(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            frame = Path(tmp) / "frame"
            build_synthetic_trace(frame, tamper_blob=True)
            with self.assertRaises(m6_frame.FrameError):
                m6_frame.load_trace(frame)

    def test_truncated_trace_is_refused(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            frame = Path(tmp) / "frame"
            build_synthetic_trace(frame)
            manifest = json.loads((frame / "frame-trace.json").read_text())
            manifest["complete"] = False
            manifest["reason"] = "draw_cap"
            (frame / "frame-trace.json").write_text(json.dumps(manifest))
            with self.assertRaises(m6_frame.FrameError):
                m6_frame.load_trace(frame)

    def test_event_count_disagreement_is_refused(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            frame = Path(tmp) / "frame"
            build_synthetic_trace(frame)
            manifest = json.loads((frame / "frame-trace.json").read_text())
            manifest["events"] = 99
            (frame / "frame-trace.json").write_text(json.dumps(manifest))
            with self.assertRaises(m6_frame.FrameError):
                m6_frame.load_trace(frame)

    def test_missing_seed_is_reported_as_missing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            frame = Path(tmp) / "frame"
            build_synthetic_trace(frame)
            events = [
                json.loads(line)
                for line in (frame / "frame-events.jsonl").read_text().splitlines()
            ]
            events[1].pop("seed")
            (frame / "frame-events.jsonl").write_text(
                "\n".join(json.dumps(event) for event in events) + "\n"
            )
            coverage = m6_frame.coverage_report(m6_frame.load_trace(frame))
            self.assertFalse(coverage["ok"])
            self.assertTrue(
                any(item["issue"] == "missing_frame_seed" for item in coverage["missing"])
            )


class CompareTests(unittest.TestCase):
    def _replay(self, directory: Path, image: bytes, **overrides) -> None:
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "metal-frame.bgra").write_bytes(image)
        value = {
            "schema": "gfx-metal-frame-replay/v1",
            "status": "ok",
            "events": 2,
            "encoded": 2,
            "failed": 0,
            "readback": True,
            "command_status": 4,
            "frame": {
                "width": WIDTH,
                "height": HEIGHT,
                "origin": "lower_left",
                "pixel_format": "BGRA8",
                "raw_file": "metal-frame.bgra",
                "raw_bytes": len(image),
            },
        }
        value.update(overrides)
        (directory / "replay.json").write_text(json.dumps(value))

    def test_replay_without_failed_field_is_refused(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            frame = Path(tmp) / "frame"
            build_synthetic_trace(frame)
            replay = Path(tmp) / "replay"
            self._replay(replay, (frame / "frame-reference.bgra").read_bytes())
            value = json.loads((replay / "replay.json").read_text())
            del value["failed"]
            (replay / "replay.json").write_text(json.dumps(value))
            policy = {
                "thresholds": {
                    "max_channel_error": 0,
                    "mismatched_fraction": 0.0,
                    "mean_abs_error": 0.0,
                },
                "alpha": {"ignored": False},
                "allowed_normalizations": ["row_origin"],
            }
            with self.assertRaises(m6_compare.CompareError):
                m6_compare.compare(frame, replay, policy)

    def test_truncated_replay_with_correct_pixels_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            frame = Path(tmp) / "frame"
            build_synthetic_trace(frame)
            replay = Path(tmp) / "replay"
            self._replay(
                replay,
                (frame / "frame-reference.bgra").read_bytes(),
                events=1,
                encoded=1,
            )
            policy = {
                "thresholds": {
                    "max_channel_error": 0,
                    "mismatched_fraction": 0.0,
                    "mean_abs_error": 0.0,
                },
                "alpha": {"ignored": False},
                "allowed_normalizations": ["row_origin"],
            }
            with self.assertRaises(m6_compare.CompareError):
                m6_compare.compare(frame, replay, policy)

    @staticmethod
    def _policy() -> dict:
        return {
            "allowed_normalizations": ["row_origin"],
            "alpha": {"ignored": False},
            "thresholds": {
                "max_channel_error": 0,
                "mismatched_fraction": 0.0,
                "mean_abs_error": 0.0,
            },
        }

    def test_same_file_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            frame = Path(tmp) / "frame"
            build_synthetic_trace(frame)
            replay = Path(tmp) / "replay"
            self._replay(replay, (frame / "frame-reference.bgra").read_bytes())
            value = json.loads((replay / "replay.json").read_text())
            value["frame"]["raw_file"] = str(frame / "frame-reference.bgra")
            (replay / "replay.json").write_text(json.dumps(value))
            with self.assertRaises(m6_compare.CompareError) as raised:
                m6_compare.compare(frame, replay, self._policy())
            self.assertIn("same file", str(raised.exception))

    def test_symlink_alias_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            frame = Path(tmp) / "frame"
            build_synthetic_trace(frame)
            replay = Path(tmp) / "replay"
            self._replay(replay, (frame / "frame-reference.bgra").read_bytes())
            (replay / "metal-frame.bgra").unlink()
            (replay / "metal-frame.bgra").symlink_to(frame / "frame-reference.bgra")
            with self.assertRaises(m6_compare.CompareError) as raised:
                m6_compare.compare(frame, replay, self._policy())
            self.assertIn("same file", str(raised.exception))

    def test_hard_link_alias_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            frame = Path(tmp) / "frame"
            build_synthetic_trace(frame)
            replay = Path(tmp) / "replay"
            self._replay(replay, (frame / "frame-reference.bgra").read_bytes())
            (replay / "metal-frame.bgra").unlink()
            os.link(frame / "frame-reference.bgra", replay / "metal-frame.bgra")
            with self.assertRaises(m6_compare.CompareError) as raised:
                m6_compare.compare(frame, replay, self._policy())
            self.assertIn("alias", str(raised.exception))

    def test_independent_identical_files_pass(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            frame = Path(tmp) / "frame"
            build_synthetic_trace(frame)
            replay = Path(tmp) / "replay"
            self._replay(replay, (frame / "frame-reference.bgra").read_bytes())
            report = m6_compare.compare(frame, replay, self._policy())
            self.assertEqual(report["status"], "pass")

    def test_pixel_difference_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            frame = Path(tmp) / "frame"
            build_synthetic_trace(frame)
            replay = Path(tmp) / "replay"
            image = bytearray((frame / "frame-reference.bgra").read_bytes())
            image[1] ^= 0xFF
            self._replay(replay, bytes(image))
            report = m6_compare.compare(frame, replay, self._policy())
            self.assertEqual(report["status"], "fail")
            self.assertGreater(report["statistics"]["mismatched_pixels"], 0)

    def test_exact_frame_passes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            frame = Path(tmp) / "frame"
            manifest = build_synthetic_trace(frame)
            replay = Path(tmp) / "replay"
            self._replay(replay, (frame / "frame-reference.bgra").read_bytes())
            policy = {
                "allowed_normalizations": ["row_origin"],
                "alpha": {"ignored": False},
                "thresholds": {
                    "max_channel_error": 0,
                    "mismatched_fraction": 0.0,
                    "mean_abs_error": 0.0,
                },
            }
            report = m6_compare.compare(frame, replay, policy)
            self.assertEqual(report["status"], "pass")
            self.assertEqual(report["statistics"]["mismatched_pixels"], 0)
            self.assertEqual(
                report["candidate"]["sha256"], manifest["frame"]["reference_sha256"]
            )

    def test_single_channel_difference_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            frame = Path(tmp) / "frame"
            build_synthetic_trace(frame)
            reference = bytearray((frame / "frame-reference.bgra").read_bytes())
            reference[0] = 255
            replay = Path(tmp) / "replay"
            self._replay(replay, bytes(reference))
            policy = {
                "allowed_normalizations": ["row_origin"],
                "alpha": {"ignored": False},
                "thresholds": {
                    "max_channel_error": 0,
                    "mismatched_fraction": 0.0,
                    "mean_abs_error": 0.0,
                },
            }
            report = m6_compare.compare(frame, replay, policy)
            self.assertEqual(report["status"], "fail")
            self.assertEqual(report["statistics"]["mismatched_pixels"], 1)
            self.assertIsNotNone(report["worst_region"])

    def test_failed_replay_is_refused(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            frame = Path(tmp) / "frame"
            build_synthetic_trace(frame)
            replay = Path(tmp) / "replay"
            self._replay(
                replay,
                (frame / "frame-reference.bgra").read_bytes(),
                failed=1,
            )
            with self.assertRaises(m6_compare.CompareError):
                m6_compare.compare(
                    frame, replay, {"allowed_normalizations": ["row_origin"]}
                )

    def test_extent_mismatch_is_refused(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            frame = Path(tmp) / "frame"
            build_synthetic_trace(frame)
            replay = Path(tmp) / "replay"
            self._replay(
                replay,
                (frame / "frame-reference.bgra").read_bytes(),
                frame={
                    "width": WIDTH + 1,
                    "height": HEIGHT,
                    "origin": "lower_left",
                    "raw_file": "metal-frame.bgra",
                    "raw_bytes": WIDTH * HEIGHT * 4,
                },
            )
            with self.assertRaises(m6_compare.CompareError):
                m6_compare.compare(
                    frame, replay, {"allowed_normalizations": ["row_origin"]}
                )

    def test_row_origin_normalization_requires_policy(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            frame = Path(tmp) / "frame"
            build_synthetic_trace(frame)
            image = (frame / "frame-reference.bgra").read_bytes()
            replay = Path(tmp) / "replay"
            self._replay(
                replay,
                image,
                frame={
                    "width": WIDTH,
                    "height": HEIGHT,
                    "origin": "top_left",
                    "raw_file": "metal-frame.bgra",
                    "raw_bytes": len(image),
                },
            )
            with self.assertRaises(m6_compare.CompareError):
                m6_compare.compare(frame, replay, {"allowed_normalizations": []})


class ShaderTranslationTests(unittest.TestCase):
    SOURCE = """#version 120
#define float4 vec4
#define float3 vec3
uniform sampler2D tex0;
uniform vec4 UNIFORM0[3];
#define Matrix mat4(UNIFORM0[0], UNIFORM0[1], UNIFORM0[2], UNIFORM0[3])
attribute float3 in_POSITION;
attribute float2 in_TEXCOORD0;
varying float2 v_TEXCOORD0;
void main()
{
    gl_Position = Matrix * float4(in_POSITION, 1.0);
    v_TEXCOORD0 = in_TEXCOORD0;
}
"""

    def test_uniform_block_layout_and_samplers(self) -> None:
        normalized = translate._normalize_source(self.SOURCE, "vertex")
        self.assertEqual(
            [(item["name"], item["offset"]) for item in normalized["uniforms"]],
            [("UNIFORM0", 0)],
        )
        self.assertEqual(
            normalized["samplers"],
            [{"name": "tex0", "type": "sampler2D", "binding": 0}],
        )
        attributes = {item["name"] for item in normalized["attributes"]}
        self.assertEqual(attributes, {"in_POSITION", "in_TEXCOORD0"})
        self.assertIn("layout(binding=0) uniform sampler2D tex0;", normalized["source"])
        self.assertIn("#define UNIFORM0 engine.UNIFORM0", normalized["source"])

    def test_size_one_array_stays_an_array(self) -> None:
        source = self.SOURCE.replace("UNIFORM0[3]", "UNIFORM0[1]")
        normalized = translate._normalize_source(source, "vertex")
        self.assertIn("vec4 UNIFORM0[1];", normalized["source"])

    def test_fragment_output_is_declared(self) -> None:
        source = self.SOURCE.replace(
            "gl_Position = Matrix * float4(in_POSITION, 1.0);",
            "gl_FragColor = vec4(1.0);",
        )
        normalized = translate._normalize_source(source, "pixel")
        self.assertIn("out vec4 fragColor;", normalized["source"])
        self.assertIn("fragColor = vec4(1.0);", normalized["source"])

    def test_std140_rejects_scalar_array(self) -> None:
        members, error = translate._std140_layout(
            [{"name": "X", "type": "float", "elements": 2, "array": True}]
        )
        self.assertEqual(members, [])
        self.assertIn("std140", error or "")


class ShaderCacheAttributeTests(unittest.TestCase):
    def test_trace_cache_records_attribute_types(self) -> None:
        import json

        from automation import gfx_shader_translate as translate

        report = {
            "programs": [
                {
                    "program": 1,
                    "status": "ok",
                    "stages": {
                        "vertex": {
                            "stage": "vertex",
                            "source_sha256": "a" * 64,
                            "compiler_sha256": "b" * 64,
                            "msl_file": "x.vert.metal",
                            "msl_entry": "main0",
                            "msl_uniform_buffer": 0,
                            "msl_samplers": [],
                            "attribute_locations": {"in_POSITION": 0,
                                                    "in_COLOR": 1},
                            "attributes": [
                                {"name": "in_POSITION", "type": "float4"},
                                {"name": "in_COLOR", "type": "int4"},
                            ],
                            "varyings": [],
                            "uniforms": [],
                            "samplers": [],
                        },
                        "pixel": {
                            "stage": "pixel",
                            "source_sha256": "c" * 64,
                            "compiler_sha256": "d" * 64,
                            "msl_file": "x.frag.metal",
                            "msl_entry": "main0",
                            "msl_uniform_buffer": 0,
                            "msl_samplers": [],
                            "attribute_locations": {},
                            "attributes": [],
                            "varyings": [],
                            "uniforms": [],
                            "samplers": [],
                        },
                    },
                }
            ]
        }
        with tempfile.TemporaryDirectory() as tmp:
            cache = translate.write_shader_cache(report, Path(tmp))
        vertex = cache["stages"]["b" * 64]
        self.assertEqual(
            vertex["attributes"],
            [
                {"name": "in_POSITION", "type": "float4"},
                {"name": "in_COLOR", "type": "int4"},
            ],
        )
        # The live loader reads the serialized document, not the in-memory dict.
        with tempfile.TemporaryDirectory() as tmp:
            translate.write_shader_cache(report, Path(tmp))
            reloaded = json.loads((Path(tmp) / "shader-cache.json").read_text())
        self.assertEqual(
            reloaded["stages"]["b" * 64]["attributes"],
            vertex["attributes"],
        )

    def test_live_merge_preserves_attribute_types(self) -> None:
        from automation import gfx_shader_translate as translate

        source = (
            "#version 120\n"
            "attribute vec4 in_POSITION;\n"
            "void main() { gl_Position = in_POSITION; }\n"
        )
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            sources = tmp_path / "sources"
            sources.mkdir()
            (sources / "live-1-vs-abc.glsl").write_text(source)
            prior = {
                "schema": "gfx-shader-cache/v1",
                "stages": {
                    "e" * 64: {
                        "stage": "vertex",
                        "attributes": [{"name": "in_POSITION", "type": "float4"}],
                    }
                },
                "programs": {},
            }
            (tmp_path / "prior.json").write_text(json.dumps(prior))
            report = translate.translate_live_sources(
                sources, tmp_path / "out", existing=tmp_path / "prior.json"
            )
            merged = report["stages"]
        self.assertIn("e" * 64, merged)
        self.assertEqual(
            merged["e" * 64]["attributes"],
            [{"name": "in_POSITION", "type": "float4"}],
        )


@unittest.skipUnless(sys.platform == "darwin", "the trace host needs Apple OpenGL")
class TraceStateHostTests(unittest.TestCase):
    def test_texture_probe_preserves_bindings_and_active_unit(self) -> None:
        source = ROOT / "tests" / "gfx_trace_state_host.m"
        trace_source = ROOT / "benchmark" / "eu4_gfx_frame_trace.c"
        with tempfile.TemporaryDirectory() as tmp:
            binary = Path(tmp) / "gfx_trace_state_host"
            built = subprocess.run(
                [
                    "clang", "-arch", "x86_64", "-O1", "-Wall", "-Wextra",
                    "-fobjc-arc", "-I", str(ROOT / "benchmark"),
                    "-o", str(binary), str(source), str(trace_source),
                    "-framework", "OpenGL", "-framework", "AppKit",
                ],
                capture_output=True, text=True, check=False,
            )
            self.assertEqual(built.returncode, 0, built.stderr)
            ran = subprocess.run([str(binary)], capture_output=True, text=True,
                                 check=False)
        self.assertEqual(ran.returncode, 0, ran.stderr)
        self.assertIn("trace_state=1", ran.stdout)


@unittest.skipUnless(sys.platform == "darwin", "the trace host needs Apple OpenGL")
class ManifestWriterTests(unittest.TestCase):
    def test_manifest_writer_emits_a_parseable_document(self) -> None:
        source = ROOT / "tests" / "gfx_frame_trace_host.c"
        trace_source = ROOT / "benchmark" / "eu4_gfx_frame_trace.c"
        with tempfile.TemporaryDirectory() as tmp:
            binary = Path(tmp) / "gfx_frame_trace_host"
            built = subprocess.run(
                [
                    "clang",
                    "-O1",
                    "-Wall",
                    "-Wextra",
                    "-I",
                    str(ROOT / "benchmark"),
                    "-o",
                    str(binary),
                    str(source),
                    str(trace_source),
                    "-framework",
                    "OpenGL",
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(built.returncode, 0, built.stderr)
            output = Path(tmp) / "out"
            ran = subprocess.run(
                [str(binary), str(output)], capture_output=True, text=True, check=False
            )
            self.assertEqual(ran.returncode, 0, ran.stderr)
            manifest = json.loads((output / "frame-trace.json").read_text())
            self.assertEqual(manifest["schema"], m6_frame.TRACE_SCHEMA)


if __name__ == "__main__":
    unittest.main()
