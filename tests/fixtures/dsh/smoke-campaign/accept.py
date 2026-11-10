#!/usr/bin/env python3
"""Stateful fixture checker: fails once on trusted external state, then passes.

The candidate is already correct. The first invocation fails regardless and
records that it ran, in a stage file beside the workspace. A second invocation
with an unchanged candidate must therefore be permitted to return ``pass``.
"""

import json
import sys
from pathlib import Path


def main() -> int:
    workspace = Path(sys.argv[1])
    stage = workspace.parent / "accept-stage"
    marker = workspace / "marker.txt"
    progress = workspace / "progress.md"
    text = marker.read_text(encoding="utf-8").strip() if marker.is_file() else ""
    notes = progress.read_text(encoding="utf-8") if progress.is_file() else ""
    ready = text == "ready" and "\n## Next" in "\n" + notes
    if not stage.exists():
        stage.write_text("seen\n", encoding="utf-8")
        print(json.dumps({"verdict": "fail",
                          "diagnostics": "confirmation round required"}))
    elif ready:
        print(json.dumps({"verdict": "pass", "diagnostics": "marker and next are ready"}))
    else:
        print(json.dumps({"verdict": "fail",
                          "diagnostics": "marker.txt must be ready and progress.md needs a Next heading"}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
