#!/usr/bin/env python3
"""Validate markdown relative links for files and directories."""

from __future__ import annotations

import argparse
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
    rel_md = md_path.relative_to(root)
    for line_no, target in extract_links(md_path, text):
        resolved = (md_path.parent / target).resolve()
        try:
            resolved.relative_to(root.resolve())
        except ValueError:
            errors.append(f"{rel_md}:{line_no}: outside repo: {target}")
            continue
        if not resolved.exists():
            errors.append(f"{rel_md}:{line_no}: missing path: {target}")
    return errors


def collect_md_under(root: Path, base: Path) -> list[Path]:
    if base.is_file():
        return [base]
    if base.is_dir():
        return sorted(base.rglob("*.md"))
    return []


def resolve_strict_inputs(root: Path, rels: list[str]) -> tuple[list[Path], list[str]]:
    """Return markdown files to scan and fatal configuration errors."""
    if not rels:
        return [], ["--strict requires at least one path"]

    paths: list[Path] = []
    errors: list[str] = []
    for rel in rels:
        base = root / rel
        if not base.exists():
            errors.append(f"missing strict input: {rel}")
            continue
        if base.is_file():
            if base.suffix.lower() != ".md":
                errors.append(f"unsupported strict input (not markdown): {rel}")
                continue
            paths.append(base)
            continue
        if base.is_dir():
            paths.extend(collect_md_under(root, base))
            continue
        errors.append(f"missing strict input: {rel}")
    return paths, errors


def scan(paths: list[Path], root: Path) -> list[str]:
    errors: list[str] = []
    for path in sorted(set(paths)):
        if path.suffix.lower() != ".md":
            continue
        if not path.is_file():
            errors.append(f"missing markdown file: {path.relative_to(root)}")
            continue
        errors.extend(check_file(path, root))
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--strict",
        nargs="*",
        default=None,
        help="Paths (markdown files or directories) that must have zero broken links",
    )
    args = parser.parse_args()

    root = repo_root()
    exit_code = 0

    if args.strict is not None:
        paths, config_errors = resolve_strict_inputs(root, args.strict)
        if config_errors:
            exit_code = 1
            print("Strict documentation link configuration errors:", file=sys.stderr)
            for e in config_errors:
                print(f"  {e}", file=sys.stderr)
        if exit_code == 0:
            errors = scan(paths, root)
            if errors:
                exit_code = 1
                print("Strict documentation link errors:", file=sys.stderr)
                for e in errors:
                    print(f"  {e}", file=sys.stderr)

    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
