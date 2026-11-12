#!/usr/bin/env python3
"""Adversarial tests for the backend-independent graphics comparator."""

from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from automation import gfx_compare

EXE = "a" * 64
LIB = "b" * 64


def payload(width: int, height: int, *, format: str = "BGRA8") -> bytes:
    data = bytearray()
    for y in range(height):
        for x in range(width):
            r = x * 40
            g = y * 40
            b = (x + y) * 20
            if format == "BGRA8":
                data += bytes((b % 256, g % 256, r % 256, 255))
            else:
                data += bytes((r % 256, g % 256, b % 256, 255))
    return bytes(data)


class CaptureFactory:
    def __init__(self, root: Path):
        self.root = root

    def gl(
        self,
        *,
        name: str = "reference",
        width: int = 4,
        height: int = 4,
        raw: bytes | None = None,
        caller: str = "0xedfcd1",
        mode: int = 5,
        count: int = 4,
        origin: str = "lower_left",
        pixel_format: str = "BGRA8",
        gpu_ok: bool = True,
        raise_on: str | None = None,
        sha_override: str | None = None,
        program: int = 17,
        glsl_sha: str = "1" * 64,
    ) -> dict:
        directory = self.root / name
        directory.mkdir(parents=True, exist_ok=True)
        body = raw if raw is not None else payload(width, height, format=pixel_format)
        suffix = "bgra" if pixel_format == "BGRA8" else "rgba"
        raw_name = f"{name}.{suffix}"
        if raise_on != "payload_missing":
            (directory / raw_name).write_bytes(body)
        if raise_on == "payload_truncated":
            (directory / raw_name).write_bytes(body[:-4])
        declared_size = len(body) if raise_on != "payload_size" else len(body) - 4
        digest = sha_override or hashlib.sha256(body).hexdigest()
        descriptor = {
            "schema": "gfx-gl-reference/v1",
            "backend": "opengl" if raise_on != "backend" else "cpu",
            "captured_utc": "2026-11-12T00:00:00.000000Z",
            "provenance": {
                "executable": "/Applications/EuropaUniversalisIV/eu4.app/Contents/MacOS/eu4",
                "executable_sha256": "" if raise_on == "provenance" else EXE,
                "library_sha256": LIB,
                "session": "test",
                "pid": 42,
            },
            "draw": {
                "symbol": "_GfxDraw",
                "call_id": 7,
                "caller_rva": caller,
                "caller_known": True,
                "thread_id": 1,
                "mode": mode,
                "effective_count": count,
                "first": 0,
            },
            "target": {
                "framebuffer": 0,
                "read_framebuffer": 0,
                "read_buffer": 1024,
                "viewport": [0, 0, width, height],
                "width": width,
                "height": height,
                "pixel_format": pixel_format,
                "origin": origin,
                "row_stride": width * 4,
                "drawable_width": width,
                "drawable_height": height,
            },
            "gpu": {
                "finish": "glFinish",
                "finish_ns": 10,
                "read_pixels": gpu_ok,
                "read_ns": 10,
                "gl_error": 0 if gpu_ok else 1282,
            },
            "state": {
                "program": program,
                "blend": 0,
                "depth_test": 0,
                "scissor_test": 0,
                "color_mask": [1, 1, 1, 1],
            },
            "capture": {
                "raw_file": raw_name,
                "raw_bytes": declared_size,
                "raw_sha256": digest,
                "pixel_format": pixel_format,
            },
            "pre_draw": None,
            "shader": {"glsl_sha256": glsl_sha},
        }
        (directory / "gl-reference.json").write_text(json.dumps(descriptor))
        return descriptor

    def metal(
        self,
        *,
        name: str = "candidate",
        width: int = 4,
        height: int = 4,
        raw: bytes | None = None,
        caller: str = "0xedfcd1",
        mode: int = 5,
        count: int = 4,
        origin: str = "top_left",
        pixel_format: str = "BGRA8",
        gpu_ok: bool = True,
        raise_on: str | None = None,
        sha_override: str | None = None,
        msl_sha: str = "2" * 64,
        presentation: bool = True,
    ) -> dict:
        directory = self.root / name
        directory.mkdir(parents=True, exist_ok=True)
        body = raw if raw is not None else payload(width, height, format=pixel_format)
        suffix = "bgra" if pixel_format == "BGRA8" else "rgba"
        raw_name = f"{name}.{suffix}"
        if raise_on != "payload_missing":
            (directory / raw_name).write_bytes(body)
        if raise_on == "payload_truncated":
            (directory / raw_name).write_bytes(body[:-4])
        declared_size = len(body) if raise_on != "payload_size" else len(body) - 4
        digest = sha_override or hashlib.sha256(body).hexdigest()
        descriptor = {
            "schema": "gfx-metal-logical/v1",
            "backend": "metal",
            "provenance": {
                "executable": "/Applications/EuropaUniversalisIV/eu4.app/Contents/MacOS/eu4",
                "executable_sha256": EXE,
                "library_sha256": LIB,
                "session": "test",
                "pid": 43,
            },
            "draw": {
                "symbol": "_GfxDraw",
                "call_id": 7,
                "caller_rva": caller,
                "mode": mode,
                "effective_count": count,
                "first": 0,
                "shader_identity": "simple-lua-Simple/asset-maincode-v0",
            },
            "target": {
                "width": width,
                "height": height,
                "pixel_format": pixel_format,
                "origin": origin,
                "row_stride": width * 4,
            },
            "gpu": {"completed": gpu_ok, "evidence": "MTLCommandBufferStatusCompleted"},
            "capture": {
                "raw_file": raw_name,
                "raw_bytes": declared_size,
                "raw_sha256": digest,
                "pixel_format": pixel_format,
            },
            "state": {"blend": "src_alpha/one_minus_src_alpha", "depth": "none"},
            "shader": {"msl_sha256": msl_sha},
            "presentation": {"shown": presentation, "method": "window-screenshot"},
        }
        (directory / "metal-logical.json").write_text(json.dumps(descriptor))
        return descriptor


