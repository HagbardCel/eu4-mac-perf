import platform
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HOST_SRC = ROOT / "tests/metal_color_ref_host.m"
SHADERS = ROOT / "analysis/metal-stage-d/v1"
BUILD = ROOT / "benchmark/.build"


@unittest.skipUnless(sys.platform == "darwin", "Color conformance runs on macOS")
class MetalColorRefTests(unittest.TestCase):
    def test_synthetic_color_fixtures_match(self):
        arch = "arm64" if platform.machine() == "arm64" else "x86_64"
        BUILD.mkdir(parents=True, exist_ok=True)
        host = BUILD / "metal_color_ref_host"
        compile = subprocess.run(
            ["clang", "-arch", arch, "-fobjc-arc", "-framework", "Metal", "-framework", "OpenGL",
             "-framework", "Foundation", "-o", str(host), str(HOST_SRC)],
            capture_output=True, text=True, check=False)
        self.assertEqual(compile.returncode, 0, compile.stderr)
        result = subprocess.run(
            [str(host), str(SHADERS / "color.vert"), str(SHADERS / "color.frag"),
             str(SHADERS / "color.vert.metal"), str(SHADERS / "color.frag.metal")],
            capture_output=True, text=True, check=False)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("ok", result.stdout)


if __name__ == "__main__":
    unittest.main()
