import base64
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "benchmark"))

import eu4_gfx_shadow_worker as shadow

GL_REF_SRC = ROOT / "tests" / "gfx_clear_gl_ref.m"
GL_REF = ROOT / "benchmark" / ".build" / "gfx_clear_gl_ref"


def build_gl_ref():
    GL_REF.parent.mkdir(parents=True, exist_ok=True)
    command = [
        "clang",
        "-arch",
        "x86_64",
        "-O2",
        "-Wall",
        "-Wextra",
        "-fobjc-arc",
        "-framework",
        "OpenGL",
        "-o",
        str(GL_REF),
        str(GL_REF_SRC),
    ]
    built = subprocess.run(command, capture_output=True, text=True, check=False)
    if built.returncode:
        raise RuntimeError(built.stderr.strip() or "gfx clear gl ref failed to build")
    return GL_REF


def gl_bytes(dim, color):
    ran = subprocess.run(
        [str(GL_REF), str(dim), *(str(c) for c in color)],
        capture_output=True,
        check=False,
    )
    if ran.returncode:
        raise RuntimeError(ran.stderr.decode() or "gl ref failed")
    want = dim * dim * 4
    if len(ran.stdout) != want:
        raise RuntimeError(f"gl ref size {len(ran.stdout)} != {want}")
    return ran.stdout


def viewport_words(width, height):
    return [height, width, 0, 0, 0, 0]


