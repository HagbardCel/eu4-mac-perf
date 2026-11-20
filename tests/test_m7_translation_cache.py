"""M7 PR4 cross-frame shader translation cache.

The cache key is the verified source identity (program/stage digest), not the
frame or event identity, so identical shader sources reuse one translation
without accepting stale source content.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from automation import gfx_shader_translate, m6_frame  # noqa: E402


def sha(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def make_frame(root: Path) -> Path:
    frame = root / "frame"
    (frame / "blobs").mkdir(parents=True)
    vertex = b"vertex-source"
    pixel = b"pixel-source"
    (frame / "blobs" / "vertex.vert").write_bytes(vertex)
    (frame / "blobs" / "pixel.frag").write_bytes(pixel)
    event = {
        "kind": "draw", "index": 0,
        "draw": {"entry": "_GfxDraw", "mode": 4, "count": 3},
        "program": {"name": 7, "shaders": [
            {"stage": "vertex", "sha256": sha(vertex),
             "file": "vertex.vert"},
            {"stage": "pixel", "sha256": sha(pixel), "file": "pixel.frag"},
        ]},
        "target": {"framebuffer": 0, "width": 4, "height": 4},
        "textures": [], "attributes": [],
    }
    (frame / "frame-events.jsonl").write_text(json.dumps(event) + "\n")
    (frame / "frame-reference.bgra").write_bytes(b"\0" * 64)
    manifest = {
        "schema": "gfx-frame-trace/v1", "complete": True,
        "reason": "scene_present", "events": 1,
        "frame": {"width": 4, "height": 4, "origin": "lower_left",
                  "pixel_format": "BGRA8", "row_stride": 16,
                  "reference": "frame-reference.bgra",
                  "reference_bytes": 64,
                  "reference_sha256": sha(b"\0" * 64)},
    }
    (frame / "frame-trace.json").write_text(json.dumps(manifest))
    return frame


class TranslationCacheTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.frame = make_frame(self.root)
        self.out = self.root / "out"
        self.cache = self.root / "cache"

    def tearDown(self):
        self.temporary.cleanup()

    def args(self, force=False):
        return argparse.Namespace(frame=str(self.frame), out=str(self.out),
                                  force=force, cache_dir=str(self.cache))

    def test_source_identity_is_frame_independent(self):
        trace = m6_frame.load_trace(self.frame)
        identity = m6_frame._shader_source_identity(trace)
        other = dict(trace)
        other["_events"] = [dict(trace["_events"][0], index=99)]
        self.assertEqual(
            m6_frame._shader_source_identity(other),
            identity)

    def test_swapped_attribute_locations_change_the_cache_identity(self):
        trace = m6_frame.load_trace(self.frame)
        event = trace["_events"][0]
        event["attributes"] = [
            {"name": "in_POSITION", "location": 0},
            {"name": "in_TEXCOORD", "location": 1},
        ]
        first = m6_frame._shader_source_identity(trace)
        event["attributes"] = [
            {"name": "in_POSITION", "location": 1},
            {"name": "in_TEXCOORD", "location": 0},
        ]
        second = m6_frame._shader_source_identity(trace)
        self.assertNotEqual(first["sha256"], second["sha256"])
        self.assertEqual(
            first["programs"]["7"]["attribute_locations"],
            {"in_POSITION": 0, "in_TEXCOORD": 1})
        self.assertEqual(
            second["programs"]["7"]["attribute_locations"],
            {"in_POSITION": 1, "in_TEXCOORD": 0})

    def test_translator_identity_hashes_content_and_tools(self):
        identity = m6_frame._translator_identity()
        self.assertEqual(identity["schema"], "gfx-translator-identity/v2")
        self.assertEqual(len(identity["sha256"]), 64)
        self.assertIn("glslang", identity["tools"])
        self.assertIn("spirv_cross", identity["tools"])

    def test_second_translation_is_a_cache_hit(self):
        def fake_translate(frame_dir, out_dir):
            out_dir.mkdir(parents=True, exist_ok=True)
            (out_dir / "shaders.json").write_text(json.dumps({
                "schema": "gfx-shader-translation/v1",
                "programs": [{"program": 7, "status": "ok"}]}))
            (out_dir / "program.metal").write_text("// msl")
            return {"ok": True, "programs": [{"program": 7, "status": "ok"}]}

        with mock.patch.object(gfx_shader_translate,
                               "translate_trace",
                               side_effect=fake_translate) as translate:
            self.assertEqual(m6_frame.translate_command(self.args()), 0)
            self.assertEqual(translate.call_count, 1)
            first = json.loads(
                (self.out / "translation-cache.json").read_text())
            self.assertFalse(first["hit"])
            self.assertEqual(m6_frame.translate_command(self.args()), 0)
            self.assertEqual(translate.call_count, 1)
            second = json.loads(
                (self.out / "translation-cache.json").read_text())
            self.assertTrue(second["hit"])
            self.assertEqual(second["identity"], first["identity"])
            self.assertTrue((self.out / "program.metal").is_file())

    def test_replay_recipe_records_consumed_overrides_and_faults(self):
        import os
        from automation import gfx_independent_replay

        with mock.patch.dict(os.environ, {
                "EU4_FRAME_REPLAY_PRECISE_MATH": "1",
                "EU4_FRAME_REPLAY_SAMPLER_SHIFT": "2",
                "UNRELATED_OVERRIDE": "ignored"}, clear=False):
            recipe = gfx_independent_replay.replay_recipe(
                faults={"sampler_shift": 2})
        self.assertEqual(recipe["schema"], "gfx-replay-recipe/v1")
        self.assertTrue(recipe["diagnostic"])
        self.assertEqual(recipe["faults"], {"sampler_shift": 2})
        self.assertEqual(recipe["consumed_env"],
                         {"EU4_FRAME_REPLAY_PRECISE_MATH": "1",
                          "EU4_FRAME_REPLAY_SAMPLER_SHIFT": "2"})
        self.assertEqual(recipe["precise_math"], "1")
        self.assertNotIn("UNRELATED_OVERRIDE", recipe["consumed_env"])

    def test_force_bypasses_the_cache(self):
        cache_key = self.root / "cache" / "placeholder"
        cache_key.mkdir(parents=True)
        (cache_key / "shaders.json").write_text("{}")

        def fake_translate(frame_dir, out_dir):
            out_dir.mkdir(parents=True, exist_ok=True)
            (out_dir / "shaders.json").write_text(json.dumps({
                "schema": "gfx-shader-translation/v1", "programs": []}))
            return {"ok": True, "programs": []}

        with mock.patch.object(gfx_shader_translate,
                               "translate_trace",
                               side_effect=fake_translate) as translate:
            self.assertEqual(
                m6_frame.translate_command(self.args(force=True)), 0)
            self.assertEqual(translate.call_count, 1)
            value = json.loads(
                (self.out / "translation-cache.json").read_text())
            self.assertFalse(value["hit"])


if __name__ == "__main__":
    unittest.main()
