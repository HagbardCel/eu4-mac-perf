"""Same-backend GL control for captured transfers."""
from __future__ import annotations

import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from automation import gfx_gl_control  # noqa: E402


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def make_frame(root: Path, *, source: bytes, destination: bytes,
               destination_event: bool = True) -> Path:
    frame = root / "frame"
    (frame / "blobs").mkdir(parents=True)
    (frame / "blobs" / "copy-source.bgra").write_bytes(source)
    (frame / "blobs" / "destination.bin").write_bytes(destination)
    events = [{
        "index": 1,
        "kind": "resolve",
        "operation": "glCopyTexSubImage2D",
        "source": "0",
        "destination": "442",
        "source_image": {
            "file": "copy-source.bgra", "sha256": _sha(source),
            "width": 2, "height": 2, "bytes": len(source),
        },
    }]
    if destination_event:
        events.append({
            "index": 2,
            "kind": "draw",
            "textures": [{
                "name": 442,
                "levels": [{
                    "width": 2, "height": 2, "pixel_format": "RGBA8",
                    "file": "destination.bin", "sha256": _sha(destination),
                }],
            }],
        })
    (frame / "frame-events.jsonl").write_text(
        "\n".join(json.dumps(item) for item in events) + "\n")
    return frame


class GlTransferControlTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        # The source is BGRA; the destination is RGBA, so the declared swap
        # must reproduce the source exactly.
        self.source = bytes((1, 2, 3, 255, 4, 5, 6, 255,
                             7, 8, 9, 255, 10, 11, 12, 255))
        self.destination = bytes((3, 2, 1, 255, 6, 5, 4, 255,
                                  9, 8, 7, 255, 12, 11, 10, 255))

    def tearDown(self):
        self.temporary.cleanup()

    def test_zero_differing_pixels_after_declared_normalization(self):
        frame = make_frame(self.root, source=self.source,
                           destination=self.destination)
        report = gfx_gl_control.control_frame(frame)
        self.assertEqual(report["status"], "match")
        self.assertTrue(report["qualified"])
        self.assertEqual(report["checks"][0]["normalization"]["channel_swap"],
                         "bgra_to_rgba")
        self.assertEqual(
            report["checks"][0]["statistics"]["mismatched_pixels"], 0)

    def test_one_changed_pixel_fails_the_control(self):
        changed = bytearray(self.destination)
        changed[0] ^= 0xFF
        frame = make_frame(self.root, source=self.source,
                           destination=bytes(changed))
        report = gfx_gl_control.control_frame(frame)
        self.assertEqual(report["status"], "different")
        self.assertFalse(report["qualified"])

    def test_missing_destination_fails_closed(self):
        frame = make_frame(self.root, source=self.source,
                           destination=self.destination,
                           destination_event=False)
        with self.assertRaises(gfx_gl_control.ControlError) as raised:
            gfx_gl_control.control_frame(frame)
        self.assertEqual(raised.exception.kind, "missing_destination")

    def test_missing_source_fails_closed(self):
        frame = make_frame(self.root, source=self.source,
                           destination=self.destination)
        (frame / "blobs" / "copy-source.bgra").unlink()
        with self.assertRaises(gfx_gl_control.ControlError) as raised:
            gfx_gl_control.control_frame(frame)
        self.assertEqual(raised.exception.kind, "missing_source")


if __name__ == "__main__":
    unittest.main()
