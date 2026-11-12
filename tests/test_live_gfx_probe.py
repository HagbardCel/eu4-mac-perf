"""Agent-facing experiment surface: authorization, probe refusal, PNG preview."""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from automation import live_gfx, launch_ledger

ROOT = Path(__file__).resolve().parents[1]


class ProbeSurfaceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.exe = self.root / "eu4"
        self.exe.write_bytes(b"pinned stub")
        self.run_root = self.root / "run"
        self.run_root.mkdir()
        self.output_root = self.run_root / "experiments"
        self.ledger_dir = self.root / "authority"

    def test_probe_refuses_without_operator_authorization(self) -> None:
        output = self.output_root / "one"
        with patch.dict(os.environ, {"EU4_LAUNCH_AUTHORITY_DIR": str(self.ledger_dir)}):
            code = live_gfx.main([
                "probe", "--backend", "metal", "--checkpoint", "after-draw",
                "--output", str(output),
            ])
        self.assertEqual(code, live_gfx.EXIT_INCOMPLETE)
        self.assertFalse(output.exists())

    def test_authorize_pins_the_executable_and_output_root(self) -> None:
        with patch.dict(os.environ, {"EU4_LAUNCH_AUTHORITY_DIR": str(self.ledger_dir)}):
            code = live_gfx.main([
                "authorize",
                "--run-root", str(self.run_root),
                "--output-root", str(self.output_root),
                "--executable", str(self.exe),
                "--limit", "2",
                "--deadline-seconds", "90",
                "--workflow-id", "wf-probe",
            ])
        self.assertEqual(code, live_gfx.EXIT_COMPLETE)
        pointer = launch_ledger.read_pointer(self.run_root)
        self.assertIsNotNone(pointer)
        value = launch_ledger.load_ledger(Path(pointer["ledger"]))
        self.assertEqual(value["limit"], 2)
        self.assertEqual(value["deadline_seconds"], 90)
        self.assertEqual(value["executable"]["path"], str(self.exe.resolve()))
        self.assertEqual(value["output_root"], str(self.output_root.resolve()))

    def test_png_preview_is_a_real_png(self) -> None:
        width = height = 2
        bgra = bytes([0, 0, 255, 255] * width * height)
        path = self.root / "preview.png"
        live_gfx._write_png(path, bgra, width, height)
        payload = path.read_bytes()
        self.assertTrue(payload.startswith(b"\x89PNG\r\n\x1a\n"))
        self.assertIn(b"IHDR", payload[:32])

    def test_status_levels_are_not_collapsed(self) -> None:
        output = self.root / "experiment"
        output.mkdir()
        raw = output / "checkpoint-after-draw-1.bgra"
        raw.write_bytes(b"\x00" * (4 * 4 * 4))
        checkpoint = {
            "checkpoint": "after-draw",
            "target": {"width": 4, "height": 4},
            "capture": {"raw_file": raw.name},
            "status": {
                "draw_reached": 1,
                "draw_encoded": 1,
                "gpu_completed": 1,
                "pixels_captured": 1,
                "presented": 0,
            },
        }
        (output / "checkpoint-after-draw-1.json").write_text(json.dumps(checkpoint))
        (output / "draw-route.json").write_text(json.dumps({"route": "live"}))
        status = live_gfx._read_status(output)
        self.assertTrue(status["draw_reached"])
        self.assertTrue(status["draw_encoded"])
        self.assertTrue(status["gpu_completed"])
        self.assertTrue(status["pixels_captured"])
        self.assertFalse(status["presented"])
        inspected = live_gfx._inspect(output)
        self.assertEqual(inspected["checkpoints"][0]["checkpoint"], "after-draw")


if __name__ == "__main__":
    unittest.main()
