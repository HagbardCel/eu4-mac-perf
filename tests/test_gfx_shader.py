import os
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HOST_SOURCE = ROOT / "tests" / "gfx_shader_host.mm"
SHADER = ROOT / "benchmark" / "eu4_gfx_shader.mm"
HOST = ROOT / "benchmark" / ".build" / "gfx_shader_host"


@unittest.skipUnless(sys.platform == "darwin", "shader replacement runs on macOS")
class GfxShaderTests(unittest.TestCase):
    def test_simple_lifecycle_and_pipeline(self) -> None:
        HOST.parent.mkdir(parents=True, exist_ok=True)
        env = os.environ.copy()
        env["MTL_DEBUG_LAYER"] = "1"
        env["MTL_DEBUG_LAYER_ERROR_MODE"] = "assert"
        command = [
            "clang",
            "-O2",
            "-Wall",
            "-Wextra",
            "-fobjc-arc",
            "-lc++",
            "-framework",
            "Metal",
            "-framework",
            "Foundation",
            "-I",
            str(ROOT / "benchmark"),
            "-o",
            str(HOST),
            str(HOST_SOURCE),
            str(SHADER),
        ]
        built = subprocess.run(command, capture_output=True, text=True, check=False)
        if built.returncode:
            self.fail(built.stderr.strip() or "shader host failed to build")
        ran = subprocess.run(
            [str(HOST)], capture_output=True, text=True, check=False, env=env
        )
        if ran.returncode:
            self.fail(ran.stderr.strip() or ran.stdout.strip() or "shader host failed")
        self.assertIn("ok", ran.stdout)


if __name__ == "__main__":
    unittest.main()
