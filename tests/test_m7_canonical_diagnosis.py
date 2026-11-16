"""M7 PR3 canonical replay diagnosis.

macOS-only: replays the existing canonical title fixture and the copy-bearing
scene-75 fixture through the production Metal host, then diagnoses the produced
results without a second replay.  The tests skip when the local evidence is not
available (for example on the portable Linux CI).
"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from automation import gfx_independent_replay  # noqa: E402


def _candidate_roots() -> list[Path]:
    roots = [ROOT, ROOT.parent / "eu4-mac-perf", ROOT.parent / "wt_infra"]
    roots.extend(Path(item) for item in
                 os.environ.get("EU4_EVIDENCE_ROOT", "").split(os.pathsep)
                 if item)
    return roots


def _frame(name: str, capture: str, shaders: str):
    for root in _candidate_roots():
        base = root / ".local" / name
        frame = base / capture
        shader_dir = base / shaders
        if (frame / "frame-trace.json").is_file() and (
                shader_dir / "shaders.json").is_file():
            return frame, shader_dir
    return None, None


@unittest.skipUnless(sys.platform == "darwin",
                     "the Metal replay core runs on macOS")
class CanonicalDiagnosisTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        cls.base = Path(cls.temporary.name)

    @classmethod
    def tearDownClass(cls):
        cls.temporary.cleanup()

    def _replay(self, frame, shaders, name):
        out = self.base / name
        return gfx_independent_replay.replay(frame, out, shaders=shaders)

    def test_canonical_title_frame_has_no_divergence(self):
        frame, shaders = _frame(
            "m6/frames/menu-occ300", "frame", "translated")
        if frame is None:
            self.skipTest("canonical title fixture is not available")
        report = self._replay(frame, shaders, "canonical")
        diagnosis = report["diagnosis"]
        self.assertEqual(diagnosis["execution"]["status"], "ok")
        self.assertEqual(diagnosis["operation_coverage"]["status"], "complete")
        self.assertEqual(diagnosis["pixels"]["status"], "match")
        self.assertEqual(
            diagnosis["pixels"]["statistics"]["mismatched_pixels"], 0)
        self.assertEqual(diagnosis["first_divergence"]["kind"], "none")
        self.assertEqual(diagnosis["checkpoint_validation"]["status"], "ok")
        self.assertEqual(diagnosis["checkpoint_validation"]["requested"],
                         diagnosis["checkpoint_validation"]["returned"])
        # The historical capture is known-partial: texture 442 has no captured
        # producer.  A pixel match must not be promoted to a capture claim.
        if diagnosis["capture"]["validity"] != "valid":
            self.assertEqual(diagnosis["classification"],
                             "incomplete_capture")
            self.assertTrue(any("442" in reason for reason in
                                diagnosis["capture"]["graph_reasons"]))
        self.assertIn("gl_dither",
                      diagnosis["state_coverage"]["unsupported"])
        self.assertIn("multisample",
                      diagnosis["state_coverage"]["unsupported"])

    def test_scene75_copy_source_remains_the_first_divergence(self):
        frame, shaders = _frame(
            "infra/strategy-b/copy-source-442", "frame", "shaders")
        if frame is None:
            self.skipTest("scene-75 copy-bearing fixture is not available")
        report = self._replay(frame, shaders, "scene75")
        diagnosis = report["diagnosis"]
        self.assertEqual(diagnosis["classification"],
                         "difference_with_partial_state_coverage")
        self.assertEqual(diagnosis["execution"]["status"], "ok")
        self.assertEqual(diagnosis["operation_coverage"]["status"], "complete")
        self.assertEqual(diagnosis["provenance"]["status"], "verified")
        self.assertEqual(diagnosis["checkpoint_validation"]["status"], "ok")
        divergence = diagnosis["first_divergence"]
        self.assertEqual(divergence["kind"], "intermediate")
        self.assertEqual(divergence["event"], 271)
        self.assertEqual(divergence["target"], "0")
        self.assertEqual(divergence["resource"], "fb:0")
        self.assertEqual(divergence["producer_event"], 270)
        self.assertEqual(divergence["producer_program"], 346)
        self.assertEqual(divergence["operation"], "glCopyTexSubImage2D")
        statistics = diagnosis["pixels"]["statistics"]
        self.assertEqual(statistics["mismatched_pixels"], 1792005)
        self.assertEqual(statistics["max_channel_error"], 23)
        self.assertEqual(diagnosis["state_coverage"]["unsupported"],
                         ["gl_dither", "multisample"])
        self.assertFalse(diagnosis["qualification"]["renderer_equivalence"])
        self.assertEqual(divergence["root_cause"], "not_established")


if __name__ == "__main__":
    unittest.main()
