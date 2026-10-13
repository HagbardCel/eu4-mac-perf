#!/usr/bin/env python3
"""Fail if git tracks paths that must stay local (game binaries, Ghidra, decompiled corpus)."""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import PurePosixPath


def normalize(path: str) -> str:
    return PurePosixPath(path).as_posix()


def listed_files() -> list[str]:
    out = subprocess.check_output(["git", "ls-files", "-z"])
    return [p.decode("utf-8") for p in out.split(b"\0") if p]


def violations(path: str) -> list[str]:
    p = normalize(path)
    reasons: list[str] = []

    if p == ".local" or p.startswith(".local/"):
        reasons.append("local working tree (.local/)")

    if p.endswith(".gpr"):
        reasons.append("Ghidra project file (*.gpr)")

    parts = p.split("/")
    if any(part.endswith(".rep") for part in parts):
        reasons.append("Ghidra repository segment (*.rep)")

    if not p.startswith("tests/re_fixtures/"):
        if "/decompiled/" in f"/{p}/" or p.endswith("/decompiled") or "decompiled" in parts:
            reasons.append("decompiled corpus export")

    base = parts[-1] if parts else p
    if base in ("eu4", "Europa Universalis IV"):
        if "Contents/MacOS" in p or p.startswith("patches/"):
            reasons.append("suspected full game executable under patches/ or bundle path")

    if p.startswith("patches/") and base.endswith(".app"):
        reasons.append("patched .app bundle under patches/")

    if re.fullmatch(r"patches/.+/eu4", p):
        reasons.append("full patched eu4 binary under patches/")

    return reasons


def main() -> int:
    bad: list[tuple[str, list[str]]] = []
    for path in listed_files():
        v = violations(path)
        if v:
            bad.append((path, v))

    if bad:
        print("Forbidden tracked paths detected:", file=sys.stderr)
        for path, v in sorted(bad):
            print(f"  {path}: {', '.join(v)}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
