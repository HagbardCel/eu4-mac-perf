"""Effective sampler identity and raster-state mapping.

Exercises the production projections used by the Metal encoder without drawing:
the sampler cache key covers every descriptor field Metal consumes, cull state
maps GL front/back and the front-and-back no-fragment case, and signed scissor
boxes are intersected from both endpoints before the Metal origin conversion.
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BENCH = ROOT / "benchmark"
CORE = BENCH / "eu4_gfx_frame_replay.mm"
HOST = ROOT / "tests" / "gfx_render_state_host.mm"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from automation import native_build  # noqa: E402

MTL_CULL_NONE = 0
MTL_CULL_FRONT = 1
MTL_CULL_BACK = 2


class RenderStateProjectionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        cls.base = Path(cls.temporary.name)
        cls.binary = cls.base / "gfx_render_state_host"
        native_build.require_prerequisites(
            sources=[HOST, CORE], frameworks=("Foundation", "Metal"))
        native_build.build(
            ["clang", "-arch", "arm64", "-O1", "-fobjc-arc", "-lc++",
             "-I", str(BENCH), "-framework", "Foundation",
             "-framework", "Metal", "-o", str(cls.binary),
             str(HOST), str(CORE)],
            label="render-state-host", timeout=300)

    @classmethod
    def tearDownClass(cls):
        cls.temporary.cleanup()

    def query(self, document: dict) -> dict:
        path = self.base / "state.json"
        path.write_text(json.dumps(document))
        result = subprocess.run([str(self.binary), str(path)],
                                capture_output=True, text=True, check=False)
        self.assertEqual(result.returncode, 0, result.stderr)
        values: dict = {"raw": result.stdout}
        for line in result.stdout.splitlines():
            if line.startswith("key="):
                values["key"] = line[4:]
            elif line.startswith("cull="):
                parts = line.split()
                values["cull"] = int(parts[0].split("=")[1])
                values["cull_empty"] = int(parts[1].split("=")[1])
                values["scissor_empty"] = int(parts[2].split("=")[1])
                rect = parts[3].split("=")[1].split(",")
                values["rect"] = tuple(int(item) for item in rect)
            elif line.startswith("viewport="):
                values["viewport"] = tuple(
                    float(item) for item in line.split("=", 1)[1].split(","))
        return values

    def sampler(self, **overrides):
        value = {
            "min": "0x2701", "mag": "0x2601", "wrap_s": "0x2901",
            "wrap_t": "0x2901", "wrap_r": "0x2901",
            "min_lod": -1000, "max_lod": 1000, "lod_bias": 0,
            "compare_mode": 0, "compare_func": "0x0203",
        }
        value.update(overrides)
        return value

    def test_every_consumed_sampler_field_changes_the_key(self):
        base = self.query({"sampler": self.sampler(), "width": 8, "height": 8})
        for name, value in (
                ("wrap_r", "0x812f"), ("min_lod", 2), ("max_lod", 7),
                ("compare_mode", "0x884c")):
            changed = self.query({
                "sampler": self.sampler(**{name: value}),
                "width": 8, "height": 8})
            self.assertNotEqual(base["key"], changed["key"], name)
        compare = self.query({"sampler": self.sampler(
            compare_mode="0x884c", compare_func="0x0203"),
            "width": 8, "height": 8})["key"]
        changed = self.query({"sampler": self.sampler(
            compare_mode="0x884c", compare_func="0x0201"),
            "width": 8, "height": 8})["key"]
        self.assertNotEqual(compare, changed)
        # compare_func is inert without compare-to-texture.
        inert = self.query({"sampler": self.sampler(
            compare_mode=0, compare_func="0x0201"),
            "width": 8, "height": 8})["key"]
        self.assertEqual(base["key"], inert)

    def test_six_observed_max_lods_do_not_alias(self):
        keys = {
            self.query({"sampler": self.sampler(max_lod=value),
                        "width": 8, "height": 8})["key"]
            for value in (2, 7, 8, 9, 10, 11)
        }
        self.assertEqual(len(keys), 6)

    def test_identical_sampler_state_reuses_one_key(self):
        first = self.query({"sampler": self.sampler(), "width": 8,
                            "height": 8})["key"]
        second = self.query({"sampler": self.sampler(), "width": 8,
                             "height": 8})["key"]
        self.assertEqual(first, second)

    def test_equivalent_enum_representations_share_one_identity(self):
        # GL enums arrive sometimes as "0x2701" and sometimes as integers.
        numeric = self.sampler(min=0x2701, mag=0x2601, wrap_s=0x2901,
                               wrap_t=0x2901, wrap_r=0x2901,
                               compare_func=0x0203)
        first = self.query({"sampler": self.sampler(), "width": 8,
                            "height": 8})["key"]
        second = self.query({"sampler": numeric, "width": 8,
                             "height": 8})["key"]
        self.assertEqual(first, second)
        # Float and integer LODs are the same effective value.
        third = self.query({"sampler": self.sampler(
            min_lod=-1000.0, max_lod=1000.0, lod_bias=0.0),
            "width": 8, "height": 8})["key"]
        self.assertEqual(first, third)

    def cull_render(self, mode, enabled, primitive=0x0004, no_cull=0):
        result = subprocess.run(
            [str(self.binary), "--cull", str(mode), str(enabled),
             str(primitive), str(no_cull)],
            capture_output=True, text=True, check=False)
        self.assertEqual(result.returncode, 0, result.stderr)
        values = dict(item.split("=") for item in result.stdout.split())
        return int(values["left"]), int(values["right"])

    def test_rendered_winding_respects_front_and_back_culling(self):
        left, right = self.cull_render(0x0405, 1)  # GL_BACK
        self.assertEqual(left, 0)
        self.assertGreater(right, 0)
        left, right = self.cull_render(0x0404, 1)  # GL_FRONT
        self.assertGreater(left, 0)
        self.assertEqual(right, 0)

    def test_front_and_back_is_empty_only_when_culling_is_effective(self):
        left, right = self.cull_render(0x0408, 1)
        self.assertEqual((left, right), (0, 0))
        left, right = self.cull_render(0x0408, 0)  # cull disabled
        self.assertGreater(left, 0)
        self.assertGreater(right, 0)
        left, right = self.cull_render(0x0408, 1, no_cull=1)
        self.assertGreater(left, 0)
        self.assertGreater(right, 0)
        left, right = self.cull_render(0x0408, 1, primitive=0x0001)  # lines
        self.assertGreater(left, 0)
        self.assertGreater(right, 0)

    def test_cull_faces_and_front_and_back(self):
        back = self.query({"cull": {"enabled": 1, "mode": "0x405",
                                    "front": "0x901"}})
        self.assertEqual(back["cull"], MTL_CULL_BACK)
        self.assertEqual(back["cull_empty"], 0)
        front = self.query({"cull": {"enabled": 1, "mode": "0x404",
                                     "front": "0x901"}})
        self.assertEqual(front["cull"], MTL_CULL_FRONT)
        both = self.query({"cull": {"enabled": 1, "mode": "0x408",
                                    "front": "0x901"}})
        self.assertEqual(both["cull"], MTL_CULL_NONE)
        self.assertEqual(both["cull_empty"], 1)

    def test_unknown_cull_mode_fails_closed(self):
        path = self.base / "bad-cull.json"
        path.write_text(json.dumps({"cull": {"enabled": 1, "mode": "0x9999"}}))
        result = subprocess.run([str(self.binary), str(path)],
                                capture_output=True, text=True, check=False)
        self.assertNotEqual(result.returncode, 0)

    def test_viewport_conversion_at_negative_and_outside_boundaries(self):
        negative = self.query({"width": 8, "height": 8,
                               "viewport": [-2, -2, 4, 4]})
        self.assertEqual(negative["viewport"], (-2.0, 6.0, 4.0, 4.0))
        outside = self.query({"width": 8, "height": 8,
                              "viewport": [10, 10, 4, 4]})
        self.assertEqual(outside["viewport"], (10.0, -6.0, 4.0, 4.0))
        full = self.query({"width": 8, "height": 8,
                           "viewport": [0, 0, 8, 8]})
        self.assertEqual(full["viewport"], (0.0, 0.0, 8.0, 8.0))
        oversized = self.query({"width": 8, "height": 8,
                                "viewport": [-4, -4, 16, 16]})
        self.assertEqual(oversized["viewport"], (-4.0, -4.0, 16.0, 16.0))

    def test_signed_scissor_intersects_both_endpoints(self):
        inside = self.query({"width": 8, "height": 8, "scissor": {
            "enabled": 1, "box": [-2, -2, 4, 4]}})
        self.assertEqual(inside["scissor_empty"], 0)
        self.assertEqual(inside["rect"], (0, 6, 2, 2))
        outside = self.query({"width": 8, "height": 8, "scissor": {
            "enabled": 1, "box": [10, 10, 4, 4]}})
        self.assertEqual(outside["scissor_empty"], 1)
        edge = self.query({"width": 8, "height": 8, "scissor": {
            "enabled": 1, "box": [-2, 0, 4, 8]}})
        self.assertEqual(edge["scissor_empty"], 0)
        self.assertEqual(edge["rect"], (0, 0, 2, 8))
        full = self.query({"width": 8, "height": 8, "scissor": {
            "enabled": 1, "box": [0, 0, 8, 8]}})
        self.assertEqual(full["rect"], (0, 0, 8, 8))


if __name__ == "__main__":
    unittest.main()
