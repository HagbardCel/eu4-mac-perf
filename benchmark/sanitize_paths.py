#!/usr/bin/env python3
"""Rewrite absolute macOS paths in JSON for public git commits."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

REPO_MARKER = "/Projects/MiniProjects/eu4-mac-perf/"
HOME_PREFIX = re.compile(r"/Users/[^/]+/")


def _sanitize_string(value: str, repo_root: Path) -> str:
    repo_str = str(repo_root.resolve())
    if value.startswith(repo_str):
        rel = value[len(repo_str) :].lstrip("/")
        return rel or "."
    if REPO_MARKER in value:
        idx = value.index(REPO_MARKER) + len(REPO_MARKER)
        return value[idx:]
    if value.startswith("/Users/"):
        return HOME_PREFIX.sub("$HOME/", value)
    return value


def _walk(obj: object, repo_root: Path) -> object:
    if isinstance(obj, str):
        return _sanitize_string(obj, repo_root)
    if isinstance(obj, list):
        return [_walk(item, repo_root) for item in obj]
    if isinstance(obj, dict):
        return {key: _walk(val, repo_root) for key, val in obj.items()}
    return obj


def sanitize_file(path: Path, repo_root: Path) -> bool:
    text = path.read_text(encoding="utf-8")
    data = json.loads(text)
    cleaned = _walk(data, repo_root)
    new_text = json.dumps(cleaned, indent=2, ensure_ascii=False) + "\n"
    if new_text != text:
        path.write_text(new_text, encoding="utf-8")
        return True
    return False


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "paths",
        nargs="*",
        type=Path,
        help="JSON files or directories (default: results/)",
    )
    args = parser.parse_args(argv)
    repo_root = Path(__file__).resolve().parents[1]
    targets = list(args.paths) if args.paths else [repo_root / "results"]
    changed = 0
    for target in targets:
        target = (repo_root / target).resolve() if not target.is_absolute() else target.resolve()
        if target.is_dir():
            files = sorted(target.rglob("*.json"))
        else:
            files = [target]
        for path in files:
            path = path.resolve()
            if not path.is_file():
                continue
            if sanitize_file(path, repo_root):
                try:
                    label = path.relative_to(repo_root)
                except ValueError:
                    label = path
                print(f"updated {label}")
                changed += 1
    return 0 if changed >= 0 else 1


if __name__ == "__main__":
    sys.exit(main())
