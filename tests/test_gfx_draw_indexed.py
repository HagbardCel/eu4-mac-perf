import os
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HOST = ROOT / "benchmark" / ".build" / "gfx_draw_indexed_host"
VERT = ROOT / "analysis/metal-stage-d/v1/color.vert.metal"
FRAG = ROOT / "analysis/metal-stage-d/v1/color.frag.metal"


@unittest.skipUnless(sys.platform == "darwin", "Metal draw runs on macOS")
class GfxDrawIndexedTests(unittest.TestCase):
    def test_adapter_matches_synthetic_renderer(self):
        HOST.parent.mkdir(parents=True, exist_ok=True)
        sources = [
            ROOT / "tests/gfx_draw_indexed_host.c",
            ROOT / "benchmark/eu4_gfx_draw_indexed.c",
            ROOT / "benchmark/eu4_metal_draw.m",
        ]
        command = [
            "clang", "-arch", "arm64", "-Wall", "-Wextra", "-fobjc-arc",
            "-framework", "Foundation", "-framework", "Metal", "-framework", "OpenGL",
            "-framework", "ImageIO", "-framework", "CoreGraphics", "-framework", "CoreFoundation",
            "-o", str(HOST), *map(str, sources),
        ]
        built = subprocess.run(command, capture_output=True, text=True, check=False)
        self.assertEqual(built.returncode, 0, built.stderr)
        env = os.environ.copy()
        env["EU4_METAL_DRAW_DIR"] = "/tmp"
        env["EU4_METAL_DRAW_VERT"] = str(VERT)
        env["EU4_METAL_DRAW_FRAG"] = str(FRAG)
        ran = subprocess.run([str(HOST)], capture_output=True, text=True, check=False, env=env)
        self.assertEqual(ran.returncode, 0, ran.stderr)


if __name__ == "__main__":
    unittest.main()
