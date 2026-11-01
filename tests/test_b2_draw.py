import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "benchmark" / "eu4_b2_draw.mm"
DEPTH = ROOT / "benchmark" / "eu4_gfx_init.mm"
HOST_SOURCE = ROOT / "tests" / "b2_draw_host.mm"
HOST = ROOT / "benchmark" / ".build" / "b2_draw_host"
FIXTURE = ROOT / "fixtures" / "b2"


@unittest.skipUnless(sys.platform == "darwin", "Metal draw runs on macOS")
class B2DrawTests(unittest.TestCase):
    def test_fixture_matches_metal_when_present(self):
        HOST.parent.mkdir(parents=True, exist_ok=True)
        command = [
            "clang", "-arch", "x86_64", "-O2", "-Wall", "-Wextra", "-fobjc-arc", "-lc++",
            "-framework", "Foundation", "-framework", "Metal",
            "-o", str(HOST), str(HOST_SOURCE), str(SOURCE), str(DEPTH),
        ]
        built = subprocess.run(command, capture_output=True, text=True, check=False)
        if built.returncode:
            self.fail(built.stderr.strip() or "b2 draw host failed to build")
        if not (FIXTURE / "fixture.json").is_file():
            self.skipTest("operator-local B2 fixture is absent; public CI does not validate pixels")
        ran = subprocess.run([str(HOST), str(FIXTURE)], capture_output=True, text=True, check=False)
        if ran.returncode:
            self.fail(ran.stderr.strip() or ran.stdout.strip() or "b2 draw host failed")
        self.assertRegex(ran.stdout.strip(), r"^[0-9]+$")


if __name__ == "__main__":
    unittest.main()
