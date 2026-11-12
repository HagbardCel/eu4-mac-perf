#!/usr/bin/env python3
"""Fail-closed validation of original-GL and presentation evidence."""

from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from automation import live_gfx

EXE = "a" * 64
LIB = "b" * 64
CALLER = 0xEDFCD1


class GlReferenceValidationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.width = 4
        self.height = 3
        self.payload = b"".join(
            bytes(((x * 7 + y * 11) % 256, (x * 13) % 256, (y * 17) % 256, 255))
            for y in range(self.height)
            for x in range(self.width)
        )
        (self.root / "gl-reference.bgra").write_bytes(self.payload)
        self.descriptor = {
            "schema": "gfx-gl-reference/v1",
            "backend": "opengl",
            "provenance": {
                "executable": "/Applications/EuropaUniversalisIV/eu4.app/Contents/MacOS/eu4",
                "executable_sha256": EXE,
                "library_sha256": LIB,
                "session": "s",
                "pid": 1,
            },
            "draw": {
                "symbol": "_GfxDraw",
                "call_id": 4,
                "caller_rva": f"0x{CALLER:x}",
                "caller_known": True,
                "thread_id": 1,
                "mode": 5,
                "effective_count": 4,
                "first": 0,
            },
            "target": {
                "width": self.width,
                "height": self.height,
                "row_stride": self.width * 4,
                "pixel_format": "BGRA8",
                "origin": "lower_left",
                "viewport": [0, 0, self.width, self.height],
            },
            "gpu": {
                "finish": "glFinish",
                "finish_ns": 5,
                "read_pixels": True,
                "read_ns": 5,
                "gl_error": 0,
            },
            "state": {"program": 3},
            "capture": {
                "raw_file": "gl-reference.bgra",
                "raw_bytes": len(self.payload),
                "raw_sha256": hashlib.sha256(self.payload).hexdigest(),
                "pixel_format": "BGRA8",
            },
            "pre_draw": None,
        }
        self.write()

    def write(self) -> None:
        (self.root / "gl-reference.json").write_text(json.dumps(self.descriptor))

    def evidence(self) -> dict:
        return {"executable_sha256": EXE}

    def test_valid_reference_is_accepted(self) -> None:
        block = live_gfx._gl_reference_block(self.root, self.evidence(), CALLER)
        self.assertTrue(block["ok"], block["reason"])
        self.assertEqual(block["width"], self.width)
        self.assertEqual(block["raw_sha256"], self.descriptor["capture"]["raw_sha256"])

    def test_missing_reference_is_rejected(self) -> None:
        (self.root / "gl-reference.json").unlink()
        block = live_gfx._gl_reference_block(self.root, self.evidence(), CALLER)
        self.assertFalse(block["ok"])
        self.assertEqual(block["status"], "missing")

    def test_truncated_payload_is_rejected(self) -> None:
        (self.root / "gl-reference.bgra").write_bytes(self.payload[:-4])
        block = live_gfx._gl_reference_block(self.root, self.evidence(), CALLER)
        self.assertFalse(block["ok"])
        self.assertIn("sha256", block["reason"])

    def test_wrong_executable_is_rejected(self) -> None:
        block = live_gfx._gl_reference_block(
            self.root, {"executable_sha256": "c" * 64}, CALLER
        )
        self.assertFalse(block["ok"])
        self.assertIn("executable", block["reason"])

    def test_wrong_caller_is_rejected(self) -> None:
        block = live_gfx._gl_reference_block(self.root, self.evidence(), 0xEDFBEF)
        self.assertFalse(block["ok"])
        self.assertIn("caller", block["reason"])

    def test_missing_gpu_evidence_is_rejected(self) -> None:
        self.descriptor["gpu"]["read_pixels"] = False
        self.write()
        block = live_gfx._gl_reference_block(self.root, self.evidence(), CALLER)
        self.assertFalse(block["ok"])
        self.assertIn("glReadPixels", block["reason"])

    def test_gl_error_is_rejected(self) -> None:
        self.descriptor["gpu"]["gl_error"] = 1282
        self.write()
        block = live_gfx._gl_reference_block(self.root, self.evidence(), CALLER)
        self.assertFalse(block["ok"])

    def test_missing_origin_is_rejected(self) -> None:
        del self.descriptor["target"]["origin"]
        self.write()
        block = live_gfx._gl_reference_block(self.root, self.evidence(), CALLER)
        self.assertFalse(block["ok"])
        self.assertIn("origin", block["reason"])

    def test_non_simple_draw_mode_is_rejected(self) -> None:
        self.descriptor["draw"]["mode"] = 4
        self.write()
        block = live_gfx._gl_reference_block(self.root, self.evidence(), CALLER)
        self.assertFalse(block["ok"])
        self.assertIn("mode", block["reason"])

    def test_bad_backend_is_rejected(self) -> None:
        self.descriptor["backend"] = "metal"
        self.write()
        block = live_gfx._gl_reference_block(self.root, self.evidence(), CALLER)
        self.assertFalse(block["ok"])
        self.assertIn("backend", block["reason"])


class PresentationValidationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.raw = bytes((10, 20, 30, 255)) * (4 * 3)
        (self.root / "checkpoint-after-present-1.bgra").write_bytes(self.raw)
        self.descriptor = {
            "schema": "gfx-checkpoint/v1",
            "checkpoint": "after-present",
            "target": {
                "width": 4,
                "height": 3,
                "row_stride": 16,
                "pixel_format": "BGRA8",
                "origin": "top_left",
            },
            "gpu": {"completed": True, "command_status": 4},
            "capture": {
                "raw_file": "checkpoint-after-present-1.bgra",
                "raw_bytes": len(self.raw),
                "raw_sha256": hashlib.sha256(self.raw).hexdigest(),
            },
            "presentation": {
                "shown": True,
                "method": "drawable_readback",
                "drawable_readback": True,
                "drawable_scheduled": True,
            },
            "status": {"presented": 1, "pixels_captured": 1},
        }
        self.write()
        (self.root / "presented.json").write_text("{}")

    def write(self) -> None:
        (self.root / "checkpoint-after-present-1.json").write_text(
            json.dumps(self.descriptor)
        )

    def test_valid_presentation_is_accepted(self) -> None:
        block = live_gfx._presentation_block(self.root)
        self.assertTrue(block["presented"], block["reason"])

    def test_missing_marker_is_rejected(self) -> None:
        (self.root / "presented.json").unlink()
        block = live_gfx._presentation_block(self.root)
        self.assertFalse(block["presented"])
        self.assertIn("marker", block["reason"])

    def test_missing_drawable_payload_is_rejected(self) -> None:
        (self.root / "checkpoint-after-present-1.bgra").unlink()
        block = live_gfx._presentation_block(self.root)
        self.assertFalse(block["presented"])
        self.assertIn("payload", block["reason"])

    def test_drawable_sha_mismatch_is_rejected(self) -> None:
        self.descriptor["capture"]["raw_sha256"] = "f" * 64
        self.write()
        block = live_gfx._presentation_block(self.root)
        self.assertFalse(block["presented"])

    def test_no_drawable_readback_is_rejected(self) -> None:
        self.descriptor["presentation"]["drawable_readback"] = False
        self.write()
        block = live_gfx._presentation_block(self.root)
        self.assertFalse(block["presented"])

    def test_unfinished_present_command_is_rejected(self) -> None:
        self.descriptor["gpu"]["completed"] = False
        self.write()
        block = live_gfx._presentation_block(self.root)
        self.assertFalse(block["presented"])


class LogicalCaptureValidationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.raw = bytes((1, 2, 3, 255)) * (4 * 3)
        (self.root / "metal-logical.bgra").write_bytes(self.raw)
        self.descriptor = {
            "schema": "gfx-metal-logical/v1",
            "backend": "metal",
            "provenance": {"executable_sha256": EXE},
            "draw": {"symbol": "_GfxDraw", "caller_rva": f"0x{CALLER:x}"},
            "target": {
                "width": 4,
                "height": 3,
                "row_stride": 16,
                "pixel_format": "BGRA8",
                "origin": "top_left",
            },
            "gpu": {"completed": True},
            "capture": {
                "raw_file": "metal-logical.bgra",
                "raw_bytes": len(self.raw),
                "raw_sha256": hashlib.sha256(self.raw).hexdigest(),
            },
        }
        (self.root / "metal-logical.json").write_text(json.dumps(self.descriptor))

    def test_valid_logical_capture_is_accepted(self) -> None:
        block = live_gfx._logical_capture_block(self.root, {"executable_sha256": EXE})
        self.assertTrue(block["ok"], block["reason"])

    def test_wrong_executable_is_rejected(self) -> None:
        block = live_gfx._logical_capture_block(self.root, {"executable_sha256": "e" * 64})
        self.assertFalse(block["ok"])

    def test_missing_gpu_completion_is_rejected(self) -> None:
        self.descriptor["gpu"]["completed"] = False
        (self.root / "metal-logical.json").write_text(json.dumps(self.descriptor))
        block = live_gfx._logical_capture_block(self.root, {"executable_sha256": EXE})
        self.assertFalse(block["ok"])

    def test_png_size_requires_a_real_png(self) -> None:
        self.assertIsNone(live_gfx._png_size(self.root / "metal-logical.bgra"))


if __name__ == "__main__":
    unittest.main()
