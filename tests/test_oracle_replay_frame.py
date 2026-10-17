"""P4b: the independent synthetic graphics replay.

The gate is "a sealed frame replays in a fresh process; missing dependencies fail
closed". These tests hold that line: the subject's golden capture is reproduced
byte-for-byte, the replay reconstructs the frame from the sealed capture alone, and
every way of removing a dependency the frame needs yields ``INCOMPLETE_ORACLE``
instead of a plausible wrong frame.
"""
from __future__ import annotations

import copy
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from oracle.recorder.store import StoreError
from oracle.replay import capture, harness

ROOT = Path(__file__).resolve().parents[1]
EXPECTED_FRAME = {"digest": "26dc49d4", "bytes_hex": "020304054446404e9c0e0f100f101112"}


def _golden_capture() -> dict:
    return capture.parse_frame_transcript(harness.golden_transcript())


def _with_timeline(capture_document: dict, mutate) -> dict:
    """A copy of the capture whose timeline has been edited, then re-sealed."""
    edited = copy.deepcopy(capture_document)
    mutate(edited["timeline"])
    return capture.seal(edited)


class GoldenTests(unittest.TestCase):
    def test_the_graphics_subject_reproduces_its_golden_capture(self):
        self.assertEqual(harness.reproduce(), harness.golden_transcript())

    def test_the_golden_capture_parses_into_a_complete_timeline(self):
        document = _golden_capture()
        self.assertEqual(document["subject"], "gfx-v1")
        self.assertEqual([step["op"] for step in document["timeline"]],
                         ["create", "create", "create", "fill", "present", "blit", "fill",
                          "mix", "destroy", "create", "blit", "fill", "present"])


class ReplayTests(unittest.TestCase):
    def test_the_replay_reconstructs_the_recorded_frame(self):
        result = capture.replay(_golden_capture())
        self.assertEqual(result["frame"], EXPECTED_FRAME)
        self.assertEqual(result["ops"], 8)

    def test_the_replay_is_deterministic(self):
        first = capture.replay(_golden_capture())
        second = capture.replay(_golden_capture())
        self.assertEqual(first, second)

    def test_handle_reuse_is_a_new_lifetime(self):
        result = capture.replay(_golden_capture())
        self.assertEqual(result["lifetimes"], ["slot:0@0", "slot:1@0", "slot:1@1", "slot:2@0"])

    def test_the_replay_needs_only_the_capture(self):
        # The reconstruction is a pure function of the sealed artifact: no subject, no
        # recorder and no live process is consulted to produce it.
        sealed = capture.seal(_golden_capture())
        self.assertEqual(capture.replay(sealed)["frame"], EXPECTED_FRAME)

    def test_a_capture_that_records_a_failed_operation_is_rejected(self):
        document = _golden_capture()
        document["timeline"][0]["result"] = 1
        with self.assertRaises(capture.CaptureError):
            capture.seal(document)

    def test_a_capture_without_a_frame_is_rejected(self):
        document = _golden_capture()
        document["timeline"] = [step for step in document["timeline"] if step["op"] != "present"]
        with self.assertRaises(capture.CaptureError):
            capture.replay(document)


class FailClosedTests(unittest.TestCase):
    def test_a_missing_create_fails_closed(self):
        def drop_the_scratch_texture(timeline):
            del timeline[2]  # the create of the mix source

        broken = _with_timeline(_golden_capture(), drop_the_scratch_texture)
        with self.assertRaises(capture.IncompleteOracle) as raised:
            capture.replay(broken)
        self.assertIn("slot:2@0", str(raised.exception))

    def test_a_stale_reference_fails_closed(self):
        def blit_from_the_destroyed_generation(timeline):
            for step in timeline:
                if step["op"] == "blit" and step["source"]["generation"] == 1:
                    step["source"]["generation"] = 0  # destroyed at index 8

        broken = _with_timeline(_golden_capture(), blit_from_the_destroyed_generation)
        with self.assertRaises(capture.IncompleteOracle) as raised:
            capture.replay(broken)
        self.assertIn("slot:1@0", str(raised.exception))

    def test_a_reference_to_a_never_created_lifetime_fails_closed(self):
        def blit_from_nowhere(timeline):
            for step in timeline:
                if step["op"] == "blit":
                    step["source"] = {"slot": 3, "generation": 0}
                    break

        broken = _with_timeline(_golden_capture(), blit_from_nowhere)
        with self.assertRaises(capture.IncompleteOracle) as raised:
            capture.replay(broken)
        self.assertIn("slot:3@0", str(raised.exception))

    def test_a_missing_frame_dependency_is_not_silently_reconstructed(self):
        # Dropping a dependency must never yield a *different* frame: it must not
        # yield a frame at all.
        def drop_the_initial_texture(timeline):
            del timeline[0]

        broken = _with_timeline(_golden_capture(), drop_the_initial_texture)
        with self.assertRaises(capture.IncompleteOracle):
            capture.replay(broken)


