import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CAPTURE = ROOT / "analysis" / "metal-draw" / "b2" / "capture.m"
SOURCE = ROOT / "benchmark" / "eu4_b2_draw.mm"
DEPTH = ROOT / "benchmark" / "eu4_gfx_init.mm"
HOST_SOURCE = ROOT / "tests" / "b2_draw_host.mm"
ABI_SOURCE = ROOT / "tests" / "b2_abi_host.c"
DRIVER = ROOT / "tests" / "b2_smoke_driver.c"
BUILD = ROOT / "benchmark" / ".build"
HOST = BUILD / "b2_draw_host"
ABI = BUILD / "b2_abi_host"
LIBRARY = BUILD / "libeu4_b2_capture.dylib"
SMOKE = BUILD / "b2_smoke"
FIXTURE = ROOT / "fixtures" / "b2"

VERT_METAL = """
#include <metal_stdlib>
using namespace metal;
struct In { float2 pos [[attribute(3)]]; };
struct Out { float4 position [[position]]; };
vertex Out main0(In in [[stage_in]]) {
    Out out;
    out.position = float4(in.pos, 0.0, 1.0);
    return out;
}
"""

FRAG_METAL = """
#include <metal_stdlib>
using namespace metal;
struct In { float4 position [[position]]; };
fragment float4 main0(In in [[stage_in]]) {
    return float4(1.0, 0.0, 0.0, 1.0);
}
"""


def run(command):
    return subprocess.run(command, capture_output=True, text=True, check=False)


def build_host():
    BUILD.mkdir(parents=True, exist_ok=True)
    return run([
        "clang", "-arch", "x86_64", "-O2", "-Wall", "-Wextra", "-fobjc-arc", "-lc++",
        "-framework", "Foundation", "-framework", "Metal",
        "-o", str(HOST), str(HOST_SOURCE), str(SOURCE), str(DEPTH),
    ])


@unittest.skipUnless(sys.platform == "darwin", "Metal draw runs on macOS")
class B2DrawTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        built = build_host()
        if built.returncode:
            raise RuntimeError(built.stderr.strip() or "b2 draw host failed to build")

    def test_abi_preserves_arguments_and_return(self):
        BUILD.mkdir(parents=True, exist_ok=True)
        built = run([
            "clang", "-arch", "x86_64", "-O2", "-Wall", "-Wextra", "-fobjc-arc",
            "-framework", "Foundation", "-framework", "OpenGL",
            "-o", str(ABI), str(ABI_SOURCE), str(CAPTURE),
        ])
        if built.returncode:
            self.fail(built.stderr.strip() or "abi host failed to build")
        ran = run([str(ABI)])
        if ran.returncode:
            self.fail(ran.stderr.strip() or f"abi check failed: {ran.returncode}")

    def test_synthetic_metal_matches_gl_reference(self):
        BUILD.mkdir(parents=True, exist_ok=True)
        library = run([
            "clang", "-arch", "x86_64", "-O2", "-Wall", "-Wextra", "-fobjc-arc", "-dynamiclib",
            "-framework", "Foundation", "-framework", "OpenGL",
            "-o", str(LIBRARY), str(CAPTURE),
        ])
        if library.returncode:
            self.fail(library.stderr.strip() or "capture dylib failed to build")
        smoke = run([
            "clang", "-arch", "x86_64", "-O2", "-Wall", "-Wextra",
            "-framework", "OpenGL",
            "-o", str(SMOKE), str(DRIVER), str(LIBRARY),
        ])
        if smoke.returncode:
            self.fail(smoke.stderr.strip() or "smoke driver failed to build")
        with tempfile.TemporaryDirectory() as directory:
            ran = run([str(SMOKE), directory])
            if ran.returncode:
                self.fail(ran.stderr.strip() or f"gl smoke failed: {ran.returncode}")
            (Path(directory) / "draw.vert.metal").write_text(VERT_METAL)
            (Path(directory) / "draw.frag.metal").write_text(FRAG_METAL)
            compared = run([str(HOST), directory])
            if compared.returncode:
                self.fail(compared.stderr.strip() or compared.stdout.strip() or "metal compare failed")
            self.assertEqual(compared.stdout.strip(), "0")

    def test_operator_fixture_matches_when_present(self):
        if not (FIXTURE / "fixture.json").is_file():
            self.skipTest("operator-local B2 fixture is absent; public CI does not validate pixels")
        if not HOST.is_file():
            self.fail("b2 draw host is missing")
        ran = run([str(HOST), str(FIXTURE)])
        if ran.returncode:
            self.fail(ran.stderr.strip() or ran.stdout.strip() or "b2 draw host failed")
        self.assertRegex(ran.stdout.strip(), r"^[0-9]+$")


if __name__ == "__main__":
    unittest.main()
