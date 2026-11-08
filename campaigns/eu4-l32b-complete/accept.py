#!/usr/bin/env python3
"""L3.2b completion acceptance adapter for the v2 trusted checker."""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = next(
    path for path in Path(__file__).resolve().parents if (path / ".git").exists()
)
sys.path.insert(0, str(ROOT))

from automation.gfx_draw_accept_v2 import check  # noqa: E402


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print(json.dumps({"verdict": "unavailable", "diagnostics": "usage: accept.py <worktree>"}))
        return 2
    result = check(Path(argv[1]), Path(__file__).resolve().parent)
    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
