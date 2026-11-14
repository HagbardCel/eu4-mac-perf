import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HOST_SOURCE = ROOT / "tests" / "gfx_draw_capture_host.c"
CAPTURE = ROOT / "benchmark" / "eu4_gfx_draw_capture.c"
EVENTS = ROOT / "benchmark" / "eu4_metal_events.c"
INIT = ROOT / "benchmark" / "eu4_gfx_init.mm"
FRAME_OBSERVE = ROOT / "benchmark" / "eu4_gfx_frame_observe.c"
BUFFERS = ROOT / "benchmark" / "eu4_gfx_buffers.mm"
HOST = ROOT / "benchmark" / ".build" / "gfx_draw_capture_host"
SHIM_HOST = ROOT / "benchmark" / ".build" / "gfx_draw_shim_host"
SHIM_ASM = ROOT / "tests" / "gfx_draw_shim_host.S"
SHIM_STUB = ROOT / "tests" / "gfx_draw_shim_stub.c"
ENTRY = ROOT / "benchmark" / "eu4_gfx_draw_entry.S"


@unittest.skipUnless(sys.platform == "darwin", "draw capture runs on macOS")
class GfxDrawCaptureTests(unittest.TestCase):
    def test_provenance_and_partial_read(self) -> None:
        HOST.parent.mkdir(parents=True, exist_ok=True)
        command = [
            "clang", "-arch", "x86_64", "-O2", "-Wall", "-Wextra", "-fobjc-arc", "-lc++",
            "-I", str(ROOT / "benchmark"),
            "-framework", "Foundation", "-framework", "Metal", "-framework", "QuartzCore",
            "-o", str(HOST), str(HOST_SOURCE), str(CAPTURE), str(EVENTS), str(INIT), str(FRAME_OBSERVE), str(BUFFERS),
        ]
        built = subprocess.run(command, capture_output=True, text=True, check=False)
        if built.returncode:
            self.fail(built.stderr.strip() or "draw capture host failed to build")
        ran = subprocess.run([str(HOST)], capture_output=True, text=True, check=False)
        if ran.returncode:
            self.fail(ran.stderr.strip() or ran.stdout.strip() or "draw capture host failed")

    def test_assembly_shim_snapshot(self) -> None:
        SHIM_HOST.parent.mkdir(parents=True, exist_ok=True)
        command = [
            "clang", "-arch", "x86_64", "-O2", "-Wall", "-Wextra",
            "-I", str(ROOT / "benchmark"),
            "-o", str(SHIM_HOST), str(SHIM_ASM), str(SHIM_STUB), str(ENTRY),
        ]
        built = subprocess.run(command, capture_output=True, text=True, check=False)
        if built.returncode:
            self.fail(built.stderr.strip() or "draw shim host failed to build")
        ran = subprocess.run([str(SHIM_HOST)], capture_output=True, text=True, check=False)
        if ran.returncode:
            self.fail(ran.stderr.strip() or ran.stdout.strip() or "draw shim host failed")


if __name__ == "__main__":
    unittest.main()
