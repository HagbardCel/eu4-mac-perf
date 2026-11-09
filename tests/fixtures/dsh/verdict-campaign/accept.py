#!/usr/bin/env python3
"""Emit the verdict requested by a file beside the workspace.

The bridge must pass every checker verdict through unchanged and map it to the
right goal policy; this fixture makes each row reachable without five campaigns.
"""

import json
import sys
from pathlib import Path


def main() -> int:
    workspace = Path(sys.argv[1])
    requested = workspace.parent / "verdict.txt"
    text = requested.read_text(encoding="utf-8").strip() if requested.is_file() else "fail"
    verdict, _, diagnostics = text.partition("|")
    print(json.dumps({
        "verdict": verdict.strip() or "fail",
        "diagnostics": diagnostics.strip() or f"requested {verdict.strip()}",
    }))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
