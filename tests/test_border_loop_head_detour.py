#!/usr/bin/env python3
import platform
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BENCH = ROOT / "benchmark"
TESTS = ROOT / "tests"


class BorderLoopHeadDetourTests(unittest.TestCase):
    def test_gateway_preserves_r11(self) -> None:
        if platform.system() != "Darwin":
            self.skipTest("requires macOS")
        if platform.machine() != "x86_64":
            self.skipTest("runtime detour harness requires native x86_64")
        out = BENCH / ".build" / "border_loop_head_detour_harness"
        cmd = [
            "clang",
            "-arch",
            "x86_64",
            "-O2",
            "-Wall",
            "-Wextra",
            "-Werror",
            "-mno-avx",
            "-I",
            str(BENCH),
            "-o",
            str(out),
            str(BENCH / "eu4_submission_border_loop_head_gateway.S"),
            str(TESTS / "border_loop_head_detour_harness.c"),
        ]
        subprocess.run(cmd, check=True)
        runner = [str(out)]
        if platform.machine() != "x86_64":
            runner = ["arch", "-x86_64", *runner]
        result = subprocess.run(runner, capture_output=True, text=True, check=False)
        self.assertEqual(result.returncode, 0, msg=result.stderr or result.stdout)

    def test_gateway_asm_preserves_r11_push_pop(self) -> None:
        text = (BENCH / "eu4_submission_border_loop_head_gateway.S").read_text()
        self.assertIn("pushq %r11", text)
        self.assertIn("popq %r11", text)


if __name__ == "__main__":
    unittest.main()
