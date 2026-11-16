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
