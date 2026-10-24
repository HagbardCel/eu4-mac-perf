#!/usr/bin/env python3
"""P4.10 B: native register-neutral capture gateway (offline, macOS/x86_64)."""
from __future__ import annotations

import platform
import subprocess
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BENCH = ROOT / "benchmark"
TESTS = ROOT / "tests"


class GfxCaptureNativeTests(unittest.TestCase):
    def _clang(self, out: Path, *sources: str) -> None:
        out.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(
            [
                "clang", "-arch", "x86_64", "-O2", "-Wall", "-Wextra",
                "-Werror", "-mno-avx", "-I", str(BENCH), "-I", str(TESTS),
                "-o", str(out), *sources,
            ],
            check=True,
        )

    def _run(self, out: Path) -> subprocess.CompletedProcess[str]:
        runner = [str(out)]
        if platform.machine() != "x86_64":
            runner = ["arch", "-x86_64", *runner]
        return subprocess.run(runner, capture_output=True, text=True, check=False)

    def test_gateway_and_installer_runtime(self) -> None:
        if platform.system() != "Darwin":
            self.skipTest("requires macOS")
        out = BENCH / ".build" / "gfx_capture_native_harness"
        self._clang(
            out,
            str(BENCH / "eu4_gfx_capture_detour.c"),
            str(BENCH / "eu4_gfx_capture_gateway.S"),
            str(TESTS / "gfx_capture_native_leaf.S"),
            str(TESTS / "gfx_capture_native_adapter_stub.S"),
            str(TESTS / "gfx_capture_observation.S"),
            str(TESTS / "gfx_capture_native_harness.c"),
        )
        result = self._run(out)
        self.assertEqual(result.returncode, 0,
                         msg=result.stderr or result.stdout)
        self.assertIn("gfx capture native harness: ok", result.stdout)

    def test_source_encoding_has_no_register_clobber_jump(self) -> None:
        installer = (BENCH / "eu4_gfx_capture_detour.c").read_text()
        gateway = (BENCH / "eu4_gfx_capture_gateway.S").read_text()
        self.assertIn("eu4_gfx_capture_rel32_fits", installer)
        self.assertIn("0xe9", installer)
        self.assertIn("memset(patch + 5, 0x90, 8)", installer)
        self.assertIn("p[0] = 0xff", installer)
        self.assertIn("p[1] = 0x25", installer)
        self.assertNotIn("0x49", installer)
        self.assertNotIn("0xbb", installer)
        self.assertIn("movq %r11, 592(%rsp)", gateway)
        self.assertIn("movq 592(%rsp), %r11", gateway)
        self.assertIn("_eu4_gfx_capture_trampoline_ptr", gateway)
        self.assertIn("fxsave64", gateway)
        self.assertIn("fxrstor64", gateway)


if __name__ == "__main__":
    unittest.main()
