import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HOST_SOURCE = ROOT / "tests" / "gfx_buffers_host.mm"
INIT = ROOT / "benchmark" / "eu4_gfx_init.mm"
BUFFERS = ROOT / "benchmark" / "eu4_gfx_buffers.mm"
EVENTS = ROOT / "benchmark" / "eu4_metal_events.c"
HOST = ROOT / "benchmark" / ".build" / "gfx_buffers_host"


@unittest.skipUnless(sys.platform == "darwin", "Metal vertex-buffer host runs on macOS")
class GfxBufferTests(unittest.TestCase):
    def test_vertex_buffer_lifecycle(self):
        HOST.parent.mkdir(parents=True, exist_ok=True)
        command = [
            "clang", "-arch", "x86_64", "-O2", "-Wall", "-Wextra", "-fobjc-arc", "-lc++",
            "-framework", "Foundation", "-framework", "Metal", "-framework", "QuartzCore",
            "-o", str(HOST), str(HOST_SOURCE), str(INIT), str(BUFFERS), str(EVENTS),
        ]
        built = subprocess.run(command, capture_output=True, text=True, check=False)
        if built.returncode:
            self.fail(built.stderr.strip() or "vertex buffer host failed to build")
        ran = subprocess.run([str(HOST)], capture_output=True, text=True, check=False)
        if ran.returncode:
            self.fail(ran.stderr.strip() or ran.stdout.strip() or "vertex buffer host failed")


if __name__ == "__main__":
    unittest.main()