def policy(**overrides) -> dict:
    base = {
        "schema": gfx_compare.POLICY_SCHEMA,
        "name": "test-policy",
        "reference_backend": "opengl",
        "candidate_backends": ["metal"],
        "require_same_event": True,
        "require_same_extent": True,
        "require_same_pixel_format": True,
        "require_gpu_completed": True,
        "require_presentation": False,
        "allow_origin_normalization": ["vertical_flip"],
        "max_channel_error": 0,
        "max_mismatched_fraction": 0.0,
        "max_mean_abs_error": 0.0,
    }
    base.update(overrides)
    return base


class ComparatorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.factory = CaptureFactory(self.root)
        self.out = self.root / "out"

    def run_compare(self, reference: Path, candidate: Path, config: dict) -> dict:
        policy_path = self.root / "policy.json"
        policy_path.write_text(json.dumps(config))
        code = gfx_compare.main([
            "compare",
            "--reference", str(reference),
            "--candidate", str(candidate),
            "--policy", str(policy_path),
            "--out", str(self.out),
        ])
        report = json.loads((self.out / "report.json").read_text())
        self.assertEqual(code, 0 if report["status"] == "pass" else {
            "fail": gfx_compare.EXIT_FAIL,
            "invalid": gfx_compare.EXIT_INVALID,
            "unavailable": gfx_compare.EXIT_UNAVAILABLE,
        }.get(report["status"], 99))
        return report

    def test_identical_captures_pass_with_row_flip(self) -> None:
        width = height = 4
        rows = payload(width, height)
        # The GL payload is declared lower-left; the Metal payload is the same
        # image declared top-left, so its bytes are the row-reversed payload.
        gl_rows = [rows[y * width * 4:(y + 1) * width * 4] for y in range(height)]
        metal = b"".join(reversed(gl_rows))
        self.factory.gl(width=width, height=height, raw=rows)
        self.factory.metal(width=width, height=height, raw=metal)
        report = self.run_compare(self.root / "reference", self.root / "candidate", policy())
        self.assertEqual(report["status"], "pass")
        self.assertEqual(report["normalization"], ["vertical_flip"])
        self.assertEqual(report["samples"]["mismatched_pixels"], 0)
        self.assertEqual(report["samples"]["max_channel_error"], 0)
        for name in ("reference.png", "candidate.png", "difference.png"):
            self.assertTrue((self.out / name).is_file(), name)

    def test_same_payload_with_wrong_origin_is_rejected(self) -> None:
        rows = payload(4, 4)
        self.factory.gl(raw=rows)
        # Same bytes, but the candidate claims top-left while the reference
        # declares lower-left: the recorded metadata now describes different
        # images, and the comparator must not silently agree.
        self.factory.metal(raw=rows, origin="top_left")
        report = self.run_compare(self.root / "reference", self.root / "candidate", policy())
        self.assertEqual(report["status"], "fail")
        self.assertGreater(report["samples"]["max_channel_error"], 0)

    def test_one_pixel_error_is_rejected_by_exact_policy(self) -> None:
        rows = bytearray(payload(4, 4))
        self.factory.gl(raw=bytes(rows))
        flipped = bytearray(b"".join(reversed(
            [bytes(rows[y * 16:(y + 1) * 16]) for y in range(4)]
        )))
        flipped[0] = (flipped[0] + 1) % 256
        self.factory.metal(raw=bytes(flipped))
        report = self.run_compare(self.root / "reference", self.root / "candidate", policy())
        self.assertEqual(report["status"], "fail")
        self.assertEqual(report["samples"]["mismatched_pixels"], 1)

    def test_one_pixel_error_passes_only_under_a_declared_tolerance(self) -> None:
        rows = bytearray(payload(4, 4))
        self.factory.gl(raw=bytes(rows))
        flipped = bytearray(b"".join(reversed(
            [bytes(rows[y * 16:(y + 1) * 16]) for y in range(4)]
        )))
        flipped[0] = (flipped[0] + 1) % 256
        self.factory.metal(raw=bytes(flipped))
        report = self.run_compare(
            self.root / "reference",
            self.root / "candidate",
            policy(max_channel_error=1, max_mean_abs_error=1.0,
                   max_mismatched_fraction=1.0),
        )
        self.assertEqual(report["status"], "pass")

    def test_widespread_error_is_rejected(self) -> None:
        self.factory.gl()
        flipped = bytearray(b"".join(reversed(
            [payload(4, 4)[y * 16:(y + 1) * 16] for y in range(4)]
        )))
        for index in range(0, len(flipped), 4):
            flipped[index] = (flipped[index] + 8) % 256
        self.factory.metal(raw=bytes(flipped))
        report = self.run_compare(self.root / "reference", self.root / "candidate", policy())
        self.assertEqual(report["status"], "fail")
        self.assertEqual(report["samples"]["mismatched_fraction"], 1.0)

    def test_mismatched_caller_is_invalid(self) -> None:
        self.factory.gl()
        self.factory.metal(caller="0xedfbef")
        report = self.run_compare(self.root / "reference", self.root / "candidate", policy())
        self.assertEqual(report["status"], "invalid")
        self.assertTrue(any("caller_rva" in reason for reason in report["reasons"]))

    def test_mismatched_mode_and_count_are_invalid(self) -> None:
        self.factory.gl()
        self.factory.metal(mode=4, count=3)
        report = self.run_compare(self.root / "reference", self.root / "candidate", policy())
        self.assertEqual(report["status"], "invalid")
        self.assertEqual(len(report["reasons"]), 2)

    def test_mismatched_extent_is_invalid(self) -> None:
        self.factory.gl()
        self.factory.metal(width=8)
        report = self.run_compare(self.root / "reference", self.root / "candidate", policy())
        self.assertEqual(report["status"], "invalid")
        self.assertTrue(any("extent" in reason for reason in report["reasons"]))

    def test_mismatched_pixel_format_is_invalid(self) -> None:
        self.factory.gl(pixel_format="BGRA8")
        self.factory.metal(pixel_format="RGBA8")
        report = self.run_compare(self.root / "reference", self.root / "candidate", policy())
        self.assertEqual(report["status"], "invalid")

    def test_truncated_payload_is_invalid(self) -> None:
        self.factory.gl()
        self.factory.metal(raise_on="payload_truncated")
        report = self.run_compare(self.root / "reference", self.root / "candidate", policy())
        self.assertEqual(report["status"], "invalid")

    def test_declared_size_mismatch_is_invalid(self) -> None:
        self.factory.gl()
        self.factory.metal(raise_on="payload_size")
        report = self.run_compare(self.root / "reference", self.root / "candidate", policy())
        self.assertEqual(report["status"], "invalid")

    def test_digest_mismatch_is_invalid(self) -> None:
        self.factory.gl()
        self.factory.metal(sha_override="f" * 64)
        report = self.run_compare(self.root / "reference", self.root / "candidate", policy())
        self.assertEqual(report["status"], "invalid")
        self.assertTrue(any("sha256" in reason for reason in report["reasons"]))

    def test_missing_payload_is_unavailable(self) -> None:
        self.factory.gl()
        self.factory.metal(raise_on="payload_missing")
        report = self.run_compare(self.root / "reference", self.root / "candidate", policy())
        self.assertEqual(report["status"], "unavailable")

    def test_missing_gpu_completion_is_invalid(self) -> None:
        self.factory.gl(gpu_ok=False)
        self.factory.metal()
        report = self.run_compare(self.root / "reference", self.root / "candidate", policy())
        self.assertEqual(report["status"], "invalid")
        self.assertTrue(any("GPU completion" in reason for reason in report["reasons"]))

    def test_invalid_provenance_is_invalid(self) -> None:
        self.factory.gl(raise_on="provenance")
        self.factory.metal()
        report = self.run_compare(self.root / "reference", self.root / "candidate", policy())
        self.assertEqual(report["status"], "invalid")

    def test_unknown_origin_is_invalid(self) -> None:
        descriptor = self.factory.gl()
        descriptor["target"]["origin"] = "sideways"
        (self.root / "reference" / "gl-reference.json").write_text(json.dumps(descriptor))
        self.factory.metal()
        report = self.run_compare(self.root / "reference", self.root / "candidate", policy())
        self.assertEqual(report["status"], "invalid")

    def test_origin_normalization_requires_documented_policy(self) -> None:
        self.factory.gl()
        flipped = b"".join(reversed([payload(4, 4)[y * 16:(y + 1) * 16] for y in range(4)]))
        self.factory.metal(raw=flipped)
        report = self.run_compare(
            self.root / "reference", self.root / "candidate",
            policy(allow_origin_normalization=[]),
        )
        self.assertEqual(report["status"], "invalid")

    def test_nothing_is_rescaled(self) -> None:
        self.factory.gl(width=4, height=4)
        self.factory.metal(width=2, height=2)
        report = self.run_compare(self.root / "reference", self.root / "candidate", policy())
        self.assertEqual(report["status"], "invalid")
        self.assertTrue(any("extent" in reason for reason in report["reasons"]))

    def test_pinned_shader_digest_mismatch_is_invalid(self) -> None:
        self.factory.gl(glsl_sha="3" * 64)
        self.factory.metal(msl_sha="4" * 64)
        config = policy(expected={
            "executable_sha256": EXE,
            "reference_glsl_sha256": "1" * 64,
            "candidate_msl_sha256": "2" * 64,
        })
        report = self.run_compare(self.root / "reference", self.root / "candidate", config)
        self.assertEqual(report["status"], "invalid")
        self.assertEqual(len([r for r in report["reasons"] if "digest" in r]), 2)

    def test_reference_with_contradictory_backend_is_invalid(self) -> None:
        self.factory.gl(raise_on="backend")
        self.factory.metal()
        report = self.run_compare(self.root / "reference", self.root / "candidate", policy())
        self.assertEqual(report["status"], "invalid")
        self.assertIn("backend", " ".join(report["reasons"]))

    def test_candidate_with_contradictory_backend_is_invalid(self) -> None:
        self.factory.gl()
        descriptor = self.factory.metal()
        descriptor["backend"] = "cpu"
        (self.root / "candidate" / "metal-logical.json").write_text(json.dumps(descriptor))
        report = self.run_compare(self.root / "reference", self.root / "candidate", policy())
        self.assertEqual(report["status"], "invalid")
        self.assertIn("backend", " ".join(report["reasons"]))

    def test_missing_policy_field_is_invalid(self) -> None:
        self.factory.gl()
        self.factory.metal()
        config = policy()
        del config["max_channel_error"]
        report = self.run_compare(self.root / "reference", self.root / "candidate", config)
        self.assertEqual(report["status"], "invalid")

    def test_checkpoint_without_origin_is_refused(self) -> None:
        self.factory.gl()
        directory = self.root / "candidate"
        directory.mkdir(parents=True, exist_ok=True)
        body = payload(4, 4)
        (directory / "checkpoint-after-draw-1.bgra").write_bytes(body)
        descriptor = {
            "schema": "gfx-checkpoint/v1",
            "target": {"width": 4, "height": 4, "row_stride": 16,
                       "pixel_format": "BGRA8Unorm"},
            "capture": {"raw_file": "checkpoint-after-draw-1.bgra",
                        "raw_bytes": len(body),
                        "raw_sha256": hashlib.sha256(body).hexdigest()},
            "gpu": {"completed": True},
            "caller": {"rva": "0xedfcd1", "pid": 1},
            "draw": {"primitive": 5, "count": 4},
        }
        (directory / "checkpoint-after-draw-1.json").write_text(json.dumps(descriptor))
        report = self.run_compare(self.root / "reference", directory, policy())
        self.assertEqual(report["status"], "invalid")
        self.assertTrue(any("origin" in reason for reason in report["reasons"]))


if __name__ == "__main__":
    unittest.main()
