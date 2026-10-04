import platform
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BENCH = ROOT / "benchmark"
TESTS = ROOT / "tests"


def _darwin_rosetta_x86_64_available() -> bool:
    if platform.system() != "Darwin":
        return False
    try:
        subprocess.run(
            ["arch", "-x86_64", "/usr/bin/true"],
            check=True,
            capture_output=True,
        )
        return True
    except (OSError, subprocess.CalledProcessError):
        return False


@unittest.skipUnless(_darwin_rosetta_x86_64_available(), "macOS x86_64 execution (Rosetta) required")
class MeshObserverGatewayTests(unittest.TestCase):
    def test_gateway_displaced_instruction_transparency(self):
        out = BENCH / ".build" / "mesh_observer_gateway_selftest"
        subprocess.run(
            [
                "clang",
                "-arch",
                "x86_64",
                "-O2",
                "-Wall",
                "-Wextra",
                "-Werror",
                "-o",
                str(out),
                str(BENCH / "eu4_submission_mesh_thunk.S"),
                str(TESTS / "mesh_observer_gateway_selftest.S"),
                str(TESTS / "mesh_observer_gateway_stubs.S"),
                str(TESTS / "mesh_observer_gateway_selftest.c"),
            ],
            check=True,
        )
        runner = [str(out)]
        if platform.machine() != "x86_64":
            runner = ["arch", "-x86_64", str(out)]
        result = subprocess.run(runner, capture_output=True, text=True, check=False)
        self.assertEqual(result.returncode, 0, msg=result.stderr or result.stdout)


if __name__ == "__main__":
    unittest.main()
