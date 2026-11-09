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
    args = argv[1:]
    campaign_dir = Path(__file__).resolve().parent
    if len(args) >= 2 and args[0] == "--campaign-dir":
        campaign_dir = Path(args[1]).resolve()
        args = args[2:]
    if len(args) != 1:
        print(json.dumps({"verdict": "unavailable", "diagnostics": "usage: accept.py [--campaign-dir <dir>] <worktree>"}))
        return 2
    result = check(Path(args[0]), campaign_dir)
    print(json.dumps(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