@unittest.skipUnless(sys.platform == "darwin", "Metal shadow render runs on macOS")
class ShadowRenderTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        shadow.build()
        build_gl_ref()

    def setUp(self):
        self.worker = shadow.Worker()

    def tearDown(self):
        if self.worker is not None:
            self.worker.close()
            self.worker = None

    def call(self, op, args=None):
        resp = self.worker.call(op, args)
        self.assertIsNotNone(resp, self.worker.stderr_text())
        self.assertEqual(resp.get("schema"), "metal-shadow/v0")
        return resp

    def ok(self, op, args=None):
        resp = self.call(op, args)
        self.assertTrue(resp.get("ok"), resp)
        return resp

    def boot(self):
        self.ok("settings", {"display": 1, "windowed": 0, "samples": [1, 2, 4]})
        self.ok("configure_window", {"width": 800, "height": 600, "window_id": 7, "source": 9})
        self.ok("init")

    def prepare(self, dim):
        self.boot()
        configured = self.ok("configure_offscreen", {"width": dim, "height": dim})
        self.assertEqual(configured["result"]["width"], dim)
        self.assertEqual(configured["result"]["height"], dim)
        self.assertFalse(configured["result"]["contents_known"])
        self.ok("set_viewport", {"words": viewport_words(dim, dim)})

    def clear(self, flags=1, color=None):
        args = {"flags": flags}
        if color is not None:
            args["color"] = color
        resp = self.ok("clear", args)
        result = resp["result"]
        self.assertTrue(result["submitted"])
        self.assertEqual(result["command_buffer_status"], "completed")
        self.assertTrue(result["contents_known"])
        return resp

    def read_bytes(self):
        resp = self.ok("read_offscreen")
        result = resp["result"]
        raw = base64.b64decode(result["bytes_base64"])
        self.assertEqual(len(raw), result["width"] * result["height"] * 4)
        return result["width"], result["height"], raw

    def test_read_before_clear_unknown(self):
        self.prepare(16)
        resp = self.call("read_offscreen")
        self.assertFalse(resp.get("ok"), resp)
        self.assertEqual(resp.get("error"), "contents_unknown")
        self.assertNotIn("bytes_base64", str(resp.get("result")))

    def test_clear_without_configure(self):
        self.boot()
        self.ok("set_viewport", {"words": viewport_words(16, 16)})
        resp = self.call("clear", {"flags": 1})
        self.assertFalse(resp.get("ok"), resp)
        self.assertEqual(resp.get("error"), "unsupported")

    def test_black_and_red_match_gl(self):
        for dim in (16, 64):
            with self.subTest(dim=dim):
                if self.worker is not None:
                    self.worker.close()
                self.worker = shadow.Worker()
                self.prepare(dim)
                self.clear(color=[0, 0, 0, 1])
                _, _, got = self.read_bytes()
                self.assertEqual(got, gl_bytes(dim, (0, 0, 0, 1)))
                self.assertEqual(got, bytes([0x00, 0x00, 0x00, 0xFF]) * (dim * dim))
                self.clear(color=[1, 0, 0, 1])
                _, _, got = self.read_bytes()
                self.assertEqual(got, gl_bytes(dim, (1, 0, 0, 1)))
                self.assertEqual(got, bytes([0x00, 0x00, 0xFF, 0xFF]) * (dim * dim))

    def test_ordering_second_clear_wins(self):
        self.prepare(16)
        self.clear(color=[1, 0, 0, 1])
        self.clear(color=[0, 0, 0, 1])
        _, _, got = self.read_bytes()
        self.assertEqual(got, bytes([0x00, 0x00, 0x00, 0xFF]) * (16 * 16))

    def test_unsupported_flags_preserve_pixels(self):
        self.prepare(16)
        self.clear(color=[1, 0, 0, 1])
        before = self.read_bytes()[2]
        resp = self.call("clear", {"flags": 7})
        self.assertFalse(resp.get("ok"), resp)
        self.assertEqual(resp.get("error"), "unsupported")
        _, _, after = self.read_bytes()
        self.assertEqual(after, before)

    def test_bad_color_rejected(self):
        self.prepare(16)
        self.clear(color=[0, 0, 0, 1])
        before = self.read_bytes()[2]
        cases = [
            {"flags": 1, "color": [0, 0]},
            {"flags": 1, "color": [True, 0, 0, 1]},
            {"flags": 1, "color": [2, 0, 0, 1]},
            {"flags": 1, "color": [-0.1, 0, 0, 1]},
        ]
        for args in cases:
            with self.subTest(args=args):
                resp = self.call("clear", args)
                self.assertFalse(resp.get("ok"), resp)
                self.assertEqual(resp.get("error"), "bad_color")
        _, _, after = self.read_bytes()
        self.assertEqual(after, before)

    def test_bad_viewport_rejected(self):
        self.boot()
        self.ok("configure_offscreen", {"width": 16, "height": 16})
        self.ok("set_viewport", {"words": [8, 16, 0, 0, 0, 0]})
        resp = self.call("clear", {"flags": 1})
        self.assertFalse(resp.get("ok"), resp)
        self.assertEqual(resp.get("error"), "unsupported")

    def test_reconfigure_unknown_until_clear(self):
        self.prepare(16)
        self.clear(color=[1, 0, 0, 1])
        configured = self.ok("configure_offscreen", {"width": 32, "height": 32})
        self.assertFalse(configured["result"]["contents_known"])
        resp = self.call("read_offscreen")
        self.assertEqual(resp.get("error"), "contents_unknown")
        self.ok("set_viewport", {"words": viewport_words(32, 32)})
        self.clear(color=[0, 0, 0, 1])
        width, height, got = self.read_bytes()
        self.assertEqual((width, height), (32, 32))
        self.assertEqual(got, bytes([0x00, 0x00, 0x00, 0xFF]) * (32 * 32))

    def test_oversized_configure_rejected(self):
        self.boot()
        resp = self.call("configure_offscreen", {"width": 65, "height": 16})
        self.assertFalse(resp.get("ok"), resp)
        self.assertEqual(resp.get("error"), "bad_dims")

    def test_shutdown_then_fresh_worker(self):
        self.prepare(16)
        self.clear()
        self.ok("shutdown")
        self.worker.close()
        self.worker = shadow.Worker()
        self.prepare(16)
        self.clear(color=[1, 0, 0, 1])
        _, _, got = self.read_bytes()
        self.assertEqual(got, bytes([0x00, 0x00, 0xFF, 0xFF]) * (16 * 16))


if __name__ == "__main__":
    unittest.main()
