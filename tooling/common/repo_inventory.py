#!/usr/bin/env python3
"""Deterministic, read-only inventory of Git-tracked paths.

This tool reports *what Git tracks* at a pinned revision. It does not assign
lifecycle status — that is the curated layer in ``repo_lifecycle``.

Usage::

    PYTHONPATH=. python -m tooling.common.repo_inventory summary --ref 8033e35
    PYTHONPATH=. python -m tooling.common.repo_inventory list --ref 8033e35
    PYTHONPATH=. python -m tooling.common.repo_inventory csv  --ref 8033e35

``--ref`` reads a Git tree (reproducible). ``--index`` is a separate, explicitly
named mode that inventories the current index instead.
"""
from __future__ import annotations

import argparse
import csv
import io
import sys
from collections import Counter
from pathlib import Path

from .repo_snapshot import RepoSnapshot

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
    "yaml": "yaml",
    "bin": "binary",
    "gz": "gzip",
    "png": "image",
    "svg": "image",
}

# Extensions whose contents the graph analyzer treats as parseable text.
TEXT_EXT = frozenset(
    {
        "py", "c", "h", "cc", "cpp", "hpp", "m", "mm", "S", "s",
        "java", "sh", "md", "json", "jsonl", "csv", "tsv", "txt",
        "yml", "yaml", "sha256", "gitignore", "log", "stdout", "stderr",
        "recipe", "inc", "def",
    }
)


def area_of(path: str) -> str:
    return path.split("/", 1)[0] if "/" in path else "(root)"


def ext_of(path: str) -> str:
    name = path.rsplit("/", 1)[-1]
    return name.rsplit(".", 1)[-1] if "." in name else ""


def kind_of(path: str) -> str:
    name = path.rsplit("/", 1)[-1]
    if "." not in name:
        return "no-extension"
    ext = name.rsplit(".", 1)[-1]
    return KIND_BY_EXT.get(ext, ext)


def inventory_rows(snapshot: RepoSnapshot) -> list[dict]:
    rows = []
    for path in snapshot.paths():
        entry = snapshot.entry(path)
        rows.append(
            {
                "path": path,
                "area": area_of(path),
                "kind": kind_of(path),
                "size_bytes": entry.size if entry else -1,
            }
        )
    return rows


def _counts(rows: list[dict], column: str) -> list[tuple[str, int]]:
    counts = Counter(row[column] for row in rows)
    return sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))


def render_summary(snapshot: RepoSnapshot) -> str:
    rows = inventory_rows(snapshot)
    total = len(rows)
    lines = []
    lines.append(f"Tracked paths: **{total}**")
    lines.append("")
    lines.append("| Area | Role | Files |")
    lines.append("|---|---|---|")
    for area, n in _counts(rows, "area"):
        lines.append(f"| `{area}` | {AREA_ROLE.get(area, '—')} | {n} |")
    lines.append("")
    lines.append("| Kind | Files |")
    lines.append("|---|---|")
    for kind, n in _counts(rows, "kind"):
        lines.append(f"| {kind} | {n} |")
    lines.append("")
    lines.append(
        "Invariant: Σkinds = Σareas = N = "
        f"{sum(n for _, n in _counts(rows, 'kind'))} = "
        f"{sum(n for _, n in _counts(rows, 'area'))} = {total}."
    )
    return "\n".join(lines)


CSV_COLUMNS = ["path", "area", "kind", "size_bytes"]


def render_csv(rows: list[dict]) -> str:
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=CSV_COLUMNS, lineterminator="\n")
    writer.writeheader()
    for row in rows:
        writer.writerow({k: row.get(k, "") for k in CSV_COLUMNS})
    return buf.getvalue()


def _build_snapshot(args) -> RepoSnapshot:
    if args.index:
        return RepoSnapshot.from_index(args.root)
    return RepoSnapshot.from_ref(args.root, args.ref)


def make_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["summary", "list", "csv"])
    parser.add_argument("--root", default=str(REPO_ROOT), help="repository root")
    parser.add_argument("--ref", default="HEAD", help="Git tree-ish to inventory (default: HEAD)")
    parser.add_argument("--index", action="store_true", help="inventory the current index instead of a ref")
    return parser


def main(argv: list[str]) -> int:
    args = make_parser().parse_args(argv)
    snapshot = _build_snapshot(args)
    if args.command == "summary":
        print(render_summary(snapshot))
    elif args.command == "list":
        for path in snapshot.paths():
            print(f"{area_of(path)}\t{kind_of(path)}\t{path}")
    else:
        sys.stdout.write(render_csv(inventory_rows(snapshot)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
