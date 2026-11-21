"""Original-API GL control qualification before any Metal blame.

The qualification is allowed only when the exact BGRA comparison, the
seal-derived drawable/prestate evidence, the restricted replay-input package
and a signed execution receipt all hold.  Reference isolation is tested
structurally: the renderer request has no live path and the package never
contains the target checkpoint blob.
"""

from __future__ import annotations

import copy
import hashlib
import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path

from automation import gfx_ed25519
from automation import gfx_gl_control_authorities as authorities
from automation import gfx_gl_control_qualification as qualification
from automation import gfx_gl_replay_input as replay_input


WIDTH = 8
HEIGHT = 8
SEED = bytes(range(32))
PUBLIC_KEY = gfx_ed25519.derive_public_key(SEED).hex()
WRONG_SEED = bytes(range(32, 64))


def _sha_bytes(body: bytes) -> str:
    return hashlib.sha256(body).hexdigest()


def _sha(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _transform(seed: bytes) -> bytes:
    """The synthetic draw's declared effect: blue + 1 (mod 256)."""
    body = bytearray(seed)
    for offset in range(0, len(body), 4):
        body[offset] = (body[offset] + 1) & 0xFF
    return bytes(body)


RENDERER_SCRIPT = r'''\
import argparse, hashlib, json, os, sys
from pathlib import Path

from automation import gfx_ed25519
from automation import gfx_gl_replay_input as replay_input

parser = argparse.ArgumentParser()
parser.add_argument("--request", required=True)
parser.add_argument("--out", required=True)
args = parser.parse_args()
request = json.loads(Path(args.request).read_text())
out = Path(args.out)
mode = os.environ.get("GL_CONTROL_TEST_MODE", "normal")
synd = os.environ.get("GL_CONTROL_TEST_SEED", "")
wrong = os.environ.get("GL_CONTROL_TEST_WRONG_SEED", "")

package_dir = Path(request["input_package"])
verified = replay_input.verify_package(package_dir)
if verified["package_sha256"] != request["input_package_sha256"]:
    sys.exit("input package digest mismatch")
package = verified["manifest_document"]
expected = request["expected"]

if mode == "copy_reference":
    # The control must not have any path to the post-draw reference.  Search
    # every directory it can reach from its working directory and package,
    # then try to copy a captured checkpoint if one is found.
    roots = [Path.cwd(), *Path.cwd().parents, package_dir, package_dir.parent]
    artifact_candidate = out / "result.bgra"
    for root in roots:
        for name in ("frame-seal.json", "frame-events.jsonl"):
            candidate = root / name
            if candidate.is_file():
                artifact_candidate.write_bytes(candidate.read_bytes())
                sys.exit(0)
        blobs = root / "blobs"
        if blobs.is_dir():
            for candidate in sorted(blobs.glob("default-checkpoint-*.bin")):
                artifact_candidate.write_bytes(candidate.read_bytes())
                sys.exit(0)
    sys.exit("no reference image is reachable")

if mode == "does_not_run":
    sys.exit(9)

color = package["prestate"]["color"]
if color["source"] != "checkpoint":
    sys.exit("synthetic renderer needs the captured colour seed")
seed = (package_dir / color["path"]).read_bytes()
body = bytearray(seed)
for offset in range(0, len(body), 4):
    body[offset] = (body[offset] + 1) & 0xFF
if mode == "residual":
    for offset in (0, 4, len(body) - 4, len(body) - 8):
        body[offset] ^= 1
if mode == "alpha":
    body[3] = 254
if mode == "single_channel":
    body[1] = (body[1] + 1) & 0xFF
artifact = out / "result.bgra"
artifact.write_bytes(bytes(body))
if mode == "symlink_artifact":
    artifact.unlink()
    artifact.symlink_to(package_dir / color["path"])

drawable = package["drawable"]
if mode == "wrong_drawable":
    drawable = dict(drawable)
    drawable["samples"] = 4

receipt = {
    "schema": "gfx-gl-control-receipt/v2",
    "nonce": request["nonce"] if mode != "wrong_nonce" else "0" * 32,
    "renderer": {
        "name": "synthetic-original-gl",
        "sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "components": [],
    },
    "api": "opengl",
    "execution": "unsupported_drawable" if mode == "unsupported" else "completed",
    "frame": {
        "session_id": expected["session_id"],
        "capture_frame_id": expected["capture_frame_id"],
        "present_index": expected["present_index"],
        "event_stream_sha256": expected["event_stream_sha256"],
    },
    "checkpoint": {
        "event": expected["checkpoint_event"],
        "resource": expected["checkpoint_resource"],
    },
    "draw": {"event": expected["draw_event"]},
    "input": {
        "package_sha256": ("0" * 64 if mode == "wrong_package"
                           else verified["package_sha256"]),
    },
    "artifact": {
        "file": "result.bgra",
        "sha256": hashlib.sha256(artifact.read_bytes()).hexdigest(),
        "bytes": artifact.stat().st_size,
        "width": request["width"],
        "height": request["height"],
        "pixel_format": "BGRA8",
        "origin": "lower_left",
    },
    "gl": {
        "vendor": "Apple",
        "renderer": "Apple M5 Max",
        "version": "2.1 Metal - 91.7",
        "draw_calls": 1,
        "error": 0,
        "target": drawable,
    },
}
if mode == "unsupported":
    receipt["unsupported"] = ["the driver cannot create a matching drawable"]
if mode == "wrong_frame":
    receipt["frame"]["session_id"] = "other-session"
if mode == "wrong_stream":
    receipt["frame"]["event_stream_sha256"] = "b" * 64
if mode == "api_metal":
    receipt["api"] = "metal"
if mode == "wrong_checkpoint":
    receipt["checkpoint"]["event"] = 99
if mode == "wrong_draw":
    receipt["draw"]["event"] = 99
if mode == "wrong_components":
    receipt["renderer"]["components"] = [
        {"role": "native-gl-executor", "sha256": "0" * 64}]
body = dict(receipt)
body.pop("signature", None)
canonical = json.dumps(
    body, sort_keys=True, separators=(",", ":"), ensure_ascii=True,
    allow_nan=False).encode("utf-8")
if mode == "no_signature":
    pass
elif mode == "forge_signature":
    receipt["signature"] = {"algorithm": "ed25519", "value": "00" * 64}
else:
    signing_seed = bytes.fromhex(wrong) if mode == "wrong_key" else bytes.fromhex(synd)
    receipt["signature"] = {
        "algorithm": "ed25519",
        "value": gfx_ed25519.sign(signing_seed, canonical).hex(),
    }
(out / "receipt.json").write_text(json.dumps(receipt, indent=2))
if mode == "no_artifact":
    artifact.unlink()
if mode == "truncated":
    artifact.write_bytes(b"\x00\x00\x00\x00")
'''


class ControlQualificationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.output_temporary = tempfile.TemporaryDirectory()
        self.output_root = Path(self.output_temporary.name)
        self.addCleanup(self.output_temporary.cleanup)
        self.frame = self.root / "frame"
        (self.frame / "blobs").mkdir(parents=True)
        self.base = bytes([10, 20, 30, 255]) * (WIDTH * HEIGHT)
        self.reference = _transform(self.base)
        self.session_id = "sess-1"

        # Authorised pre-draw inputs.  None of these is the target blob.
        self._write_blob("seed.bin", self.base)
        self._write_blob("shader-vertex.bin", b"#version 120\nvoid main(){}\n")
        self._write_blob("shader-pixel.bin", b"#version 120\nvoid main(){}\n")
        self._write_blob("uniform.bin", bytes(32))
        self._write_blob("vb.bin", bytes(64))
        self._write_blob("ib.bin", bytes(12))
        self._write_blob("tex.bin", bytes(64))
        self._write_blob("texc.bin", bytes(8))
        # The target checkpoint output.  It must never enter the package.
        self._write_blob("reference.bin", self.reference)

        self.renderer = self.root / "renderer.py"
        self.renderer.write_text(RENDERER_SCRIPT, encoding="utf-8")
        self.renderer_sha = _sha(self.renderer)
        self.authority = {
            "name": "synthetic-original-gl",
            "api": "opengl",
            "qualified": True,
            "qualified_by": "test",
            "public_key": PUBLIC_KEY,
            "components": [],
            "source": "test",
        }
        authorities.AUTHORITIES[self.renderer_sha] = self.authority
        self.addCleanup(
            lambda: authorities.AUTHORITIES.pop(self.renderer_sha, None))
        os.environ["GL_CONTROL_TEST_SEED"] = SEED.hex()
        os.environ["GL_CONTROL_TEST_WRONG_SEED"] = WRONG_SEED.hex()
        self.addCleanup(os.environ.pop, "GL_CONTROL_TEST_SEED", None)
        self.addCleanup(os.environ.pop, "GL_CONTROL_TEST_WRONG_SEED", None)
        self.addCleanup(os.environ.pop, "GL_CONTROL_TEST_MODE", None)
        self._build_frame()
        self._out_index = 0

    def tearDown(self):
        self.temporary.cleanup()

    # -- fixture ---------------------------------------------------------

    def _write_blob(self, name: str, body: bytes) -> None:
        (self.frame / "blobs" / name).write_bytes(body)

    def _draw_event(self, index: int, *, with_checkpoint: bool = False,
                    target_framebuffer: int = 0) -> dict:
        event = {
            "kind": "draw",
            "index": index,
            "state": {
                "blend": {"enabled": False, "src_rgb": "0x1",
                          "dst_rgb": "0x0", "src_alpha": "0x1",
                          "dst_alpha": "0x0", "equation_rgb": "0x8006",
                          "equation_alpha": "0x8006"},
                "color_mask": [1, 1, 1, 1],
                "depth": {"test": True, "func": "0x203", "write": True,
                          "range": [0.0, 1.0]},
                "stencil": {"test": False, "func": "0x207", "ref": 0,
                            "value_mask": -1, "fail": "0x1e00",
                            "zfail": "0x1e00", "zpass": "0x1e00",
                            "write_mask": -1},
                "cull": {"enabled": True, "mode": "0x405", "front": "0x901"},
                "polygon": {"mode": ["0x1b02", "0x1b02"],
                            "offset_fill": False, "offset_factor": 0,
                            "offset_units": 0},
                "alpha_test": {"enabled": False, "func": "0x206", "ref": 1},
                "scissor": {"enabled": False, "box": [0, 0, WIDTH, HEIGHT]},
                "multisample": True,
                "dither": True,
                "framebuffer_srgb": False,
                "viewport": [0, 0, WIDTH, HEIGHT],
            },
            "target": {"framebuffer": target_framebuffer,
                       "draw_buffer": "0x405", "read_buffer": "0x405",
                       "viewport": [0, 0, WIDTH, HEIGHT],
                       "width": WIDTH, "height": HEIGHT},
            "draw": {"entry": "_GfxDrawIndexed", "mode": 4, "first": 0,
                     "count": 6, "instance_count": 1, "indexed": True,
                     "base_vertex": 0, "caller_rva": "0x10d4e50"},
            "program": {
                "name": 301,
                "shaders": [
                    {"stage": "vertex", "file": "shader-vertex.bin",
                     "sha256": _sha(self.frame / "blobs" / "shader-vertex.bin"),
                     "bytes": 26},
                    {"stage": "pixel", "file": "shader-pixel.bin",
                     "sha256": _sha(self.frame / "blobs" / "shader-pixel.bin"),
                     "bytes": 25},
                ],
                "uniforms": [
                    {"name": "UNIFORM0[0]", "type": "float", "components": 4,
                     "elements": 2, "file": "uniform.bin",
                     "sha256": _sha(self.frame / "blobs" / "uniform.bin"),
                     "bytes": 32},
                    {"name": "tex0", "type": "sampler", "unit": 0,
                     "index": 1},
                ],
            },
            "attributes": [
                {"index": 0, "name": "in_POSITION", "location": 0,
                 "enabled": True, "components": 4, "gl_type": "0x1402",
                 "normalized": False, "stride": 8, "offset": 0, "divisor": 0,
                 "range_begin": 0, "range_bytes": 64, "first_vertex": 0,
                 "data": "vb.bin",
                 "sha256": _sha(self.frame / "blobs" / "vb.bin")},
            ],
            "indices": {"buffer": 7, "bytes": 12, "first_index": 0,
                        "count": 6, "instance_count": 1, "base_vertex": 0,
                        "vertex_origin": 0, "range_valid": True,
                        "read_error": False, "file": "ib.bin",
                        "sha256": _sha(self.frame / "blobs" / "ib.bin")},
            "textures": [
                {"unit": 0, "sampler_name": "tex0", "target": "2D",
                 "name": 9, "width": 4, "height": 4,
                 "internal_format": "DXT1",
                 "sampler": {"min": "0x2701", "mag": "0x2601",
                             "wrap_s": "0x2901", "wrap_t": "0x2901",
                             "wrap_r": "0x2901", "min_lod": 0, "max_lod": 0,
                             "lod_bias": 0, "base_level": 0, "max_level": 0,
                             "compare_mode": "0x0", "compare_func": "0x203",
                             "anisotropy": 1},
                 "levels": [
                     {"level": 0, "width": 4, "height": 4,
                      "internal_format": "DXT1", "pixel_format": "RGBA8",
                      "pixel_type": "unsigned_byte", "row_stride": 16,
                      "bytes": 64, "file": "tex.bin",
                      "sha256": _sha(self.frame / "blobs" / "tex.bin"),
                      "compressed": {"format": "DXT1", "bytes": 8,
                                     "row_stride": 8, "file": "texc.bin",
                                     "sha256": _sha(
                                         self.frame / "blobs" / "texc.bin")}},
                 ]},
            ],
        }
        if with_checkpoint:
            seed_body = (self.frame / "blobs" / "seed.bin").read_bytes()
            event["default_checkpoint"] = {
                "schema": "gfx-default-framebuffer-checkpoint/v1",
                "event": index,
                "target": "0",
                "resource": "fb:0",
                "width": WIDTH,
                "height": HEIGHT,
                "bytes": len(seed_body),
                "file": "seed.bin",
                "sha256": _sha_bytes(seed_body),
                "pixel_format": "BGRA8",
                "origin": "lower_left",
                "timing": "pre_event",
                "status": "captured",
                "session_id": self.session_id,
                "capture_frame_id": 1,
            }
        return event

    def _clear_event(self, index: int, *, colour: bool = True) -> dict:
        return {
            "kind": "clear",
            "index": index,
            "flags": 7 if colour else 3,
            "color": [0, 0, 0, 1],
            "depth": 1,
            "stencil": 0,
            "target": {"framebuffer": 0, "draw_buffer": "0x405",
                       "viewport": [0, 0, WIDTH, HEIGHT],
                       "width": WIDTH, "height": HEIGHT},
            "state": {"scissor": {"enabled": False,
                                  "box": [0, 0, WIDTH, HEIGHT]},
                      "color_mask": [1, 1, 1, 1],
                      "depth_mask": True,
                      "stencil_mask": -1},
        }

    def _checkpoint_event(self, index: int) -> dict:
        event = self._draw_event(index)
        body = self.reference
        event["default_checkpoint"] = {
            "schema": "gfx-default-framebuffer-checkpoint/v1",
            "event": index,
            "target": "0",
            "resource": "fb:0",
            "width": WIDTH,
            "height": HEIGHT,
            "bytes": len(body),
            "file": "reference.bin",
            "sha256": _sha_bytes(body),
            "pixel_format": "BGRA8",
            "origin": "lower_left",
            "timing": "pre_event",
            "status": "captured",
            "session_id": self.session_id,
            "capture_frame_id": 1,
        }
        return event

    def _build_frame(self, *, events: list[dict] | None = None,
                     drawable: dict | None | str = "default",
                     present_index: int = 904,
                     session_id: str | None = None,
                     reference: bytes | None = None) -> None:
        session = session_id or self.session_id
        if reference is not None:
            self.reference = reference
        if events is None:
            events = [
                self._clear_event(7),
                self._draw_event(14, with_checkpoint=True),
                self._checkpoint_event(15),
            ]
        self.events = events
        events_path = self.frame / "frame-events.jsonl"
        events_path.write_text(
            "".join(json.dumps(event) + "\n" for event in events),
            encoding="utf-8")
        if drawable == "default":
            drawable = {
                "samples": 0, "sample_buffers": 0, "depth_bits": 32,
                "stencil_bits": 8, "red_bits": 8, "green_bits": 8,
                "blue_bits": 8, "alpha_bits": 8, "double_buffer": True,
                "stereo": False, "drawn_framebuffer_binding": 0,
            }
        trace_path = self.frame / "frame-trace.json"
        trace_path.write_text(json.dumps({
            "schema": "gfx-frame-trace/v1",
            "complete": True,
            "events": len(events),
            "present_index": present_index,
            "gl": {"vendor": "Apple", "renderer": "Apple M5 Max",
                   "version": "2.1 Metal - 91.7",
                   **({"drawable": drawable, "drawable_observed": True}
                      if drawable is not None else
                      {"drawable_observed": False})},
            "frame": {
                "width": WIDTH,
                "height": HEIGHT,
                "reference": "frame-reference.bgra",
                "reference_bytes": len(self.reference),
                "reference_sha256": _sha_bytes(self.reference),
            },
        }), encoding="utf-8")
        (self.frame / "frame-reference.bgra").write_bytes(self.reference)
        seal_path = self.frame / "frame-seal.json"
        seal_path.write_text(json.dumps({
            "schema": "gfx-frame-seal/v1",
            "sealed": True,
            "complete": True,
            "struct_size": 2256,
            "abi_version": 2,
            "capture_frame_id": 1,
            "session_id": session,
            "pid": 1234,
            "present_index": present_index,
            "scene_index": present_index,
            "capture_dir": str(self.frame.resolve()),
            "manifest_file": "frame-trace.json",
            "manifest_sha256": _sha(trace_path),
            "reference_file": "frame-reference.bgra",
            "reference_bytes": len(self.reference),
            "reference_sha256": _sha_bytes(self.reference),
            "event_stream_file": "frame-events.jsonl",
            "event_stream_bytes": events_path.stat().st_size,
            "event_stream_sha256": _sha(events_path),
            "operation_count": len(events),
            "draw_count": sum(1 for event in events
                              if event.get("kind") == "draw"),
            "clear_count": sum(1 for event in events
                               if event.get("kind") == "clear"),
            "copy_count": sum(1 for event in events
                              if event.get("kind") == "resolve"),
            "width": WIDTH,
            "height": HEIGHT,
            "pixel_format": "BGRA8",
            "trace_complete": True,
            "trace_integrity": True,
            "readback_ok": True,
            "event_stream_verified": True,
        }), encoding="utf-8")

    # -- helpers ---------------------------------------------------------

    def _qualify(self, *, mode: str = "normal", renderer: Path | None = None,
                 frame_dir: Path | None = None, live: Path | None = None,
                 out_dir: Path | None = None, evidence_patch=None,
                 checkpoint_event: int = 15, draw_event: int = 14,
                 caller_drawable=None, caller_prestate=None):
        os.environ["GL_CONTROL_TEST_MODE"] = mode
        self._out_index += 1
        target = out_dir or (self.output_root / f"out-{self._out_index}")
        target.mkdir(parents=True, exist_ok=True)
        return qualification.qualify_sealed(
            live or (self.frame / "blobs" / "reference.bin"),
            WIDTH, HEIGHT,
            frame_dir=frame_dir or self.frame,
            checkpoint_event=checkpoint_event, draw_event=draw_event,
            renderer=renderer or self.renderer, out_dir=target,
            caller_drawable=caller_drawable,
            caller_prestate=caller_prestate)

    def _request(self, value: dict) -> dict:
        path = Path(value["qualification"]["control"]["path"]).parent \
            / "request.json"
        return json.loads(path.read_text())

    def _package_manifest(self, value: dict) -> dict:
        path = Path(value["qualification"]["input_package"]["path"]) \
            / "replay-input.json"
        return json.loads(path.read_text())

    def _rewrite_events(self, events: list[dict]) -> None:
        self._build_frame(events=events)

    # -- admission and exactness ----------------------------------------

    def test_exact_authenticated_control_is_qualified_and_can_blame_metal(self):
        value = self._qualify()
        self.assertEqual(value["status"], "qualified", value)
        self.assertTrue(value["qualified"])
        self.assertTrue(value["metal_blame_allowed"])
        self.assertTrue(value["qualification"]["authenticated"])
        self.assertTrue(value["qualification"]["evidence_proven"])
        self.assertTrue(value["qualification"]["receipt_verified"])
        self.assertTrue(value["comparison"]["exact"])
        self.assertEqual(value["statistics"]["mismatched_pixels"], 0)
        self.assertEqual(value["missing_observation"], [])
        self.assertIsNotNone(value["qualification"]["receipt"])
        self.assertTrue(qualification.attribution(value)["allowed"])

    def test_the_renderer_request_has_no_reference_and_no_frame_directory(self):
        value = self._qualify()
        self.assertTrue(value["qualified"], value)
        request = self._request(value)
        for forbidden in ("live_image", "frame_dir", "reference",
                          "reference_sha256", "live_sha256"):
            self.assertNotIn(forbidden, request)
        self.assertEqual(request["schema"], qualification.REQUEST_SCHEMA)
        self.assertIn("input_package", request)

    def test_the_package_never_contains_the_target_checkpoint_blob(self):
        value = self._qualify()
        self.assertTrue(value["qualified"], value)
        manifest = self._package_manifest(value)
        reference_sha = _sha_bytes(self.reference)
        digests = {record["sha256"] for record in manifest["files"]}
        self.assertNotIn(reference_sha, digests)
        self.assertNotIn("reference.bin",
                         {Path(record["path"]).name
                          for record in manifest["files"]})
        # The pre-draw colour seed is an authorised input and is present.
        self.assertEqual(manifest["prestate"]["color"]["sha256"],
                         _sha_bytes(self.base))
        # The compressed block path is preferred over the decoded copy.
        levels = manifest["textures"][0]["levels"]
        self.assertTrue(levels[0]["compressed"])

    def test_a_no_op_draw_cannot_prove_reference_isolation(self):
        # If the draw changes nothing, the pre-draw seed equals the target
        # checkpoint.  An exact result is then indistinguishable from copying
        # the reference, so it is an explicit unsupported state.
        (self.frame / "blobs" / "reference.bin").write_bytes(self.base)
        self._build_frame(reference=self.base)
        value = self._qualify()
        self.assertEqual(value["status"], "unsupported_state")
        self.assertFalse(value["qualified"])
        self.assertTrue(any("no observable effect" in item
                            for item in value["unsupported_state"]))

    def test_a_renderer_that_tries_to_copy_the_reference_is_refused(self):
        value = self._qualify(mode="copy_reference")
        self.assertFalse(value["qualified"])
        reasons = " ".join(value["qualification"]["reasons"])
        self.assertIn("failed with exit", reasons)
        # The renderer actively searched its cwd, ancestors and package and
        # found no sealed frame or checkpoint blob.
        self.assertIn("no reference image is reachable", reasons)

    def test_an_output_directory_inside_the_capture_tree_is_refused(self):
        value = self._qualify(out_dir=self.frame / "control-out")
        self.assertEqual(value["status"], "unsupported_state")
        self.assertFalse(value["qualified"])
        self.assertTrue(any("overlaps the sealed capture tree" in item
                            for item in value["unsupported_state"]))

    def test_caller_supplied_evidence_is_refused_at_the_api(self):
        value = self._qualify(caller_drawable={"observed": True},
                              caller_prestate={"depth_clear": 1.0})
        self.assertFalse(value["qualified"])
        self.assertTrue(any("caller_supplied_evidence" in item
                            for item in value["unsupported_state"]))

    def test_an_intervening_same_target_draw_breaks_the_pair(self):
        events = [
            self._clear_event(7),
            self._draw_event(14),
            self._draw_event(15),
            self._checkpoint_event(16),
        ]
        self._rewrite_events(events)
        value = self._qualify(checkpoint_event=16)
        self.assertFalse(value["qualified"])
        self.assertIn("between the draw and the checkpoint",
                      " ".join(value["qualification"]["reasons"]))

    def test_an_unindexed_same_target_clear_breaks_the_pair(self):
        clear = self._clear_event(0)
        clear["index"] = None
        events = [
            self._clear_event(7),
            self._draw_event(14),
            clear,
            self._checkpoint_event(16),
        ]
        self._rewrite_events(events)
        value = self._qualify(checkpoint_event=16)
        self.assertFalse(value["qualified"])
        self.assertIn("between the draw and the checkpoint",
                      " ".join(value["qualification"]["reasons"]))

    def test_a_metal_executed_image_is_not_an_original_api_control(self):
        value = self._qualify(mode="api_metal")
        self.assertFalse(value["qualified"])
        self.assertIn("original OpenGL API",
                      " ".join(value["qualification"]["reasons"]))

    def test_an_unqualified_registry_entry_is_not_accepted(self):
        authorities.AUTHORITIES[self.renderer_sha] = {
            **self.authority, "qualified": False}
        value = self._qualify()
        self.assertFalse(value["qualified"])
        self.assertIn("not qualified",
                      " ".join(value["qualification"]["reasons"]))

    def test_missing_seal_fails_closed_as_unqualified(self):
        (self.frame / "frame-seal.json").unlink()
        value = self._qualify()
        self.assertFalse(value["qualified"])
        self.assertIn("frame seal is not authentic",
                      " ".join(value["qualification"]["reasons"]))

    def test_a_symlinked_artifact_is_refused(self):
        value = self._qualify(mode="symlink_artifact")
        self.assertFalse(value["qualified"])
        self.assertIn("symlink",
                      " ".join(value["qualification"]["reasons"]))

    def test_mutating_the_control_artifact_after_qualification_refuses_blame(self):
        value = self._qualify()
        self.assertTrue(qualification.attribution(value)["allowed"])
        control = Path(value["qualification"]["control"]["path"])
        body = bytearray(control.read_bytes())
        body[-1] ^= 0xFF
        control.write_bytes(bytes(body))
        self.assertFalse(qualification.attribution(value)["allowed"])

    def test_mutating_the_renderer_after_qualification_refuses_blame(self):
        value = self._qualify()
        self.assertTrue(qualification.attribution(value)["allowed"])
        self.renderer.write_text(
            self.renderer.read_text() + "\n# changed\n", encoding="utf-8")
        self.assertFalse(qualification.attribution(value)["allowed"])

    def test_a_multisampled_colour_seed_is_not_proven(self):
        self._build_frame(drawable={
            "samples": 4, "sample_buffers": 1, "depth_bits": 32,
            "stencil_bits": 8, "red_bits": 8, "green_bits": 8,
            "blue_bits": 8, "alpha_bits": 8, "double_buffer": True,
            "stereo": False, "drawn_framebuffer_binding": 0,
        })
        value = self._qualify()
        self.assertEqual(value["status"], "unsupported_state")
        self.assertTrue(any("multisampled" in item
                            for item in value["unsupported_state"]))

    def test_an_unclassified_event_kind_is_refused(self):
        events = copy.deepcopy(self.events)
        events.insert(2, {"kind": "scene_entry", "seq": 1})
        self._build_frame(events=events)
        value = self._qualify()
        self.assertFalse(value["qualified"])
        self.assertTrue(any(
            "unclassified" in item
            for item in (value["missing_observation"]
                         + value["unsupported_state"]
                         + value["qualification"]["reasons"])))

    def test_a_clear_without_a_recorded_value_is_refused(self):
        events = copy.deepcopy(self.events)
        events[0].pop("depth")
        events[1]["default_checkpoint"].pop("depth", None)
        self._build_frame(events=events)
        result = self._qualify()
        self.assertFalse(result["qualified"])
        self.assertTrue(any("clear depth value" in item
                            for item in
                            result["qualification"]["evidence"]["missing"]))

    def test_a_precreated_package_or_run_symlink_is_refused(self):
        for name in ("replay-input", "renderer-run"):
            with self.subTest(name=name):
                out = self.output_root / f"symlink-{name}"
                out.mkdir(parents=True, exist_ok=True)
                (out / name).symlink_to(self.frame)
                value = self._qualify(out_dir=out)
                self.assertFalse(value["qualified"])
                self.assertTrue(any("symlink" in item
                                    for item in value["unsupported_state"]))

    def test_a_legacy_capture_without_the_new_state_fields_is_unqualified(self):
        mutations = (
            ("read_buffer", lambda events: events[1]["target"].pop(
                "read_buffer", None), "read buffer"),
            ("framebuffer_srgb", lambda events: events[1]["state"].pop(
                "framebuffer_srgb", None), "framebuffer_srgb"),
            ("depth.range", lambda events: events[1]["state"]["depth"].pop(
                "range", None), "depth.range"),
        )
        for label, mutate, needle in mutations:
            with self.subTest(field=label):
                events = copy.deepcopy(self.events)
                mutate(events)
                self._build_frame(events=events)
                value = self._qualify()
                self.assertFalse(value["qualified"])
                evidence = value["qualification"]["evidence"]
                self.assertTrue(any(needle in item
                                    for item in evidence["missing"]))

    def test_unproven_colour_seed_metadata_is_refused(self):
        for field, value in (("bytes", 4), ("pixel_format", "RGBA8"),
                             ("origin", "upper_left")):
            with self.subTest(field=field):
                events = copy.deepcopy(self.events)
                events[1]["default_checkpoint"][field] = value
                self._build_frame(events=events)
                result = self._qualify()
                self.assertFalse(result["qualified"])
                self.assertTrue(any("colour seed is unproven" in item
                                    for item in
                                    result["qualification"]["evidence"]["missing"]))

    def test_an_exact_image_with_missing_drawable_is_refused(self):
        self._build_frame(drawable=None)
        value = self._qualify()
        self.assertFalse(value["qualified"])
        self.assertEqual(value["comparison"]["status"], "unavailable")
        self.assertEqual(value["status"], "unqualified")
        self.assertTrue(any("drawable" in item
                            for item in value["missing_observation"]))

    def test_an_exact_image_with_missing_prestate_is_refused(self):
        # Keep the colour seed but remove the depth/stencil clear history and
        # put a writer after the clear, so depth cannot be proven.
        events = [
            self._clear_event(7),
            self._draw_event(10),
            self._draw_event(14, with_checkpoint=True),
            self._checkpoint_event(15),
        ]
        self._rewrite_events(events)
        value = self._qualify()
        # The colour seed is present, but depth/stencil are unproven, so the
        # qualifier refuses before it can run the renderer at all.
        self.assertFalse(value["qualified"])
        self.assertTrue(any("depth" in item and "unproven" in item
                            for item in value["missing_observation"]))

    def test_a_forged_external_drawable_or_prestate_is_refused(self):
        forged = self.root / "forged.json"
        forged.write_text(json.dumps({"observed": True, "samples": 0}))
        for flag in ("--drawable", "--prestate"):
            with self.subTest(flag=flag):
                code = qualification.main([
                    str(self.frame / "blobs" / "reference.bin"),
                    "--width", str(WIDTH), "--height", str(HEIGHT),
                    "--frame-dir", str(self.frame),
                    "--checkpoint-event", "15", "--draw-event", "14",
                    "--renderer", str(self.renderer),
                    "--control-out", str(self.output_root / f"forged-{flag[2:]}"),
                    flag, str(forged)])
                self.assertEqual(code, 2)

    def test_a_divergent_control_is_unqualified_and_cannot_blame_metal(self):
        for mode, field in (("residual", "mismatched_pixels"),
                            ("alpha", "alpha_different_pixels"),
                            ("single_channel", "mismatched_pixels")):
            with self.subTest(mode=mode):
                value = self._qualify(mode=mode)
                self.assertFalse(value["qualified"])
                self.assertGreater(value["statistics"][field], 0)
                self.assertFalse(value["metal_blame_allowed"])
                self.assertFalse(qualification.attribution(value)["allowed"])

    def test_an_alpha_only_residual_is_not_exact(self):
        value = self._qualify(mode="alpha")
        self.assertEqual(value["statistics"]["mismatched_pixels"], 1)
        self.assertEqual(value["statistics"]["alpha_different_pixels"], 1)
        self.assertFalse(value["comparison"]["exact"])

    # -- execution authentication ---------------------------------------

    def test_an_unregistered_renderer_cannot_authorize_blame(self):
        authorities.AUTHORITIES.pop(self.renderer_sha, None)
        value = self._qualify()
        self.assertTrue(value["comparison"]["exact"])
        self.assertFalse(value["qualified"])
        self.assertIn("not in the checked-in authority registry",
                      " ".join(value["qualification"]["reasons"]))

    def test_a_registry_entry_with_the_wrong_public_key_is_refused(self):
        authorities.AUTHORITIES[self.renderer_sha] = {
            **self.authority,
            "public_key": gfx_ed25519.derive_public_key(WRONG_SEED).hex(),
        }
        value = self._qualify()
        self.assertFalse(value["qualified"])
        self.assertIn("signature does not verify",
                      " ".join(value["qualification"]["reasons"]))

    def test_an_unsigned_receipt_is_refused(self):
        value = self._qualify(mode="no_signature")
        self.assertTrue(value["comparison"]["exact"])
        self.assertFalse(value["qualified"])
        self.assertIn("not signed",
                      " ".join(value["qualification"]["reasons"]))

    def test_a_forged_signature_is_refused(self):
        value = self._qualify(mode="forge_signature")
        self.assertTrue(value["comparison"]["exact"])
        self.assertFalse(value["qualified"])
        self.assertIn("signature does not verify",
                      " ".join(value["qualification"]["reasons"]))

    def test_a_receipt_signed_by_a_different_key_is_refused(self):
        value = self._qualify(mode="wrong_key")
        self.assertTrue(value["comparison"]["exact"])
        self.assertFalse(value["qualified"])
        self.assertIn("signature does not verify",
                      " ".join(value["qualification"]["reasons"]))

    def test_a_receipt_with_forged_frame_identity_is_refused(self):
        for mode, needle in (("wrong_frame", "frame session_id"),
                             ("wrong_stream", "event stream"),
                             ("wrong_checkpoint", "checkpoint event"),
                             ("wrong_draw", "draw event"),
                             ("wrong_nonce", "challenge"),
                             ("wrong_package", "input package")):
            with self.subTest(mode=mode):
                value = self._qualify(mode=mode)
                self.assertFalse(value["qualified"])
                self.assertIn(needle,
                              " ".join(value["qualification"]["reasons"]))

    def test_a_receipt_with_extra_components_is_refused(self):
        value = self._qualify(mode="wrong_components")
        self.assertFalse(value["qualified"])
        self.assertIn("component digests",
                      " ".join(value["qualification"]["reasons"]))

    def test_a_renderer_that_cannot_reproduce_the_drawable_is_unsupported(self):
        value = self._qualify(mode="unsupported")
        self.assertEqual(value["status"], "unsupported_state")
        self.assertFalse(value["qualified"])
        self.assertTrue(value["unsupported_state"])
        self.assertFalse(value["metal_blame_allowed"])

    def test_a_receipt_that_does_not_match_the_drawable_is_refused(self):
        value = self._qualify(mode="wrong_drawable")
        self.assertFalse(value["qualified"])
        self.assertIn("drawable samples",
                      " ".join(value["qualification"]["reasons"]))

    def test_a_failed_execution_is_not_a_control(self):
        value = self._qualify(mode="does_not_run")
        self.assertFalse(value["qualified"])
        self.assertIn("failed with exit",
                      " ".join(value["qualification"]["reasons"]))

    def test_a_missing_artifact_is_unqualified(self):
        value = self._qualify(mode="no_artifact")
        self.assertFalse(value["qualified"])
        self.assertIn("artifact",
                      " ".join(value["qualification"]["reasons"]))

    def test_a_truncated_artifact_is_unqualified_not_a_traceback(self):
        value = self._qualify(mode="truncated")
        self.assertFalse(value["qualified"])
        self.assertIn("bytes, expected",
                      " ".join(value["qualification"]["reasons"]))

    # -- cross-frame and cross-seal -------------------------------------

    def test_a_live_image_outside_the_sealed_checkpoint_is_refused(self):
        other = self.root / "other.bin"
        other.write_bytes(self.reference)
        value = self._qualify(live=other)
        self.assertFalse(value["qualified"])
        self.assertIn("sealed checkpoint blob",
                      " ".join(value["qualification"]["reasons"]))

    def test_a_swapped_live_blob_is_refused(self):
        body = bytearray(self.reference)
        body[0] ^= 1
        (self.frame / "blobs" / "reference.bin").write_bytes(bytes(body))
        value = self._qualify()
        self.assertFalse(value["qualified"])
        self.assertIn("blob digest mismatch",
                      " ".join(value["qualification"]["reasons"]))

    def test_a_checkpoint_from_another_seal_is_refused(self):
        # Build a second sealed frame with its own session and reference, then
        # try to use its checkpoint as the reference of the first seal.
        other = self.root / "other-frame"
        shutil.copytree(self.frame, other)
        other_reference = bytes([1, 2, 3, 4]) * (WIDTH * HEIGHT)
        (other / "blobs" / "reference.bin").write_bytes(other_reference)
        events_path = other / "frame-events.jsonl"
        document = json.loads(events_path.read_text().splitlines()[-1])
        document["default_checkpoint"]["sha256"] = _sha_bytes(other_reference)
        events_path.write_text(
            "\n".join(json.dumps(item) for item in self.events[:-1])
            + "\n" + json.dumps(document) + "\n", encoding="utf-8")
        trace = json.loads((other / "frame-trace.json").read_text())
        trace["events"] = len(self.events)
        (other / "frame-trace.json").write_text(json.dumps(trace))
        seal = json.loads((other / "frame-seal.json").read_text())
        seal["session_id"] = "other-session"
        seal["capture_dir"] = str(other.resolve())
        seal["event_stream_sha256"] = _sha(events_path)
        seal["manifest_sha256"] = _sha(other / "frame-trace.json")
        (other / "frame-seal.json").write_text(json.dumps(seal))
        value = self._qualify(frame_dir=other)
        self.assertFalse(value["qualified"])
        self.assertIn("session",
                      " ".join(value["qualification"]["reasons"]))

    def test_attribution_rechecks_the_signature_from_disk(self):
        value = self._qualify()
        self.assertTrue(qualification.attribution(value)["allowed"])
        receipt = Path(value["qualification"]["receipt"]["path"])
        document = json.loads(receipt.read_text())
        document["execution"] = "failed"
        receipt.write_text(json.dumps(document))
        self.assertFalse(qualification.attribution(value)["allowed"])

    def test_attribution_rechecks_the_sealed_reference_from_disk(self):
        value = self._qualify()
        self.assertTrue(qualification.attribution(value)["allowed"])
        body = bytearray((self.frame / "blobs" / "reference.bin").read_bytes())
        body[0] ^= 0xFF
        (self.frame / "blobs" / "reference.bin").write_bytes(bytes(body))
        self.assertFalse(qualification.attribution(value)["allowed"])

    def test_attribution_rechecks_the_package_from_disk(self):
        value = self._qualify()
        self.assertTrue(qualification.attribution(value)["allowed"])
        package = Path(value["qualification"]["input_package"]["path"])
        blob = next((package / "blobs").iterdir())
        body = bytearray(blob.read_bytes())
        body[0] ^= 1
        blob.write_bytes(bytes(body))
        self.assertFalse(qualification.attribution(value)["allowed"])

    def test_flipped_flags_cannot_authorize_a_divergent_control(self):
        value = self._qualify(mode="residual")
        forged = copy.deepcopy(value)
        forged["status"] = "qualified"
        forged["qualified"] = True
        forged["metal_blame_allowed"] = True
        forged["qualification"]["status"] = "qualified"
        forged["qualification"]["qualified"] = True
        forged["qualification"]["authenticated"] = True
        forged["qualification"]["evidence_proven"] = True
        forged["comparison"]["exact"] = True
        forged["comparison"]["status"] = "exact"
        forged["missing_observation"] = []
        self.assertFalse(qualification.attribution(forged)["allowed"])

    def test_a_forged_qualification_object_cannot_authorize_blame(self):
        forged = {
            "schema": qualification.SCHEMA,
            "status": "qualified",
            "qualified": True,
            "metal_blame_allowed": True,
            "comparison": {"exact": True},
            "qualification": {"authenticated": True,
                              "evidence_proven": True},
        }
        self.assertFalse(qualification.attribution(forged)["allowed"])

    def test_a_malformed_projection_identity_refuses_without_raising(self):
        value = self._qualify()
        self.assertTrue(qualification.attribution(value)["allowed"])
        value["qualification"]["identity"] = "not-a-dict"
        self.assertFalse(qualification.attribution(value)["allowed"])

    # -- CLI and adapter ------------------------------------------------

    def test_cli_comparison_only_reports_exact_without_qualification(self):
        code = qualification.main([
            str(self.frame / "blobs" / "reference.bin"),
            "--width", str(WIDTH), "--height", str(HEIGHT),
            "--comparison-only", str(self.frame / "blobs" / "reference.bin"),
            str(self.frame / "blobs" / "reference.bin")])
        self.assertEqual(code, 0)

    def test_cli_refuses_to_qualify_without_a_renderer(self):
        output = self.root / "refused.json"
        code = qualification.main([
            str(self.frame / "blobs" / "reference.bin"),
            "--width", str(WIDTH), "--height", str(HEIGHT),
            "--out", str(output)])
        self.assertEqual(code, 2)
        value = json.loads(output.read_text())
        self.assertFalse(value["qualified"])
        self.assertIn("pixel equality alone",
                      " ".join(value["missing_observation"]))

    def test_cli_qualifies_the_authenticated_sealed_control(self):
        os.environ["GL_CONTROL_TEST_MODE"] = "normal"
        output = self.root / "qualified.json"
        code = qualification.main([
            str(self.frame / "blobs" / "reference.bin"),
            "--width", str(WIDTH), "--height", str(HEIGHT),
            "--frame-dir", str(self.frame), "--checkpoint-event", "15",
            "--draw-event", "14", "--renderer", str(self.renderer),
            "--control-out", str(self.output_root / "cli-out"),
            "--out", str(output)])
        self.assertEqual(code, 0, output.read_text() if output.exists() else "")
        self.assertTrue(json.loads(output.read_text())["qualified"])

    def test_agent_tool_exposes_the_same_qualification(self):
        import contextlib
        import io
        from automation import agent_tools

        os.environ["GL_CONTROL_TEST_MODE"] = "normal"
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            code = agent_tools.main([
                "eu4_capture", "gl-control-qualify",
                "--live-image", str(self.frame / "blobs" / "reference.bin"),
                "--width", str(WIDTH), "--height", str(HEIGHT),
                "--frame-dir", str(self.frame),
                "--checkpoint-event", "15", "--draw-event", "14",
                "--renderer", str(self.renderer),
                "--control-out", str(self.output_root / "agent-out")])
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
                "--live-image", str(self.frame / "blobs" / "reference.bin"),
                "--width", str(WIDTH), "--height", str(HEIGHT)])
        value = json.loads(buffer.getvalue())
        self.assertNotEqual(code, 0, value)
        self.assertIn("pixel equality alone", value["reason"])


class EvidenceDerivationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.output_temporary = tempfile.TemporaryDirectory()
        self.output_root = Path(self.output_temporary.name)
        self.addCleanup(self.output_temporary.cleanup)
        self.frame = self.root / "frame"
        (self.frame / "blobs").mkdir(parents=True)
        (self.frame / "blobs" / "seed.bin").write_bytes(b"\x00" * 64)
        (self.frame / "blobs" / "reference.bin").write_bytes(b"\x01" * 64)
        self.seed_sha = _sha_bytes(b"\x00" * 64)
        self.reference_sha = _sha_bytes(b"\x01" * 64)
        self.events = [
            {"kind": "clear", "index": 7, "flags": 7, "color": [0, 0, 0, 1],
             "depth": 1, "stencil": 0,
             "target": {"framebuffer": 0, "draw_buffer": "0x405",
                        "read_buffer": "0x405", "width": 4, "height": 4},
             "state": {"scissor": {"enabled": False, "box": [0, 0, 4, 4]},
                       "color_mask": [1, 1, 1, 1], "depth_mask": True,
                       "stencil_mask": -1}},
            {"kind": "draw", "index": 14,
             "state": {
                 "blend": {"enabled": False, "src_rgb": "0x1",
                           "dst_rgb": "0x0", "src_alpha": "0x1",
                           "dst_alpha": "0x0", "equation_rgb": "0x8006",
                           "equation_alpha": "0x8006"},
                 "color_mask": [1, 1, 1, 1],
                 "depth": {"test": True, "write": True, "func": "0x203",
                           "range": [0.0, 1.0]},
                 "stencil": {"test": True, "func": "0x207", "ref": 0,
                             "value_mask": -1, "fail": "0x1e00",
                             "zfail": "0x1e00", "zpass": "0x1e00",
                             "write_mask": -1},
                 "cull": {"enabled": False, "mode": "0x405",
                          "front": "0x901"},
                 "polygon": {"mode": ["0x1b02", "0x1b02"],
                             "offset_fill": False, "offset_factor": 0,
                             "offset_units": 0},
                 "alpha_test": {"enabled": False, "func": "0x206", "ref": 1},
                 "scissor": {"enabled": False, "box": [0, 0, 4, 4]},
                 "framebuffer_srgb": False, "dither": True,
                 "multisample": True, "viewport": [0, 0, 4, 4]},
             "target": {"framebuffer": 0, "draw_buffer": "0x405",
                        "read_buffer": "0x405", "width": 4, "height": 4},
             "draw": {"mode": 4, "count": 6, "indexed": True},
             "default_checkpoint": {
                 "schema": "gfx-default-framebuffer-checkpoint/v1",
                 "event": 14, "target": "0", "resource": "fb:0",
                 "timing": "pre_event",
                 "status": "captured", "file": "seed.bin",
                 "sha256": self.seed_sha, "width": 4,
                 "height": 4, "session_id": "s", "bytes": 64,
                 "pixel_format": "BGRA8", "origin": "lower_left"}},
            {"kind": "draw", "index": 15,
             "target": {"framebuffer": 0, "width": 4, "height": 4},
             "draw": {"mode": 4, "count": 6, "indexed": True},
             "default_checkpoint": {
                 "schema": "gfx-default-framebuffer-checkpoint/v1",
                 "event": 15, "target": "0", "resource": "fb:0",
                 "timing": "pre_event",
                 "status": "captured", "file": "reference.bin",
                 "sha256": self.reference_sha, "width": 4,
                 "height": 4, "session_id": "s", "bytes": 64,
                 "pixel_format": "BGRA8", "origin": "lower_left"}},
        ]

    def tearDown(self):
        self.temporary.cleanup()

    def _derive(self, *, drawable=None, events=None):
        trace = {
            "gl": ({"drawable_observed": True, "drawable": drawable}
                   if drawable is not None else {"drawable_observed": False}),
            "_events": events if events is not None else self.events,
        }
        seal = {"session_id": "s", "capture_frame_id": 1, "present_index": 904,
                "event_stream_sha256": "0" * 64}
        return replay_input.derive_evidence(
            trace, seal, checkpoint_event=15, draw_event=14,
            resource="fb:0", width=4, height=4)

    def test_a_complete_clear_history_proves_depth_and_stencil(self):
        evidence = self._derive(drawable={
            "samples": 0, "sample_buffers": 0, "depth_bits": 32,
            "stencil_bits": 8, "red_bits": 8, "green_bits": 8,
            "blue_bits": 8, "alpha_bits": 8, "double_buffer": True,
            "stereo": False, "drawn_framebuffer_binding": 0,
        })
        self.assertEqual(evidence["status"], "proven", evidence)
        self.assertEqual(evidence["prestate"]["depth"]["source"],
                         "clear_history")
        self.assertEqual(evidence["prestate"]["stencil"]["source"],
                         "clear_history")

    def test_a_later_writer_breaks_the_clear_history(self):
        events = list(self.events)
        events.insert(1, {
            "kind": "draw", "index": 10,
            "target": {"framebuffer": 0, "width": 4, "height": 4},
            "draw": {"mode": 4, "count": 6, "indexed": True},
        })
        evidence = self._derive(drawable={
            "samples": 0, "sample_buffers": 0, "depth_bits": 32,
            "stencil_bits": 8, "red_bits": 8, "green_bits": 8,
            "blue_bits": 8, "alpha_bits": 8, "double_buffer": True,
            "stereo": False, "drawn_framebuffer_binding": 0,
        }, events=events)
        self.assertEqual(evidence["status"], "missing")
        self.assertTrue(any("depth" in item
                            for item in evidence["missing"]))

    def test_a_scissored_clear_does_not_prove_full_coverage(self):
        events = copy.deepcopy(self.events)
        events[0]["state"]["scissor"] = {"enabled": True,
                                         "box": [0, 0, 2, 2]}
        evidence = self._derive(drawable={
            "samples": 0, "sample_buffers": 0, "depth_bits": 32,
            "stencil_bits": 8, "red_bits": 8, "green_bits": 8,
            "blue_bits": 8, "alpha_bits": 8, "double_buffer": True,
            "stereo": False, "drawn_framebuffer_binding": 0,
        }, events=events)
        self.assertEqual(evidence["status"], "missing")
        self.assertTrue(any("scissor" in item
                            for item in evidence["missing"]))

    def test_an_unrecorded_clear_mask_is_not_assumed(self):
        events = copy.deepcopy(self.events)
        events[0].pop("state")
        evidence = self._derive(drawable={
            "samples": 0, "sample_buffers": 0, "depth_bits": 32,
            "stencil_bits": 8, "red_bits": 8, "green_bits": 8,
            "blue_bits": 8, "alpha_bits": 8, "double_buffer": True,
            "stereo": False, "drawn_framebuffer_binding": 0,
        }, events=events)
        self.assertEqual(evidence["status"], "missing")
        self.assertTrue(any("mask state was not recorded" in item
                            for item in evidence["missing"]))

    def test_the_target_checkpoint_cannot_be_a_prestate_seed(self):
        events = copy.deepcopy(self.events)
        events[1]["default_checkpoint"]["file"] = "reference.bin"
        events[1]["default_checkpoint"]["sha256"] = self.reference_sha
        evidence = self._derive(drawable={
            "samples": 0, "sample_buffers": 0, "depth_bits": 32,
            "stencil_bits": 8, "red_bits": 8, "green_bits": 8,
            "blue_bits": 8, "alpha_bits": 8, "double_buffer": True,
            "stereo": False, "drawn_framebuffer_binding": 0,
        }, events=events)
        self.assertEqual(evidence["status"], "unsupported_state")
        self.assertTrue(any("reference output" in item
                            for item in evidence["unsupported"]))


    def _drawable(self, **overrides):
        value = {
            "samples": 0, "sample_buffers": 0, "depth_bits": 32,
            "stencil_bits": 8, "red_bits": 8, "green_bits": 8,
            "blue_bits": 8, "alpha_bits": 8, "double_buffer": True,
            "stereo": False, "drawn_framebuffer_binding": 0,
        }
        value.update(overrides)
        return value

    def test_a_clear_without_a_scissor_record_is_not_proven(self):
        events = copy.deepcopy(self.events)
        events[0]["state"].pop("scissor")
        evidence = self._derive(drawable=self._drawable(), events=events)
        self.assertEqual(evidence["status"], "missing")
        self.assertTrue(any("scissor" in item
                            for item in evidence["missing"]))

    def test_a_clear_with_depth_mask_zero_is_not_proven(self):
        events = copy.deepcopy(self.events)
        events[0]["state"]["depth_mask"] = 0
        evidence = self._derive(drawable=self._drawable(), events=events)
        self.assertEqual(evidence["status"], "missing")
        self.assertTrue(any("depth" in item and "unproven" in item
                            for item in evidence["missing"]))

    def test_a_clear_cannot_substitute_for_the_colour_seed(self):
        events = copy.deepcopy(self.events)
        events[1].pop("default_checkpoint")
        evidence = self._derive(drawable=self._drawable(), events=events)
        self.assertEqual(evidence["status"], "missing")
        self.assertTrue(any("pre-draw colour seed was not captured" in item
                            for item in evidence["missing"]))

    def test_a_targetless_writer_is_not_ignored(self):
        events = copy.deepcopy(self.events)
        events.insert(1, {"kind": "draw", "index": 10,
                          "draw": {"mode": 4, "count": 6, "indexed": True}})
        evidence = self._derive(drawable=self._drawable(), events=events)
        self.assertEqual(evidence["status"], "missing")
        self.assertTrue(any("no recorded target" in item
                            for item in evidence["missing"]))

    def test_an_unsupported_copy_is_not_ignored(self):
        events = copy.deepcopy(self.events)
        events.insert(1, {"kind": "resolve",
                          "operation": "glCopyTexSubImage2D",
                          "source_framebuffer": 0})
        evidence = self._derive(drawable=self._drawable(), events=events)
        self.assertEqual(evidence["status"], "missing")
        self.assertTrue(any("unsupported" in item
                            for item in evidence["missing"]))

    def test_a_clear_without_a_depth_value_is_not_proven(self):
        events = copy.deepcopy(self.events)
        events[0].pop("depth")
        events[1]["default_checkpoint"].pop("depth", None)
        evidence = self._derive(drawable=self._drawable(), events=events)
        self.assertEqual(evidence["status"], "missing")
        self.assertTrue(any("clear depth value" in item
                            for item in evidence["missing"]))

    def test_a_clear_without_a_stencil_value_is_not_proven(self):
        events = copy.deepcopy(self.events)
        events[0].pop("stencil")
        evidence = self._derive(drawable=self._drawable(), events=events)
        self.assertEqual(evidence["status"], "missing")
        self.assertTrue(any("clear stencil value" in item
                            for item in evidence["missing"]))

    def test_an_unclassified_event_kind_is_not_ignored(self):
        events = copy.deepcopy(self.events)
        events.insert(1, {"kind": "scene_entry", "seq": 1})
        with self.assertRaises(replay_input.ReplayInputError):
            self._derive(drawable=self._drawable(), events=events)

    def test_a_v2_checkpoint_record_is_accepted(self):
        events = copy.deepcopy(self.events)
        events[1]["default_checkpoint"]["schema"] = \
            "gfx-default-framebuffer-checkpoint/v2"
        evidence = self._derive(drawable=self._drawable(), events=events)
        self.assertEqual(evidence["status"], "proven", evidence)


class ShippedAuthorityRegistryTests(unittest.TestCase):
    """The production registry stays fail-closed until a receipt is bound."""

    def test_the_shipped_registry_is_empty(self):
        self.assertEqual(authorities.AUTHORITIES, {})
        self.assertEqual(
            authorities.SCHEMA,
            "gfx-gl-control-renderer-authorities/v2")

    def test_an_arbitrary_renderer_digest_is_refused_without_a_registry_entry(self):
        authority = qualification._load_authority("f" * 64)
        self.assertFalse(authority["qualified"])
        self.assertFalse(authority["self_attested"])


if __name__ == "__main__":
    unittest.main()
