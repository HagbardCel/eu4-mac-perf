#!/usr/bin/env python3
import platform
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "benchmark"))


class BorderMultidrawBuildTests(unittest.TestCase):
    def test_build_border_multidraw_x86_64(self) -> None:
        if platform.system() != "Darwin":
            self.skipTest("macOS x86_64 border dylib build is Darwin-only evidence")
        import submission_control as control

        control.build_border_multidraw()


if __name__ == "__main__":
    unittest.main()
