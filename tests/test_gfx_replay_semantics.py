"""Native clear-flag semantics for the Metal replay host."""
from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


@unittest.skipUnless(sys.platform == "darwin", "native C host build")
class ReplaySemanticsTests(unittest.TestCase):
    def test_clear_flags_are_not_gl_bitfields(self):
        with tempfile.TemporaryDirectory() as temporary:
            binary = Path(temporary) / "semantics"
            built = subprocess.run(
                ["clang", "-O1", "-Wall", "-Wextra", "-I", str(ROOT / "benchmark"),
                 "-o", str(binary), str(ROOT / "tests" / "gfx_replay_semantics_host.c")],
                capture_output=True, text=True, check=False)
            self.assertEqual(built.returncode, 0, built.stderr)
            ran = subprocess.run([str(binary)], capture_output=True, text=True,
                                 check=False)
            self.assertEqual(ran.returncode, 0, ran.stderr)
            self.assertEqual(ran.stdout.strip(), "ok")


if __name__ == "__main__":
    unittest.main()
