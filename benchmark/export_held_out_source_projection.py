#!/usr/bin/env python3
"""Export committed terrain source projection for held-out fixture CI replay."""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import eu4_benchmark as base  # noqa: E402
import eu4_draw_trace as trace  # noqa: E402
import frame_model_workload as workload  # noqa: E402


def main() -> int:
    if not workload.SOURCE.is_file():
        raise SystemExit(f"Missing passive trace: {workload.SOURCE}")
    rows = list(trace.read_records(workload.SOURCE, trace.read_header(workload.SOURCE)["used"]))
    key = workload._trace_frame_key(rows)
    sites = {row["return_offset"]: row["category"] for row in json.loads(workload.INVENTORY.read_text())["direct_sites"]}
    terrain_rows = [
        dict(row)
        for row in rows
        if (row["window"], row["frame"]) == key and workload._row_category(row, sites) in workload.HELD_OUT_CATEGORIES
    ]
    projection = {
        "schema": "held-out-source-projection-v1",
        "selection_rule": workload.HELD_OUT_SELECTION_RULE_ID,
        "source_trace_path": str(workload.SOURCE.relative_to(workload.ROOT)),
        "source_trace_sha256": base.sha256(workload.SOURCE),
        "inventory_path": str(workload.INVENTORY.relative_to(workload.ROOT)),
        "inventory_sha256": base.sha256(workload.INVENTORY),
        "source_frame": list(key),
        "terrain_record_count": len(terrain_rows),
        "terrain_records": terrain_rows,
    }
    workload.HELD_OUT_SOURCE_DIR.mkdir(parents=True, exist_ok=True)
    workload.HELD_OUT_SOURCE_PROJECTION.write_text(json.dumps(projection, indent=2) + "\n")
    payload, meta = workload.build_held_out_fixture_material(rows=rows, key=key, sites=sites)
    print(
        projection["terrain_record_count"],
        "terrain records;",
        meta["sha256"],
        meta["draws"],
        "selected draws",
    )
    if meta["sha256"] != workload.FROZEN_HELD_OUT_RECIPE_SHA256:
        raise SystemExit("Projection does not reproduce frozen held-out SHA")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
