"""Original-API GL control qualification before any Metal blame.

The qualification is only allowed when the exact BGRA comparison and the
authenticated sealed-observation binding both hold.  An exact image from a
different seal is not a control.
"""

from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from automation import gfx_gl_control_qualification as qualification


def _sha(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


class ControlQualificationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.frame = self.root / "frame"
        (self.frame / "blobs").mkdir(parents=True)
        self.base = bytes([10, 20, 30, 255]) * (8 * 8)
        self.live = self.frame / "blobs" / "default-checkpoint-live.bin"
        self.live.write_bytes(self.base)
        self.control = self.root / "control.bgra"
        self.control.write_bytes(self.base)
        self.reference = self.frame / "frame-reference.bgra"
        self.reference.write_bytes(self.base)
        self.events_path = self.frame / "frame-events.jsonl"
        events = [
            {"index": 1, "kind": "draw", "target": {"framebuffer": 0}},
            {"index": 2, "kind": "draw", "target": {"framebuffer": 0}},
        ]
        self.events_path.write_text(
            "".join(json.dumps(event) + "\n" for event in events),
            encoding="utf-8")
        self.trace_path = self.frame / "frame-trace.json"
        self.trace_path.write_text(json.dumps({
            "schema": "gfx-frame-trace/v1",
            "complete": True,
            "events": len(events),
            "present_index": 904,
            "frame": {
                "width": 8,
                "height": 8,
                "reference": "frame-reference.bgra",
                "reference_bytes": len(self.base),
                "reference_sha256": _sha(self.reference),
            },
        }), encoding="utf-8")
        self.seal_path = self.frame / "frame-seal.json"
        self.seal_path.write_text(json.dumps({
            "schema": "gfx-frame-seal/v1",
            "sealed": True,
            "complete": True,
            "struct_size": 2256,
            "abi_version": 2,
            "capture_frame_id": 1,
            "session_id": "sess-1",
            "pid": 1234,
            "present_index": 904,
            "scene_index": 904,
            "capture_dir": str(self.frame.resolve()),
            "manifest_file": "frame-trace.json",
            "manifest_sha256": _sha(self.trace_path),
            "reference_file": "frame-reference.bgra",
            "reference_bytes": len(self.base),
            "reference_sha256": _sha(self.reference),
            "event_stream_file": "frame-events.jsonl",
            "event_stream_bytes": self.events_path.stat().st_size,
            "event_stream_sha256": _sha(self.events_path),
            "width": 8,
            "height": 8,
            "pixel_format": "BGRA8",
            "trace_complete": True,
            "trace_integrity": True,
            "readback_ok": True,
            "event_stream_verified": True,
        }), encoding="utf-8")
        self.checkpoint_request = self.root / "checkpoint-request.json"
        self.checkpoint_request.write_text(json.dumps({
            "schema": "gfx-frame-checkpoint-request/v1",
            "checkpoints": [{
                "event": 2,
                "target": "0",
                "resource": "fb:0",
                "reference_file": self.live.name,
                "reference_sha256": _sha(self.live),
                "reference_width": 8,
                "reference_height": 8,
                "producer_events": [],
                "session_id": "sess-1",
                "capture_frame_id": 1,
            }],
        }), encoding="utf-8")
        self.manifest = self.root / "control-manifest.json"
        self._write_manifest()
        self.drawable = {"observed": True, "samples": 0,
                         "sample_buffers": 0, "depth_bits": 24,
                         "stencil_bits": 8, "red_bits": 8, "green_bits": 8,
                         "blue_bits": 8, "alpha_bits": 8}
        self.prestate = {"depth_clear": 1.0, "stencil_clear": 0,
                         "color": [0, 0, 0, 1]}

    def tearDown(self):
        self.temporary.cleanup()

    def _write_manifest(self, **overrides) -> None:
        value = {
            "schema": qualification.MANIFEST_SCHEMA,
            "api": "opengl",
            "execution": "completed",
            "session_id": "sess-1",
            "capture_frame_id": 1,
            "present_index": 904,
            "event_stream_sha256": _sha(self.events_path),
            "checkpoint_event": 2,
            "draw_event": 1,
            "resource": "fb:0",
            "artifact_sha256": _sha(self.control),
            "width": 8,
            "height": 8,
            "pixel_format": "BGRA8",
            "renderer": {"vendor": "Apple", "renderer": "Apple M5",
                         "version": "2.1"},
            "executable_sha256": "a" * 64,
        }
        value.update(overrides)
        self.manifest.write_text(json.dumps(value), encoding="utf-8")

    def _qualify(self, **overrides):
        options = {
            "frame_dir": self.frame,
            "checkpoint_request": self.checkpoint_request,
            "checkpoint_event": 2,
            "draw_event": 1,
            "manifest": self.manifest,
            "drawable": self.drawable,
            "prestate": self.prestate,
        }
        options.update(overrides)
        return qualification.qualify_sealed(
            self.live, self.control, 8, 8, **options)

    def test_exact_authenticated_control_is_qualified_and_can_blame_metal(self):
        value = self._qualify()
        self.assertEqual(value["status"], "qualified", value)
        self.assertTrue(value["qualified"])
        self.assertTrue(value["metal_blame_allowed"])
        self.assertTrue(value["qualification"]["authenticated"])
        self.assertTrue(value["comparison"]["exact"])
        self.assertEqual(value["statistics"]["mismatched_pixels"], 0)
        self.assertEqual(value["missing_observation"], [])
        self.assertTrue(qualification.attribution(value)["allowed"])

    def test_an_exact_image_without_authentication_is_not_qualified(self):
        value = qualification.qualify(
            self.live, self.control, 8, 8, drawable=self.drawable,
            prestate=self.prestate)
        self.assertEqual(value["status"], "unqualified")
        self.assertTrue(value["comparison"]["exact"])
        self.assertFalse(value["qualified"])
        self.assertFalse(value["metal_blame_allowed"])
        self.assertIn("authenticated frame/stream", value["missing_observation"][0])

    def test_an_exact_image_from_a_different_seal_is_not_qualified(self):
        self._write_manifest(session_id="other-session",
                             event_stream_sha256="b" * 64)
        value = self._qualify()
        self.assertTrue(value["comparison"]["exact"])
        self.assertFalse(value["qualified"])
        reasons = " ".join(value["qualification"]["reasons"])
        self.assertIn("session_id", reasons)
        self.assertIn("event stream", reasons)

    def test_a_metal_executed_image_is_not_an_original_api_control(self):
        self._write_manifest(api="metal")
        value = self._qualify()
        self.assertFalse(value["qualified"])
        self.assertIn("original OpenGL API",
                      " ".join(value["qualification"]["reasons"]))

    def test_a_failed_execution_is_not_a_control(self):
        self._write_manifest(execution="failed")
        value = self._qualify()
        self.assertFalse(value["qualified"])
        self.assertIn("not declared complete",
                      " ".join(value["qualification"]["reasons"]))

    def test_a_mismatched_checkpoint_or_draw_event_is_not_qualified(self):
        self._write_manifest(checkpoint_event=3)
        value = self._qualify()
        self.assertFalse(value["qualified"])
        self.assertIn("checkpoint event",
                      " ".join(value["qualification"]["reasons"]))

    def test_a_live_image_outside_the_sealed_checkpoint_is_not_qualified(self):
        other = self.root / "other.bin"
        other.write_bytes(self.base)
        value = self._qualify()
        self.assertTrue(value["qualified"])
        # Same bytes, but not the checkpoint's named blob: the binding must
        # reject the path rather than accept a copied digest.
        value = qualification.qualify_sealed(
            other, self.control, 8, 8, frame_dir=self.frame,
            checkpoint_request=self.checkpoint_request,
            checkpoint_event=2, draw_event=1, manifest=self.manifest)
        self.assertFalse(value["qualified"])
        self.assertIn("sealed checkpoint blob",
                      " ".join(value["qualification"]["reasons"]))

    def test_four_pixel_residual_is_not_parity(self):
        body = bytearray(self.base)
        for offset in (0, 4, 8 * 8 * 4 - 4, 8 * 8 * 4 - 8):
            body[offset] ^= 1
        self.control.write_bytes(bytes(body))
        self._write_manifest()
        value = self._qualify()
        self.assertEqual(value["status"], "unqualified")
        self.assertEqual(value["statistics"]["mismatched_pixels"], 4)
        self.assertFalse(value["metal_blame_allowed"])
        verdict = qualification.attribution(value)
        self.assertFalse(verdict["allowed"])
        self.assertIn("not an authenticated exact", verdict["reason"])

    def test_alpha_only_residual_is_unqualified(self):
        body = bytearray(self.base)
        body[3] = 254
        self.control.write_bytes(bytes(body))
        self._write_manifest()
        value = self._qualify()
        self.assertFalse(value["qualified"])
        self.assertEqual(value["statistics"]["alpha_different_pixels"], 1)

    def test_missing_drawable_and_prestate_are_named(self):
        body = bytearray(self.base)
        body[0] ^= 1
        self.control.write_bytes(bytes(body))
        self._write_manifest()
        value = self._qualify(drawable=None, prestate=None)
        self.assertFalse(value["qualified"])
        self.assertTrue(any("drawable" in item
                            for item in value["missing_observation"]))
        self.assertTrue(any("pre-draw" in item
                            for item in value["missing_observation"]))

    def test_size_mismatch_fails_closed(self):
        self.control.write_bytes(b"\x00" * 4)
        with self.assertRaises(qualification.QualificationError):
            qualification.compare(self.live, self.control, 8, 8)

    def test_missing_seal_fails_closed_as_unqualified(self):
        self.seal_path.unlink()
        value = self._qualify()
        self.assertFalse(value["qualified"])
        self.assertIn("frame seal is not authentic",
                      " ".join(value["qualification"]["reasons"]))

    def test_a_forged_qualified_object_cannot_authorize_blame(self):
        forged = {
            "schema": qualification.SCHEMA,
            "status": "qualified",
            "qualified": True,
            "metal_blame_allowed": True,
        }
        self.assertFalse(qualification.attribution(forged)["allowed"])

    def test_cli_comparison_only_reports_exact_without_qualification(self):
        value = qualification.main([
            str(self.live), str(self.control), "--width", "8",
            "--height", "8", "--comparison-only"])
        self.assertEqual(value, 0)

    def test_cli_refuses_to_qualify_on_pixels_alone(self):
        output = self.root / "refused.json"
        code = qualification.main([
            str(self.live), str(self.control), "--width", "8",
            "--height", "8", "--out", str(output)])
        self.assertEqual(code, 2)
        value = json.loads(output.read_text())
        self.assertFalse(value["qualified"])
        self.assertIn("pixel equality alone",
                      " ".join(value["missing_observation"]))

    def test_cli_qualifies_the_authenticated_sealed_control(self):
        output = self.root / "qualified.json"
        code = qualification.main([
            str(self.live), str(self.control), "--width", "8",
            "--height", "8", "--frame-dir", str(self.frame),
            "--checkpoint-request", str(self.checkpoint_request),
            "--checkpoint-event", "2", "--draw-event", "1",
            "--manifest", str(self.manifest), "--out", str(output)])
        self.assertEqual(code, 0)
        self.assertTrue(json.loads(output.read_text())["qualified"])

    def test_agent_tool_exposes_the_same_qualification(self):
        import contextlib
        import io
        from automation import agent_tools

        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            code = agent_tools.main([
                "eu4_capture", "gl-control-qualify",
                "--live-image", str(self.live),
                "--control-image", str(self.control),
                "--width", "8", "--height", "8",
                "--frame-dir", str(self.frame),
                "--checkpoint-request", str(self.checkpoint_request),
                "--checkpoint-event", "2", "--draw-event", "1",
                "--control-manifest", str(self.manifest)])
        value = json.loads(buffer.getvalue())
        self.assertEqual(code, 0, value)
        self.assertTrue(value["result"]["qualified"])
        self.assertEqual(value["outcome"]["verdict"], "equal")

    def test_agent_tool_refuses_pixel_only_qualification(self):
        import contextlib
        import io
        from automation import agent_tools

        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            code = agent_tools.main([
                "eu4_capture", "gl-control-qualify",
                "--live-image", str(self.live),
                "--control-image", str(self.control),
                "--width", "8", "--height", "8"])
        value = json.loads(buffer.getvalue())
        self.assertNotEqual(code, 0, value)
        self.assertIn("pixel equality alone", value["reason"])


if __name__ == "__main__":
    unittest.main()
