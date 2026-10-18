"""Oracle P4b: independent synthetic graphics replay.

Offline only. Nothing in this package launches EU IV, opens a game process, or
attaches to a binary. P4b reconstructs a frame from a *sealed capture* of a synthetic
graphics subject, in a fresh process, without linking the P4 recorder, without the
original process and without re-running the subject.
"""
from oracle.replay.capture import (
    FRAME_CAPTURE_VERSION,
    FRAME_SEAL_VERSION,
    CaptureError,
    IncompleteOracle,
    ReplayError,
    fnv1a32,
    parse_frame_transcript,
    read_sealed,
    replay,
    seal,
    write_sealed,
)

__all__ = [
    "CaptureError",
    "FRAME_CAPTURE_VERSION",
    "FRAME_SEAL_VERSION",
    "IncompleteOracle",
    "ReplayError",
    "fnv1a32",
    "parse_frame_transcript",
    "read_sealed",
    "replay",
    "seal",
    "write_sealed",
]
