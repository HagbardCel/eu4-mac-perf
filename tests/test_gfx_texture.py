import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HOST_SOURCE = ROOT / "tests" / "gfx_texture_host.mm"
TEXTURE = ROOT / "benchmark" / "eu4_gfx_texture.mm"
INIT = ROOT / "benchmark" / "eu4_gfx_init.mm"
FRAME_OBSERVE = ROOT / "benchmark" / "eu4_gfx_frame_observe.c"
BUFFERS = ROOT / "benchmark" / "eu4_gfx_buffers.mm"
EVENTS = ROOT / "benchmark" / "eu4_metal_events.c"
HOST = ROOT / "benchmark" / ".build" / "gfx_texture_host"


@unittest.skipUnless(sys.platform == "darwin", "texture replacement runs on macOS")
class GfxTextureTests(unittest.TestCase):
    def test_fail_closed_and_bgra_upload(self) -> None:
        HOST.parent.mkdir(parents=True, exist_ok=True)
        command = [
            "clang", "-arch", "x86_64", "-O2", "-Wall", "-Wextra", "-fobjc-arc", "-lc++",
            "-I", str(ROOT / "benchmark"),
            "-framework", "Foundation", "-framework", "Metal",
            "-o", str(HOST), str(HOST_SOURCE), str(TEXTURE), str(INIT), str(FRAME_OBSERVE), str(BUFFERS), str(EVENTS),
        ]
        built = subprocess.run(command, capture_output=True, text=True, check=False)
        if built.returncode:
            self.fail(built.stderr.strip() or "texture host failed to build")
        ran = subprocess.run([str(HOST)], capture_output=True, text=True, check=False)
        if ran.returncode:
            self.fail(ran.stderr.strip() or ran.stdout.strip() or "texture host failed")


if __name__ == "__main__":
    unittest.main()
