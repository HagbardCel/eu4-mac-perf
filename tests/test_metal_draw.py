import os
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "benchmark/eu4_metal_draw.m"
VERT = ROOT / "analysis/metal-stage-d/v1/color.vert.metal"
FRAG = ROOT / "analysis/metal-stage-d/v1/color.frag.metal"
BUILD = ROOT / "benchmark/.build"


@unittest.skipUnless(sys.platform == "darwin", "Metal draw runs on macOS")
class MetalDrawTests(unittest.TestCase):
    def test_known_triangle_produces_nonzero_pixels(self):
        BUILD.mkdir(parents=True, exist_ok=True)
        library = BUILD / "libeu4_metal_draw.dylib"
        host = BUILD / "metal_draw_host"
        compile = subprocess.run(
            ["clang", "-arch", "arm64", "-fobjc-arc", "-dynamiclib", "-framework", "Metal",
             "-framework", "OpenGL", "-framework", "Foundation", "-framework", "ImageIO",
             "-framework", "CoreGraphics", "-framework", "CoreFoundation", "-o", str(library), str(SOURCE)],
            capture_output=True, text=True, check=False)
        self.assertEqual(compile.returncode, 0, compile.stderr)
        host_compile = subprocess.run(
            ["clang", "-arch", "arm64", "-framework", "OpenGL", "-o", str(host),
             str(ROOT / "tests/metal_draw_host.c"), str(library)],
            capture_output=True, text=True, check=False)
        self.assertEqual(host_compile.returncode, 0, host_compile.stderr)
        env = os.environ.copy()
        env["EU4_METAL_DRAW_DIR"] = "/tmp"
        env["EU4_METAL_DRAW_VERT"] = str(VERT)
        env["EU4_METAL_DRAW_FRAG"] = str(FRAG)
        result = subprocess.run([str(host)], capture_output=True, text=True, check=False, env=env)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertGreater(int(result.stdout.strip()), 0)


if __name__ == "__main__":
    unittest.main()
