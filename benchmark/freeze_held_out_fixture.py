#!/usr/bin/env python3
"""Write hash-frozen held-out fixture bytes under analysis/held-out/ (no harness timing)."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import frame_model_workload as workload  # noqa: E402


def main() -> int:
    meta = workload.write_held_out_fixture_files()
    print(
        meta["sha256"],
        meta["draws"],
        "draws",
        f"rule={meta['held_out_selection_rule']}",
    )
    print(f"Wrote {workload.HELD_OUT_FIXTURE} and {workload.HELD_OUT_FIXTURE_META}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
