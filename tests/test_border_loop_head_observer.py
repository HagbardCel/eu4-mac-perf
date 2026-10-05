#!/usr/bin/env python3
import platform
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BENCH = ROOT / "benchmark"
TESTS = ROOT / "tests"


class BorderLoopHeadObserverTests(unittest.TestCase):
    def test_epoch_gating_native(self) -> None:
        if platform.system() != "Darwin":
            self.skipTest("requires macOS")
        out = BENCH / ".build" / "border_loop_head_observer_test"
        cmd = [
            "clang",
            "-arch",
            "x86_64",
            "-O2",
            "-Wall",
            "-Wextra",
            "-Werror",
            "-mno-avx",
            "-framework",
            "OpenGL",
            "-I",
            str(BENCH),
            "-o",
            str(out),
            str(BENCH / "border_loop_classifier.c"),
            str(BENCH / "eu4_border_loop_head_decode.c"),
            str(BENCH / "eu4_submission_border_loop_head.c"),
            str(TESTS / "border_loop_head_observer_test.c"),
        ]
        subprocess.run(cmd, check=True)
        runner = [str(out)]
        if platform.machine() != "x86_64":
            runner = ["arch", "-x86_64", *runner]
        result = subprocess.run(runner, capture_output=True, text=True, check=False)
        self.assertEqual(result.returncode, 0, msg=result.stderr or result.stdout)


if __name__ == "__main__":
    unittest.main()
