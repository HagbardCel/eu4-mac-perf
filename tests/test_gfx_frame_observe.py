"""Native tests for the shared per-frame observation stream."""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


@unittest.skipUnless(sys.platform == "darwin", "native C host build")
class FrameObserveHostTests(unittest.TestCase):
    @classmethod
    def build(cls) -> Path:
        cls._temporary = tempfile.TemporaryDirectory()
        binary = Path(cls._temporary.name) / "observe_host"
        built = subprocess.run(
            [
                "clang", "-O1", "-Wall", "-Wextra", "-I", str(ROOT / "benchmark"),
                "-o", str(binary),
                str(ROOT / "tests" / "gfx_frame_observe_host.c"),
                str(ROOT / "benchmark" / "eu4_gfx_frame_observe.c"),
            ],
            capture_output=True, text=True, check=False,
        )
        if built.returncode:
            raise RuntimeError(built.stderr)
        return binary

    @classmethod
    def tearDownClass(cls):
        cls._temporary.cleanup()

    def run_host(self, mode: str) -> tuple[Path, subprocess.CompletedProcess]:
        binary = self.build()
        directory = Path(tempfile.mkdtemp(dir=self._temporary.name))
        ran = subprocess.run([str(binary), str(directory), mode],
                             capture_output=True, text=True, check=False)
        return directory, ran

    def test_records_have_hash_preview_and_composition(self):
        directory, ran = self.run_host("frames")
        self.assertEqual(ran.returncode, 0, ran.stderr)
        lines = (directory / "frame-observe.jsonl").read_text().splitlines()
        self.assertEqual(len(lines), 10)
        records = [json.loads(line) for line in lines]
        self.assertEqual([row["frame_index"] for row in records], list(range(1, 11)))
        self.assertTrue(all(row["schema"] == "gfx-frame-observe/v1" for row in records))
        first = records[0]
        self.assertEqual(first["width"], 8)
        self.assertEqual(first["height"], 6)
        self.assertEqual(first["origin"], "lower_left")
        self.assertEqual(first["draws"], 3)
        self.assertEqual(first["distinct_callers"], 2)
        self.assertTrue(first["gpu_completed"])
        self.assertTrue(first["capture_valid"])
        # Recompute the exact full-frame digest from the host's deterministic fill.
        frame = 1
        payload = bytearray()
        for y in range(6):
            for x in range(8):
                payload += bytes(((frame * 7 + x) & 0xFF, (frame * 11 + y) & 0xFF,
                                  (frame * 13 + x * y) & 0xFF, 255))
        self.assertEqual(first["sha256"], hashlib.sha256(bytes(payload)).hexdigest())
        preview = directory / first["preview_file"]
        self.assertTrue(preview.is_file())
        self.assertEqual(preview.stat().st_size,
                         first["preview_width"] * first["preview_height"] * 4)
        self.assertEqual(hashlib.sha256(preview.read_bytes()).hexdigest(),
                         first["preview_sha256"])

    def test_on_demand_ring_dump_is_exact(self):
        directory, ran = self.run_host("frames")
        self.assertEqual(ran.returncode, 0, ran.stderr)
        dump = json.loads((directory / "captured" / "frame-3.json").read_text())
        self.assertEqual(dump["frame_index"], 3)
        self.assertEqual(dump["backend"], "opengl")
        self.assertEqual(dump["session"], "session-test")
        self.assertEqual(dump["raw_bytes"], dump["width"] * dump["height"] * 4)
        self.assertEqual(dump["draws"], 3)
        self.assertNotEqual(dump["caller_signature"], "0x0000000000000000")
        raw = Path(dump["raw_file"]).read_bytes()
        self.assertEqual(len(raw), dump["raw_bytes"])
        self.assertEqual(hashlib.sha256(raw).hexdigest(), dump["sha256"])
        self.assertTrue((directory / "captured" / "frame-9.bgra").is_file())

    def test_gap_is_accounted_not_hidden(self):
        directory, ran = self.run_host("gap")
        self.assertEqual(ran.returncode, 0, ran.stderr)
        records = [json.loads(line) for line in
                   (directory / "frame-observe.jsonl").read_text().splitlines()]
        self.assertEqual([row["frame_index"] for row in records], [1, 2, 5, 4])
        self.assertEqual(records[2]["skipped_before"], 2)

    def test_metadata_stream_has_no_pixels_and_honours_requests(self):
        directory, ran = self.run_host("metadata")
        self.assertEqual(ran.returncode, 0, ran.stderr)
        records = [json.loads(line) for line in
                   (directory / "frame-observe.jsonl").read_text().splitlines()]
        self.assertEqual(len(records), 5)
        self.assertTrue(all(row["content"] == "metadata" for row in records))
        self.assertTrue(all(row["sha256"] == "" for row in records))
        self.assertTrue(all(row["width"] == 1728 and row["height"] == 1117
                            for row in records))
        self.assertIn("wants3=1", ran.stdout)
        self.assertIn("wants4=0", ran.stdout)
        self.assertEqual(list((directory / "captured").glob("frame-*")), [])
        self.assertEqual(list((directory / "previews").iterdir()), [])

    def test_lockstep_timeout_is_recorded(self):
        directory, ran = self.run_host("lockstep-timeout")
        self.assertEqual(ran.returncode, 0, ran.stderr)
        self.assertIn("waited=1", ran.stdout)
        rows = [json.loads(line) for line in
                (directory / "lockstep-timeout.jsonl").read_text().splitlines()]
        self.assertEqual(rows[0]["schema"], "gfx-lockstep-timeout/v1")
        self.assertEqual(rows[0]["frame_index"], 1)
        self.assertEqual(rows[0]["backend"], "opengl")

    def test_disabled_stream_writes_nothing(self):
        directory, ran = self.run_host("disabled")
        self.assertEqual(ran.returncode, 0, ran.stderr)
        self.assertIn("enabled=0", ran.stdout)
        self.assertFalse((directory / "frame-observe.jsonl").exists())


if __name__ == "__main__":
    unittest.main()
