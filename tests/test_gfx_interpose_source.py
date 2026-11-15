"""Native non-default framebuffer source metadata regression."""
from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


@unittest.skipUnless(sys.platform == "darwin", "the source host needs macOS OpenGL")
class InterposeSourceTests(unittest.TestCase):
    def test_selected_attachment_metadata_is_used(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            binary = Path(tmp) / "gfx_interpose_source_host"
            built = subprocess.run(
                [
                    "clang", "-O1", "-Wall", "-Wextra",
                    "-DEU4_GFX_INTERPOSE_TEST",
                    "-I", str(ROOT / "benchmark"),
                    "-o", str(binary),
                    str(ROOT / "tests" / "gfx_interpose_source_host.m"),
                    str(ROOT / "benchmark" / "eu4_gfx_gl_interpose.c"),
                    "-framework", "OpenGL", "-framework", "AppKit",
                ],
                capture_output=True, text=True, check=False,
            )
            self.assertEqual(built.returncode, 0, built.stderr)
            ran = subprocess.run([str(binary)], capture_output=True, text=True,
                                 check=False)
        self.assertEqual(ran.returncode, 0, ran.stderr)
        self.assertIn("source_metadata=1", ran.stdout)
        self.assertIn("source_metadata_unsupported=1", ran.stdout)


if __name__ == "__main__":
    unittest.main()
