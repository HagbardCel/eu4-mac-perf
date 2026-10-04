import platform
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BENCH = ROOT / "benchmark"


@unittest.skipUnless(platform.system() == "Darwin" and platform.machine() == "x86_64", "x86_64 macOS gateway test")
class MeshObserverGatewayTests(unittest.TestCase):
    def test_gateway_save_restore_selftest(self):
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
                "-I",
                str(BENCH),
                "-o",
                str(out),
                str(BENCH / "eu4_submission_mesh_thunk.S"),
                str(BENCH / "eu4_submission_mesh_site.c"),
                str(BENCH / "subrecord_equivalence.c"),
                str(ROOT / "tests/mesh_observer_gateway_selftest.c"),
            ],
            check=True,
        )
        result = subprocess.run([str(out)], capture_output=True, text=True, check=False)
        self.assertEqual(result.returncode, 0, msg=result.stderr or result.stdout)


if __name__ == "__main__":
    unittest.main()
