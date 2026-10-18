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


def _with_timeline(capture_document: dict, mutate) -> dict:
    """A copy of the capture whose timeline has been edited, then re-sealed."""
    edited = copy.deepcopy(capture_document)
    mutate(edited["timeline"])
    return capture.seal(edited)


def _transcript(*, drop: int | None = None, replace: tuple[int, str] | None = None,
                insert: tuple[int, str] | None = None, append: str | None = None) -> str:
    """The golden transcript, edited at the line level: the wire-format tests need it."""
    lines = harness.golden_transcript().splitlines()
    if drop is not None:
        del lines[drop]
    if replace is not None:
        lines[replace[0]] = replace[1]
    if insert is not None:
        lines.insert(insert[0], insert[1])
    if append is not None:
        lines.append(append)
    return "\n".join(lines) + "\n"


def _line_index(prefix: str) -> int:
    """The first golden transcript line beginning with ``prefix``."""
    return next(index for index, line in enumerate(harness.golden_transcript().splitlines())
                if line.startswith(prefix))


class TerminalAccountingTests(unittest.TestCase):
    """A capture that lost records must not read as a complete one (P1)."""

    def test_a_transcript_that_lost_records_but_kept_its_end_is_rejected(self):
        # Everything after the first PRESENT is gone; the terminal record still claims
        # the full 13-effect run. Without terminal accounting this replays as 6045a395.
        lines = harness.golden_transcript().splitlines()
        truncated = lines[:_line_index("PRESENT") + 1] + [lines[_line_index("END")]]
        with self.assertRaises(capture.CaptureError) as raised:
            capture.parse_frame_transcript("\n".join(truncated) + "\n")
        message = str(raised.exception)
        self.assertIn("accounting does not match", message)
        self.assertIn("'effects': 13", message)   # declared
        self.assertIn("'effects': 5", message)    # recomputed

    def test_each_declared_counter_is_checked(self):
        for key, declaration in (("creates", "3"), ("destroys", "0"), ("ops", "5"),
                                 ("effects", "12")):
            end = {"creates": "4", "destroys": "1", "ops": "6", "effects": "13"}
            end[key] = declaration
            record = "END " + " ".join(f"{field}={value}" for field, value in end.items())
            with self.subTest(field=key):
                with self.assertRaises(capture.CaptureError):
                    capture.parse_frame_transcript(_transcript(replace=(_line_index("END"), record)))

    def test_a_record_after_the_end_is_rejected(self):
        with self.assertRaises(capture.CaptureError) as raised:
            capture.parse_frame_transcript(
                _transcript(append="FILL slot=0 gen=0 offset=0 span=4 pattern=1 result=0"))
        self.assertIn("continues after its END record", str(raised.exception))

    def test_a_second_end_record_is_rejected(self):
        # The first END closes the transcript, so a second one is content after it.
        with self.assertRaises(capture.CaptureError) as raised:
            capture.parse_frame_transcript(
                _transcript(append="END creates=4 destroys=1 ops=6 effects=13"))
        self.assertIn("continues after its END record", str(raised.exception))

    def test_a_missing_end_is_rejected(self):
        with self.assertRaises(capture.CaptureError) as raised:
            capture.parse_frame_transcript(_transcript(drop=_line_index("END")))
        self.assertIn("no END record", str(raised.exception))

    def test_the_end_record_must_declare_exactly_its_fields(self):
        cases = {
            "unexpected field": "END creates=4 destroys=1 ops=6 effects=13 locus=2",
            "missing field": "END creates=4 destroys=1 ops=6",
            "duplicate field": "END creates=4 creates=4 destroys=1 ops=6 effects=13",
        }
        for label, record in cases.items():
            with self.subTest(case=label):
                with self.assertRaises(capture.CaptureError):
                    capture.parse_frame_transcript(_transcript(replace=(_line_index("END"), record)))

    def test_end_counters_must_be_canonical_non_negative_decimals(self):
        for value in ("04", "+4", "-4", "4.0", "0x4"):
            with self.subTest(value=value):
                with self.assertRaises(capture.CaptureError):
                    capture.parse_frame_transcript(_transcript(
                        replace=(_line_index("END"),
                                 f"END creates={value} destroys=1 ops=6 effects=13")))

    def test_the_golden_transcript_passes_terminal_accounting(self):
        self.assertEqual(capture.parse_frame_transcript(harness.golden_transcript()),
                         _golden_capture())

    def test_a_truncated_capture_that_re_accounts_is_consistent_but_is_not_the_original(self):
        # The honest limit of terminal accounting, kept executable rather than prose:
        # loss that is *also* re-accounted is internally consistent, so only the seal
        # and the upstream admission gate can catch it.
        lines = harness.golden_transcript().splitlines()
        truncated = lines[:_line_index("PRESENT") + 1] + ["END creates=3 destroys=0 ops=1 effects=5"]
        replayable = capture.parse_frame_transcript("\n".join(truncated) + "\n")
        self.assertEqual(capture.replay(replayable)["frame"]["digest"], "6045a395")
        self.assertNotEqual(capture.replay(replayable)["frame"]["digest"],
                            EXPECTED_FRAME["digest"])


