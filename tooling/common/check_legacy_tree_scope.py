#!/usr/bin/env python3
"""Ensure PR A does not modify legacy code trees or sealed Phase C evidence."""

from __future__ import annotations

import subprocess
import sys

SEALED = "results/20261003T210202Z-intrusive-diagnostic/"
LEGACY = ("benchmark/", "analysis/")


def changed_files(base: str, head: str) -> list[str]:
    out = subprocess.check_output(["git", "diff", "--name-only", f"{base}...{head}"])
    return [line.decode("utf-8") for line in out.splitlines() if line]


def allowed_legacy(path: str) -> bool:
    if path in ("benchmark/README.md", "analysis/README.md", "results/README.md"):
        return True
    return False


def main() -> int:
    if len(sys.argv) != 3:
        print("usage: check_legacy_tree_scope.py <base> <head>", file=sys.stderr)
        return 2
    base, head = sys.argv[1], sys.argv[2]
    bad: list[str] = []
    for path in changed_files(base, head):
        if path.startswith(SEALED):
            bad.append(path)
            continue
        for prefix in LEGACY:
            if path.startswith(prefix) and not allowed_legacy(path):
                bad.append(path)
    if bad:
        print("Disallowed changes under legacy trees or sealed evidence:", file=sys.stderr)
        for p in sorted(bad):
            print(f"  {p}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
