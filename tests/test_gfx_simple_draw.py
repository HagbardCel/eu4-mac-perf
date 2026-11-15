import hashlib
import os
import struct
import subprocess
import sys
import unittest
from pathlib import Path
from typing import Optional

ROOT = Path(__file__).resolve().parents[1]
HOST_SOURCE = ROOT / "tests" / "gfx_simple_draw_host.mm"
HOST = ROOT / "benchmark" / ".build" / "gfx_simple_draw_host"
PACKET = ROOT / "analysis" / "gfx-draw" / "v0" / "l32b" / "effective-state" / "packet"
VB = PACKET / "payloads" / "vb_3.bin"
MATRIX = PACKET / "payloads" / "matrix.bin"
DDS = Path("/Applications/EuropaUniversalisIV/gfx/loadingscreens/load_0.dds")

SOURCES = [
    ROOT / "benchmark" / "eu4_gfx_shader.mm",
    ROOT / "benchmark" / "eu4_gfx_buffers.mm",
    ROOT / "benchmark" / "eu4_gfx_texture.mm",
    ROOT / "benchmark" / "eu4_gfx_init.mm",
    ROOT / "benchmark" / "eu4_gfx_frame_observe.c",
    ROOT / "benchmark" / "eu4_gfx_simple_draw.mm",
    ROOT / "benchmark" / "eu4_metal_events.c",
]

# Established by the L3.2b-I packet manifest (tracked, reviewed).
VB_SHA256 = "f13dea22399b9649d84a09965ec6b21bc13b72db3c17049916a675922a39c8ab"
MATRIX_SHA256 = "e00f5824739f2976ef02dbc5019f54cf89260b8f882a96b8989eab888b7d5671"


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


@unittest.skipUnless(sys.platform == "darwin", "Simple draw runs on Metal/macOS")
class GfxSimpleDrawTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        HOST.parent.mkdir(parents=True, exist_ok=True)
        command = [
            "clang", "-O2", "-Wall", "-Wextra", "-fobjc-arc", "-lc++",
            "-I", str(ROOT / "benchmark"),
            "-framework", "Foundation", "-framework", "Metal", "-framework", "QuartzCore",
            "-o", str(HOST), str(HOST_SOURCE), *[str(source) for source in SOURCES],
        ]
        built = subprocess.run(command, capture_output=True, text=True, check=False)
        if built.returncode:
            raise AssertionError(built.stderr.strip() or "simple draw host failed to build")

    def test_authentic_packet_bytes(self) -> None:
        self.assertEqual(sha256(VB), VB_SHA256)
        self.assertEqual(sha256(MATRIX), MATRIX_SHA256)
        rows = struct.unpack("<16f", MATRIX.read_bytes())
        self.assertEqual(rows[0], 1.0 / 512.0)
        self.assertAlmostEqual(rows[5], 2.0 / 661.0, places=6)
        self.assertEqual(rows[10], 1.0)
        self.assertEqual(rows[15], 1.0)
        vertices = VB.read_bytes()
        self.assertEqual(len(vertices), 96)
        self.assertEqual(struct.unpack("<3f", vertices[0:12]), (-512.0, 384.0, 0.5))
        self.assertEqual(struct.unpack("<2f", vertices[12:20]), (0.0, 0.0))
        self.assertEqual(struct.unpack("<2f", vertices[24 + 12:24 + 20]), (1.0, 0.0))

    def _run(self, dds: Optional[Path]) -> str:
        command = [str(HOST), "--vb", str(VB), "--matrix", str(MATRIX)]
        if dds is not None:
            command += ["--dds", str(dds)]
        env = os.environ.copy()
        env["MTL_DEBUG_LAYER"] = "1"
        env["MTL_DEBUG_LAYER_ERROR_MODE"] = "assert"
        ran = subprocess.run(command, capture_output=True, text=True, check=False, env=env)
        if ran.returncode:
            self.fail(ran.stderr.strip() or ran.stdout.strip() or "simple draw host failed")
        return ran.stdout

    def test_original_loading_screen_draw(self) -> None:
        if not DDS.is_file():
            self.skipTest("original loading-screen asset is not installed")
        stdout = self._run(DDS)
        self.assertIn("texture=original-dds 2048x1536", stdout)
        self.assertIn("ok checked=1600", stdout)
        self.assertIn("blend_bad=0", stdout)
        self.assertIn("order_bad=0", stdout)
        self.assertIn("repeat_bad=0", stdout)
        self.assertIn("first_bad=0", stdout)
        self.assertNotIn("mismatches", stdout)
        self.assertIn("done", stdout)

    def test_hermetic_synthetic_texture_draw(self) -> None:
        stdout = self._run(None)
        self.assertIn("texture=synthetic-bgr 2048x1536", stdout)
        self.assertIn("ok checked=1600", stdout)
        self.assertIn("blend_bad=0", stdout)
        self.assertIn("order_bad=0", stdout)
        self.assertIn("repeat_bad=0", stdout)
        self.assertIn("first_bad=0", stdout)
        self.assertNotIn("mismatches", stdout)


if __name__ == "__main__":
    unittest.main()
