"""Regression: the window-capture helper must pass the drawable extent.

Without it, ``live_gfx._window_capture`` silently disables screenshot
qualification and reports a captured-but-unqualified image as success.
"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from automation import m6_live_capture  # noqa: E402


class LiveWindowCaptureTests(unittest.TestCase):
    def run_capture(self, *, qualified: bool):
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary)
            (output / "checkpoint-after-draw-1.json").write_text(json.dumps({
                "target": {"width": 1728, "height": 1117},
            }))
            (output / "metadata.json").write_text(json.dumps({"pid": 4321}))
            (output / "swap-frame.bgra").write_bytes(b"\x00" * 16)
            calls = []

            def fake_window_capture(path, pid, drawable=None, **kwargs):
                calls.append({"path": path, "pid": pid, "drawable": drawable, **kwargs})
                return {"status": "captured", "qualified": qualified, "file": "window.png",
                        "width": 1728, "height": 1117, "method": "test"}

            with mock.patch.object(m6_live_capture.live_gfx, "_window_capture",
                                   side_effect=fake_window_capture):
                code = m6_live_capture.main([
                    "--output", str(output), "--timeout", "5", "--settle", "0",
                ])
            record = json.loads((output / "window-capture.json").read_text())
        return code, calls, record

    def test_drawable_extent_is_forwarded(self):
        code, calls, record = self.run_capture(qualified=True)
        self.assertEqual(code, 0)
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0]["drawable_size"], (1728, 1117))
        self.assertEqual(record["capture"]["qualified"], True)

    def test_unqualified_window_is_not_success(self):
        code, calls, record = self.run_capture(qualified=False)
        self.assertEqual(code, 1)
        self.assertEqual(calls[0]["drawable_size"], (1728, 1117))
        self.assertEqual(record["capture"]["qualified"], False)


if __name__ == "__main__":
    unittest.main()