class SealTests(unittest.TestCase):
    def test_a_sealed_capture_round_trips(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "frame.json"
            sealed = capture.seal(_golden_capture())
            self.assertEqual(capture.write_sealed(path, sealed), "created")
            self.assertEqual(capture.read_sealed(path), sealed)

    def test_republication_refuses_different_content(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "frame.json"
            sealed = capture.seal(_golden_capture())
            capture.write_sealed(path, sealed)
            self.assertEqual(capture.write_sealed(path, sealed), "unchanged")
            other = _with_timeline(_golden_capture(), lambda timeline: timeline.pop(2))
            with self.assertRaises(StoreError):
                capture.write_sealed(path, other)

    def test_a_tampered_payload_is_detected_at_load(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "frame.json"
            sealed = capture.seal(_golden_capture())
            capture.write_sealed(path, sealed)
            tampered = copy.deepcopy(sealed)
            tampered["payload"]["timeline"][0]["contents_hex"] = "ff" * 16
            path.write_bytes(capture.canonical_bytes(tampered))
            with self.assertRaises(capture.CaptureError):
                capture.read_sealed(path)

    def test_a_tampered_digest_is_detected_at_load(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "frame.json"
            sealed = capture.seal(_golden_capture())
            sealed["capture_sha256"] = "0" * 64
            path.write_bytes(capture.canonical_bytes(sealed))
            with self.assertRaises(capture.CaptureError):
                capture.read_sealed(path)

    def test_a_non_canonical_file_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "frame.json"
            sealed = capture.seal(_golden_capture())
            path.write_bytes(json.dumps(sealed, indent=2).encode("utf-8"))
            with self.assertRaises(capture.CaptureError):
                capture.read_sealed(path)


class FreshProcessTests(unittest.TestCase):
    """The gate, literally: the replay runs as its own process over a file."""

    def _environment(self) -> dict[str, str]:
        environment = dict(os.environ)
        environment["PYTHONPATH"] = os.pathsep.join([str(ROOT / "benchmark"), str(ROOT)])
        return environment

    def _run(self, path: Path) -> subprocess.CompletedProcess:
        return subprocess.run([sys.executable, "-m", "oracle.replay", str(path)],
                              cwd=str(ROOT), env=self._environment(), capture_output=True,
                              text=True, timeout=60)

    def test_a_sealed_frame_replays_in_a_fresh_process(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "frame.json"
            capture.write_sealed(path, capture.seal(_golden_capture()))
            finished = self._run(path)
            self.assertEqual(finished.returncode, 0, finished.stderr)
            self.assertIn(f"digest={EXPECTED_FRAME['digest']}", finished.stdout)
            self.assertIn(f"bytes={EXPECTED_FRAME['bytes_hex']}", finished.stdout)

    def test_a_capture_missing_a_dependency_fails_closed_in_a_fresh_process(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "frame.json"
            broken = _with_timeline(_golden_capture(), lambda timeline: timeline.pop(2))
            capture.write_sealed(path, broken)
            finished = self._run(path)
            self.assertEqual(finished.returncode, 2)
            self.assertIn("INCOMPLETE_ORACLE", finished.stderr)
            self.assertNotIn("FRAME", finished.stdout)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
