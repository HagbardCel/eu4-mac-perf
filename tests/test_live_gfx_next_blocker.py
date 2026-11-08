#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "benchmark"))

from automation.next_blocker import classify  # noqa: E402

GL_RUN = ROOT / "results" / "20261102T120220Z-metal-init"
DRAW_RUN = ROOT / "results" / "20261102T213704Z-metal-init"


class NextBlockerTests(unittest.TestCase):
    @unittest.skipUnless(GL_RUN.is_dir(), "recorded glGenTextures session is absent")
    def test_recorded_gl_stop_is_actionable(self):
        result = classify(GL_RUN)
        self.assertEqual(result["status"], "actionable")
        self.assertEqual(result["kind"], "unsupported_gl_dependency")
        self.assertEqual(result["symbol"], "glGenTextures")
        self.assertEqual(result["caller_rva"], "0x15ec4ed")
        self.assertEqual(result["owner"]["name"], "initialize_texture")
        self.assertTrue(result["attribution"]["session_ranges_known"])
        self.assertFalse(result["attribution"]["installed_in_session"])
        self.assertEqual(result["next_step"], "reconstruct")
        self.assertTrue(result["evidence"]["stop.json"].endswith("stop.json"))

    @unittest.skipUnless(DRAW_RUN.is_dir(), "recorded draw session is absent")
    def test_recorded_draw_reached_is_evidence(self):
        result = classify(DRAW_RUN)
        self.assertEqual(result["status"], "needs_evidence")
        self.assertEqual(result["kind"], "draw_nonvisible")
        self.assertEqual(result["symbol"], "_GfxDraw")
        self.assertEqual(result["next_step"], "investigate")

    def test_missing_run_is_unavailable(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = classify(Path(tmp) / "absent")
            self.assertEqual(result["status"], "unavailable")
            self.assertEqual(result["kind"], "missing_run")
            self.assertNotIn("owner", result)

    def test_incomplete_run_does_not_guess(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "metadata.json").write_text("{}\n")
            result = classify(root)
            self.assertEqual(result["status"], "unavailable")
            self.assertNotIn("owner", result)

    @unittest.skipUnless(GL_RUN.is_dir(), "recorded glGenTextures session is absent")
    def test_crash_is_not_attributed_to_a_function(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "crash"
            shutil.copytree(GL_RUN, root)
            metadata = json.loads((root / "metadata.json").read_text())
            metadata["exit_code"] = -11
            metadata["launcher_signaled"] = False
            (root / "metadata.json").write_text(json.dumps(metadata) + "\n")
            result = classify(root)
            self.assertEqual(result["status"], "needs_evidence")
            self.assertEqual(result["kind"], "attribution_ambiguous")
            self.assertNotIn("owner", result)


def _draw_run(
    root: Path,
    *,
    symbol: str = "_GfxDraw",
    count: int = 6,
    corrupt_pixels: bool = False,
    receipt_pid: int | None = None,
) -> Path:
    shutil.copytree(GL_RUN, root)
    meta = json.loads((root / "metadata.json").read_text())
    stop = json.loads((root / "stop.json").read_text())
    draw = {
        "symbol": symbol,
        "invocation_id": 1,
        "provenance": "game",
        "image": meta["gog"]["executable"],
        "caller_rva": "0xedfcd1",
        "pid": meta["pid"],
        "thread": stop["thread"],
        "effective_count": count,
        "shader_ptr": "0x1234567812345678",
    }
    (root / "draw_invocation.json").write_text(json.dumps(draw) + "\n")
    pixels = (root / "pixels")
    pixels.mkdir()
    data = b"\x01\x02\x03\x04"
    (pixels / "frame.bin").write_bytes(data)
    digest = hashlib.sha256(data).hexdigest()
    if corrupt_pixels:
        (pixels / "frame.bin").write_bytes(b"\xff\xff\xff\xff")
    receipt = {
        "schema": "gfx-draw-executed/v0",
        "symbol": symbol,
        "pid": meta["pid"] if receipt_pid is None else receipt_pid,
        "image": meta["gog"]["executable"],
        "executable_sha256": meta["gog"]["executable_sha256"],
        "provenance": "game",
        "invocation_id": 1,
        "caller_rva": "0xedfcd1",
        "thread": stop["thread"],
        "effective_count": count,
        "mode": 5,
        "shader_non_null": True,
        "draw_reached": True,
        "packet_ready": True,
        "draw_executed": True,
        "metal_completed": True,
        "engine_path_complete": True,
        "non_clear": True,
        "pixels_file": "pixels/frame.bin",
        "pixels_size": len(data),
        "pixels_sha256": digest,
    }
    (root / "draw_executed.json").write_text(json.dumps(receipt) + "\n")
    return root


class DrawReceiptTests(unittest.TestCase):
    @unittest.skipUnless(GL_RUN.is_dir(), "recorded glGenTextures session is absent")
    def test_valid_receipt_survives_later_blocker(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = _draw_run(Path(tmp) / "run")
            result = classify(root)
            self.assertEqual(result["status"], "advanced")
            self.assertEqual(result["milestone"], "draw_executed")
            self.assertEqual(result["symbol"], "_GfxDraw")
            self.assertEqual(result["next_blocker"]["status"], "actionable")
            self.assertEqual(result["next_blocker"]["symbol"], "glGenTextures")
            self.assertTrue(result["draw"]["invocation_id"] == 1)

    @unittest.skipUnless(GL_RUN.is_dir(), "recorded glGenTextures session is absent")
    def test_indexed_draw_symbol_is_preserved(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = _draw_run(Path(tmp) / "run", symbol="_GfxDrawIndexed")
            result = classify(root)
            self.assertEqual(result["status"], "advanced")
            self.assertEqual(result["symbol"], "_GfxDrawIndexed")
            self.assertEqual(result["draw"]["symbol"], "_GfxDrawIndexed")

    @unittest.skipUnless(GL_RUN.is_dir(), "recorded glGenTextures session is absent")
    def test_invalid_pid_does_not_advance(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = _draw_run(Path(tmp) / "run", receipt_pid=1)
            result = classify(root)
            self.assertNotEqual(result["status"], "advanced")
            self.assertIn("pid", result["draw_receipt_error"]["fields"])

    @unittest.skipUnless(GL_RUN.is_dir(), "recorded glGenTextures session is absent")
    def test_pixel_file_must_match_receipt(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = _draw_run(Path(tmp) / "run", corrupt_pixels=True)
            result = classify(root)
            self.assertNotEqual(result["status"], "advanced")
            self.assertIn(
                "pixels_file_sha256", result["draw_receipt_error"]["fields"]
            )

    @unittest.skipUnless(GL_RUN.is_dir(), "recorded glGenTextures session is absent")
    def test_unknown_session_inventory_is_not_actionable(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "run"
            shutil.copytree(GL_RUN, root)
            meta = json.loads((root / "metadata.json").read_text())
            meta.pop("probe_sha256", None)
            meta.pop("gfx_entry_ranges", None)
            (root / "metadata.json").write_text(json.dumps(meta) + "\n")
            result = classify(root)
            self.assertEqual(result["status"], "needs_evidence")
            self.assertEqual(result["kind"], "replacement_inventory_unknown")

    @unittest.skipUnless(GL_RUN.is_dir(), "recorded glGenTextures session is absent")
    def test_stale_session_range_is_not_actionable(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / "run"
            shutil.copytree(GL_RUN, root)
            meta = json.loads((root / "metadata.json").read_text())
            meta["gfx_entry_ranges"] = [[0x15EC4E0, 0x15EC500]]
            (root / "metadata.json").write_text(json.dumps(meta) + "\n")
            result = classify(root)
            self.assertEqual(result["status"], "needs_evidence")
            self.assertEqual(result["kind"], "stale_replacement_inventory")
            self.assertFalse(result["attribution"]["matches_current_owner"])


if __name__ == "__main__":
    unittest.main()
