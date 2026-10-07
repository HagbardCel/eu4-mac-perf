"""Parse R0 probe swap records into autonomous_runner probe_rows shape."""

from __future__ import annotations

import csv
from pathlib import Path

import eu4_benchmark as base


def r0_probe_rows(path: Path, anchor: dict) -> list[dict]:
    if not path.is_file():
        return []
    output: list[dict] = []
    with path.open(newline="") as source:
        for row in csv.reader(source):
            if len(row) != 7 or row[0] != "S":
                continue
            try:
                clock_ns, wall_ns, _mode, interval_ns, swaps, paused = map(int, row[1:])
                if interval_ns <= 0 or swaps < 0 or not 0 <= paused <= swaps:
                    continue
                output.append(
                    {
                        "clock_ns": clock_ns,
                        "wall_ns": wall_ns,
                        "monotonic_ns": anchor["monotonic_ns"] + wall_ns - anchor["wall_ns"],
                        "interval_ns": interval_ns,
                        "swaps": swaps,
                        "paused_swaps": paused,
                        "swaps_s": swaps * 1e9 / interval_ns,
                    }
                )
            except ValueError:
                continue
    if output:
        offsets = [r["wall_ns"] - r["clock_ns"] for r in output]
        if max(offsets) - min(offsets) > 100_000_000:
            raise base.BenchmarkError("R0 probe wall/uptime clocks diverged")
    return output
