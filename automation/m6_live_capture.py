#!/usr/bin/env python3
"""Captures the EU4 window while a live probe is holding a draw checkpoint.

Used for M6 live acceptance: the probe reaches the title scene, holds the game
at the checkpoint, and this helper captures the actual window (CGWindowList +
screencapture) so the presented frame can be reviewed independently of any
reconstructed readback.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from automation import live_gfx  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, help="probe output directory")
    parser.add_argument("--timeout", type=float, default=240.0)
    parser.add_argument("--settle", type=float, default=1.0)
    args = parser.parse_args(argv)
    output = Path(args.output).expanduser().resolve()
    deadline = time.monotonic() + args.timeout
    checkpoint = None
    pid = None
    while time.monotonic() < deadline:
        if checkpoint is None:
            matches = sorted(output.glob("checkpoint-after-draw-*.json"))
            if matches:
                checkpoint = matches[-1]
        if pid is None:
            metadata = output / "metadata.json"
            if metadata.is_file():
                try:
                    pid = json.loads(metadata.read_text()).get("pid")
                except (OSError, json.JSONDecodeError):
                    pid = None
        if checkpoint is not None and pid:
            break
        time.sleep(0.25)
    if checkpoint is None or not pid:
        print(json.dumps({"status": "timeout", "checkpoint": str(checkpoint), "pid": pid}))
        return 2
    time.sleep(args.settle)
    drawable = output / "swap-frame.bgra"
    drawable_size = None
    try:
        checkpoint_record = json.loads(checkpoint.read_text())
        target = checkpoint_record.get("target") or {}
        width, height = target.get("width"), target.get("height")
        if type(width) is int and type(height) is int and width > 0 and height > 0:
            drawable_size = (width, height)
    except (OSError, json.JSONDecodeError):
        drawable_size = None
    result = live_gfx._window_capture(
        output,
        int(pid),
        drawable if drawable.is_file() else None,
        drawable_size=drawable_size,
    )
    record = {
        "schema": "m6-live-window-capture/v1",
        "probe_output": str(output),
        "checkpoint": checkpoint.name,
        "pid": pid,
        "capture": result,
    }
    (output / "window-capture.json").write_text(json.dumps(record, indent=2) + "\n")
    summary = {
        "status": result.get("status"),
        "file": result.get("file"),
        "window_id": result.get("window_id"),
        "width": result.get("width"),
        "height": result.get("height"),
        "method": result.get("method"),
    }
    print(json.dumps(summary, indent=2))
    accepted = result.get("status") == "captured" and result.get("qualified") is True
    return 0 if accepted else 1


if __name__ == "__main__":
    raise SystemExit(main())
