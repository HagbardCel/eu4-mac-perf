import sys
import unittest

from tests.test_gfx_hidpi import build_host, run_host


@unittest.skipUnless(sys.platform == "darwin", "Metal AppKit host runs on macOS")
class GfxHiDPIAppKitTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        build_host()

    def test_window_backed_real_drawable_scale_change(self):
        ran = run_host("appkit")
        if ran.returncode == 77:
            raise unittest.SkipTest(
                ran.stderr.strip() or "window-backed CAMetalDrawable unavailable"
            )
        if ran.returncode:
            self.fail(ran.stderr.strip() or ran.stdout.strip() or "AppKit host failed")


if __name__ == "__main__":
    unittest.main()
