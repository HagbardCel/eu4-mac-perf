#!/usr/bin/env python3
"""Resolve a per-path lifecycle classification and an independent archival decision.

Classification precedence::

    explicit override  ->  curated rule (first match)  ->  consumer signal  ->  UNKNOWN

An archival decision is computed separately and conservatively:

* a positive consumer reference (import / include / CI ref) always blocks archival,
  regardless of which rule produced the classification (``dependency_conflict``);
* ``archive_candidate`` only flags retired/generated paths worth investigating;
* ``archive_eligible`` requires an *explicit* entry in ``approved_for_archive`` and
  no conflict. Nothing is eligible by default — static analysis never authorizes
  relocation.

Invariants: every tracked path gets exactly one resolved entry; no path is
``archive_eligible`` without explicit approval.
"""
from __future__ import annotations

import argparse
import csv
import fnmatch
import io
import json
import sys
from pathlib import Path

from .repo_graph import build as build_graph
from .repo_inventory import REPO_ROOT
from .repo_snapshot import RepoSnapshot

DEFAULT_POLICY_PATH = Path(__file__).resolve().with_name("repo_lifecycle.json")

ARCHIVAL_LIFECYCLES = frozenset({"RETIRED_EXPERIMENT", "GENERATED_DERIVED"})
LIFECYCLES = (
    "ACTIVE",
    "SHARED_DEPENDENCY",
    "FROZEN_VERIFIER",
    "RETIRED_EXPERIMENT",
    "GENERATED_DERIVED",
    "UNKNOWN",
)

CSV_COLUMNS = [
    "path",
    "lifecycle",
    "source",
    "owner",
    "reason",
    "dependency_conflict",
    "archive_candidate",
    "archive_eligible",
    "review_required",
    "analysis_status",
    "analysis_reason",
]


def match(pattern: str, path: str) -> bool:
    if pattern.endswith("/**"):
        prefix = pattern[:-3]
        return path == prefix or path.startswith(prefix + "/")
    if any(ch in pattern for ch in "*?["):
        return fnmatch.fnmatchcase(path, pattern)
    return path == pattern


def load_policy(path=None, data=None) -> dict:
    if data is not None:
        return data
    text = Path(path or DEFAULT_POLICY_PATH).read_text(encoding="utf-8")
    return json.loads(text)


def _classify(path, policy, summary, consumer_targets):
    for override in policy.get("overrides", []):
        if override.get("path") == path:
            return override["lifecycle"], override.get("reason", ""), "override", override.get("owner", "")
    for rule in policy.get("rules", []):
        if match(rule.get("match", ""), path):
            return rule["lifecycle"], rule.get("reason", ""), "rule", rule.get("owner", "")
    counts = summary.get(path, {})
    if counts.get("consumer_refs", 0) > 0:
        if counts.get("py_imported_by_tests", 0) > 0:
            return "SHARED_DEPENDENCY", "Imported by the test suite", "signal", ""
        return "ACTIVE", "Referenced by active code or CI", "signal", ""
    return "UNKNOWN", "No positive consumer reference detected", "default", ""


def resolve(snapshot: RepoSnapshot, graph, policy: dict) -> list[dict]:
    consumer_targets = graph.consumer_targets()
    summary = graph.summary_rows(snapshot.paths())
    approved = set(policy.get("approved_for_archive", []))
    rows = []
    for path in snapshot.paths():
        lifecycle, reason, source, owner = _classify(path, policy, summary, consumer_targets)
        conflict = lifecycle == "RETIRED_EXPERIMENT" and path in consumer_targets
        candidate = lifecycle in ARCHIVAL_LIFECYCLES and not conflict
        eligible = bool(path in approved and not conflict and lifecycle in ARCHIVAL_LIFECYCLES)
        status, status_reason = graph.analysis.get(path, ("unknown", "not analyzed"))
        rows.append(
            {
                "path": path,
                "lifecycle": lifecycle,
                "source": source,
                "owner": owner,
                "reason": reason,
                "dependency_conflict": conflict,
                "archive_candidate": candidate,
                "archive_eligible": eligible,
                "review_required": bool(conflict or candidate),
                "analysis_status": status,
                "analysis_reason": status_reason,
            }
        )
    return rows


def invariants(rows: list[dict], n_tracked: int, approved) -> dict:
    approved = set(approved)
    unique = {row["path"] for row in rows}
    eligible_unapproved = sum(
        1 for row in rows if row["archive_eligible"] and row["path"] not in approved
    )
    return {
        "n_tracked": n_tracked,
        "n_classified": len(rows),
        "n_unique_paths": len(unique),
        "all_classified": len(rows) == n_tracked and len(unique) == n_tracked,
        "n_dependency_conflicts": sum(1 for row in rows if row["dependency_conflict"]),
        "n_archive_candidates": sum(1 for row in rows if row["archive_candidate"]),
        "n_archive_eligible": sum(1 for row in rows if row["archive_eligible"]),
        "n_archive_eligible_but_unapproved": eligible_unapproved,
    }


def render_csv(rows: list[dict]) -> str:
    buf = io.StringIO()
    writer = csv.DictWriter(buf, fieldnames=CSV_COLUMNS, lineterminator="\n")
    writer.writeheader()
    for row in rows:
        out = {}
        for key in CSV_COLUMNS:
            value = row.get(key, "")
            out[key] = str(value).lower() if isinstance(value, bool) else value
        writer.writerow(out)
    return buf.getvalue()


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", default=str(REPO_ROOT))
    parser.add_argument("--ref", default="HEAD")
    parser.add_argument("--index", action="store_true")
    parser.add_argument("--policy", default=str(DEFAULT_POLICY_PATH))
    args = parser.parse_args(argv)

    snapshot = RepoSnapshot.from_index(args.root) if args.index else RepoSnapshot.from_ref(args.root, args.ref)
    graph = build_graph(snapshot)
    policy = load_policy(args.policy)
    rows = resolve(snapshot, graph, policy)
    sys.stdout.write(render_csv(rows))
    stats = invariants(rows, len(snapshot.paths()), policy.get("approved_for_archive", []))
    print(stats, file=sys.stderr)
    return 0 if stats["all_classified"] and stats["n_archive_eligible_but_unapproved"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
