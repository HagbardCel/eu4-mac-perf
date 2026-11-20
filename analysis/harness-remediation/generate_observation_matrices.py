#!/usr/bin/env python3
"""Regenerate the present-616/904 observation/writer matrices.

Reads only the sealed local frames; writes compact JSON/CSV artifacts.  The
generator is committed so the matrices are reproducible rather than a
hand-edited projection.
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from automation import gfx_frame_graph  # noqa: E402
DEFAULT_FRAMES = {
    616: Path.cwd() / ".local/m7/goal-round1/capture/frames/frame-000001",
    904: Path.cwd() / ".local/m7/goal-round1/capture-c/frame",
}


def render(present: int, frame: Path, out: Path) -> None:
    graph = gfx_frame_graph.build(frame)
    plan = gfx_frame_graph.checkpoint_plan(graph)
    by = {(row["event"], str(row["resource"])): row for row in plan}
    rows = gfx_frame_graph.provenance_matrix(graph, plan)
    for row in rows:
        key = (row["observation"]["event"], row["observation"]["resource"])
        source = by[key]
        row["reference"] = {
            field: source.get(field) for field in
            ("file", "reference_file", "reference_sha256",
             "reference_width", "reference_height")}
        row["declared_writer_events"] = source.get("producer_events")
        row["declared_writer_authority"] = source.get("writer_authority")
    document = {
        "schema": "gfx-observation-writer-matrix/v1",
        "present_index": present,
        "frame_dir": str(frame),
        "capture": graph["capture"],
        "graph_status": graph["status"],
        "counts": graph["counts"],
        "generator": "analysis/harness-remediation/generate_observation_matrices.py",
        "rows": rows,
    }
    (out / f"observation-writer-{present}.json").write_text(
        json.dumps(document, indent=2) + "\n")
    with (out / f"observation-writer-{present}.csv").open(
            "w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle, lineterminator="\n")
        writer.writerow([
            "observation_event", "resource", "phase", "writer_events",
            "writer_authority", "producer_event", "upcoming_entry",
            "upcoming_program", "required_generation", "present_index",
            "session_id", "capture_frame_id"])
        for row in rows:
            writer.writerow([
                row["observation"]["event"], row["observation"]["resource"],
                row["observation"]["phase"],
                " ".join(map(str, row["writer_events"])),
                row["writer_authority"], row["producer_event"],
                (row["upcoming_event"] or {}).get("entry"),
                (row["upcoming_event"] or {}).get("program"),
                row["generation"]["required"], row["frame"].get("present_index"),
                row["frame"].get("session_id"),
                row["frame"].get("capture_frame_id")])


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", type=Path,
                        default=Path(__file__).resolve().parent)
    parser.add_argument("--frame", action="append", default=[],
                        help="PRESENT=PATH override")
    args = parser.parse_args()
    frames = dict(DEFAULT_FRAMES)
    for item in args.frame:
        present, path = item.split("=", 1)
        frames[int(present)] = Path(path)
    for present, frame in sorted(frames.items()):
        if not frame.is_dir():
            raise SystemExit(f"missing sealed frame for {present}: {frame}")
        render(present, frame, args.out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
