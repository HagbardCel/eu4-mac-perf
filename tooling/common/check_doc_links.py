#!/usr/bin/env python3
"""Validate markdown relative file links; optional baseline for legacy docs."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

LINK_RE = re.compile(r"\]\(([^)]+)\)")
SKIP_PREFIXES = ("http://", "https://", "mailto:", "#")


def repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def extract_links(md_path: Path, text: str) -> list[tuple[int, str]]:
    out: list[tuple[int, str]] = []
    for i, line in enumerate(text.splitlines(), start=1):
        for m in LINK_RE.finditer(line):
            target = m.group(1).strip()
            if not target or any(target.startswith(p) for p in SKIP_PREFIXES):
                continue
            if "#" in target:
                target = target.split("#", 1)[0]
            if not target:
                continue
            out.append((i, target))
    return out


def check_file(md_path: Path, root: Path) -> list[str]:
    errors: list[str] = []
    text = md_path.read_text(encoding="utf-8")
    for line_no, target in extract_links(md_path, text):
        resolved = (md_path.parent / target).resolve()
        try:
            resolved.relative_to(root.resolve())
        except ValueError:
            errors.append(f"{md_path.relative_to(root)}:{line_no}: outside repo: {target}")
            continue
        if not resolved.is_file():
            errors.append(f"{md_path.relative_to(root)}:{line_no}: missing file: {target}")
    return errors


def scan(paths: list[Path], root: Path) -> list[str]:
    errors: list[str] = []
    for path in sorted(paths):
        if path.suffix.lower() != ".md":
            continue
        if not path.is_file():
            errors.append(f"missing markdown file: {path.relative_to(root)}")
            continue
        errors.extend(check_file(path, root))
    return errors


def collect_md_under(root: Path, rel: str) -> list[Path]:
    base = root / rel
    if base.is_file():
        return [base]
    return sorted(base.rglob("*.md")) if base.is_dir() else []


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--strict",
        nargs="*",
        default=None,
        help="Paths (files or dirs) that must have zero broken file links",
    )
    parser.add_argument(
        "--baseline",
        type=Path,
        default=None,
        help="JSON list of allowed broken links under --legacy-root",
    )
    parser.add_argument(
        "--legacy-root",
        default="docs",
        help="Directory scanned for baseline regression checks",
    )
    parser.add_argument("--write-baseline", action="store_true", help="Print baseline JSON to stdout")
    args = parser.parse_args()

    root = repo_root()

    if args.write_baseline:
        legacy = collect_md_under(root, args.legacy_root)
        errors = scan(legacy, root)
        print(json.dumps(sorted(errors), indent=2))
        return 0

    exit_code = 0

    if args.strict is not None:
        strict_paths: list[Path] = []
        for item in args.strict:
            strict_paths.extend(collect_md_under(root, item))
        errors = scan(strict_paths, root)
        if errors:
            exit_code = 1
            print("Strict documentation link errors:", file=sys.stderr)
            for e in errors:
                print(f"  {e}", file=sys.stderr)

    if args.baseline is not None:
        legacy = collect_md_under(root, args.legacy_root)
        current = sorted(scan(legacy, root))
        allowed = set(json.loads(args.baseline.read_text(encoding="utf-8")))
        new_failures = sorted(set(current) - allowed)
        if new_failures:
            exit_code = 1
            print("New documentation link regressions under legacy root:", file=sys.stderr)
            for e in new_failures:
                print(f"  {e}", file=sys.stderr)

    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
