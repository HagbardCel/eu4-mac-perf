"""Native compressed-texture chain contract for the Metal replay host.

These tests are synthetic and game-independent.  They exercise the production
selection/upload path (BC1/BC2/BC3, legacy decoded, partial/mixed/invalid
chains, raw GL enum preservation and device capability) without EU4 or a
captured frame.
"""

from __future__ import annotations

import hashlib
import json
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
              *, raw: int, name: str, compressed: bool = True) -> dict:
        decoded = self.blob(
            f"decoded-{name}.bin", bytes([index]) * (width * height * 4))
        entry = {
            "level": index, "width": width, "height": height,
            **decoded, "pixel_format": "RGBA8",
        }
        if compressed:
            blocks_wide = (width + 3) // 4
            blocks_high = (height + 3) // 4
            payload = bytes([0x11 + index]) * (
                blocks_wide * blocks_high * block)
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
