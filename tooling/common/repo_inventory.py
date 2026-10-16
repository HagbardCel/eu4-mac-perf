#!/usr/bin/env python3
"""Deterministic, read-only inventory of Git-tracked paths.

Emits a mechanical manifest used by ``docs/repo-map.md`` (P1 audit). This tool
does not classify lifecycle (ACTIVE / RETIRED_EXPERIMENT / ...) — that is an
interpretation recorded by hand in the map. It only reports what Git tracks.

Usage::

    PYTHONPATH=. python -m tooling.common.repo_inventory summary
    PYTHONPATH=. python -m tooling.common.repo_inventory list
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]

# Mechanical role by top-level area. Interpretation lives in docs/repo-map.md.
AREA_ROLE = {
    "results": "sealed/historical run evidence",
    "tests": "portable test suite",
    "analysis": "legacy analysis + generated evidence",
    "benchmark": "legacy profiler / CLI entry points",
    "docs": "documentation",
    "reverse_engineering": "static RE tooling",
    "tooling": "shared guards / measurement tooling",
    "experiments": "isolated hypotheses (placeholder)",
    "measurements": "measurement registry (placeholder)",
    "patches": "version-pinned modifications (placeholder)",
    "fixtures": "local fixtures / references",
    ".github": "CI workflow",
    ".githooks": "Git hooks",
}

KIND_BY_EXT = {
    "py": "python",
    "c": "c",
    "h": "c-header",
    "S": "asm",
    "java": "java",
    "sh": "shell",
    "ts": "typescript",
    "md": "markdown",
    "json": "json",
    "jsonl": "jsonl",
    "csv": "csv",
    "tsv": "tsv",
    "txt": "text",
    "yml": "yaml",
    "bin": "binary",
    "gz": "gzip",
    "png": "image",
    "svg": "image",
}


def tracked_paths() -> list[str]:
    out = subprocess.run(
        ["git", "-C", str(REPO_ROOT), "ls-files"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    return sorted(line for line in out.splitlines() if line.strip())


def area_of(path: str) -> str:
    return path.split("/", 1)[0] if "/" in path else "(root)"


def kind_of(path: str) -> str:
    name = path.rsplit("/", 1)[-1]
    if "." not in name:
        return "no-extension"
    return KIND_BY_EXT.get(name.rsplit(".", 1)[-1], name.rsplit(".", 1)[-1])


def _counts(paths: list[str], key) -> list[tuple[str, int]]:
    from collections import Counter

    return sorted(Counter(key(p) for p in paths).items(), key=lambda kv: (-kv[1], kv[0]))


def render_summary(paths: list[str]) -> str:
    lines: list[str] = []
    lines.append(f"Tracked paths: **{len(paths)}**")
    lines.append("")
    lines.append("| Area | Role | Files |")
    lines.append("|---|---|---|")
    for area, n in _counts(paths, area_of):
        lines.append(f"| `{area}` | {AREA_ROLE.get(area, '—')} | {n} |")
    lines.append("")
    lines.append("| Kind | Files |")
    lines.append("|---|---|")
    for kind, n in _counts(paths, kind_of):
        lines.append(f"| {kind} | {n} |")
    return "\n".join(lines)


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["summary", "list"], help="summary table or full manifest")
    args = parser.parse_args(argv)

    paths = tracked_paths()
    if args.command == "summary":
        print(render_summary(paths))
        return 0
    for p in paths:
        print(f"{area_of(p)}\t{kind_of(p)}\t{p}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
