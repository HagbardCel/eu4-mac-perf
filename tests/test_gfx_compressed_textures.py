"""Native compressed-texture chain contract for the Metal replay host.

These tests are synthetic and game-independent.  They exercise the production
selection/upload path (BC1/BC2/BC3, legacy decoded, partial/mixed/invalid
chains, raw GL enum preservation and device capability) without EU4 or a
captured frame.
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
CORE = BENCH / "eu4_gfx_frame_replay.mm"
HOST = ROOT / "tests" / "gfx_compressed_texture_host.mm"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


MTL_RGBA8_UNORM = 70
MTL_BC1_RGBA = 130
MTL_BC2_RGBA = 132
MTL_BC3_RGBA = 134


class CompressedTextureChainTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        cls.base = Path(cls.temporary.name)
        cls.frame = cls.base / "frame"
        (cls.frame / "blobs").mkdir(parents=True)
        if sys.platform != "darwin":
            raise unittest.SkipTest("the Metal replay core is macOS")
        if shutil.which("clang") is None:
            raise unittest.SkipTest("clang is unavailable")
        cls.binary = cls.base / "gfx_compressed_texture_host"
        built = subprocess.run(
            ["clang", "-arch", "arm64", "-O1", "-fobjc-arc", "-lc++",
             "-I", str(BENCH), "-framework", "Foundation",
             "-framework", "Metal", "-o", str(cls.binary),
             str(HOST), str(CORE)],
            capture_output=True, text=True, check=False, timeout=300)
        if built.returncode:
            raise AssertionError(
                "eligible compressed-texture host build failed: "
                + (built.stderr or "<empty>"))

    @classmethod
    def tearDownClass(cls):
        cls.temporary.cleanup()

    def blob(self, name: str, payload: bytes) -> dict:
        path = self.frame / "blobs" / name
        path.write_bytes(payload)
        return {"file": name, "sha256": hashlib.sha256(payload).hexdigest(),
                "bytes": len(payload)}

    def level(self, index: int, width: int, height: int, block: int,
              *, raw: int, name: str, compressed: bool = True,
              decoded_payload: bytes | None = None,
              block_payload: bytes | None = None) -> dict:
        decoded = self.blob(
            f"decoded-{name}.bin",
            decoded_payload if decoded_payload is not None
            else bytes([index]) * (width * height * 4))
        entry = {
            "level": index, "width": width, "height": height,
            **decoded, "pixel_format": "RGBA8",
        }
        if compressed:
            blocks_wide = (width + 3) // 4
            blocks_high = (height + 3) // 4
            payload = (block_payload if block_payload is not None
                       else bytes([0x11 + index]) * (
                           blocks_wide * blocks_high * block))
            entry["compressed"] = {
                **self.blob(f"compressed-{name}.bin", payload),
                "format": {0x83F0: "DXT1", 0x83F1: "DXT1_ALPHA",
                           0x83F2: "DXT3", 0x83F3: "DXT5"}.get(
                               raw, "UNKNOWN"),
                "internal_format": raw,
                "row_stride": blocks_wide * block,
            }
        return entry

    def texture(self, levels: list[dict], name: str) -> Path:
        path = self.base / f"{name}.json"
        path.write_text(json.dumps({
            "target": "2D", "width": levels[0]["width"],
            "height": levels[0]["height"], "levels": levels,
        }))
        return path

    def run_texture(self, path: Path):
        return subprocess.run(
            [str(self.binary), str(self.frame), str(path)],
            capture_output=True, text=True, check=False)

    def run_sample(self, path: Path):
        result = subprocess.run(
            [str(self.binary), "--sample", str(self.frame), str(path)],
            capture_output=True, text=True, check=False)
        self.assertEqual(result.returncode, 0, result.stderr)
        return [tuple(int(value) for value in line.split(","))
                for line in result.stdout.splitlines() if line.strip()]

    def assert_built(self, levels, name, pixel_format, mip_levels=2):
        result = self.run_texture(self.texture(levels, name))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(),
                         f"format={pixel_format} levels={mip_levels}")

    def test_complete_bc1_alpha_bc2_and_bc3_chains(self):
        for raw, block, expected in (
                (0x83F1, 8, MTL_BC1_RGBA),
                (0x83F2, 16, MTL_BC2_RGBA),
                (0x83F3, 16, MTL_BC3_RGBA)):
            levels = [
                self.level(0, 8, 8, block, raw=raw, name=f"l0-{raw:x}"),
                self.level(1, 4, 4, block, raw=raw, name=f"l1-{raw:x}"),
            ]
            self.assert_built(levels, f"complete-{raw:x}", expected)

    def test_dxt1_rgb_is_not_conflated_with_one_bit_alpha(self):
        # GL 0x83F0 guarantees opaque alpha.  Metal has no BC1 RGB format, so
        # the production path must use the decoded representation rather than
        # BC1_RGBA, whose transparent index would change sampled alpha.
        levels = [
            self.level(0, 8, 8, 8, raw=0x83F0, name="rgb0"),
            self.level(1, 4, 4, 8, raw=0x83F0, name="rgb1"),
        ]
        self.assert_built(levels, "dxt1-rgb", MTL_RGBA8_UNORM)

    def test_legacy_all_decoded_texture_still_uses_decoded_path(self):
        levels = [
            self.level(0, 8, 8, 8, raw=0x83F3, name="dec0", compressed=False),
            self.level(1, 4, 4, 8, raw=0x83F3, name="dec1", compressed=False),
        ]
        self.assert_built(levels, "decoded", MTL_RGBA8_UNORM)

    def test_bc1_transparent_index_alpha_is_preserved_per_gl_enum(self):
        # color0=0, color1=0xffff, every index=3.  RGBA DXT1 samples the
        # transparent index (alpha 0); RGB DXT1 must remain opaque and use the
        # decoded representation because Metal has no BC1 RGB format.
        block = bytes.fromhex("0000ffffffffffff") * 4
        opaque = bytes([12, 34, 56, 255]) * (8 * 8)
        cases = ((0x83F1, "DXT1_ALPHA", 0), (0x83F0, "DXT1", 255))
        for raw, label, expected_alpha in cases:
            level = self.level(0, 8, 8, 8, raw=raw, name=f"alpha-{raw:x}",
                               decoded_payload=opaque, block_payload=block)
            pixels = self.run_sample(
                self.texture([level], f"alpha-sample-{raw:x}"))
            alphas = {pixel[3] for pixel in pixels}
            self.assertEqual(alphas, {expected_alpha}, label)
            if raw == 0x83F0:
                self.assertEqual({pixel[:3] for pixel in pixels},
                                 {(12, 34, 56)})

    def test_compressed_and_decoded_paths_sample_the_same_orientation(self):
        # The qualified uncompressed convention uploads rows unchanged; the
        # compressed path must match it.  Encode the same logical rows as RGBA8
        # and BC1 and require identical sampled pixels.
        red = bytes.fromhex("00f8e00700000000")
        green = bytes.fromhex("e00700f800000000")
        decoded = (bytes([255, 0, 0, 255]) * (8 * 4)
                   + bytes([0, 255, 0, 255]) * (8 * 4))
        compressed = self.level(0, 8, 8, 8, raw=0x83F1, name="orient-comp",
                                block_payload=red * 2 + green * 2)
        decoded_level = self.level(0, 8, 8, 8, raw=0x83F1, name="orient-dec",
                                   compressed=False, decoded_payload=decoded)
        compressed_pixels = self.run_sample(
            self.texture([compressed], "orient-compressed"))
        decoded_pixels = self.run_sample(
            self.texture([decoded_level], "orient-decoded"))
        self.assertEqual(compressed_pixels, decoded_pixels)

    def test_missing_all_expected_blocks_is_not_the_decoded_legacy_path(self):
        level = self.level(0, 8, 8, 8, raw=0x83F1, name="lost", compressed=False)
        level["compressed_expected"] = True
        result = self.run_texture(self.texture([level], "lost-blocks"))
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("invalid geometry", result.stderr)

    def test_wrong_mip_extent_bytes_and_level_index_are_rejected(self):
        cases = {
            "extent": lambda levels: levels[1].update({"width": 2, "height": 2}),
            "bytes": lambda levels: levels[0]["compressed"].update(
                {"bytes": 8}),
            "level": lambda levels: levels[1].update({"level": 0}),
        }
        for name, mutate in cases.items():
            levels = [
                self.level(0, 8, 8, 16, raw=0x83F3, name=f"m-{name}-0"),
                self.level(1, 4, 4, 16, raw=0x83F3, name=f"m-{name}-1"),
            ]
            mutate(levels)
            result = self.run_texture(self.texture(levels, f"mip-{name}"))
            self.assertNotEqual(result.returncode, 0, name)
            self.assertIn("invalid", result.stderr.lower(), name)

    def test_unsupported_bc_device_refuses_the_texture(self):
        level = self.level(0, 8, 8, 8, raw=0x83F1, name="no-bc")
        path = self.texture([level], "no-bc")
        result = subprocess.run(
            [str(self.binary), str(self.frame), str(path)],
            capture_output=True, text=True, check=False,
            env={**os.environ, "EU4_FRAME_REPLAY_FORCE_NO_BC": "1"})
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("does not support BC", result.stderr)

    def test_partial_chain_is_rejected_not_silently_decoded(self):
        levels = [
            self.level(0, 8, 8, 16, raw=0x83F3, name="p0"),
            self.level(1, 4, 4, 16, raw=0x83F3, name="p1",
                       compressed=False),
        ]
        result = self.run_texture(self.texture(levels, "partial"))
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("partial", result.stderr)

    def test_mixed_formats_are_rejected(self):
        levels = [
            self.level(0, 8, 8, 16, raw=0x83F3, name="m0"),
            self.level(1, 4, 4, 16, raw=0x83F2, name="m1"),
        ]
        result = self.run_texture(self.texture(levels, "mixed"))
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("mixes", result.stderr)

    def test_invalid_size_or_stride_is_rejected(self):
        levels = [
            self.level(0, 8, 8, 16, raw=0x83F3, name="i0"),
            self.level(1, 4, 4, 16, raw=0x83F3, name="i1"),
        ]
        levels[0]["compressed"]["row_stride"] = 16  # should be 32
        result = self.run_texture(self.texture(levels, "invalid"))
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("invalid geometry", result.stderr)

    def test_unsupported_raw_enum_is_rejected(self):
        levels = [
            self.level(0, 8, 8, 16, raw=0x9999, name="u0"),
            self.level(1, 4, 4, 16, raw=0x9999, name="u1"),
        ]
        result = self.run_texture(self.texture(levels, "unsupported"))
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("unsupported", result.stderr)


if __name__ == "__main__":
    unittest.main()
