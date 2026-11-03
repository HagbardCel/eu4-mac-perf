#!/usr/bin/env python3
"""Acceptance check for the automation smoke campaign.

The first run always fails, even if the workspace is already correct. Later
runs pass only when marker.txt is ``ready`` and progress.md has a Next heading.
The stage file lives beside the live workspace, outside it.
"""

from __future__ import annotations

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
        verdict = "fail"
        diagnostics = "Confirmation round required. Leave marker.txt as ready and keep a Next heading in progress.md."
    elif ready:
        verdict = "pass"
        diagnostics = "marker.txt is ready and progress.md has a Next heading."
    else:
        verdict = "fail"
        diagnostics = "marker.txt must be exactly ready and progress.md needs a Next heading."
    print(json.dumps({"verdict": verdict, "diagnostics": diagnostics}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
