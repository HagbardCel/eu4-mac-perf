"""Output ownership and overlap regressions for replay/case tooling."""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from automation import gfx_independent_replay, gfx_replay_case  # noqa: E402


class ReplayOutputSafetyTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.frame = self.root / "frame"
        self.shaders = self.frame / "shaders"
        self.shaders.mkdir(parents=True)
        (self.frame / "frame-trace.json").write_text("{}")

    def tearDown(self):
        self.temporary.cleanup()

    def test_fresh_output_is_created(self):
        out = self.root / "out"
        gfx_independent_replay.prepare_replay_output(
            out, [self.frame, self.shaders])
        self.assertTrue(out.is_dir())
        self.assertTrue((out / gfx_independent_replay.OUTPUT_MARKER).is_file())

    def test_owned_output_can_be_replaced(self):
        out = self.root / "out"
        gfx_independent_replay.prepare_replay_output(
            out, [self.frame, self.shaders])
        (out / "old-result").write_text("old")
        gfx_independent_replay.prepare_replay_output(
            out, [self.frame, self.shaders])
        self.assertFalse((out / "old-result").exists())

    def test_legacy_replay_manifest_is_recognized(self):
        out = self.root / "out"
        out.mkdir()
        (out / "replay.json").write_text(json.dumps({
            "schema": "gfx-metal-frame-replay/v1"}))
        (out / "old-result").write_text("old")
        gfx_independent_replay.prepare_replay_output(
            out, [self.frame, self.shaders])
        self.assertFalse((out / "old-result").exists())

    def test_unrelated_nonempty_output_is_refused(self):
        out = self.root / "out"
        out.mkdir()
        marker = out / "keep.txt"
        marker.write_text("keep")
        with self.assertRaises(gfx_independent_replay.ReplayError) as caught:
            gfx_independent_replay.prepare_replay_output(
                out, [self.frame, self.shaders])
        self.assertEqual(caught.exception.kind, "output_not_owned")
        self.assertTrue(marker.is_file())

    def test_repository_root_is_refused(self):
        with self.assertRaises(gfx_independent_replay.ReplayError) as caught:
            gfx_independent_replay.prepare_replay_output(
                ROOT, [self.frame, self.shaders])
        self.assertEqual(caught.exception.kind, "output_protected")

    def test_capture_overlap_is_refused(self):
        out = self.frame / "out"
        with self.assertRaises(gfx_independent_replay.ReplayError) as caught:
            gfx_independent_replay.prepare_replay_output(
                out, [self.frame, self.shaders])
        self.assertEqual(caught.exception.kind, "output_overlap")

    def test_shader_overlap_is_refused(self):
        out = self.shaders / "out"
        with self.assertRaises(gfx_independent_replay.ReplayError) as caught:
            gfx_independent_replay.prepare_replay_output(
                out, [self.frame, self.shaders])
        self.assertEqual(caught.exception.kind, "output_overlap")


class CaseOutputSafetyTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.frame = self.root / "frame"
        self.shaders = self.frame / "shaders"
        self.shaders.mkdir(parents=True)
        self.replay = self.root / "replay"
        self.replay.mkdir()
        (self.replay / "replay-report.json").write_text("{}")

    def tearDown(self):
        self.temporary.cleanup()

    def test_fresh_case_is_created(self):
        case = self.root / "case"
        gfx_replay_case.prepare_case_output(
            case, [self.frame, self.replay, self.shaders])
        self.assertTrue(case.is_dir())

    def test_case_equal_to_replay_input_is_refused(self):
        with self.assertRaises(gfx_replay_case.CaseError) as caught:
            gfx_replay_case.prepare_case_output(
                self.replay, [self.frame, self.replay, self.shaders])
        self.assertEqual(caught.exception.kind, "output_overlap")
        self.assertTrue((self.replay / "replay-report.json").is_file())

    def test_case_containing_shader_input_is_refused(self):
        with self.assertRaises(gfx_replay_case.CaseError) as caught:
            gfx_replay_case.prepare_case_output(
                self.frame, [self.frame, self.replay, self.shaders])
        self.assertEqual(caught.exception.kind, "output_overlap")
        self.assertTrue((self.shaders).is_dir())

    def test_unrelated_nonempty_case_is_refused(self):
        case = self.root / "case"
        case.mkdir()
        keep = case / "keep.txt"
        keep.write_text("keep")
        with self.assertRaises(gfx_replay_case.CaseError) as caught:
            gfx_replay_case.prepare_case_output(
                case, [self.frame, self.replay, self.shaders])
        self.assertEqual(caught.exception.kind, "output_not_owned")
        self.assertTrue(keep.is_file())

    def test_prior_portable_case_is_replaced_safely(self):
        case = self.root / "case"
        case.mkdir()
        (case / "case.json").write_text(json.dumps({
            "schema": gfx_replay_case.SCHEMA}))
        old = case / "old"
        old.write_text("old")
        gfx_replay_case.prepare_case_output(
            case, [self.frame, self.replay, self.shaders])
        self.assertFalse(old.exists())
        self.assertTrue(case.is_dir())


if __name__ == "__main__":
    unittest.main()
