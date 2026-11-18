"""Same-backend GL control for captured transfers, including versioning."""
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


def _draw(index: int, *, textures=None, target_name=None) -> dict:
    value = {"index": index, "kind": "draw"}
    if target_name is not None:
        value["target"] = {"color_attachment": {"name": int(target_name)}}
    if textures:
        value["textures"] = textures
    return value


def _texture(name: str, file: str, digest: str, *, pixel_format="RGBA8"):
    return {
        "name": int(name),
        "levels": [{
            "width": 2, "height": 2, "pixel_format": pixel_format,
            "file": file, "sha256": digest,
        }],
    }


def make_frame(root: Path, *, source: bytes, destination: bytes,
               destination_event: bool = True,
               events: list[dict] | None = None,
               lifecycle: list[dict] | None = None,
               destination_format: str = "RGBA8",
               write_sequence: bool = True,
               declared_destination_sha: str | None = None,
               write_source: bool = True,
               render_targets: list[str] | None = None) -> Path:
    frame = root / "frame"
    (frame / "blobs").mkdir(parents=True)
    if write_source:
        (frame / "blobs" / "copy-source.bgra").write_bytes(source)
    (frame / "blobs" / "destination.bin").write_bytes(destination)
    if events is None:
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
            events.append(_draw(2, textures=[_texture(
                442, "destination.bin",
                declared_destination_sha or _sha(destination),
                pixel_format=destination_format)]))
    (frame / "frame-events.jsonl").write_text(
        "\n".join(json.dumps(item) for item in events) + "\n")
    if write_sequence:
        if lifecycle is None:
            lifecycle = [{"kind": "mutation", "event": -1,
                          "lifecycle_sequence": 1, "names": ["442"]}]
        (frame / "frame-sequence.json").write_text(json.dumps({
            "schema": "gfx-frame-sequence/v1",
            "session_id": "test", "pid": 1, "sequence_index": 1,
            "capture_frame_id": 1, "complete": True,
            "render_targets": (["442"] if render_targets is None
                               else render_targets),
            "lifecycle": lifecycle,
        }))
    return frame


class GlTransferControlTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        # Source is BGRA; destination is RGBA, so the declared swap must
        # reproduce the source exactly.
        self.source = bytes((1, 2, 3, 255, 4, 5, 6, 255,
                             7, 8, 9, 255, 10, 11, 12, 255))
        self.destination = bytes((3, 2, 1, 255, 6, 5, 4, 255,
                                  9, 8, 7, 255, 12, 11, 10, 255))

    def tearDown(self):
        self.temporary.cleanup()

    def test_one_copy_followed_by_sample_matches(self):
        frame = make_frame(self.root, source=self.source,
                           destination=self.destination)
        report = gfx_gl_control.control_frame(frame)
        self.assertEqual(report["status"], "match")
        self.assertTrue(report["qualified"])
        self.assertEqual(report["checks"][0]["normalization"]["channel_swap"],
                         "bgra_to_rgba")
        self.assertEqual(
            report["checks"][0]["statistics"]["mismatched_pixels"], 0)
        self.assertEqual(report["checks"][0]["provenance"],
                         {"selected_copy_event": 1,
                          "first_consumer_event": 2,
                          "lifecycle": "checked"})

    def test_partial_copy_compares_only_the_copied_region(self):
        source = bytes((1, 2, 3, 255, 4, 5, 6, 255,
                        7, 8, 9, 255, 10, 11, 12, 255))
        # Only destination (0,0) is written; it is the RGBA swap of source
        # (1,1). The other three destination pixels are unrelated.
        destination = bytes((12, 11, 10, 255, 99, 99, 99, 255,
                             88, 88, 88, 255, 77, 77, 77, 255))
        events = [{
            "index": 1, "kind": "resolve",
            "operation": "glCopyTexSubImage2D", "source": "0",
            "destination": "442",
            "source_rect": [1, 1, 1, 1],
            "destination_offset": [0, 0],
            "width": 2, "height": 2,
            "source_image": {"file": "copy-source.bgra",
                             "sha256": _sha(source),
                             "width": 2, "height": 2},
        }, _draw(2, textures=[_texture(
            442, "destination.bin", _sha(destination))])]
        frame = make_frame(self.root, source=source,
                           destination=destination, events=events)
        report = gfx_gl_control.control_frame(frame)
        self.assertEqual(report["status"], "match")
        check = report["checks"][0]
        self.assertEqual(check["copy_region"], {
            "source_x": 1, "source_y": 1, "width": 1, "height": 1,
            "destination_x": 0, "destination_y": 0})
        self.assertEqual(check["statistics"]["pixels"], 1)
        self.assertEqual(check["statistics"]["mismatched_pixels"], 0)

    def test_out_of_bounds_partial_copy_is_unavailable(self):
        events = [{
            "index": 1, "kind": "resolve",
            "operation": "glCopyTexSubImage2D", "source": "0",
            "destination": "442",
            "source_rect": [2, 2, 1, 1],
            "destination_offset": [0, 0],
            "width": 2, "height": 2,
            "source_image": {"file": "copy-source.bgra",
                             "sha256": _sha(self.source),
                             "width": 2, "height": 2},
        }, _draw(2, textures=[_texture(
            442, "destination.bin", _sha(self.destination))])]
        frame = make_frame(self.root, source=self.source,
                           destination=self.destination, events=events)
        report = gfx_gl_control.control_frame(frame)
        self.assertEqual(report["status"], "incomplete")
        self.assertEqual(report["checks"][0]["reason_code"],
                         "copy_region_out_of_bounds")

    def test_lifecycle_coverage_after_the_copy_is_too_late(self):
        frame = make_frame(
            self.root, source=self.source, destination=self.destination,
            lifecycle=[{"kind": "mutation", "event": 3,
                        "lifecycle_sequence": 9, "names": ["442"]}])
        report = gfx_gl_control.control_frame(frame)
        self.assertEqual(report["status"], "incomplete")
        self.assertEqual(report["checks"][0]["reason_code"],
                         "unverified_lifecycle_coverage")

    def test_lifecycle_change_at_the_copy_index_is_not_intervening(self):
        frame = make_frame(
            self.root, source=self.source, destination=self.destination,
            lifecycle=[{"kind": "mutation", "event": 1,
                        "lifecycle_sequence": 6, "names": ["442"]}])
        report = gfx_gl_control.control_frame(frame)
        self.assertEqual(report["status"], "match")
        self.assertEqual(report["checks"][0]["statistics"]["mismatched_pixels"],
                         0)

    def test_lifecycle_change_at_the_consumer_index_is_unavailable(self):
        frame = make_frame(
            self.root, source=self.source, destination=self.destination,
            lifecycle=[{"kind": "mutation", "event": -1,
                        "lifecycle_sequence": 1, "names": ["442"]},
                       {"kind": "mutation", "event": 2,
                        "lifecycle_sequence": 8, "names": ["442"]}])
        report = gfx_gl_control.control_frame(frame)
        self.assertEqual(report["status"], "incomplete")
        self.assertEqual(report["checks"][0]["reason_code"],
                         "lifecycle_mutation")

    def test_rgb16_destination_forces_opaque_alpha(self):
        source = bytes((1, 2, 3, 128, 4, 5, 6, 128,
                        7, 8, 9, 128, 10, 11, 12, 128))
        destination = bytes((3, 2, 1, 255, 6, 5, 4, 255,
                             9, 8, 7, 255, 12, 11, 10, 255))
        events = [{
            "index": 1, "kind": "resolve",
            "operation": "glCopyTexSubImage2D", "source": "0",
            "destination": "442", "internal_format": 32852,
            "source_image": {"file": "copy-source.bgra",
                             "sha256": _sha(source),
                             "width": 2, "height": 2},
        }, _draw(2, textures=[_texture(
            442, "destination.bin", _sha(destination))])]
        frame = make_frame(self.root, source=source,
                           destination=destination, events=events)
        report = gfx_gl_control.control_frame(frame)
        self.assertEqual(report["status"], "match")
        self.assertEqual(report["checks"][0]["normalization"]["alpha"],
                         "force_opaque")

    def test_uncovered_copy_destination_is_unavailable(self):
        frame = make_frame(self.root, source=self.source,
                           destination=self.destination,
                           render_targets=[], lifecycle=[])
        report = gfx_gl_control.control_frame(frame)
        self.assertEqual(report["status"], "incomplete")
        self.assertEqual(report["checks"][0]["reason_code"],
                         "unverified_lifecycle_coverage")

    def test_rgb_destination_forces_opaque_alpha(self):
        source = bytes((1, 2, 3, 128, 4, 5, 6, 128,
                        7, 8, 9, 128, 10, 11, 12, 128))
        destination = bytes((3, 2, 1, 255, 6, 5, 4, 255,
                             9, 8, 7, 255, 12, 11, 10, 255))
        events = [{
            "index": 1, "kind": "resolve",
            "operation": "glCopyTexSubImage2D", "source": "0",
            "destination": "442", "internal_format": 32849,
            "source_image": {"file": "copy-source.bgra",
                             "sha256": _sha(source),
                             "width": 2, "height": 2},
        }, _draw(2, textures=[_texture(
            442, "destination.bin", _sha(destination))])]
        frame = make_frame(self.root, source=source,
                           destination=destination, events=events)
        report = gfx_gl_control.control_frame(frame)
        self.assertEqual(report["status"], "match")
        self.assertEqual(report["checks"][0]["normalization"]["alpha"],
                         "force_opaque")

    def test_unknown_destination_internal_format_is_unavailable(self):
        events = [{
            "index": 1, "kind": "resolve",
            "operation": "glCopyTexSubImage2D", "source": "0",
            "destination": "442", "internal_format": 0x9999,
            "source_image": {"file": "copy-source.bgra",
                             "sha256": _sha(self.source),
                             "width": 2, "height": 2},
        }, _draw(2, textures=[_texture(
            442, "destination.bin", _sha(self.destination))])]
        frame = make_frame(self.root, source=self.source,
                           destination=self.destination, events=events)
        report = gfx_gl_control.control_frame(frame)
        self.assertEqual(report["status"], "incomplete")
        self.assertEqual(report["checks"][0]["reason_code"],
                         "unsupported_internal_format")

    def test_two_copies_before_sample_make_the_first_unavailable(self):
        source_a = self.source
        source_b = bytes((21, 22, 23, 255, 24, 25, 26, 255,
                          27, 28, 29, 255, 30, 31, 32, 255))
        destination_b = bytes((23, 22, 21, 255, 26, 25, 24, 255,
                               29, 28, 27, 255, 32, 31, 30, 255))
        events = [{
            "index": 1, "kind": "resolve",
            "operation": "glCopyTexSubImage2D", "source": "0",
            "destination": "442",
            "source_image": {"file": "copy-source-a.bgra",
                             "sha256": _sha(source_a),
                             "width": 2, "height": 2},
        }, {
            "index": 2, "kind": "resolve",
            "operation": "glCopyTexSubImage2D", "source": "0",
            "destination": "442",
            "source_image": {"file": "copy-source-b.bgra",
                             "sha256": _sha(source_b),
                             "width": 2, "height": 2},
        }, _draw(3, textures=[_texture(
            442, "destination.bin", _sha(destination_b))])]
        frame = make_frame(self.root, source=source_a,
                           destination=destination_b, events=events)
        (frame / "blobs" / "copy-source-a.bgra").write_bytes(source_a)
        (frame / "blobs" / "copy-source-b.bgra").write_bytes(source_b)
        report = gfx_gl_control.control_frame(frame)
        self.assertEqual(report["status"], "incomplete")
        self.assertEqual(report["checks"][0]["status"], "unavailable")
        self.assertEqual(report["checks"][0]["reason_code"],
                         "intervening_copy")
        self.assertEqual(report["checks"][1]["status"], "match")

    def test_intervening_write_makes_the_copy_unavailable(self):
        events = [{
            "index": 1, "kind": "resolve",
            "operation": "glCopyTexSubImage2D", "source": "0",
            "destination": "442",
            "source_image": {"file": "copy-source.bgra",
                             "sha256": _sha(self.source),
                             "width": 2, "height": 2},
        }, _draw(2, target_name="442"),
           _draw(3, textures=[_texture(
               442, "destination.bin", _sha(self.destination))])]
        frame = make_frame(self.root, source=self.source,
                           destination=self.destination, events=events)
        report = gfx_gl_control.control_frame(frame)
        self.assertEqual(report["status"], "incomplete")
        self.assertEqual(report["checks"][0]["reason_code"],
                         "intervening_write")

    def test_incorrect_bytes_in_the_correct_version_are_a_difference(self):
        changed = bytearray(self.destination)
        changed[0] ^= 0xFF
        frame = make_frame(self.root, source=self.source,
                           destination=bytes(changed))
        report = gfx_gl_control.control_frame(frame)
        self.assertEqual(report["status"], "different")
        self.assertFalse(report["qualified"])
        self.assertEqual(report["checks"][0]["status"], "different")

    def test_missing_lifecycle_provenance_is_incomplete(self):
        frame = make_frame(self.root, source=self.source,
                           destination=self.destination,
                           write_sequence=False)
        report = gfx_gl_control.control_frame(frame)
        self.assertEqual(report["status"], "incomplete")
        self.assertEqual(report["checks"][0]["reason_code"],
                         "missing_lifecycle_provenance")

    def test_unsupported_channels_are_unavailable(self):
        frame = make_frame(self.root, source=self.source,
                           destination=self.destination,
                           destination_format="RGB10")
        report = gfx_gl_control.control_frame(frame)
        self.assertEqual(report["status"], "incomplete")
        self.assertEqual(report["checks"][0]["reason_code"],
                         "unsupported_channels")

    def test_missing_source_is_unavailable(self):
        frame = make_frame(self.root, source=self.source,
                           destination=self.destination,
                           write_source=False)
        report = gfx_gl_control.control_frame(frame)
        self.assertEqual(report["status"], "incomplete")
        self.assertEqual(report["checks"][0]["reason_code"],
                         "missing_source")

    def test_missing_destination_sample_is_unavailable(self):
        frame = make_frame(self.root, source=self.source,
                           destination=self.destination,
                           destination_event=False)
        report = gfx_gl_control.control_frame(frame)
        self.assertEqual(report["status"], "incomplete")
        self.assertEqual(report["checks"][0]["reason_code"], "no_consumer")

    def test_digest_corruption_is_invalid(self):
        frame = make_frame(self.root, source=self.source,
                           destination=self.destination,
                           declared_destination_sha="0" * 64)
        report = gfx_gl_control.control_frame(frame)
        self.assertEqual(report["status"], "invalid")
        self.assertEqual(report["checks"][0]["reason_code"],
                         "destination_digest")

    def test_lifecycle_mutation_between_copy_and_sample_is_unavailable(self):
        frame = make_frame(
            self.root, source=self.source, destination=self.destination,
            lifecycle=[{"kind": "mutation", "event": -1,
                        "lifecycle_sequence": 1, "names": ["442"]},
                       {"kind": "mutation", "event": 2,
                        "lifecycle_sequence": 7, "names": ["442"]}])
        report = gfx_gl_control.control_frame(frame)
        self.assertEqual(report["status"], "incomplete")
        self.assertEqual(report["checks"][0]["reason_code"],
                         "lifecycle_mutation")

    def test_clear_between_copy_and_sample_is_unavailable(self):
        events = [{
            "index": 1, "kind": "resolve",
            "operation": "glCopyTexSubImage2D", "source": "0",
            "destination": "442",
            "source_image": {"file": "copy-source.bgra",
                             "sha256": _sha(self.source),
                             "width": 2, "height": 2},
        }, {"index": 2, "kind": "clear",
            "target": {"color_attachment": {"name": 442}}},
           _draw(3, textures=[_texture(
               442, "destination.bin", _sha(self.destination))])]
        frame = make_frame(self.root, source=self.source,
                           destination=self.destination, events=events)
        report = gfx_gl_control.control_frame(frame)
        self.assertEqual(report["status"], "incomplete")
        self.assertEqual(report["checks"][0]["reason_code"],
                         "intervening_clear")

    def test_no_control_is_unavailable_not_different(self):
        events = [_draw(1, textures=[_texture(
            442, "destination.bin", _sha(self.destination))])]
        frame = make_frame(self.root, source=self.source,
                           destination=self.destination, events=events)
        report = gfx_gl_control.control_frame(frame)
        self.assertEqual(report["status"], "unavailable")
        self.assertFalse(report["qualified"])


