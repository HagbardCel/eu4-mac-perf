import os
import platform
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HOST_SRC = ROOT / "tests/metal_simple_ref_host.m"
SHADERS = ROOT / "analysis/gfx-shader/v0"
BUILD = ROOT / "benchmark/.build"


@unittest.skipUnless(sys.platform == "darwin", "Simple GL–Metal ref runs on macOS")
class MetalSimpleRefTests(unittest.TestCase):
    def test_simple_gl_metal_agreement(self):
        arch = "arm64" if platform.machine() == "arm64" else "x86_64"
        BUILD.mkdir(parents=True, exist_ok=True)
        host = BUILD / "metal_simple_ref_host"
        env = os.environ.copy()
        env["MTL_DEBUG_LAYER"] = "1"
        env["MTL_DEBUG_LAYER_ERROR_MODE"] = "assert"
        compile = subprocess.run(
            [
                "clang",
                "-arch",
                arch,
                "-fobjc-arc",
                "-framework",
                "Metal",
                "-framework",
                "OpenGL",
                "-framework",
                "Foundation",
                "-o",
                str(host),
                str(HOST_SRC),
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(compile.returncode, 0, compile.stderr)
        result = subprocess.run(
            [
                str(host),
                str(SHADERS / "simple.vert.glsl"),
                str(SHADERS / "simple.frag.glsl"),
                str(SHADERS / "simple.metal"),
            ],
            capture_output=True,
            text=True,
            check=False,
            env=env,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("ok", result.stdout)


if __name__ == "__main__":
    unittest.main()
