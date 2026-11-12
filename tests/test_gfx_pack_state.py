#!/usr/bin/env python3
"""Focused native test for original-GL pixel-pack save/restore."""

from __future__ import annotations

import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HOST = ROOT / "benchmark" / ".build" / "gfx_pack_state_host"


@unittest.skipUnless(sys.platform == "darwin", "CGL pack-state host runs on macOS")
class PackStateTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        HOST.parent.mkdir(parents=True, exist_ok=True)
        command = [
            "clang", "-O2", "-Wall", "-Wextra", "-framework", "OpenGL",
            "-I", str(ROOT / "benchmark"),
            "-o", str(HOST),
            str(ROOT / "tests" / "gfx_pack_state_host.c"),
            str(ROOT / "benchmark" / "eu4_gfx_observe_framebuffer.c"),
        ]
        built = subprocess.run(command, capture_output=True, text=True, check=False)
        if built.returncode:
            raise AssertionError(built.stderr.strip() or "pack-state host failed to build")

    def test_pack_state_roundtrip_failure_and_buffers(self) -> None:
        ran = subprocess.run([str(HOST)], capture_output=True, text=True, check=False)
        self.assertEqual(ran.returncode, 0, ran.stderr or ran.stdout)
        self.assertIn("roundtrip=0 restored=1", ran.stdout)
        self.assertIn("failure=0 failure_restored=1", ran.stdout)
        self.assertIn(
            "default_ok=1 draw_front=1 separate_fbo=1 mismatch=1 read_front=1",
            ran.stdout,
        )


if __name__ == "__main__":
    unittest.main()
