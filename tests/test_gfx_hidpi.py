import os
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "benchmark" / "eu4_gfx_init.mm"
FRAME_OBSERVE = ROOT / "benchmark" / "eu4_gfx_frame_observe.c"
BUFFERS = ROOT / "benchmark" / "eu4_gfx_buffers.mm"
HOST_SOURCE = ROOT / "tests" / "gfx_hidpi_host.mm"
GEOMETRY_VIEW = ROOT / "benchmark" / "eu4_metal_geometry_view.m"
EVENTS = ROOT / "benchmark" / "eu4_metal_events.c"
HOST = ROOT / "benchmark" / ".build" / "gfx_hidpi_host"

BUILD = [
    "clang", "-arch", "x86_64", "-O2", "-Wall", "-Wextra", "-fobjc-arc", "-lc++",
    "-framework", "AppKit", "-framework", "Foundation", "-framework", "Metal",
    "-framework", "QuartzCore",
    "-o", str(HOST), str(HOST_SOURCE), str(SOURCE), str(FRAME_OBSERVE), str(BUFFERS), str(GEOMETRY_VIEW),
    str(EVENTS),
]


def build_host() -> None:
    HOST.parent.mkdir(parents=True, exist_ok=True)
    built = subprocess.run(BUILD, capture_output=True, text=True, check=False)
    if built.returncode:
        raise AssertionError(built.stderr.strip() or "gfx hidpi host failed to build")


def run_host(*args: str) -> subprocess.CompletedProcess:
    env = os.environ.copy()
    env["MTL_DEBUG_LAYER"] = "1"
    env["MTL_DEBUG_LAYER_ERROR_MODE"] = "assert"
    return subprocess.run([str(HOST), *args], capture_output=True, text=True, check=False,
                          env=env)


@unittest.skipUnless(sys.platform == "darwin", "Metal HiDPI host runs on macOS")
class GfxHiDPITests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        build_host()

    def test_geometry_lifecycle(self):
        ran = run_host()
        if ran.returncode:
            self.fail(ran.stderr.strip() or ran.stdout.strip() or "gfx hidpi host failed")

    def test_ordinary_pending_second_clear_is_fatal(self):
        ran = run_host("pending-double-clear")
        self.assertEqual(ran.returncode, 2,
                         ran.stderr.strip() or "ordinary pending clear was not fatal")


if __name__ == "__main__":
    unittest.main()