class TranscriptStructureTests(unittest.TestCase):
    """The transcript is a wire format; ambiguity in it is a defect, not a tolerance."""

    def test_the_header_must_be_the_first_line(self):
        lines = harness.golden_transcript().splitlines()
        reordered = [lines[1], lines[0], *lines[2:]]
        with self.assertRaises(capture.CaptureError) as raised:
            capture.parse_frame_transcript("\n".join(reordered) + "\n")
        self.assertIn("must begin with its GFX header", str(raised.exception))

    def test_a_missing_header_is_rejected(self):
        with self.assertRaises(capture.CaptureError) as raised:
            capture.parse_frame_transcript(_transcript(drop=0))
        self.assertIn("GFX header", str(raised.exception))

    def test_a_duplicate_header_is_rejected(self):
        with self.assertRaises(capture.CaptureError) as raised:
            capture.parse_frame_transcript(_transcript(insert=(_line_index("END"), "GFX v1")))
        self.assertIn("more than once", str(raised.exception))

    def test_an_unknown_header_version_is_rejected(self):
        with self.assertRaises(capture.CaptureError):
            capture.parse_frame_transcript(_transcript(replace=(0, "GFX v2")))

    def test_a_duplicate_field_is_rejected(self):
        fill = _line_index("FILL")
        with self.assertRaises(capture.CaptureError) as raised:
            capture.parse_frame_transcript(_transcript(
                replace=(fill, "FILL slot=0 gen=0 offset=0 span=16 span=16 pattern=3 result=0")))
        self.assertIn("duplicate field", str(raised.exception))

    def test_a_non_canonical_integer_is_rejected(self):
        fill = _line_index("FILL")
        with self.assertRaises(capture.CaptureError):
            capture.parse_frame_transcript(_transcript(
                replace=(fill, "FILL slot=0 gen=0 offset=00 span=16 pattern=3 result=0")))

    def test_an_unrecognized_record_is_rejected(self):
        with self.assertRaises(capture.CaptureError) as raised:
            capture.parse_frame_transcript(
                _transcript(insert=(_line_index("END"), "RESIZE slot=0 gen=0 result=0")))
        self.assertIn("unrecognized", str(raised.exception))


class PresentationTests(unittest.TestCase):
    """Every presentation is verified where it happens, not only the last one (P2)."""

    def test_an_earlier_presentation_with_a_wrong_digest_is_rejected(self):
        document = _golden_capture()
        document["timeline"][4]["digest"] = "00000000"  # the first of two PRESENTs
        with self.assertRaises(capture.CaptureError) as raised:
            capture.replay(document)
        self.assertIn("timeline index 4 digests to 6045a395", str(raised.exception))

    def test_an_earlier_presentation_with_wrong_bytes_is_rejected(self):
        document = _golden_capture()
        document["timeline"][4]["bytes_hex"] = "ff" * 16
        with self.assertRaises(capture.CaptureError) as raised:
            capture.replay(document)
        self.assertIn("timeline index 4 differs from the capture", str(raised.exception))

    def test_the_reported_frame_is_the_last_presentation(self):
        document = _golden_capture()
        self.assertEqual(document["timeline"][-1]["op"], "present")
        self.assertEqual(document["timeline"][-1]["digest"], EXPECTED_FRAME["digest"])
        self.assertEqual(capture.replay(document)["frame"], EXPECTED_FRAME)


class DirectSealValidationTests(unittest.TestCase):
    """`replay()` protects the seal for callers that bypass `read_sealed()`."""

    def test_a_sealed_wrapper_with_a_tampered_payload_is_rejected(self):
        sealed = capture.seal(_golden_capture())
        sealed["payload"]["timeline"][0]["contents_hex"] = "ff" * 16
        with self.assertRaises(capture.CaptureError) as raised:
            capture.replay(sealed)
        self.assertIn("does not match its digest", str(raised.exception))

    def test_a_sealed_wrapper_with_a_forged_digest_is_rejected(self):
        sealed = capture.seal(_golden_capture())
        sealed["capture_sha256"] = "0" * 64
        with self.assertRaises(capture.CaptureError):
            capture.replay(sealed)

    def test_an_intact_sealed_wrapper_still_replays(self):
        sealed = capture.seal(_golden_capture())
        self.assertEqual(capture.replay(sealed)["frame"], EXPECTED_FRAME)

    def test_a_non_object_capture_is_rejected(self):
        for value in (None, [], "frame.json", 4):
            with self.subTest(value=value):
                with self.assertRaises(capture.CaptureError):
                    capture.replay(value)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
