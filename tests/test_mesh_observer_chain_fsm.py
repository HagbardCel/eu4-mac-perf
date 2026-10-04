import platform
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BENCH = ROOT / "benchmark"
TESTS = ROOT / "tests"


class MeshObserverChainFsmTests(unittest.TestCase):
    def test_chain_semantics_native(self):
        out = BENCH / ".build" / "mesh_observer_chain_fsm_test"
        cmd = ["clang"]
        if platform.system() == "Darwin":
            cmd += ["-arch", "x86_64"]
        out.parent.mkdir(parents=True, exist_ok=True)
        cmd += [
            "-O2",
            "-Wall",
            "-Wextra",
            "-Werror",
            "-I",
            str(BENCH),
            "-o",
            str(out),
            str(BENCH / "eu4_submission_mesh_chain.c"),
            str(BENCH / "subrecord_equivalence.c"),
            str(TESTS / "mesh_observer_chain_counter_stubs.c"),
            str(TESTS / "mesh_observer_chain_fsm_test.c"),
        ]
        subprocess.run(cmd, check=True)
        runner = [str(out)]
        if platform.system() == "Darwin" and platform.machine() != "x86_64":
            runner = ["arch", "-x86_64", str(out)]
        result = subprocess.run(runner, capture_output=True, text=True, check=False)
        self.assertEqual(result.returncode, 0, msg=result.stderr or result.stdout)


if __name__ == "__main__":
    unittest.main()
