import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "benchmark" / "eu4_gfx_init.mm"
HOST_SOURCE = ROOT / "tests" / "gfx_init_host.mm"
HOST = ROOT / "benchmark" / ".build" / "gfx_init_host"


@unittest.skipUnless(sys.platform == "darwin", "Metal depth-stencil slice runs on macOS")
class GfxInitTests(unittest.TestCase):
    def test_default_depth_stencil_contract(self):
        HOST.parent.mkdir(parents=True, exist_ok=True)
        command = [
            "clang", "-arch", "x86_64", "-O2", "-Wall", "-Wextra", "-fobjc-arc", "-lc++",
            "-framework", "Foundation", "-framework", "Metal", "-framework", "CoreFoundation",
            "-o", str(HOST), str(HOST_SOURCE), str(SOURCE),
        ]
        built = subprocess.run(command, capture_output=True, text=True, check=False)
        if built.returncode:
            self.fail(built.stderr.strip() or "gfx init host failed to build")
        ran = subprocess.run([str(HOST)], capture_output=True, text=True, check=False)
        if ran.returncode:
            self.fail(ran.stderr.strip() or ran.stdout.strip() or "gfx init host failed")


if __name__ == "__main__":
    unittest.main()
