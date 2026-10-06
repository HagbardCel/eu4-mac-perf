#!/usr/bin/env python3
import platform
import re
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BENCH = ROOT / "benchmark"
sys.path.insert(0, str(BENCH))


class BorderStartupInvariantTests(unittest.TestCase):
    def test_source_startup_contract(self) -> None:
        import submission_control as control

        self.assertNotIn(
            BENCH / "eu4_submission_border_gl_interpose.c",
            control.SUBMISSION_DYLIB_SOURCES_BORDER,
        )
        loop_head = (BENCH / "eu4_submission_border_loop_head.c").read_text(encoding="utf-8")
        gate0 = re.search(r"static void record_gate0_once\(void\) \{.*?\n\}", loop_head, re.S)
        self.assertIsNotNone(gate0)
        self.assertNotIn("eu4_border_init_gl_apis", gate0.group(0))
        self.assertIn('dlsym(RTLD_NEXT, "glMultiDrawElements")', gate0.group(0))
        install = (BENCH / "eu4_submission_border_install.c").read_text(encoding="utf-8")
        self.assertNotIn("eu4_border_init_gl_apis", install)

    def test_library_border_mach_o_invariants(self) -> None:
        if platform.system() != "Darwin":
            self.skipTest("requires macOS")
        import submission_control as control

        control.build_border_multidraw()
        dylib = control.LIBRARY_BORDER
        nm = subprocess.run(
            ["nm", "-u", str(dylib)],
            capture_output=True,
            text=True,
            check=True,
        )
        self.assertNotIn("_glDrawElementsBaseVertex", nm.stdout)
        self.assertIn("_CGLFlushDrawable", nm.stdout)
        otool = subprocess.run(
            ["otool", "-l", str(dylib)],
            capture_output=True,
            text=True,
            check=True,
        )
        interpose_size = None
        lines = otool.stdout.splitlines()
        for index, line in enumerate(lines):
            if "sectname __interpose" not in line:
                continue
            for follow in lines[index + 1 : index + 8]:
                stripped = follow.strip()
                if stripped.startswith("size"):
                    interpose_size = int(stripped.split()[1], 16)
                    break
            break
        self.assertEqual(interpose_size, 0x10, msg="LIBRARY_BORDER must have exactly one interpose pair")


if __name__ == "__main__":
    unittest.main()
