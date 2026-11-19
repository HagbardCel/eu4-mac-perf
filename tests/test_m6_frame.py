"""M6 frame capture, translation, replay and comparison semantics.

These tests are offline and synthetic where a live frame is not required. They
pin the fail-closed behavior of the new tooling: a trace is only usable when its
manifest, payload digests, events and coverage agree, and a differential only
passes when both sides' provenance validates and every pixel matches the
declared policy.
"""

from __future__ import annotations

import contextlib
import hashlib
import io
import json
import os
import struct
import subprocess
import sys
import tempfile
from unittest import mock
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

    def test_balanced_but_malformed_json_is_refused_by_python_loader(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            frame = Path(tmp) / "frame"
            build_synthetic_trace(frame)
            lines = (frame / "frame-events.jsonl").read_text().splitlines()
            # This line satisfies the native framing checks (leading '{',
            # trailing '}', containing "kind") but is not valid JSON.
            lines[0] = '{"kind":"draw",}'
            (frame / "frame-events.jsonl").write_text("\n".join(lines) + "\n")
            with self.assertRaises(m6_frame.FrameError):
                m6_frame.load_trace(frame)

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

    def test_coverage_understands_supported_transfers(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            frame = Path(tmp) / "frame"
            build_synthetic_trace(frame)
            events = [
                json.loads(line)
                for line in (frame / "frame-events.jsonl").read_text().splitlines()
            ]
            events.append({
                "kind": "resolve", "operation": "glCopyTexSubImage2D",
                "is_blit": False, "source_framebuffer": 0,
                "read_buffer": 0x0405, "source": "0",
                "destination": "10", "source_level": 0,
                "destination_level": 0, "source_resolved": True,
                "destination_resolved": True,
                "source_rect": [0, 0, WIDTH, HEIGHT],
                "destination_offset": [0, 0], "width": WIDTH,
                "height": HEIGHT, "source_width": WIDTH,
                "source_height": HEIGHT, "source_internal_format": 0,
                "destination_width": WIDTH, "destination_height": HEIGHT,
                "internal_format": 0x8058,
            })
            sampled = json.loads(json.dumps(events[1]))
            sampled["target"] = {
                "framebuffer": 1, "width": WIDTH, "height": HEIGHT,
                "color_attachment": {
                    "type": "texture", "name": "10", "level": 0,
                    "internal_format": "RGBA8", "width": WIDTH,
                    "height": HEIGHT,
                },
            }
            sampled["textures"][0]["name"] = 10
            events.append(sampled)
            (frame / "frame-events.jsonl").write_text(
                "\n".join(json.dumps(event) for event in events) + "\n")
            manifest = json.loads((frame / "frame-trace.json").read_text())
            manifest["events"] = len(events)
            (frame / "frame-trace.json").write_text(json.dumps(manifest))
            coverage = m6_frame.coverage_report(m6_frame.load_trace(frame))
            self.assertEqual(coverage["resolve_count"], 1)
            self.assertEqual(coverage["copy_count"], 0)
            self.assertEqual(coverage["transfer_count"], 1)
            self.assertFalse(any(item["issue"] == "unknown_event_kind"
                                 for item in coverage["missing"]))
            self.assertTrue(coverage["ok"], coverage["missing"])

    def test_invalid_transfer_is_reported(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            frame = Path(tmp) / "frame"
            build_synthetic_trace(frame)
            events = [
                json.loads(line)
                for line in (frame / "frame-events.jsonl").read_text().splitlines()
            ]
            events.append({"kind": "resolve", "operation": "glBlitFramebuffer"})
            (frame / "frame-events.jsonl").write_text(
                "\n".join(json.dumps(event) for event in events) + "\n")
            manifest = json.loads((frame / "frame-trace.json").read_text())
            manifest["events"] = len(events)
            (frame / "frame-trace.json").write_text(json.dumps(manifest))
            coverage = m6_frame.coverage_report(m6_frame.load_trace(frame))
            self.assertEqual(coverage["resolve_count"], 1)
            self.assertTrue(any(item["issue"] == "invalid_transfer"
                                for item in coverage["missing"]))

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


class ShaderReceiptIdentityTests(unittest.TestCase):
    """One owner for the three shader-receipt fields, with a pinned digest."""

    FILES = {
        "shaders.json": b'{"schema":"test"}\n',
        "nested/a.metal": b"// a\n",
        "nested/deep/b.metal": b"// b\n",
        "ready": b'{"status":"ready"}\n',
        "shader-receipt.json": b'{"schema":"receipt"}\n',
    }
    # Independently precomputed from FILES, sorted by relative path, with the
    # two publish markers excluded.  A production-helper bug must not be able
    # to redefine the expected value.
    EXPECTED_DIGEST = (
        "af863d3f805230d2681c7e68cf8ed9efa00bc1bd01b6879e893796ffd7078e0c")

    def write(self, directory: Path, *,
              files: dict | None = None) -> Path:
        for name, payload in (files or self.FILES).items():
            path = directory / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(payload)
        return directory

    def test_nested_tree_digest_and_count_are_pinned(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            shader_dir = self.write(Path(temporary) / "shaders")
            identity = m6_frame.shader_receipt_identity(shader_dir)
        self.assertEqual(identity["artifacts_sha256"], self.EXPECTED_DIGEST)
        self.assertEqual(identity["artifact_count"], 3)
        self.assertEqual(
            identity["shaders_json_sha256"],
            hashlib.sha256(self.FILES["shaders.json"]).hexdigest())

    def test_creation_order_does_not_change_the_digest(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            first = Path(temporary) / "first"
            second = Path(temporary) / "second"
            self.write(first)
            self.write(second, files=dict(reversed(list(self.FILES.items()))))
            one = m6_frame.shader_receipt_identity(first)
            two = m6_frame.shader_receipt_identity(second)
        self.assertEqual(one, two)

    def test_tampered_payload_changes_the_digest(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            shader_dir = self.write(Path(temporary) / "shaders")
            before = m6_frame.shader_receipt_identity(shader_dir)
            (shader_dir / "nested" / "a.metal").write_bytes(b"// altered\n")
            after = m6_frame.shader_receipt_identity(shader_dir)
        self.assertNotEqual(before["artifacts_sha256"],
                            after["artifacts_sha256"])
        self.assertEqual(before["artifact_count"], after["artifact_count"])

    def test_optional_reader_returns_empty_for_missing_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            missing = Path(temporary) / "absent"
            self.assertEqual(
                m6_frame.shader_receipt_identity(missing),
                {"shaders_json_sha256": "", "artifacts_sha256": "",
                 "artifact_count": 0})
            self.assertEqual(
                m6_frame.shader_receipt_identity(None)["artifact_count"], 0)
            no_report = self.write(
                Path(temporary) / "no-report",
                files={"nested/a.metal": b"// a\n"})
            self.assertEqual(
                m6_frame.shader_receipt_identity(no_report)["artifact_count"],
                0)

    def test_live_publication_requires_the_shader_report(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            missing = Path(temporary) / "absent"
            with self.assertRaises(m6_frame.FrameError):
                m6_frame.shader_receipt_identity(missing, required=True)
            no_report = self.write(
                Path(temporary) / "no-report",
                files={"nested/a.metal": b"// a\n"})
            with self.assertRaises(m6_frame.FrameError):
                m6_frame.shader_receipt_identity(no_report, required=True)


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

    def test_sampler_binding_preserves_tex_unit_number(self) -> None:
        source = (
            "#version 120\n"
            "uniform sampler2D tex0;\n"
            "uniform sampler2D tex4;\n"
            "uniform sampler2D tex7;\n"
            "void main() { gl_FragColor = vec4(1.0); }\n"
        )
        normalized = translate._normalize_source(source, "pixel")
        self.assertEqual(
            [(item["name"], item["binding"]) for item in normalized["samplers"]],
            [("tex0", 0), ("tex4", 4), ("tex7", 7)],
        )

    def test_non_numbered_sampler_does_not_displace_tex_units(self) -> None:
        source = (
            "#version 120\n"
            "uniform sampler2D palette;\n"
            "uniform sampler2D tex0;\n"
            "uniform sampler2D tex4;\n"
            "uniform sampler2D tex7;\n"
            "void main() { gl_FragColor = vec4(1.0); }\n"
        )
        normalized = translate._normalize_source(source, "pixel")
        self.assertEqual(
            [(item["name"], item["binding"]) for item in normalized["samplers"]],
            [("palette", 1), ("tex0", 0), ("tex4", 4), ("tex7", 7)],
        )

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

    def test_compiler_input_keeps_texture_lod_extension(self) -> None:
        source = (
            "#version 120\n"
            "#extension GL_ARB_shader_texture_lod : enable\n"
            "#ifdef NO_SHADER_TEXTURE_LOD\n"
            "float3 normal = float3(0, 1, 0);\n"
            "#else\n"
            "float3 normal = tex2Dlod(normalTex, uv).rgb;\n"
            "#endif\n"
        )
        compiled = translate.compiler_input(source)
        self.assertIn("#extension GL_ARB_shader_texture_lod : enable", compiled)
        self.assertNotIn("#define NO_SHADER_TEXTURE_LOD", compiled)
        self.assertIn("tex2Dlod(normalTex, uv)", compiled)

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
                    str(ROOT / "benchmark" / "eu4_gfx_frame_observe.c"),
                    "-framework", "OpenGL", "-framework", "AppKit",
                ],
                capture_output=True, text=True, check=False,
            )
            self.assertEqual(built.returncode, 0, built.stderr)
            capture_dir = Path(tmp) / "capture"
            capture_dir.mkdir()
            ran = subprocess.run([str(binary), str(capture_dir)],
                                 capture_output=True, text=True, check=False)
            reference = capture_dir / "frame-reference.bgra"
            self.assertTrue(reference.is_file())
            self.assertEqual(reference.stat().st_size, 4 * 4 * 4)
        self.assertEqual(ran.returncode, 0, ran.stderr)
        self.assertIn("trace_state=1", ran.stdout)
        self.assertIn("reference=1", ran.stdout)


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
                    str(ROOT / "benchmark" / "eu4_gfx_frame_observe.c"),
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


@unittest.skipUnless(sys.platform == "darwin", "the replay host needs Metal")
class IsolationOrderingTests(unittest.TestCase):
    """The isolated draw must start from the supplied GL image, not a clear.

    A four-pixel triangle strip paints one 2x2 block; every other pixel must
    retain the distinctive pre-image values. The pre-fix harness encoded a
    black clear into the frame command buffer and committed it after the
    pre-image blit, so the unaffected pixels became black.
    """

    VERTEX = """#include <metal_stdlib>
using namespace metal;
struct VOut { float4 position [[position]]; };
vertex VOut test_vertex(uint vid [[vertex_id]]) {
    float2 p[4] = { float2(-1.0, -1.0), float2(1.0, -1.0),
                    float2(-1.0, 1.0), float2(1.0, 1.0) };
    VOut out;
    out.position = float4(p[vid], 0.0, 1.0);
    return out;
}
"""

    FRAGMENT = """#include <metal_stdlib>
using namespace metal;
struct FOut { float4 color [[color(0)]]; };
fragment FOut test_fragment(float4 position [[position]]) {
    uint x = uint(position.x);
    uint y = uint(position.y);
    if (x >= 2 && x < 4 && y >= 2 && y < 4) {
        FOut out;
        out.color = float4(1.0, 0.0, 0.0, 1.0);
        return out;
    }
    discard_fragment();
}
"""

    WIDTH = 8
    HEIGHT = 6

    def _write(self, path: Path, data: bytes) -> dict:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return {
            "file": path.name,
            "bytes": len(data),
            "sha256": _sha(data),
        }

    def _frame(self, root: Path) -> tuple[Path, Path, bytes]:
        frame = root / "frame"
        blobs = frame / "blobs"
        width, height = self.WIDTH, self.HEIGHT
        pre = bytearray()
        for y in range(height):
            for x in range(width):
                pre += bytes([(x * 7 + y * 3) & 0xFF, (x * 5 + y * 11) & 0xFF,
                              180 + x, 255])
        pre = bytes(pre)
        pre_member = self._write(blobs / "pre-image.bin", pre)
        post_member = self._write(blobs / "post-image.bin", pre)
        reference = bytes([9, 8, 7, 255] * (width * height))
        (frame / "frame-reference.bgra").write_bytes(reference)
        event = {
            "kind": "draw",
            "index": 0,
            "pre_image": {
                "width": width,
                "height": height,
                "file": pre_member["file"],
                "sha256": pre_member["sha256"],
            },
            "post_image": {
                "width": width,
                "height": height,
                "file": post_member["file"],
                "sha256": post_member["sha256"],
            },
            "state": {
                "blend": {"enabled": False},
                "color_mask": [1, 1, 1, 1],
                "depth": {"test": False, "func": "0x203", "write": False},
                "stencil": {"test": False},
                "cull": {"enabled": False, "mode": "0x405", "front": "0x901"},
                "scissor": {"enabled": False, "box": [0, 0, width, height]},
                "viewport": [0, 0, width, height],
            },
            "program_diagnostics": {
                "active_attributes": 0,
                "active_uniforms": 0,
                "link": True,
                "enabled_bits": [0] * 16,
            },
            "program": {"name": 1, "shaders": [], "uniforms": []},
            "textures": [],
            "target": {
                "framebuffer": 0,
                "width": width,
                "height": height,
                "viewport": [0, 0, width, height],
            },
            "attributes": [],
            "draw": {
                "entry": "_GfxDraw",
                "mode": 5,  # GL_TRIANGLE_STRIP; mode 3 is a line strip
                "first": 0,
                "count": 4,
                "instance_count": 1,
                "indexed": False,
                "base_vertex": 0,
                "caller_rva": "0x123",
            },
        }
        manifest = {
            "schema": m6_frame.TRACE_SCHEMA,
            "complete": True,
            "reason": "present",
            "truncated": False,
            "pid": 1,
            "present_index": 1,
            "events": 1,
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
            "selection": {"callers": ["0x123"], "min_draws": 1,
                          "occurrence": 1, "required": True},
            "seed": {"width": width, "height": height},
            "blobs": 1,
        }
        (frame / "frame-trace.json").write_text(json.dumps(manifest))
        (frame / "frame-events.jsonl").write_text(json.dumps(event) + "\n")
        shaders = root / "shaders"
        shaders.mkdir()
        (shaders / "test.vert.metal").write_text(self.VERTEX)
        (shaders / "test.frag.metal").write_text(self.FRAGMENT)
        (shaders / "shaders.json").write_text(json.dumps({
            "programs": [{
                "program": 1,
                "status": "ok",
                "stages": {
                    "vertex": {
                        "msl_file": "test.vert.metal",
                        "msl_entry": "test_vertex",
                        "attributes": [],
                        "attribute_locations": {},
                        "uniforms": [],
                    },
                    "pixel": {
                        "msl_file": "test.frag.metal",
                        "msl_entry": "test_fragment",
                        "attributes": [],
                        "attribute_locations": {},
                        "uniforms": [],
                    },
                },
            }]
        }))
        return frame, shaders, pre

    def test_replay_divisor_and_constant_descriptor(self) -> None:
        host = m6_frame.build_replay_host(force=True)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            frame, shaders, pre = self._frame(root)
            events = [json.loads(line) for line in
                      (frame / "frame-events.jsonl").read_text().splitlines()]
            event = events[0]
            colors = b"".join(struct.pack("<3f", *c) for c in
                              [(0.0, 1.0, 0.0), (1.0, 0.0, 0.0),
                               (0.0, 0.0, 1.0)])
            blob = self._write(frame / "blobs" / "color.bin", colors)
            event["attributes"] = [
                {
                    "name": "in_COLOR", "location": 1, "components": 3,
                    "gl_type": "0x1406", "normalized": False, "stride": 12,
                    "divisor": 1, "enabled": True, "first_vertex": 0,
                    "last_vertex": 2, "data": blob["file"],
                    "bytes": blob["bytes"], "sha256": blob["sha256"],
                },
                {
                    "name": "in_CONSTANT", "location": 2, "components": 4,
                    "gl_type": "0x1406", "normalized": False, "stride": 0,
                    "divisor": 0, "enabled": False,
                    "constant_value": [0.0, 0.0, 1.0, 1.0],
                },
            ]
            (frame / "frame-events.jsonl").write_text(json.dumps(event) + "\n")
            manifest = json.loads((frame / "frame-trace.json").read_text())
            manifest["blobs"] = manifest.get("blobs", 0) + 1
            (frame / "frame-trace.json").write_text(json.dumps(manifest))
            env = dict(os.environ)
            env["EU4_FRAME_REPLAY_DEBUG_DESCRIPTOR"] = "1"
            out = root / "replay"
            ran = subprocess.run(
                [str(host), "--frame", str(frame), "--shaders", str(shaders),
                 "--out", str(out), "--only-event", "0", "--capture-post"],
                capture_output=True, text=True, check=False, timeout=120,
                env=env)
            self.assertEqual(ran.returncode, 0, ran.stderr + ran.stdout)
            # MTLVertexStepFunction: Constant=0, PerVertex=1, PerInstance=2.
            self.assertIn("step 2 rate 1", ran.stderr)
            self.assertIn("step 0 rate 0", ran.stderr)
            post = (out / "isolated-post-0.bgra").read_bytes()
            self.assertNotEqual(post, pre)

            # A one-event divisor-2 / three-instance frame checks the actual
            # replay descriptor without relying on multi-event isolation.
            root2 = Path(tmp) / "second"
            root2.mkdir()
            frame2, shaders2, _ = self._frame(root2)
            events2 = [json.loads(line) for line in
                       (frame2 / "frame-events.jsonl").read_text().splitlines()]
            event2 = events2[0]
            blob2 = self._write(frame2 / "blobs" / "color.bin", colors)
            event2["attributes"] = [{
                "name": "in_COLOR", "location": 1, "components": 3,
                "gl_type": "0x1406", "normalized": False, "stride": 12,
                "divisor": 2, "enabled": True, "first_vertex": 0,
                "last_vertex": 2, "data": blob2["file"],
                "bytes": blob2["bytes"], "sha256": blob2["sha256"],
            }]
            event2["draw"]["instance_count"] = 3
            (frame2 / "frame-events.jsonl").write_text(json.dumps(event2) + "\n")
            manifest2 = json.loads((frame2 / "frame-trace.json").read_text())
            manifest2["blobs"] = manifest2.get("blobs", 0) + 1
            (frame2 / "frame-trace.json").write_text(json.dumps(manifest2))
            out2 = root2 / "replay"
            ran2 = subprocess.run(
                [str(host), "--frame", str(frame2), "--shaders", str(shaders2),
                 "--out", str(out2), "--only-event", "0"],
                capture_output=True, text=True, check=False, timeout=120,
                env=env)
            self.assertEqual(ran2.returncode, 0, ran2.stderr + ran2.stdout)
            self.assertIn("step 2 rate 2", ran2.stderr)

    def test_pre_image_survives_the_selected_draw(self) -> None:
        host = m6_frame.build_replay_host(force=True)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            frame, shaders, pre = self._frame(root)
            out = root / "replay"
            ran = subprocess.run(
                [
                    str(host), "--frame", str(frame), "--shaders", str(shaders),
                    "--out", str(out), "--only-event", "0", "--seed-marked",
                    "--capture-pre", "--capture-post",
                ],
                capture_output=True, text=True, check=False, timeout=120,
            )
            self.assertEqual(ran.returncode, 0, ran.stderr + ran.stdout)
            replay = json.loads((out / "replay.json").read_text())
            isolated = replay["isolated"]
            self.assertEqual(isolated["color_initialization"], "supplied_pre_image")
            self.assertTrue(isolated["pre_capture_ok"], isolated)
            self.assertEqual(isolated["depth_contents"], "not_required")
            pre_capture = (out / "isolated-pre-0.bgra").read_bytes()
            post_capture = (out / "isolated-post-0.bgra").read_bytes()
            self.assertEqual(pre_capture, pre)
            width, height = self.WIDTH, self.HEIGHT
            self.assertEqual(len(post_capture), width * height * 4)
            changed = 0
            for pixel in range(width * height):
                before = pre[pixel * 4:pixel * 4 + 4]
                after = post_capture[pixel * 4:pixel * 4 + 4]
                if before == after:
                    continue
                changed += 1
                self.assertEqual(after, bytes([0, 0, 255, 255]), pixel)
            self.assertEqual(changed, 4)
            self.assertEqual(isolated["pre_image"]["sha256"],
                             hashlib.sha256(pre).hexdigest())


@unittest.skipUnless(sys.platform == "darwin", "the replay host needs Metal")
class SparseIndexReplayTests(unittest.TestCase):
    """Capture-format/replay regression for sparse indices and a nonzero origin.

    This builds a frame-trace document in the captured format and replays it
    through the production frame replay host. It does not exercise the OpenGL
    capture writer, so it covers the capture-format fields and the replay-side
    rebasing, not the GL recorder that produces them.

    The fixture records 16-bit indices ``[3, 5, 7]`` for a vertex buffer whose
    captured slice begins at vertex 5, and the draw carries signed base vertex
    2. The only valid rebasing is ``index + base - origin`` -> ``[0, 2, 4]``.
    The negative case appends off-screen vertices so that dropping the captured
    origin still produces a successfully executed draw, but not the captured
    triangle; it cannot pass the positive assertion.
    """

    VERTEX = """#include <metal_stdlib>
using namespace metal;
struct VIn { float2 position [[attribute(0)]]; };
struct VOut { float4 position [[position]]; };
vertex VOut test_vertex(VIn in [[stage_in]]) {
    VOut out;
    out.position = float4(in.position, 0.0, 1.0);
    return out;
}
"""

    FRAGMENT = """#include <metal_stdlib>
using namespace metal;
struct FOut { float4 color [[color(0)]]; };
fragment FOut test_fragment() {
    FOut out;
    out.color = float4(1.0, 0.0, 0.0, 1.0);
    return out;
}
"""

    WIDTH = 8
    HEIGHT = 6
    VERTICES = 5
    RED = bytes([0, 0, 255, 255])

    def _write(self, path: Path, data: bytes) -> dict:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return {
            "file": path.name,
            "bytes": len(data),
            "sha256": _sha(data),
        }

    def _pre_image(self) -> bytes:
        pixels = bytearray()
        for y in range(self.HEIGHT):
            for x in range(self.WIDTH):
                pixels += bytes([(x * 11 + y * 3) & 0xFF,
                                 (x * 5 + y * 13) & 0xFF,
                                 180 + x, 255])
        return bytes(pixels)

    def _frame(self, root: Path, *, record_origin: bool,
               vertices: int | None = None) -> tuple[Path, Path, bytes]:
        frame = root / "frame"
        blobs = frame / "blobs"
        width, height = self.WIDTH, self.HEIGHT
        pre = self._pre_image()
        pre_member = self._write(blobs / "pre-image.bin", pre)
        post_member = self._write(blobs / "post-image.bin", pre)
        reference = bytes([9, 8, 7, 255] * (width * height))
        (frame / "frame-reference.bgra").write_bytes(reference)

        # Vertices 0/2/4 form the visible triangle; 1/3 are far outside clip
        # space. Extra vertices stay off-screen so the negative case can keep
        # the rebased indices in range while fetching the wrong vertices.
        positions = [(-1.0, -1.0), (99.0, 99.0), (1.0, -1.0),
                     (99.0, 99.0), (-1.0, 1.0)]
        count = vertices if vertices is not None else self.VERTICES
        self.assertGreaterEqual(count, len(positions))
        positions.extend([(99.0, 99.0)] * (count - len(positions)))
        vertex_data = b"".join(struct.pack("<2f", *value) for value in positions)
        vertex_member = self._write(blobs / "vertices.bin", vertex_data)
        index_member = self._write(blobs / "indices.bin", struct.pack("<3H", 3, 5, 7))

        indices = {
            "buffer": 4,
            "bytes": index_member["bytes"],
            "count": 3,
            "instance_count": 1,
            "file": index_member["file"],
            "sha256": index_member["sha256"],
            "range_valid": True,
            "read_error": False,
        }
        if record_origin:
            indices["vertex_origin"] = 5

        event = {
            "kind": "draw",
            "index": 0,
            "pre_image": {
                "width": width,
                "height": height,
                "file": pre_member["file"],
                "sha256": pre_member["sha256"],
            },
            "post_image": {
                "width": width,
                "height": height,
                "file": post_member["file"],
                "sha256": post_member["sha256"],
            },
            "state": {
                "blend": {"enabled": False},
                "color_mask": [1, 1, 1, 1],
                "depth": {"test": False, "func": "0x203", "write": False},
                "stencil": {"test": False},
                "cull": {"enabled": False, "mode": "0x405", "front": "0x901"},
                "scissor": {"enabled": False, "box": [0, 0, width, height]},
                "viewport": [0, 0, width, height],
            },
            "program_diagnostics": {
                "active_attributes": 1,
                "active_uniforms": 0,
                "link": True,
                "enabled_bits": [1] + [0] * 15,
            },
            "program": {"name": 1, "shaders": [], "uniforms": []},
            "textures": [],
            "target": {
                "framebuffer": 0,
                "width": width,
                "height": height,
                "viewport": [0, 0, width, height],
            },
            "attributes": [{
                "index": 0,
                "name": "in_POSITION",
                "location": 0,
                "enabled": True,
                "components": 2,
                "gl_type": "0x1406",
                "normalized": False,
                "stride": 8,
                "offset": 0,
                "divisor": 0,
                "buffer": 3,
                "range_bytes": vertex_member["bytes"],
                "data": vertex_member["file"],
                "sha256": vertex_member["sha256"],
            }],
            "indices": indices,
            "draw": {
                "entry": "_GfxDrawIndexed",
                "mode": 5,  # GL_TRIANGLE_STRIP
                "first": 0,
                "count": 3,
                "instance_count": 1,
                "indexed": True,
                "base_vertex": 2,
                "caller_rva": "0x456",
            },
        }
        manifest = {
            "schema": m6_frame.TRACE_SCHEMA,
            "complete": True,
            "reason": "present",
            "truncated": False,
            "pid": 1,
            "present_index": 1,
            "events": 1,
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
            "selection": {"callers": ["0x456"], "min_draws": 1,
                          "occurrence": 1, "required": True},
            "seed": {"width": width, "height": height},
            "blobs": 4,
        }
        (frame / "frame-trace.json").write_text(json.dumps(manifest))
        (frame / "frame-events.jsonl").write_text(json.dumps(event) + "\n")

        shaders = root / "shaders"
        shaders.mkdir()
        (shaders / "sparse.vert.metal").write_text(self.VERTEX)
        (shaders / "sparse.frag.metal").write_text(self.FRAGMENT)
        (shaders / "shaders.json").write_text(json.dumps({
            "programs": [{
                "program": 1,
                "status": "ok",
                "stages": {
                    "vertex": {
                        "msl_file": "sparse.vert.metal",
                        "msl_entry": "test_vertex",
                        "attributes": [],
                        "attribute_locations": {},
                        "uniforms": [],
                    },
                    "pixel": {
                        "msl_file": "sparse.frag.metal",
                        "msl_entry": "test_fragment",
                        "attributes": [],
                        "attribute_locations": {},
                        "uniforms": [],
                    },
                },
            }]
        }))
        return frame, shaders, pre

    def _replay(self, root: Path, frame: Path, shaders: Path) -> subprocess.CompletedProcess:
        out = root / "replay"
        host = m6_frame.build_replay_host(force=True)
        return subprocess.run(
            [str(host), "--frame", str(frame), "--shaders", str(shaders),
             "--out", str(out), "--only-event", "0", "--seed-marked",
             "--capture-pre", "--capture-post"],
            capture_output=True, text=True, check=False, timeout=120,
        )

    def test_sparse_index_with_nonzero_origin_rebases_into_the_capture(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            frame, shaders, pre = self._frame(root, record_origin=True)
            ran = self._replay(root, frame, shaders)
            self.assertEqual(ran.returncode, 0, ran.stderr + ran.stdout)
            post = (root / "replay" / "isolated-post-0.bgra").read_bytes()
            self.assertEqual(len(post), self.WIDTH * self.HEIGHT * 4)
            changed = 0
            for pixel in range(self.WIDTH * self.HEIGHT):
                before = pre[pixel * 4:pixel * 4 + 4]
                after = post[pixel * 4:pixel * 4 + 4]
                if before == after:
                    continue
                changed += 1
                self.assertEqual(after, self.RED, pixel)
            # The visible triangle covers part, not all, of the target.
            self.assertGreater(changed, 0)
            self.assertLess(changed, self.WIDTH * self.HEIGHT)

    def test_missing_origin_cannot_fetch_the_captured_vertices(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            # Ten vertices keep the unrebased indices 5/7/9 inside the buffer.
            # They are off-screen, so the draw completes without reproducing
            # the captured triangle.
            frame, shaders, pre = self._frame(
                root, record_origin=False, vertices=10)
            ran = self._replay(root, frame, shaders)
            self.assertEqual(ran.returncode, 0, ran.stderr + ran.stdout)
            result = json.loads(ran.stdout)
            self.assertEqual(result["status"], "ok")
            self.assertEqual(result["encoded"], 1)
            self.assertEqual(result["failed"], 0)
            post = (root / "replay" / "isolated-post-0.bgra").read_bytes()
            self.assertEqual(post, pre)
            for pixel in range(self.WIDTH * self.HEIGHT):
                before = pre[pixel * 4:pixel * 4 + 4]
                after = post[pixel * 4:pixel * 4 + 4]
                self.assertEqual(after, before,
                                 f"pixel {pixel} exposed a falsely correct draw")


if __name__ == "__main__":
    unittest.main()


class CaptureBudgetTests(unittest.TestCase):
    def test_default_event_budget_covers_the_authentic_scene(self):
        args = m6_frame._parser().parse_args(
            ["capture", "--output", "/tmp/frame-capture"])
        self.assertGreaterEqual(args.max_draws, 384)
        self.assertEqual(args.max_draws, 512)

    def test_default_checkpoint_option_is_opt_in(self):
        args = m6_frame._parser().parse_args(
            ["capture", "--output", "/tmp/frame-capture"])
        self.assertIsNone(args.default_checkpoint_event)
        args = m6_frame._parser().parse_args(
            ["capture", "--output", "/tmp/frame-capture",
             "--default-checkpoint-event", "142"])
        self.assertEqual(args.default_checkpoint_event, 142)

    def test_default_checkpoint_selection_is_validated(self):
        self.assertIsNone(m6_frame.checkpoint_selection_error(142, 512))
        self.assertIn("non-negative",
                      m6_frame.checkpoint_selection_error(-1, 512))
        self.assertIn("below",
                      m6_frame.checkpoint_selection_error(512, 512))
        self.assertIn("below",
                      m6_frame.checkpoint_selection_error(513, 512))

    def test_multiple_default_checkpoint_option_parses_an_ordered_set(self):
        args = m6_frame._parser().parse_args(
            ["capture", "--output", "/tmp/frame-capture",
             "--default-checkpoint-events", "14,20,71,142"])
        self.assertEqual(args.default_checkpoint_events, [14, 20, 71, 142])
        self.assertIsNone(args.default_checkpoint_event)
        self.assertEqual(args.max_checkpoints, 4)
        self.assertEqual(args.checkpoint_max_bytes, 64 * 1024 * 1024)
        self.assertIsNone(m6_frame.checkpoint_options_error(args))

    def test_multiple_default_checkpoint_malformed_list_is_rejected(self):
        for value in ("", "14,,20", "14,a", ","):
            with self.subTest(value=value):
                with self.assertRaises(SystemExit):
                    with contextlib.redirect_stderr(io.StringIO()):
                        m6_frame._parser().parse_args(
                            ["capture", "--output", "/tmp/frame-capture",
                             "--default-checkpoint-events", value])

    def test_multiple_default_checkpoint_selection_is_validated(self):
        self.assertIsNone(m6_frame.checkpoint_events_selection_error(
            [14, 20, 71, 142], 512, 4))
        self.assertIn("strictly increasing",
                      m6_frame.checkpoint_events_selection_error(
                          [20, 14], 512, 4))
        self.assertIn("strictly increasing",
                      m6_frame.checkpoint_events_selection_error(
                          [14, 14], 512, 4))
        self.assertIn("below",
                      m6_frame.checkpoint_events_selection_error(
                          [512], 512, 4))
        self.assertIn("exceed",
                      m6_frame.checkpoint_events_selection_error(
                          [1, 2, 3, 4, 5], 512, 4))
        self.assertIn("empty",
                      m6_frame.checkpoint_events_selection_error(
                          [], 512, 4))

    def test_checkpoint_byte_budget_is_forwarded_for_both_selection_forms(self):
        import eu4_gfx_startup_trace as startup

        with tempfile.TemporaryDirectory() as temporary:
            for index, extra in enumerate((
                    ["--default-checkpoint-event", "14"],
                    ["--default-checkpoint-events", "14,20"])):
                with self.subTest(selection=extra[0]):
                    output = str(Path(temporary) / f"budget-{index}")
                    args = m6_frame._parser().parse_args(
                        ["capture", "--output", output,
                         "--checkpoint-max-bytes", "12345", *extra])
                    seen = {}

                    def fake_run_ops(**kwargs):
                        seen.update(kwargs)
                        raise RuntimeError("stop after environment capture")

                    with mock.patch.object(startup, "preflight_ops",
                                           return_value={}), \
                         mock.patch.object(startup, "run_ops",
                                           side_effect=fake_run_ops):
                        status = m6_frame.capture(args)
                    self.assertEqual(status, 4)
                    self.assertEqual(
                        seen["env_extra"].get(
                            "EU4_GFX_FRAME_TRACE_CHECKPOINT_MAX_BYTES"),
                        "12345")

    def test_checkpoint_options_refuse_both_selection_forms(self):
        args = m6_frame._parser().parse_args(
            ["capture", "--output", "/tmp/frame-capture",
             "--default-checkpoint-event", "14",
             "--default-checkpoint-events", "14,20"])
        self.assertIn("mutually exclusive",
                      m6_frame.checkpoint_options_error(args))

    def test_checkpoint_options_require_a_positive_byte_budget(self):
        args = m6_frame._parser().parse_args(
            ["capture", "--output", "/tmp/frame-capture",
             "--default-checkpoint-events", "14,20",
             "--checkpoint-max-bytes", "0"])
        self.assertIn("positive", m6_frame.checkpoint_options_error(args))
        # An unbounded selection has no checkpoint budget to enforce.
        args = m6_frame._parser().parse_args(
            ["capture", "--output", "/tmp/frame-capture",
             "--checkpoint-max-bytes", "0"])
        self.assertIsNone(m6_frame.checkpoint_options_error(args))
