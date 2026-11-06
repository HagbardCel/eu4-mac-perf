import os
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "benchmark" / "eu4_gfx_init.mm"
BUFFERS = ROOT / "benchmark" / "eu4_gfx_buffers.mm"
HOST_SOURCE = ROOT / "tests" / "gfx_default_target_host.mm"
EVENTS = ROOT / "benchmark" / "eu4_metal_events.c"
HOST = ROOT / "benchmark" / ".build" / "gfx_default_target_host"


@unittest.skipUnless(sys.platform == "darwin", "Metal default-target host runs on macOS")
class GfxDefaultTargetTests(unittest.TestCase):
    def test_default_target_lifecycle(self):
        HOST.parent.mkdir(parents=True, exist_ok=True)
        command = [
            "clang", "-arch", "x86_64", "-O2", "-Wall", "-Wextra", "-fobjc-arc", "-lc++",
            "-framework", "Foundation", "-framework", "Metal", "-framework", "QuartzCore",
            "-o", str(HOST), str(HOST_SOURCE), str(SOURCE), str(BUFFERS), str(EVENTS),
        ]
        built = subprocess.run(command, capture_output=True, text=True, check=False)
        if built.returncode:
            self.fail(built.stderr.strip() or "gfx default target host failed to build")
        env = os.environ.copy()
        env["MTL_DEBUG_LAYER"] = "1"
        env["MTL_DEBUG_LAYER_ERROR_MODE"] = "assert"
        ran = subprocess.run([str(HOST)], capture_output=True, text=True, check=False, env=env)
        if ran.returncode:
            self.fail(ran.stderr.strip() or ran.stdout.strip() or "gfx default target host failed")


if __name__ == "__main__":
    unittest.main()
