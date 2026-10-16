#!/usr/bin/env python3
"""Generate (or verify) the repository audit artifacts from pinned inputs.

Writes, deterministically and with no timestamps or absolute paths:

* ``docs/repo-inventory.csv``    — one row per tracked path (area/kind/size)
* ``docs/repo-edges.csv``        — normalized reference edges (auditable)
* ``docs/repo-lifecycle.csv``    — resolved lifecycle + independent archival decision
* ``docs/repo-audit-context.json`` — the identity triple (source, policy, tools)
* the marked generated block inside ``docs/repo-map.md``

Usage::

    PYTHONPATH=. python -m tooling.common.repo_audit --ref 8033e35 --write
    PYTHONPATH=. python -m tooling.common.repo_audit --ref 8033e35 --check
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections import Counter
from pathlib import Path

from . import repo_graph, repo_lifecycle
from .repo_graph import build as build_graph
from .repo_inventory import CSV_COLUMNS as INVENTORY_COLUMNS
from .repo_inventory import render_csv as render_inventory_csv
from .repo_inventory import render_summary
from .repo_lifecycle import load_policy, resolve
from .repo_snapshot import RepoSnapshot

REPO_ROOT = Path(__file__).resolve().parents[2]
TOOLS_DIR = Path(__file__).resolve().parent
TOOL_FILES = ("repo_snapshot.py", "repo_inventory.py", "repo_graph.py", "repo_lifecycle.py", "repo_audit.py")

BEGIN_MARKER = "<!-- BEGIN GENERATED: repo-inventory -->"
END_MARKER = "<!-- END GENERATED: repo-inventory -->"

EDGE_COLUMNS = ["source", "target", "relationship", "confidence", "location"]


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _rel(path: Path, root: Path) -> str:
    return path.resolve().relative_to(root.resolve()).as_posix()


def render_edges_csv(graph) -> str:
    lines = [",".join(EDGE_COLUMNS)]
    for edge in graph.edges:
        fields = [edge.source, edge.target, edge.relationship, edge.confidence, edge.location]
        lines.append(",".join('"' + f.replace('"', '""') + '"' if "," in f or '"' in f else f for f in fields))
    return "\n".join(lines) + "\n"


def render_block(snapshot, rows, graph, lifecycle_rows, stats) -> str:
    lifecycle_counts = Counter(row["lifecycle"] for row in lifecycle_rows)
    status_counts = Counter(row["analysis_status"] for row in lifecycle_rows)
    source_counts = Counter(row["source"] for row in lifecycle_rows)
    rel_counts = Counter(edge.relationship for edge in graph.edges)

    lines = [render_summary(snapshot), ""]
    lines.append("| Lifecycle | Files |")
    lines.append("|---|---|")
    for name, n in sorted(lifecycle_counts.items(), key=lambda kv: (-kv[1], kv[0])):
        lines.append(f"| {name} | {n} |")
    lines.append("")
    lines.append("| Classification source | Files |")
    lines.append("|---|---|")
    for name, n in sorted(source_counts.items(), key=lambda kv: (-kv[1], kv[0])):
        lines.append(f"| {name} | {n} |")
    lines.append("")
    lines.append("| Analysis coverage | Files |")
    lines.append("|---|---|")
    for name, n in sorted(status_counts.items(), key=lambda kv: (-kv[1], kv[0])):
        lines.append(f"| {name} | {n} |")
    lines.append("")
    lines.append("| Reference edge | Count |")
    lines.append("|---|---|")
    for name, n in sorted(rel_counts.items(), key=lambda kv: (-kv[1], kv[0])):
        lines.append(f"| {name} | {n} |")
    lines.append("")
    lines.append("Audit invariants:")
    lines.append("")
    lines.append(f"- `N_classified = N_tracked` → `{stats['n_classified']} = {stats['n_tracked']}` "
                 f"({'ok' if stats['all_classified'] else 'FAIL'})")
    lines.append(f"- `N_archive_eligible_but_unapproved = {stats['n_archive_eligible_but_unapproved']}` "
                 f"({'ok' if stats['n_archive_eligible_but_unapproved'] == 0 else 'FAIL'})")
    lines.append(f"- dependency conflicts: {stats['n_dependency_conflicts']}; "
                 f"archive candidates: {stats['n_archive_candidates']}; "
                 f"archive eligible: {stats['n_archive_eligible']}")
    return "\n".join(lines)


def build_context(root, snapshot, policy, policy_path, stats) -> dict:
    policy_bytes = Path(policy_path).read_bytes()
    tools = {}
    for name in TOOL_FILES:
        tools[f"tooling/common/{name}"] = _sha256_bytes((TOOLS_DIR / name).read_bytes())
    return {
        "policy_version": policy.get("policy_version"),
        "source": snapshot.identity(),
        "policy": {"path": _rel(Path(policy_path), root), "sha256": _sha256_bytes(policy_bytes)},
        "tools": tools,
        "invariants": stats,
    }


def generate(root: Path, ref: str, index: bool, policy_path: Path) -> dict[str, str]:
    root = root.resolve()
    snapshot = RepoSnapshot.from_index(root) if index else RepoSnapshot.from_ref(root, ref)
    graph = build_graph(snapshot)
    policy = load_policy(policy_path)
    rows = repo_lifecycle.resolve(snapshot, graph, policy)
    stats = repo_lifecycle.invariants(rows, len(snapshot.paths()), policy.get("approved_for_archive", []))

    return {
        "docs/repo-inventory.csv": render_inventory_csv(rows_inventory(snapshot)),
        "docs/repo-edges.csv": render_edges_csv(graph),
        "docs/repo-lifecycle.csv": repo_lifecycle.render_csv(rows),
        "docs/repo-audit-context.json": json.dumps(build_context(root, snapshot, policy, policy_path, stats),
                                                   indent=2, sort_keys=True) + "\n",
        "__map_block__": render_block(snapshot, rows_inventory(snapshot), graph, rows, stats),
    }


def rows_inventory(snapshot):
    from .repo_inventory import inventory_rows

    return inventory_rows(snapshot)


def _updated_map(existing: str, block: str) -> str:
    if BEGIN_MARKER not in existing or END_MARKER not in existing:
        raise SystemExit("docs/repo-map.md is missing the generated-block markers")
    start = existing.index(BEGIN_MARKER) + len(BEGIN_MARKER)
    end = existing.index(END_MARKER)
    return existing[:start] + "\n\n" + block.strip() + "\n\n" + existing[end:]


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", default=str(REPO_ROOT))
    parser.add_argument("--ref", default="HEAD")
    parser.add_argument("--index", action="store_true")
    parser.add_argument("--policy", default=str(repo_lifecycle.DEFAULT_POLICY_PATH))
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--write", action="store_true")
    group.add_argument("--check", action="store_true")
    args = parser.parse_args(argv)

    root = Path(args.root).resolve()
    outputs = generate(root, args.ref, args.index, Path(args.policy))
    map_path = root / "docs" / "repo-map.md"
    new_map = _updated_map(map_path.read_text(encoding="utf-8"), outputs.pop("__map_block__"))

    if args.write:
        for relpath, content in outputs.items():
            (root / relpath).write_text(content, encoding="utf-8")
        map_path.write_text(new_map, encoding="utf-8")
        print(f"wrote {len(outputs)} artifacts + docs/repo-map.md generated block")
        return 0

    mismatches = []
    for relpath, content in outputs.items():
        path = root / relpath
        if not path.exists() or path.read_text(encoding="utf-8") != content:
            mismatches.append(relpath)
    if map_path.read_text(encoding="utf-8") != new_map:
        mismatches.append("docs/repo-map.md (generated block)")
    if mismatches:
        print("audit artifacts are stale:")
        for name in mismatches:
            print(f"  {name}")
        return 1
    print("audit artifacts reproduce exactly")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
