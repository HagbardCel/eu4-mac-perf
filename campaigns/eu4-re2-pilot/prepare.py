#!/usr/bin/env python3
"""Apply the signed-division near miss to a clean detached worktree.

Operator-only. The runner does not call this. The anchor is the same text the
RE2 differential test mutates; a missing anchor refuses the edit.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

HEADER = Path(
    "analysis/reconstruction/_GfxSetConstantBuffer/reconstruction/gfx_set_constant_buffer.hpp")
ANCHOR = "    return (first_dword + 3u) >> 2;"
MUTATION = (
    "    return static_cast<std::uint32_t>("
    "static_cast<std::int32_t>(first_dword + 3u) / 4);"
)


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) != 1:
        print("usage: prepare.py <worktree>", file=sys.stderr)
        return 2
    workspace = Path(args[0]).resolve()
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
    header = workspace / HEADER
    text = header.read_text(encoding="utf-8")
    if ANCHOR not in text:
        print("mutation anchor is missing", file=sys.stderr)
        return 2
    header.write_text(text.replace(ANCHOR, MUTATION, 1), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
