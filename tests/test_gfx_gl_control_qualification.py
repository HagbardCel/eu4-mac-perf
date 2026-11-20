"""Original-API GL control qualification before any Metal blame.

The qualification is only allowed when the exact BGRA comparison and the
authenticated sealed-observation binding both hold, and when the declared
renderer actually ran and produced a receipt that echoes the qualifier's
challenge. An exact image from a different seal, a copied live image without
the renderer receipt, or any missing evidence is explicitly unqualified.
"""

from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from automation import gfx_gl_control_qualification as qualification


def _sha_bytes(body: bytes) -> str:
    return hashlib.sha256(body).hexdigest()


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
        self.reference = self.frame / "frame-reference.bgra"
        self.reference.write_bytes(self.base)
        self.events_path = self.frame / "frame-events.jsonl"
        events = [
            {"index": 1, "kind": "draw", "target": {"framebuffer": 0}},
            {"index": 2, "kind": "draw", "target": {"framebuffer": 0},
             "default_checkpoint": {
                 "schema": "gfx-default-framebuffer-checkpoint/v1",
                 "event": 2,
                 "target": "0",
                 "resource": "fb:0",
                 "width": 8,
                 "height": 8,
                 "bytes": len(self.base),
                 "file": self.live.name,
                 "sha256": _sha(self.live),
                 "pixel_format": "BGRA8",
                 "origin": "lower_left",
                 "timing": "pre_event",
                 "status": "captured",
                 "session_id": "sess-1",
                 "capture_frame_id": 1,
             }},
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
        self.renderer = self.root / "renderer.py"
        self.registry_entry = {
            "name": "test-original-api-renderer",
            "api": "opengl",
            "qualified": True,
            "qualified_by": "test",
        }
        self._write_renderer()
        self.drawable = {"observed": True, "samples": 0,
                         "sample_buffers": 0, "depth_bits": 24,
                         "stencil_bits": 8, "red_bits": 8, "green_bits": 8,
                         "blue_bits": 8, "alpha_bits": 8}
        self.prestate = {"depth_clear": 1.0, "stencil_clear": 0,
                         "color": [0, 0, 0, 1]}
        self._out_index = 0

    def tearDown(self):
        self.temporary.cleanup()

    def _write_renderer(self, mode: str = "normal") -> None:
        script = f'''\
import hashlib, json, shutil, sys
from pathlib import Path
args = dict(zip(sys.argv[1::2], sys.argv[2::2]))
request = json.loads(Path(args["--request"]).read_text())
out = Path(args["--out"])
artifact = out / "result.bgra"
shutil.copyfile(request["live_image"], artifact)
mode = {mode!r}
if mode == "fail":
    sys.exit(7)
if mode == "residual":
    body = bytearray(artifact.read_bytes())
    for offset in (0, 4, len(body) - 4, len(body) - 8):
        body[offset] ^= 1
    artifact.write_bytes(bytes(body))
if mode == "alpha":
    body = bytearray(artifact.read_bytes())
    body[3] = 254
    artifact.write_bytes(bytes(body))
checkpoint = request["checkpoint"]
receipt = {{
    "schema": "gfx-gl-control-receipt/v1",
    "nonce": request["nonce"],
    "renderer_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
    "api": "opengl",
    "execution": "completed",
    "frame": {{
        "session_id": request["session_id"],
        "capture_frame_id": request["capture_frame_id"],
        "present_index": request["present_index"],
        "event_stream_sha256": request["event_stream_sha256"],
    }},
    "checkpoint": {{
        "event": checkpoint["event"],
        "resource": checkpoint["resource"],
        "reference_sha256": checkpoint["reference_sha256"],
    }},
    "draw": {{"event": request["draw"]["event"]}},
    "artifact": {{
        "file": "result.bgra",
        "sha256": hashlib.sha256(artifact.read_bytes()).hexdigest(),
        "width": request["width"],
        "height": request["height"],
        "pixel_format": "BGRA8",
    }},
    "renderer": {{"vendor": "Apple", "renderer": "Apple M5",
                  "version": "2.1"}},
}}
if mode == "api_metal":
    receipt["api"] = "metal"
if mode == "failed_exec":
    receipt["execution"] = "failed"
if mode == "wrong_frame":
    receipt["frame"]["session_id"] = "other-session"
    receipt["frame"]["event_stream_sha256"] = "b" * 64
if mode == "wrong_checkpoint":
    receipt["checkpoint"]["event"] = 3
if mode == "wrong_draw":
    receipt["draw"]["event"] = 99
if mode == "wrong_nonce":
    receipt["nonce"] = "0" * 32
if mode == "bad_artifact":
    receipt["artifact"]["sha256"] = "0" * 64
if mode == "bad_width":
    receipt["artifact"]["width"] = "big"
if mode != "no_receipt":
    (out / "receipt.json").write_text(json.dumps(receipt))
if mode == "no_artifact":
    artifact.unlink()
if mode == "truncated":
    artifact.write_bytes(bytes(4))
    receipt["artifact"]["sha256"] = hashlib.sha256(artifact.read_bytes()).hexdigest()
if mode == "overwrite_sealed":
    Path(request["frame_dir"], "blobs", "default-checkpoint-live.bin").write_bytes(
        bytes([1, 2, 3, 255]) * (request["width"] * request["height"]))
if mode == "toctou":
    Path(request["live_image"]).write_bytes(
        bytes([9, 9, 9, 255]) * (request["width"] * request["height"]))
'''
        self.renderer.write_text(script, encoding="utf-8")
        self.renderer_sha = _sha(self.renderer)
        self.registry = {self.renderer_sha: self.registry_entry}
        from automation import gfx_gl_control_authorities as authorities
        authorities.AUTHORITIES.pop(self.renderer_sha, None)
        authorities.AUTHORITIES.update(self.registry)
        self.addCleanup(
            lambda: authorities.AUTHORITIES.pop(self.renderer_sha, None))

    def _qualify(self, *, mode: str = "normal", renderer: Path | None = None,
                 frame_dir: Path | None = None,
                 live: Path | None = None, out_dir: Path | None = None,
                 drawable=..., prestate=...):
        self._out_index += 1
        target = out_dir or (self.root / f"out-{self._out_index}")
        target.mkdir(parents=True, exist_ok=True)
        return qualification.qualify_sealed(
            live or self.live, 8, 8,
            frame_dir=frame_dir or self.frame,
            checkpoint_event=2, draw_event=1,
            renderer=renderer or self.renderer, out_dir=target,
            drawable=self.drawable if drawable is ... else drawable,
            prestate=self.prestate if prestate is ... else prestate)

    def test_exact_authenticated_control_is_qualified_and_can_blame_metal(self):
        value = self._qualify()
        self.assertEqual(value["status"], "qualified", value)
        self.assertTrue(value["qualified"])
        self.assertTrue(value["metal_blame_allowed"])
        self.assertTrue(value["qualification"]["authenticated"])
        self.assertTrue(value["comparison"]["exact"])
        self.assertEqual(value["statistics"]["mismatched_pixels"], 0)
        self.assertEqual(value["missing_observation"], [])
        self.assertIsNotNone(value["qualification"]["receipt"])
        self.assertTrue(qualification.attribution(value)["allowed"])

    def _pop_registry(self):
        from automation import gfx_gl_control_authorities as authorities
        entry = authorities.AUTHORITIES.pop(self.renderer_sha, None)
        self.addCleanup(
            lambda: authorities.AUTHORITIES.__setitem__(
                self.renderer_sha, entry) if entry is not None else None)
        return entry

    def test_an_unregistered_renderer_cannot_authorize_blame(self):
        self._pop_registry()
        value = self._qualify()
        self.assertTrue(value["comparison"]["exact"])
        self.assertFalse(value["qualified"])
        self.assertFalse(value["metal_blame_allowed"])
        self.assertIn("self-attested",
                      " ".join(value["missing_observation"]))

    def test_a_wrong_registry_digest_is_not_qualified(self):
        self._pop_registry()
        value = self._qualify()
        self.assertFalse(value["qualified"])
        self.assertIn("not in the checked-in authority registry",
                      " ".join(value["qualification"]["reasons"]))

    def test_an_unqualified_registry_entry_is_not_accepted(self):
        from automation import gfx_gl_control_authorities as authorities
        entry = authorities.AUTHORITIES[self.renderer_sha]
        authorities.AUTHORITIES[self.renderer_sha] = {
            **entry, "qualified": False}
        self.addCleanup(
            lambda: authorities.AUTHORITIES.__setitem__(
                self.renderer_sha, entry))
        value = self._qualify()
        self.assertFalse(value["qualified"])
        self.assertIn("not qualified",
                      " ".join(value["qualification"]["reasons"]))

    def test_a_metal_executed_image_is_not_an_original_api_control(self):
        self._write_renderer("api_metal")
        value = self._qualify()
        self.assertFalse(value["qualified"])
        self.assertIn("original OpenGL API",
                      " ".join(value["qualification"]["reasons"]))

    def test_a_failed_execution_is_not_a_control(self):
        self._write_renderer("failed_exec")
        value = self._qualify()
        self.assertFalse(value["qualified"])
        self.assertIn("not declared complete",
                      " ".join(value["qualification"]["reasons"]))

    def test_a_receipt_from_a_different_seal_is_not_qualified(self):
        self._write_renderer("wrong_frame")
        value = self._qualify()
        self.assertTrue(value["comparison"]["exact"])
        self.assertFalse(value["qualified"])
        reasons = " ".join(value["qualification"]["reasons"])
        self.assertIn("frame session_id", reasons)
        self.assertIn("event stream", reasons)

    def test_a_mismatched_checkpoint_or_draw_is_not_qualified(self):
        for mode, needle in (("wrong_checkpoint", "checkpoint event"),
                             ("wrong_draw", "draw event")):
            with self.subTest(mode=mode):
                self._write_renderer(mode)
                value = self._qualify()
                self.assertFalse(value["qualified"])
                self.assertIn(needle,
                              " ".join(value["qualification"]["reasons"]))

    def test_a_renderer_that_does_not_echo_the_challenge_is_not_qualified(self):
        self._write_renderer("wrong_nonce")
        value = self._qualify()
        self.assertFalse(value["qualified"])
        self.assertIn("challenge",
                      " ".join(value["qualification"]["reasons"]))

    def test_a_receipt_with_a_wrong_artifact_digest_is_not_qualified(self):
        self._write_renderer("bad_artifact")
        value = self._qualify()
        self.assertFalse(value["qualified"])
        self.assertIn("artifact digest",
                      " ".join(value["qualification"]["reasons"]))

    def test_a_renderer_failure_is_an_explicit_unqualified_outcome(self):
        self._write_renderer("fail")
        value = self._qualify()
        self.assertFalse(value["qualified"])
        self.assertIn("renderer failed",
                      " ".join(value["qualification"]["reasons"]))

    def test_a_missing_receipt_or_artifact_is_unqualified(self):
        for mode in ("no_receipt", "no_artifact"):
            with self.subTest(mode=mode):
                self._write_renderer(mode)
                value = self._qualify()
                self.assertFalse(value["qualified"])
                self.assertIn("did not produce",
                              " ".join(value["qualification"]["reasons"]))

    def test_a_malformed_receipt_field_is_unqualified_not_a_traceback(self):
        self._write_renderer("bad_width")
        value = self._qualify()
        self.assertFalse(value["qualified"])
        self.assertIn("extent", " ".join(value["qualification"]["reasons"]))

    def test_a_live_image_outside_the_sealed_checkpoint_is_not_qualified(self):
        other = self.root / "other.bin"
        other.write_bytes(self.base)
        value = self._qualify(live=other)
        self.assertFalse(value["qualified"])
        self.assertIn("sealed checkpoint blob",
                      " ".join(value["qualification"]["reasons"]))

    def test_a_swapped_live_blob_is_not_qualified(self):
        body = bytearray(self.base)
        body[0] ^= 1
        self.live.write_bytes(bytes(body))
        value = self._qualify()
        self.assertFalse(value["qualified"])
        self.assertIn("blob digest mismatch",
                      " ".join(value["qualification"]["reasons"]))

    def test_four_pixel_residual_is_not_parity(self):
        self._write_renderer("residual")
        value = self._qualify()
        self.assertEqual(value["status"], "unqualified")
        self.assertEqual(value["statistics"]["mismatched_pixels"], 4)
        self.assertFalse(value["metal_blame_allowed"])
        verdict = qualification.attribution(value)
        self.assertFalse(verdict["allowed"])
        self.assertIn("not an authenticated exact", verdict["reason"])

    def test_alpha_only_residual_is_unqualified(self):
        self._write_renderer("alpha")
        value = self._qualify()
        self.assertFalse(value["qualified"])
        self.assertEqual(value["statistics"]["alpha_different_pixels"], 1)

    def test_missing_drawable_and_prestate_are_named(self):
        self._write_renderer("residual")
        value = self._qualify(drawable=None, prestate=None)
        self.assertFalse(value["qualified"])
        self.assertTrue(any("drawable" in item
                            for item in value["missing_observation"]))
        self.assertTrue(any("pre-draw" in item
                            for item in value["missing_observation"]))

    def test_an_intervening_same_target_draw_breaks_the_pair(self):
        events = [
            {"index": 1, "kind": "draw", "target": {"framebuffer": 0}},
            {"index": 2, "kind": "draw", "target": {"framebuffer": 0}},
            {"index": 3, "kind": "draw", "target": {"framebuffer": 0}},
        ]
        intervening = qualification._intervening_writer(events, 0, 2, 0)
        self.assertIsNotNone(intervening)
        self.assertEqual(intervening["index"], 2)
        self.assertIsNone(qualification._intervening_writer(events, 1, 2, 0))

    def test_an_unindexed_same_target_clear_breaks_the_pair(self):
        events = [
            {"index": 1, "kind": "draw", "target": {"framebuffer": 0}},
            {"index": None, "kind": "clear", "target": {"framebuffer": 0}},
            {"index": 2, "kind": "draw", "target": {"framebuffer": 0}},
        ]
        intervening = qualification._intervening_writer(events, 0, 2, 0)
        self.assertIsNotNone(intervening)
        self.assertEqual(intervening["kind"], "clear")

    def test_attribution_rechecks_the_registry_after_qualification(self):
        value = self._qualify()
        self.assertTrue(qualification.attribution(value)["allowed"])
        self._pop_registry()
        self.assertFalse(qualification.attribution(value)["allowed"])

    def test_a_renderer_cannot_change_the_compared_live_bytes(self):
        # The renderer gets a copy; changing that copy must not change the
        # authenticated bytes used by the comparison.
        self._write_renderer("toctou")
        value = self._qualify()
        self.assertTrue(value["qualified"], value)
        self.assertEqual(value["qualification"]["live_sha256"],
                         value["qualification"]["checkpoint_reference_sha256"])

    def test_a_renderer_that_changes_the_sealed_blob_is_not_qualified(self):
        self._write_renderer("overwrite_sealed")
        value = self._qualify()
        self.assertFalse(value["qualified"])
        self.assertIn("changed during the renderer run",
                      " ".join(value["qualification"]["reasons"]))

    def test_a_truncated_artifact_is_unqualified_not_a_traceback(self):
        self._write_renderer("truncated")
        value = self._qualify()
        self.assertFalse(value["qualified"])
        self.assertIn("bytes, expected",
                      " ".join(value["qualification"]["reasons"]))

    def test_size_mismatch_fails_closed(self):
        self._write_renderer("normal")
        value = self._qualify()
        self.assertTrue(value["qualified"])
        with self.assertRaises(qualification.QualificationError):
            qualification.compare(self.live, self.live, 4, 4)

    def test_missing_seal_fails_closed_as_unqualified(self):
        self.seal_path.unlink()
        value = self._qualify()
        self.assertFalse(value["qualified"])
        self.assertIn("frame seal is not authentic",
                      " ".join(value["qualification"]["reasons"]))

    def test_a_forged_qualification_object_cannot_authorize_blame(self):
        forged = {
            "schema": qualification.SCHEMA,
            "status": "qualified",
            "qualified": True,
            "metal_blame_allowed": True,
            "comparison": {"exact": True},
            "qualification": {"authenticated": True},
        }
        self.assertFalse(qualification.attribution(forged)["allowed"])

    def test_cli_comparison_only_reports_exact_without_qualification(self):
        value = qualification.main([
            str(self.live), "--width", "8", "--height", "8",
            "--comparison-only", str(self.live), str(self.live)])
        self.assertEqual(value, 0)

    def test_cli_refuses_to_qualify_without_a_renderer(self):
        output = self.root / "refused.json"
        code = qualification.main([
            str(self.live), "--width", "8", "--height", "8",
            "--out", str(output)])
        self.assertEqual(code, 2)
        value = json.loads(output.read_text())
        self.assertFalse(value["qualified"])
        self.assertIn("pixel equality alone",
                      " ".join(value["missing_observation"]))

    def test_cli_qualifies_the_authenticated_sealed_control(self):
        out = self.root / "cli-out"
        output = self.root / "qualified.json"
        code = qualification.main([
            str(self.live), "--width", "8", "--height", "8",
            "--frame-dir", str(self.frame), "--checkpoint-event", "2",
            "--draw-event", "1", "--renderer", str(self.renderer),
            "--control-out", str(out), "--out", str(output)])
        self.assertEqual(code, 0, output.read_text() if output.exists() else "")
        self.assertTrue(json.loads(output.read_text())["qualified"])

    def test_agent_tool_exposes_the_same_qualification(self):
        import contextlib
        import io
        from automation import agent_tools

        out = self.root / "agent-out"
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            code = agent_tools.main([
                "eu4_capture", "gl-control-qualify",
                "--live-image", str(self.live),
                "--width", "8", "--height", "8",
                "--frame-dir", str(self.frame),
                "--checkpoint-event", "2", "--draw-event", "1",
                "--renderer", str(self.renderer),
                "--control-out", str(out)])
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
                "--width", "8", "--height", "8"])
        value = json.loads(buffer.getvalue())
        self.assertNotEqual(code, 0, value)
        self.assertIn("pixel equality alone", value["reason"])


if __name__ == "__main__":
    unittest.main()
