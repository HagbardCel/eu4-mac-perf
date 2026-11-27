"""M7 PR4 cross-frame shader translation cache.

The cache key is the verified source identity (program/stage digest), not the
frame or event identity, so identical shader sources reuse one translation
without accepting stale source content.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from automation import gfx_shader_translate, gfx_frame  # noqa: E402


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


@unittest.skipUnless(sys.platform == "darwin", "native compile-option cache")
class ShaderCompileOptionsCacheTests(unittest.TestCase):
    def test_native_cache_uses_the_mode_passed_to_the_compiler(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            (directory / "test.metal").write_text("mock MSL")
            binary = directory / "shader-compile-options-test"
            source = ROOT / "tests/gfx_shader_compile_options_host.mm"
            subprocess.run(
                ["clang++", "-arch", "arm64", "-fobjc-arc", "-Wno-deprecated-declarations",
                 "-framework", "Foundation", "-framework", "Metal", "-o", str(binary),
                 str(source)],
                check=True, capture_output=True, timeout=120)
            subprocess.run([str(binary), str(directory)], check=True, timeout=10)


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

    def write_translation(self, out_dir: Path, *,
                          text: str = "// msl\n") -> dict:
        """A complete, content-verified translation report and artifacts."""
        out_dir.mkdir(parents=True, exist_ok=True)
        metal = out_dir / "program.metal"
        glsl = out_dir / "program.glsl"
        spv = out_dir / "program.spv"
        metal.write_text(text)
        glsl.write_text("// glsl\n")
        spv.write_bytes(b"spv")
        stage = {
            "stage": "vertex",
            "source_sha256": sha(b"vertex-source"),
            "compiler_sha256": sha(text.encode()),
            "glsl_file": glsl.name,
            "glsl_sha256": sha(glsl.read_bytes()),
            "spv_file": spv.name,
            "spv_sha256": sha(spv.read_bytes()),
            "msl_file": metal.name,
            "msl_sha256": sha(metal.read_bytes()),
            "msl_entry": "v_main",
            "msl_uniform_buffer": None,
            "msl_samplers": [],
            "attribute_locations": {},
            "attributes": [],
            "varyings": [],
            "uniforms": [],
            "samplers": [],
        }
        pixel = dict(stage, stage="pixel", msl_entry="f_main")
        report = {
            "schema": "gfx-shader-translation/v1",
            "ok": True,
            "programs": [{
                "program": 7,
                "status": "ok",
                "draws": 1,
                "sources": [
                    {"stage": "vertex", "sha256": sha(b"vertex-source")},
                    {"stage": "pixel", "sha256": sha(b"pixel-source")},
                ],
                "stages": {"vertex": stage, "pixel": pixel},
            }],
        }
        (out_dir / "shaders.json").write_text(json.dumps(report))
        (out_dir / "translation.json").write_text(json.dumps(report))
        return report

    def write_cache_metadata(self, cache_root: Path, identity: dict) -> None:
        (cache_root / "translation-cache.json").write_text(json.dumps({
            "schema": "gfx-shader-translation-cache/v1",
            "identity": identity,
            "cache_dir": str(self.cache),
            "hit": False,
        }))

    def identity(self) -> dict:
        return gfx_frame._shader_source_identity(
            gfx_frame.load_trace(self.frame))

    def test_source_identity_is_frame_independent(self):
        trace = gfx_frame.load_trace(self.frame)
        identity = gfx_frame._shader_source_identity(trace)
        other = dict(trace)
        other["_events"] = [dict(trace["_events"][0], index=99)]
        self.assertEqual(
            gfx_frame._shader_source_identity(other),
            identity)

    def test_swapped_attribute_locations_change_the_cache_identity(self):
        trace = gfx_frame.load_trace(self.frame)
        event = trace["_events"][0]
        event["attributes"] = [
            {"name": "in_POSITION", "location": 0},
            {"name": "in_TEXCOORD", "location": 1},
        ]
        first = gfx_frame._shader_source_identity(trace)
        event["attributes"] = [
            {"name": "in_POSITION", "location": 1},
            {"name": "in_TEXCOORD", "location": 0},
        ]
        second = gfx_frame._shader_source_identity(trace)
        self.assertNotEqual(first["sha256"], second["sha256"])
        self.assertEqual(
            first["programs"]["7"]["attribute_locations"],
            {"in_POSITION": 0, "in_TEXCOORD": 1})
        self.assertEqual(
            second["programs"]["7"]["attribute_locations"],
            {"in_POSITION": 1, "in_TEXCOORD": 0})

    def test_translator_identity_hashes_content_and_tools(self):
        identity = gfx_frame._translator_identity()
        self.assertEqual(identity["schema"], "gfx-translator-identity/v2")
        self.assertEqual(len(identity["sha256"]), 64)
        self.assertIn("glslang", identity["tools"])
        self.assertIn("spirv_cross", identity["tools"])

    def test_second_translation_is_a_cache_hit(self):
        def fake_translate(frame_dir, out_dir):
            return self.write_translation(out_dir)

        with mock.patch.object(gfx_shader_translate,
                               "translate_trace",
                               side_effect=fake_translate) as translate:
            self.assertEqual(gfx_frame.translate_command(self.args()), 0)
            self.assertEqual(translate.call_count, 1)
            first = json.loads(
                (self.out / "translation-cache.json").read_text())
            self.assertFalse(first["hit"])
            self.assertEqual(gfx_frame.translate_command(self.args()), 0)
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
                "EU4_FRAME_REPLAY_FAST_MATH": "0",
                "EU4_FRAME_REPLAY_SAMPLER_SHIFT": "2",
                "UNRELATED_OVERRIDE": "ignored"}, clear=False):
            recipe = gfx_independent_replay.replay_recipe(
                faults={"sampler_shift": 2})
        self.assertEqual(recipe["schema"], "gfx-replay-recipe/v3")
        self.assertTrue(recipe["diagnostic"])
        self.assertEqual(recipe["faults"], {"sampler_shift": 2})
        self.assertEqual(recipe["consumed_env"],
                         {"EU4_FRAME_REPLAY_FAST_MATH": "0",
                          "EU4_FRAME_REPLAY_SAMPLER_SHIFT": "2"})
        self.assertEqual(recipe["effective_env"],
                         {"EU4_FRAME_REPLAY_FAST_MATH": "0",
                          "EU4_FRAME_REPLAY_SAMPLER_SHIFT": "2"})
        self.assertIn("EU4_FRAME_REPLAY_FAST_MATH",
                      recipe["inherited_env"])
        self.assertIn("EU4_FRAME_REPLAY_SAMPLER_SHIFT",
                      recipe["injected_env"])
        self.assertEqual(recipe["shader_compile_options"],
                         {"default_fast_math": True, "fast_math": False})
        self.assertEqual(len(recipe["sha256"]), 64)
        self.assertNotIn("UNRELATED_OVERRIDE", recipe["consumed_env"])

    def _cache_root(self) -> tuple[Path, dict]:
        identity = self.identity()
        return self.cache / identity["sha256"], identity

    def test_missing_cached_stage_is_rebuilt_not_adopted(self):
        with mock.patch.object(gfx_shader_translate, "translate_trace",
                               side_effect=lambda frame_dir, out_dir: self.write_translation(out_dir)) as translate:
            self.assertEqual(gfx_frame.translate_command(self.args()), 0)
            cache_root, identity = self._cache_root()
            self.assertTrue(gfx_frame._translation_cache_valid(
                cache_root, identity))
            (cache_root / "program.metal").unlink()
            self.assertEqual(gfx_frame.translate_command(self.args()), 0)
            self.assertEqual(translate.call_count, 2)
            self.assertFalse(json.loads(
                (self.out / "translation-cache.json").read_text())["hit"])
            self.assertTrue(gfx_frame._translation_cache_valid(
                cache_root, identity))
            self.assertTrue((self.out / "program.metal").is_file())

    def test_corrupted_cached_stage_is_rebuilt_not_adopted(self):
        with mock.patch.object(gfx_shader_translate, "translate_trace",
                               side_effect=lambda frame_dir, out_dir: self.write_translation(out_dir)) as translate:
            self.assertEqual(gfx_frame.translate_command(self.args()), 0)
            cache_root, identity = self._cache_root()
            (cache_root / "program.metal").write_text("// corrupt\n")
            self.assertEqual(gfx_frame.translate_command(self.args()), 0)
            self.assertEqual(translate.call_count, 2)
            self.assertFalse(json.loads(
                (self.out / "translation-cache.json").read_text())["hit"])
            self.assertTrue(gfx_frame._translation_cache_valid(
                cache_root, identity))
            self.assertEqual((cache_root / "program.metal").read_text(),
                             "// msl\n")

    def test_published_cache_with_a_corrupt_stage_is_repaired(self):
        identity = self.identity()
        cache_root = self.cache / identity["sha256"]
        self.write_translation(cache_root, text="// stale but valid\n")
        (cache_root / "program.metal").write_text("// corrupt\n")
        self.write_cache_metadata(cache_root, identity)
        self.assertFalse(gfx_frame._translation_cache_valid(
            cache_root, identity))
        with mock.patch.object(gfx_shader_translate, "translate_trace",
                               side_effect=lambda frame_dir, out_dir: self.write_translation(out_dir)) as translate:
            self.assertEqual(gfx_frame.translate_command(self.args()), 0)
            self.assertEqual(translate.call_count, 1)
        self.assertTrue(gfx_frame._translation_cache_valid(cache_root, identity))
        self.assertEqual((cache_root / "program.metal").read_text(),
                         "// msl\n")
        leftovers = sorted(path.name for path in self.cache.iterdir()
                           if path.name != identity["sha256"])
        self.assertEqual(leftovers, [])

    def test_two_concurrent_warmers_publish_one_valid_entry(self):
        identity = self.identity()
        cache_root = self.cache / identity["sha256"]
        sources = []
        for name in ("source-a", "source-b"):
            source = self.root / name
            self.write_translation(source, text=f"// {name}\n")
            self.write_cache_metadata(source, identity)
            sources.append(source)
        # Start from a corrupt published entry so both builders must repair.
        self.write_translation(cache_root, text="// corrupt winner\n")
        (cache_root / "program.metal").write_text("// corrupt\n")
        self.write_cache_metadata(cache_root, identity)
        barrier = threading.Barrier(2)
        outcomes: list[str] = []

        def publish(source: Path) -> None:
            barrier.wait(timeout=10)
            outcomes.append(
                gfx_frame._publish_translation_cache(
                    source, cache_root, identity))

        threads = [threading.Thread(target=publish, args=(source,))
                   for source in sources]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=30)
        self.assertFalse(any(thread.is_alive() for thread in threads))
        self.assertTrue(gfx_frame._translation_cache_valid(cache_root, identity))
        self.assertTrue(outcomes)
        self.assertIn((cache_root / "program.metal").read_text(),
                      {"// source-a\n", "// source-b\n"})
        leftovers = sorted(path.name for path in self.cache.iterdir()
                           if path.name != identity["sha256"])
        self.assertEqual(leftovers, [])

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
                gfx_frame.translate_command(self.args(force=True)), 0)
            self.assertEqual(translate.call_count, 1)
            value = json.loads(
                (self.out / "translation-cache.json").read_text())
            self.assertFalse(value["hit"])


if __name__ == "__main__":
    unittest.main()
