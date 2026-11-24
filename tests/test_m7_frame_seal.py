"""PR 1 frame-seal contract: authoritative handoff validation.

The native producer scenarios are exercised in ``test_gfx_frame_trace_writer``.
This module validates the same immutable artifacts from Python and the capture
CLI's opt-in wiring.  No EU4 process or GPU is involved.
"""
from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
import sys

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from automation import m6_frame  # noqa: E402


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _write_frame(directory: Path, *, sealed: bool = True,
                 operation_count: int = 4) -> dict:
    directory.mkdir(parents=True, exist_ok=True)
    reference = bytes((index * 7) & 0xFF for index in range(4 * 4 * 4))
    (directory / "frame-reference.bgra").write_bytes(reference)
    blob = b"attribute-payload"
    (directory / "blobs").mkdir(exist_ok=True)
    (directory / "blobs" / "attr.bin").write_bytes(blob)
    events = [
        {
            "kind": "draw",
            "draw": {"entry": "_GfxDraw", "caller_rva": "0x1000"},
            "attributes": [
                {
                    "name": "in_POSITION",
                    "data": "attr.bin",
                    "sha256": _sha(blob),
                    "bytes": len(blob),
                }
            ],
        },
        {"kind": "clear", "flags": 1},
        {"kind": "draw", "draw": {"entry": "_GfxDraw", "caller_rva": "0x1000"}},
        {"kind": "resolve", "operation": "glCopyTexSubImage2D"},
    ]
    if operation_count != len(events):
        events = events[:operation_count]
    events_bytes = "".join(json.dumps(event) + "\n" for event in events).encode()
    (directory / "frame-events.jsonl").write_bytes(events_bytes)
    manifest = {
        "schema": "gfx-frame-trace/v1",
        "complete": True,
        "reason": "present",
        "truncated": False,
        "stream_validated": True,
        "pid": 4242,
        "present_index": 2,
        "scene_index": 2,
        "events": len(events),
        "gl": {"vendor": "Apple", "renderer": "test", "version": "2.1"},
        "frame": {
            "width": 4,
            "height": 4,
            "origin": "lower_left",
            "pixel_format": "BGRA8",
            "row_stride": 16,
            "reference": "frame-reference.bgra",
            "reference_bytes": len(reference),
            "reference_sha256": _sha(reference),
        },
        "selection": {"callers": [], "min_draws": 0, "occurrence": 1,
                      "max_frame_draws": 0, "at_frame": None,
                      "boundary": "scene", "required": False},
        "seed": {"width": 0, "height": 0},
        "blobs": 0,
    }
    manifest_bytes = (json.dumps(manifest) + "\n").encode()
    (directory / "frame-trace.json").write_bytes(manifest_bytes)
    seal = {
        "schema": "gfx-frame-seal/v1",
        "sealed": sealed,
        "struct_size": 2256,
        "abi_version": 2,
        "capture_frame_id": 7,
        "session_id": "session-1",
        "pid": 4242,
        "process_start_ns": 100,
        "boundary": "scene",
        "present_index": 2,
        "scene_index": 2,
        "operation_count": len(events),
        "draw_count": sum(1 for event in events if event["kind"] == "draw"),
        "clear_count": sum(1 for event in events if event["kind"] == "clear"),
        "copy_count": sum(1 for event in events if event["kind"] == "resolve"),
        "event_stream_file": "frame-events.jsonl",
        "event_stream_bytes": len(events_bytes),
        "event_stream_sha256": _sha(events_bytes),
        "event_stream_verified": True,
        "width": 4,
        "height": 4,
        "pixel_format": "BGRA8",
        "complete": sealed,
        "trace_complete": True,
        "trace_integrity": True,
        "readback_ok": True,
        "reference_file": "frame-reference.bgra",
        "reference_bytes": len(reference),
        "reference_sha256": _sha(reference),
        "manifest_file": "frame-trace.json",
        "manifest_sha256": _sha(manifest_bytes),
        "capture_dir": str(directory),
        "gpu_status": "gl_readpixels_synchronous",
        "unsupported_dependencies": "",
        "failure_reason": "" if sealed else "draw_cap",
        "readback_ns": 1,
        "finalize_ns": 2,
        "seal_ns": 3,
    }
    (directory / "frame-seal.json").write_text(json.dumps(seal) + "\n")
    return seal


