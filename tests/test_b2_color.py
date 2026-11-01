import json
import os
import struct
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CAPTURE = ROOT / "analysis/metal-draw/b2/color_capture.m"
DRIVER = ROOT / "tests/b2_color_driver.m"
HOST = ROOT / "tests/metal_color_ref_host.m"
SHADERS = ROOT / "analysis/metal-stage-d/v1"
BUILD = ROOT / "benchmark/.build"
PIN = "b3d38876abf4e61cdae57509186d7cb7dcb03bfeaca4c95c10c713794715141d"
HISTORICAL = "ea46e32:results/20261031T144434Z-metal-draw/matched.glsl"


def run(command, env=None, check=True):
    result = subprocess.run(command, capture_output=True, text=True, env=env, check=False)
    if check and result.returncode != 0:
        raise AssertionError(result.stderr or result.stdout)
    return result


@unittest.skipUnless(sys.platform == "darwin", "Color capture runs on macOS")
class B2ColorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        BUILD.mkdir(parents=True, exist_ok=True)
        cls.dylib = BUILD / "libeu4_b2_color_capture_x86_64.dylib"
        cls.driver = BUILD / "b2_color_driver"
        cls.host = BUILD / "metal_color_ref_host_x86_64"
        run(["clang", "-arch", "x86_64", "-O2", "-Wall", "-Wextra", "-dynamiclib", "-framework", "OpenGL",
             "-o", str(cls.dylib), str(CAPTURE)])
        run(["clang", "-arch", "x86_64", "-Wall", "-Wextra", "-framework", "OpenGL",
             "-o", str(cls.driver), str(DRIVER)])
        run(["clang", "-arch", "x86_64", "-fobjc-arc", "-Wall", "-Wextra", "-framework", "Metal",
             "-framework", "OpenGL", "-framework", "Foundation", "-o", str(cls.host), str(HOST)])

    def env(self, directory):
        env = os.environ.copy()
        env["DYLD_INSERT_LIBRARIES"] = str(self.dylib)
        env["EU4_B2_COLOR_DIR"] = str(directory)
        env["EU4_B2_COLOR_VERT"] = str(SHADERS / "color.vert")
        env["EU4_B2_COLOR_FRAG"] = str(SHADERS / "color.frag")
        env["EU4_B2_COLOR_EXE_SHA256"] = PIN
        env["EU4_B2_COLOR_MSL_VERT"] = str(SHADERS / "color.vert.metal")
        env["EU4_B2_COLOR_MSL_FRAG"] = str(SHADERS / "color.frag.metal")
        return env

    def drive(self, scenario, directory):
        return run([str(self.driver), scenario], self.env(directory))

    def match(self, kind, reference, source):
        return run([str(self.driver), kind, str(reference), str(source)], self.env(BUILD), check=False)

    def test_canonical_sources(self):
        vert = (SHADERS / "color.vert").read_text()
        frag = (SHADERS / "color.frag").read_text()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            same = root / "same.vert"
            same.write_text(vert)
            self.assertEqual(self.match("match-vertex", SHADERS / "color.vert", same).returncode, 0)
            moved = vert
            start = moved.index("attribute float2 in_POSITION;")
            end = moved.index("VS_OUTPUT glsl_main")
            decls = moved[start:end]
            moved = moved[:start] + moved[end:]
            at = moved.index("void main()")
            moved = moved[:at] + decls + moved[at:]
            moved = moved.replace(
                "#define NO_SHADER_TEXTURE_LOD\n",
                "#extension GL_ARB_shader_texture_lod : enable\n"
                "#define MAP_SIZE_X 5632.000\n#define MAP_SIZE_Y 2048.000\n"
                "#define MAP_POW2_X 1.000\n#define MAP_POW2_Y 1.000\n"
                "#define FOW_POW2_X 1.000\n#define FOW_POW2_Y 1.000\n",
                1)
            reordered = root / "reordered.vert"
            reordered.write_text(moved)
            self.assertEqual(self.match("match-vertex", SHADERS / "color.vert", reordered).returncode, 0)
            position = root / "position.vert"
            position.write_text(vert.replace(
                "float4(v.vPosition.x, v.vPosition.y, 0, 1)",
                "float4(v.vPosition.x, v.vPosition.y, 1, 1)", 1))
            self.assertNotEqual(self.match("match-vertex", SHADERS / "color.vert", position).returncode, 0)
            extra = root / "extra.vert"
            extra.write_text(vert.replace("Out.vColor = v.vColor;", "Out.vColor = v.vColor + v.vColor;", 1))
            self.assertNotEqual(self.match("match-vertex", SHADERS / "color.vert", extra).returncode, 0)
            wrong_map = root / "map.vert"
            wrong_map.write_text(moved.replace("5632.000", "5632.001", 1))
            self.assertNotEqual(self.match("match-vertex", SHADERS / "color.vert", wrong_map).returncode, 0)
            same_frag = root / "same.frag"
            same_frag.write_text(frag)
            self.assertEqual(self.match("match-fragment", SHADERS / "color.frag", same_frag).returncode, 0)
            constant = root / "constant.frag"
            constant.write_text(frag.replace("return v.vColor;", "return float4(1.0, 0.0, 0.0, 1.0);", 1))
            self.assertNotEqual(self.match("match-fragment", SHADERS / "color.frag", constant).returncode, 0)

    def test_historical_vertex_when_present(self):
        present = subprocess.run(["git", "cat-file", "-e", HISTORICAL], cwd=ROOT, check=False)
        if present.returncode != 0:
            self.skipTest("historical blob is not in this clone")
        source = subprocess.check_output(["git", "show", HISTORICAL], cwd=ROOT)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "matched.glsl"
            path.write_bytes(source)
            self.assertEqual(self.match("match-vertex", SHADERS / "color.vert", path).returncode, 0)

    def test_index_range(self):
        def check(offset, count, size, nbytes, want):
            result = run(
                [str(self.driver), "range", str(offset), str(count), str(size), str(nbytes), want],
                self.env(BUILD), check=False)
            self.assertEqual(result.returncode, 0, result.stderr)
        check(0, 3, 2, 6, "ok")
        check(4, 3, 2, 8, "no")
        check(2 ** 64 - 2, 2, 2, 2 ** 64 - 1, "no")

    def test_draw_symbols_and_attribute_types(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.drive("symbols", Path(tmp))
            self.drive("accept", Path(tmp))
            fixture = json.loads((Path(tmp) / "fixture" / "fixture.json").read_text())
            by_name = {item["name"]: item for item in fixture["attributes"]}
            self.assertEqual(by_name["in_PDX_COLOR"]["shader_type"], "GL_FLOAT_VEC4")
            self.assertEqual(by_name["in_PDX_COLOR"]["component_type"], "GL_UNSIGNED_BYTE")
            self.assertTrue(by_name["in_PDX_COLOR"]["normalized"])
            self.assertEqual(by_name["in_POSITION"]["shader_type"], "GL_FLOAT_VEC2")
            self.assertEqual(by_name["in_POSITION"]["location"], 3)
            self.assertEqual(by_name["in_PDX_COLOR"]["location"], 5)
            self.assertFalse(fixture["dither"])
            host = run([str(self.host), "--fixture", str(Path(tmp) / "fixture")], self.env(tmp))
            self.assertIn("ok worst=0 coverage=0", host.stdout)

    def test_rejections_and_second_matrix(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.drive("reject-then-accept", Path(tmp))
            matrix = struct.unpack("<16f", (Path(tmp) / "fixture" / "uniform0.bin").read_bytes())
            self.assertEqual(matrix[14], 0.25)
        for scenario in ("nonfinite", "oob", "mapped"):
            with tempfile.TemporaryDirectory() as tmp:
                self.drive(scenario, Path(tmp))
                self.assertFalse((Path(tmp) / "fixture" / "fixture.json").exists())

    def test_lifecycle(self):
        for scenario in ("relink", "shader-edit", "delete", "arb-link"):
            with tempfile.TemporaryDirectory() as tmp:
                self.drive(scenario, Path(tmp))

    def test_bindings_and_dither(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.drive("bindings", Path(tmp))
        with tempfile.TemporaryDirectory() as tmp:
            self.drive("dither-on", Path(tmp))
            fixture = json.loads((Path(tmp) / "fixture" / "fixture.json").read_text())
            self.assertTrue(fixture["dither"])
            host = run([str(self.host), "--fixture", str(Path(tmp) / "fixture")], self.env(tmp))
            self.assertIn("coverage=0", host.stdout)
            worst = int(host.stdout.split("worst=")[1].split()[0])
            self.assertLessEqual(worst, 1)
        with tempfile.TemporaryDirectory() as tmp:
            self.drive("uint32", Path(tmp))
            host = run([str(self.host), "--fixture", str(Path(tmp) / "fixture")], self.env(tmp))
            self.assertIn("ok worst=0 coverage=0", host.stdout)

    def test_indexed_selftest_and_isolation(self):
        result = run([
            str(self.host), "--selftest",
            str(SHADERS / "color.vert"), str(SHADERS / "color.frag"),
            str(SHADERS / "color.vert.metal"), str(SHADERS / "color.frag.metal"),
        ])
        self.assertIn("ok", result.stdout)
        nm = subprocess.check_output(["nm", "-g", str(self.dylib)], text=True)
        self.assertNotIn("glReadPixels", nm)
        self.assertNotIn("glGenFramebuffers", nm)

    def test_launcher_refuses_without_both_authorizations(self):
        launcher = ROOT / "benchmark/eu4_b2_color_capture.py"
        env = os.environ.copy()
        env.pop("EU4_B2_COLOR_AUTHORIZE", None)
        missing = run([sys.executable, str(launcher)], env, check=False)
        self.assertEqual(missing.returncode, 2)
        flagged = run([sys.executable, str(launcher), "--authorize-live"], env, check=False)
        self.assertEqual(flagged.returncode, 2)
        env["EU4_B2_COLOR_AUTHORIZE"] = "yes"
        only_env = run([sys.executable, str(launcher)], env, check=False)
        self.assertEqual(only_env.returncode, 2)


if __name__ == "__main__":
    unittest.main()
