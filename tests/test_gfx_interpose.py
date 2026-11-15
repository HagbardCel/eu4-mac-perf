"""Native interposer resolution and forwarding regressions."""
from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


@unittest.skipUnless(sys.platform == "darwin", "the interposer host is macOS")
class InterposeHostTests(unittest.TestCase):
    def test_resolution_self_rejection_and_forwarding(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            binary = directory / "gfx_interpose_host"
            built = subprocess.run(
                [
                    "clang", "-O1", "-Wall", "-Wextra",
                    "-DEU4_GFX_INTERPOSE_TEST",
                    "-I", str(ROOT / "benchmark"),
                    "-o", str(binary),
                    str(ROOT / "tests" / "gfx_interpose_host.c"),
                    str(ROOT / "benchmark" / "eu4_gfx_gl_interpose.c"),
                    "-framework", "OpenGL",
                ],
                capture_output=True, text=True, check=False,
            )
            self.assertEqual(built.returncode, 0, built.stderr)
            marker_dir = directory / "marker"
            marker_dir.mkdir()
            ran = subprocess.run(
                [str(binary), str(marker_dir)],
                capture_output=True, text=True, check=False,
            )
            marker = marker_dir / "interpose-error.jsonl"
            self.assertTrue(marker.is_file())
            self.assertIn("interpose_fatal", marker.read_text())
            self.assertIn("glCopyTexSubImage2D", marker.read_text())
        self.assertEqual(ran.returncode, 0, ran.stderr)
        self.assertIn("interpose_ok=1", ran.stdout)
        self.assertIn("calls=1", ran.stdout)


if __name__ == "__main__":
    unittest.main()