def _write_notification(output: Path, seal: dict, **overrides) -> dict:
    notification = {
        "schema": "gfx-frame-seal-notification/v1",
        "notification_index": 1,
        "pid": seal["pid"],
        "capture_frame_id": seal["capture_frame_id"],
        "session_id": seal["session_id"],
        "manifest_sha256": seal["manifest_sha256"],
        "reference_sha256": seal["reference_sha256"],
        "operation_count": seal["operation_count"],
        "draw_count": seal["draw_count"],
        "clear_count": seal["clear_count"],
        "copy_count": seal["copy_count"],
        "verified_manifest": True,
        "verified_reference": True,
    }
    notification.update(overrides)
    (output / "frame-seal-notification.json").write_text(
        json.dumps(notification) + "\n"
    )
    return notification


class FrameSealValidationTests(unittest.TestCase):
    def test_valid_seal_loads_with_the_existing_trace_loader(self):
        with tempfile.TemporaryDirectory() as temporary:
            frame = Path(temporary) / "frame"
            seal = _write_frame(frame)
            loaded = m6_frame.load_seal(frame)
            self.assertEqual(loaded["capture_frame_id"], seal["capture_frame_id"])
            trace = m6_frame.load_trace(frame)
            self.assertEqual(len(trace["_events"]), 4)

    def test_legacy_m6_frame_without_a_seal_still_loads(self):
        candidates = [
            ROOT / ".local/m6/frames/menu-occ300/frame",
            ROOT.parent / "eu4-mac-perf/.local/m6/frames/menu-occ300/frame",
        ]
        fixture = next((path for path in candidates if (path / "frame-trace.json").is_file()), None)
        if fixture is None:
            self.skipTest("canonical M6 frame is absent in this worktree")
        trace = m6_frame.load_trace(fixture)
        self.assertGreater(len(trace["_events"]), 0)
        self.assertFalse((fixture / "frame-seal.json").exists())

    def test_unsealed_frame_is_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            frame = Path(temporary) / "frame"
            _write_frame(frame, sealed=False)
            with self.assertRaisesRegex(m6_frame.FrameError, "not sealed"):
                m6_frame.load_seal(frame)

    def test_manifest_or_reference_tamper_is_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            frame = Path(temporary) / "frame"
            _write_frame(frame)
            with (frame / "frame-trace.json").open("a") as handle:
                handle.write(" ")
            with self.assertRaisesRegex(m6_frame.FrameError, "manifest digest"):
                m6_frame.load_seal(frame)
            _write_frame(frame)
            (frame / "frame-reference.bgra").write_bytes(b"\0" * 64)
            with self.assertRaisesRegex(m6_frame.FrameError,
                                        "reference digest"):
                m6_frame.load_seal(frame)

    def test_capture_directory_mismatch_is_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            frame = Path(temporary) / "frame"
            _write_frame(frame)
            seal = json.loads((frame / "frame-seal.json").read_text())
            seal["capture_dir"] = str(Path(temporary) / "elsewhere")
            (frame / "frame-seal.json").write_text(json.dumps(seal))
            with self.assertRaisesRegex(m6_frame.FrameError, "capture directory"):
                m6_frame.load_seal(frame)

    def test_invalid_abi_or_escaping_member_path_is_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            frame = Path(temporary) / "frame"
            _write_frame(frame)
            seal = json.loads((frame / "frame-seal.json").read_text())
            seal["abi_version"] = 3
            (frame / "frame-seal.json").write_text(json.dumps(seal))
            with self.assertRaisesRegex(m6_frame.FrameError, "ABI"):
                m6_frame.load_seal(frame)
            _write_frame(frame)
            seal = json.loads((frame / "frame-seal.json").read_text())
            seal["reference_file"] = "../outside.bgra"
            (frame / "frame-seal.json").write_text(json.dumps(seal))
            with self.assertRaisesRegex(m6_frame.FrameError, "path is invalid"):
                m6_frame.load_seal(frame)

    def test_operation_accounting_mismatch_is_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            frame = Path(temporary) / "frame"
            _write_frame(frame)
            seal = json.loads((frame / "frame-seal.json").read_text())
            seal["draw_count"] = 99
            (frame / "frame-seal.json").write_text(json.dumps(seal))
            with self.assertRaisesRegex(m6_frame.FrameError, "draw_count"):
                m6_frame.load_seal(frame)

    def test_missing_manifest_or_reference_is_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            frame = Path(temporary) / "frame"
            _write_frame(frame)
            (frame / "frame-trace.json").unlink()
            with self.assertRaisesRegex(m6_frame.FrameError, "manifest is absent"):
                m6_frame.load_seal(frame)
            _write_frame(frame)
            (frame / "frame-reference.bgra").unlink()
            with self.assertRaisesRegex(m6_frame.FrameError,
                                        "reference pixels are absent"):
                m6_frame.load_seal(frame)

    def test_seal_rejects_incomplete_trace_manifest(self):
        with tempfile.TemporaryDirectory() as temporary:
            frame = Path(temporary) / "frame"
            _write_frame(frame)
            manifest = json.loads((frame / "frame-trace.json").read_text())
            manifest["complete"] = False
            data = (json.dumps(manifest) + "\n").encode()
            (frame / "frame-trace.json").write_bytes(data)
            seal = json.loads((frame / "frame-seal.json").read_text())
            seal["manifest_sha256"] = _sha(data)
            (frame / "frame-seal.json").write_text(json.dumps(seal))
            with self.assertRaisesRegex(m6_frame.FrameError, "incomplete"):
                m6_frame.load_seal(frame)

    def test_event_field_change_invalidates_the_seal(self):
        with tempfile.TemporaryDirectory() as temporary:
            frame = Path(temporary) / "frame"
            _write_frame(frame)
            events_path = frame / "frame-events.jsonl"
            data = events_path.read_bytes()
            self.assertIn(b'"0x1000"', data)
            events_path.write_bytes(data.replace(b'"0x1000"', b'"0x1001"', 1))
            with self.assertRaisesRegex(m6_frame.FrameError, "event stream"):
                m6_frame.load_seal(frame)

    def test_truncated_or_appended_event_stream_invalidates_the_seal(self):
        with tempfile.TemporaryDirectory() as temporary:
            frame = Path(temporary) / "frame"
            _write_frame(frame)
            events_path = frame / "frame-events.jsonl"
            original = events_path.read_bytes()
            events_path.write_bytes(original[:-1])
            with self.assertRaisesRegex(m6_frame.FrameError, "event stream"):
                m6_frame.load_seal(frame)
            _write_frame(frame)
            (frame / "frame-events.jsonl").write_bytes(original + b"\n")
            with self.assertRaisesRegex(m6_frame.FrameError, "event stream"):
                m6_frame.load_seal(frame)

    def test_missing_event_stream_invalidates_the_seal(self):
        with tempfile.TemporaryDirectory() as temporary:
            frame = Path(temporary) / "frame"
            _write_frame(frame)
            (frame / "frame-events.jsonl").unlink()
            with self.assertRaisesRegex(m6_frame.FrameError, "event stream"):
                m6_frame.load_seal(frame)

    def test_modified_referenced_blob_is_rejected_before_qualification(self):
        with tempfile.TemporaryDirectory() as temporary:
            frame = Path(temporary) / "frame"
            _write_frame(frame)
            (frame / "blobs" / "attr.bin").write_bytes(b"tampered")
            with self.assertRaisesRegex(m6_frame.FrameError, "blob"):
                m6_frame.load_seal(frame)

    def test_notification_must_be_single_and_verified(self):
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary)
            frame = output / "frame"
            seal = _write_frame(frame)
            _write_notification(output, seal)
            self.assertEqual(
                m6_frame.load_notification(output)["notification_index"], 1
            )
            _write_notification(output, seal, notification_index=2)
            with self.assertRaisesRegex(m6_frame.FrameError,
                                        "more than one"):
                m6_frame.load_notification(output)
            _write_notification(output, seal, verified_reference=False)
            with self.assertRaisesRegex(m6_frame.FrameError,
                                        "verify the sealed reference"):
                m6_frame.load_notification(output)


