#!/usr/bin/env python3
"""Confirm the pinned worktree. This does not edit it."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


def _repo() -> Path:
    start = Path(__file__).resolve().parent
    for parent in (start, *start.parents):
        if (parent / ".git").exists():
            return parent
    raise RuntimeError("trusted repository not found")


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) != 1:
        print("usage: prepare.py <worktree>", file=sys.stderr)
        return 2
    workspace = Path(args[0]).resolve()
    root = _repo()
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    from automation.state import require_worktree
    commit = json.loads((Path(__file__).resolve().parent / "campaign.json").read_text())
    try:
        require_worktree(workspace, root, str(commit["workspace"]["commit"]))
    except RuntimeError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    status = subprocess.run(
        ["git", "-C", str(workspace), "status", "--porcelain"],
        capture_output=True, text=True,
    )
    if status.returncode != 0:
        print(status.stderr.strip() or "git status failed", file=sys.stderr)
        return 2
    if status.stdout.strip():
        print("worktree is already modified", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
