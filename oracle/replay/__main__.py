"""Replay a sealed synthetic frame capture in a fresh process.

``python -m oracle.replay <sealed-capture.json>`` reconstructs the frame the capture
describes and prints it, exiting 0. A capture that does not hold a resource the frame
depends on exits 2 with an ``INCOMPLETE_ORACLE`` diagnostic - it fails closed instead
of reconstructing a plausible wrong frame - and any other defect exits 1.

Nothing here opens a game process or the EU IV binary; the only input is a file.
"""
from __future__ import annotations

import argparse
import sys
from typing import Sequence

from oracle.replay import capture


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m oracle.replay",
        description="Reconstruct a frame from a sealed synthetic capture.")
    parser.add_argument("capture", help="path to a sealed frame capture (canonical JSON)")
    arguments = parser.parse_args(argv)

    try:
        sealed = capture.read_sealed(arguments.capture)
        result = capture.replay(sealed)
    except capture.IncompleteOracle as exc:
        print(f"INCOMPLETE_ORACLE: {exc}", file=sys.stderr)
        return 2
    except capture.ReplayError as exc:
        print(f"REPLAY_FAILED: {exc}", file=sys.stderr)
        return 1

    frame = result["frame"]
    print(f"FRAME digest={frame['digest']} bytes={frame['bytes_hex']} "
          f"lifetimes={len(result['lifetimes'])} ops={result['ops']}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