class CaptureSealWiringTests(unittest.TestCase):
    def _args(self, output: Path, *extra: str):
        return m6_frame._parser().parse_args(
            ["capture", "--output", str(output), *extra]
        )

    def test_seal_is_disabled_by_default(self):
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "run"
            args = self._args(output)
            captured = {}

            def fake_run_ops(**kwargs):
                captured.update(kwargs["env_extra"])
                frame = output / "frame"
                _write_frame(frame)
                (frame / "frame-seal.json").unlink()
                return {"pid": 4242, "session": "s", "display": None}

            with mock.patch.object(
                __import__("eu4_gfx_startup_trace", fromlist=["x"]),
                "preflight_ops",
                return_value={},
            ), mock.patch.object(
                __import__("eu4_gfx_startup_trace", fromlist=["x"]),
                "run_ops",
                side_effect=fake_run_ops,
            ), mock.patch.object(
                m6_frame, "coverage_report", return_value={"ok": True}
            ):
                self.assertEqual(m6_frame.capture(args), 0)
            self.assertNotIn("EU4_GFX_FRAME_SEAL", captured)
            self.assertFalse((output / "frame" / "frame-seal.json").exists())

    def test_seal_flag_enables_bounded_handoff_and_validates_notification(self):
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "run"
            args = self._args(
                output, "--seal", "--seal-timeout-ms", "1234"
            )
            captured = {}

            def fake_run_ops(**kwargs):
                captured.update(kwargs["env_extra"])
                frame = output / "frame"
                seal = _write_frame(frame)
                if captured.get("EU4_GFX_FRAME_SEAL_LOG") == "1":
                    _write_notification(output, seal)
                return {"pid": 4242, "session": "s", "display": None}

            with mock.patch.object(
                __import__("eu4_gfx_startup_trace", fromlist=["x"]),
                "preflight_ops",
                return_value={},
            ), mock.patch.object(
                __import__("eu4_gfx_startup_trace", fromlist=["x"]),
                "run_ops",
                side_effect=fake_run_ops,
            ), mock.patch.object(
                m6_frame, "coverage_report", return_value={"ok": True}
            ):
                self.assertEqual(m6_frame.capture(args), 0)
            self.assertEqual(captured["EU4_GFX_FRAME_SEAL"], "1")
            self.assertEqual(captured["EU4_GFX_FRAME_SEAL_LOG"], "1")
            self.assertEqual(captured["EU4_GFX_FRAME_TRACE_TIMEOUT_MS"], "1234")
            report = json.loads((output / "capture-report.json").read_text())
            self.assertEqual(report["seal"]["capture_frame_id"], 7)
            self.assertEqual(report["notification"]["notification_index"], 1)

    def test_seal_log_without_seal_is_refused(self):
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "run"
            args = self._args(output, "--seal-log")
            with mock.patch.object(
                __import__("eu4_gfx_startup_trace", fromlist=["x"]),
                "preflight_ops",
                return_value={},
            ):
                self.assertEqual(m6_frame.capture(args), 2)
            self.assertFalse(output.exists())

    def test_missing_notification_fails_the_capture(self):
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "run"
            args = self._args(output, "--seal")

            def fake_run_ops(**kwargs):
                _write_frame(output / "frame")
                return {"pid": 4242, "session": "s", "display": None}

            with mock.patch.object(
                __import__("eu4_gfx_startup_trace", fromlist=["x"]),
                "preflight_ops",
                return_value={},
            ), mock.patch.object(
                __import__("eu4_gfx_startup_trace", fromlist=["x"]),
                "run_ops",
                side_effect=fake_run_ops,
            ), mock.patch.object(
                m6_frame, "coverage_report", return_value={"ok": True}
            ):
                self.assertEqual(m6_frame.capture(args), 6)
            report = json.loads((output / "capture-report.json").read_text())
            self.assertIn("seal_error", report)


if __name__ == "__main__":
    unittest.main()
