#!/usr/bin/env python3
"""Load this campaign and call the shared reconstruction checker."""

from __future__ import annotations

import json
import sys
from pathlib import Path


def trusted_root() -> Path:
    for parent in Path(__file__).resolve().parents:
        checker = parent / "automation" / "reconstruction_accept.py"
        if checker.is_file() and (parent / ".git").exists():
            return parent
    raise SystemExit("trusted repository not found")


sys.path.insert(0, str(trusted_root()))
from automation.reconstruction_accept import check  # noqa: E402


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: accept.py <worktree>", file=sys.stderr)
        return 2
    here = Path(__file__).resolve().parent
    campaign = json.loads((here / "campaign.json").read_text(encoding="utf-8"))
    config = json.loads((here / "reconstruction.json").read_text(encoding="utf-8"))
    result = check(Path(sys.argv[1]), campaign, config)
    print(json.dumps(result))
    if result["verdict"] == "pass":
        return 0
    if result["verdict"] == "fail":
        return 1
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
