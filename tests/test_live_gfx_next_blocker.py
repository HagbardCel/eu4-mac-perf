#!/usr/bin/env python3
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "benchmark"))

from automation.next_blocker import classify  # noqa: E402

GL_RUN = ROOT / "results" / "20261102T120220Z-metal-init"
DRAW_RUN = ROOT / "results" / "20261102T213704Z-metal-init"


class NextBlockerTests(unittest.TestCase):
    @unittest.skipUnless(GL_RUN.is_dir(), "recorded glGenTextures session is absent")
    def test_recorded_gl_stop_is_actionable(self):
        result = classify(GL_RUN)
        self.assertEqual(result["status"], "actionable")
        self.assertEqual(result["kind"], "unsupported_gl_dependency")
        self.assertEqual(result["symbol"], "glGenTextures")
        self.assertEqual(result["caller_rva"], "0x15ec4ed")
        self.assertEqual(result["owner"]["name"], "initialize_texture")
        self.assertEqual(result["next_step"], "reconstruct")
        self.assertTrue(result["evidence"]["stop.json"].endswith("stop.json"))

    @unittest.skipUnless(DRAW_RUN.is_dir(), "recorded draw session is absent")
    def test_recorded_draw_reached_is_evidence(self):
        result = classify(DRAW_RUN)
        self.assertEqual(result["status"], "needs_evidence")
        self.assertEqual(result["kind"], "draw_nonvisible")
        self.assertEqual(result["symbol"], "_GfxDraw")
        self.assertEqual(result["next_step"], "investigate")

    def test_missing_run_is_unavailable(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = classify(Path(tmp) / "absent")
            self.assertEqual(result["status"], "unavailable")
            self.assertEqual(result["kind"], "missing_run")
            self.assertNotIn("owner", result)

    def test_incomplete_run_does_not_guess(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "metadata.json").write_text("{}\n")
            result = classify(root)
            self.assertEqual(result["status"], "unavailable")
            self.assertNotIn("owner", result)

    @unittest.skipUnless(GL_RUN.is_dir(), "recorded glGenTextures session is absent")
    def test_crash_is_not_attributed_to_a_function(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "crash"
            shutil.copytree(GL_RUN, root)
            metadata = json.loads((root / "metadata.json").read_text())
            metadata["exit_code"] = -11
            metadata["launcher_signaled"] = False
            (root / "metadata.json").write_text(json.dumps(metadata) + "\n")
            result = classify(root)
            self.assertEqual(result["status"], "needs_evidence")
            self.assertEqual(result["kind"], "attribution_ambiguous")
            self.assertNotIn("owner", result)


if __name__ == "__main__":
    unittest.main()
