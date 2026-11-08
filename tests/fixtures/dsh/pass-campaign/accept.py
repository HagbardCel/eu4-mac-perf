#!/usr/bin/env python3
"""Deterministic passing fixture that counts its own invocations."""

import json
import sys
from pathlib import Path


def main() -> int:
    workspace = Path(sys.argv[1])
    counter = workspace.parent / "invocations.txt"
    count = 0
    if counter.is_file():
        count = len(counter.read_text(encoding="utf-8").splitlines())
    with counter.open("a", encoding="utf-8") as handle:
        handle.write("run\n")
    print(json.dumps({"verdict": "pass", "diagnostics": f"invocation {count + 1}"}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