if __name__ == "__main__":
    unittest.main()


class AttachmentNamespaceControlTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.source = bytes((1, 2, 3, 255, 4, 5, 6, 255,
                             7, 8, 9, 255, 10, 11, 12, 255))
        self.destination = bytes((3, 2, 1, 255, 6, 5, 4, 255,
                                  9, 8, 7, 255, 12, 11, 10, 255))

    def tearDown(self):
        self.temporary.cleanup()

    def test_renderbuffer_write_does_not_invalidate_texture_copy(self):
        events = [{
            "index": 1, "kind": "resolve",
            "operation": "glCopyTexSubImage2D", "source": "0",
            "destination": "442", "internal_format": 32856,
            "source_image": {"file": "copy-source.bgra",
                             "sha256": _sha(self.source),
                             "width": 2, "height": 2},
        }, {
            "index": 2, "kind": "draw",
            "target": {"color_attachment": {"type": "renderbuffer",
                                            "name": 442}},
        }, _draw(3, textures=[_texture(
            442, "destination.bin", _sha(self.destination))])]
        frame = make_frame(self.root, source=self.source,
                           destination=self.destination, events=events)
        report = gfx_gl_control.control_frame(frame)
        self.assertEqual(report["status"], "match")

    def test_texture_write_with_same_id_invalidates(self):
        events = [{
            "index": 1, "kind": "resolve",
            "operation": "glCopyTexSubImage2D", "source": "0",
            "destination": "442", "internal_format": 32856,
            "source_image": {"file": "copy-source.bgra",
                             "sha256": _sha(self.source),
                             "width": 2, "height": 2},
        }, {
            "index": 2, "kind": "draw",
            "target": {"color_attachment": {"type": "texture",
                                            "name": 442}},
        }, _draw(3, textures=[_texture(
            442, "destination.bin", _sha(self.destination))])]
        frame = make_frame(self.root, source=self.source,
                           destination=self.destination, events=events)
        report = gfx_gl_control.control_frame(frame)
        self.assertEqual(report["status"], "incomplete")
        self.assertEqual(report["checks"][0]["reason_code"],
                         "intervening_write")

    def test_texture_clear_invalidates(self):
        events = [{
            "index": 1, "kind": "resolve",
            "operation": "glCopyTexSubImage2D", "source": "0",
            "destination": "442", "internal_format": 32856,
            "source_image": {"file": "copy-source.bgra",
                             "sha256": _sha(self.source),
                             "width": 2, "height": 2},
        }, {"index": 2, "kind": "clear",
            "target": {"color_attachment": {"type": "texture",
                                            "name": 442}}},
           _draw(3, textures=[_texture(
               442, "destination.bin", _sha(self.destination))])]
        frame = make_frame(self.root, source=self.source,
                           destination=self.destination, events=events)
        report = gfx_gl_control.control_frame(frame)
        self.assertEqual(report["status"], "incomplete")
        self.assertEqual(report["checks"][0]["reason_code"],
                         "intervening_clear")

    def test_renderbuffer_clear_does_not_invalidate_texture(self):
        events = [{
            "index": 1, "kind": "resolve",
            "operation": "glCopyTexSubImage2D", "source": "0",
            "destination": "442", "internal_format": 32856,
            "source_image": {"file": "copy-source.bgra",
                             "sha256": _sha(self.source),
                             "width": 2, "height": 2},
        }, {"index": 2, "kind": "clear",
            "target": {"color_attachment": {"type": "renderbuffer",
                                            "name": 442}}},
           _draw(3, textures=[_texture(
               442, "destination.bin", _sha(self.destination))])]
        frame = make_frame(self.root, source=self.source,
                           destination=self.destination, events=events)
        report = gfx_gl_control.control_frame(frame)
        self.assertEqual(report["status"], "match")
