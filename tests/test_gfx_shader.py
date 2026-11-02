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
    def test_allocates_the_object_without_a_gl_name(self) -> None:
        HOST.parent.mkdir(parents=True, exist_ok=True)
        command = [
            "clang", "-arch", "x86_64", "-O2", "-Wall", "-Wextra", "-fobjc-arc", "-lc++",
            "-I", str(ROOT / "benchmark"),
            "-o", str(HOST), str(HOST_SOURCE), str(SHADER),
        ]
        built = subprocess.run(command, capture_output=True, text=True, check=False)
        if built.returncode:
            self.fail(built.stderr.strip() or "shader host failed to build")
        ran = subprocess.run([str(HOST)], capture_output=True, text=True, check=False)
        if ran.returncode:
            self.fail(ran.stderr.strip() or ran.stdout.strip() or "shader host failed")


if __name__ == "__main__":
    unittest.main()
