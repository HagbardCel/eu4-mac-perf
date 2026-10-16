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

Preserved evidence carries two curated, non-cryptographic judgement fields:
``evidence_qualification`` (``seal_descriptor`` / ``verified_sealed_evidence`` /
``preserved_historical_evidence``) and ``verification_profile``. They record what
the policy asserts; the seal itself is checked by the evidence gate.
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
    "evidence_qualification",
    "verification_profile",
    "dependency_conflict",
    "archive_candidate",
    "archive_eligible",
    "review_required",
    "analysis_status",
    "analysis_reason",
]


def match(pattern: str, path: str) -> bool:
    """Path-aware glob: ``*``/``?`` stay within one path segment; ``**`` spans segments."""
    return _match_segments(pattern.split("/"), path.split("/"))


def _match_segments(psegs: list[str], tsegs: list[str]) -> bool:
    if not psegs:
        return not tsegs
    head = psegs[0]
    if head == "**":
        rest = psegs[1:]
        if not rest:
            return True
        return any(_match_segments(rest, tsegs[i:]) for i in range(len(tsegs) + 1))
    if not tsegs:
        return False
    if fnmatch.fnmatchcase(tsegs[0], head):
        return _match_segments(psegs[1:], tsegs[1:])
    return False


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


def _evidence_fields(path: str, lifecycle: str, policy: dict) -> tuple[str, str]:
    """Curated, non-cryptographic qualification of preserved evidence.

    Returns ``(evidence_qualification, verification_profile)``. These are
    *curated judgments*, not proof of integrity: the cryptographic seal is checked
    by the evidence gate (``verify-evidence``), while these fields record what the
    policy asserts about each path. ``verified_inventory`` members are asserted to
    be covered by the capsule named by ``descriptor`` at ``profile``; the
    descriptor itself is the seal, not a member of it.
    """
    if lifecycle != "FROZEN_VERIFIER":
        return "", ""
    for capsule in policy.get("evidence_capsules", []):
        profile = capsule.get("profile", "")
        if path == capsule.get("descriptor"):
            return "seal_descriptor", profile
        if path in set(capsule.get("verified_inventory", [])):
            return "verified_sealed_evidence", profile
    return "preserved_historical_evidence", ""


def resolve(snapshot: RepoSnapshot, graph, policy: dict) -> list[dict]:
    consumer_targets = graph.consumer_targets()
    summary = graph.summary_rows(snapshot.paths())
    approved = set(policy.get("approved_for_archive", []))
    rows = []
    for path in snapshot.paths():
        lifecycle, reason, source, owner = _classify(path, policy, summary, consumer_targets)
        conflict = lifecycle in ARCHIVAL_LIFECYCLES and path in consumer_targets
        candidate = lifecycle in ARCHIVAL_LIFECYCLES and not conflict
        eligible = bool(
            path in approved and lifecycle in ARCHIVAL_LIFECYCLES and path not in consumer_targets
        )
        qualification, profile = _evidence_fields(path, lifecycle, policy)
        status, status_reason = graph.analysis.get(path, ("unknown", "not analyzed"))
        rows.append(
            {
                "path": path,
                "lifecycle": lifecycle,
                "source": source,
                "owner": owner,
                "reason": reason,
                "evidence_qualification": qualification,
                "verification_profile": profile,
                "dependency_conflict": conflict,
                "archive_candidate": candidate,
                "archive_eligible": eligible,
                "review_required": bool(conflict or candidate),
                "analysis_status": status,
                "analysis_reason": status_reason,
            }
        )
    return rows


def invariants(rows: list[dict], n_tracked: int, approved, consumer_targets=frozenset()) -> dict:
    approved = set(approved)
    consumer_targets = set(consumer_targets)
    unique = {row["path"] for row in rows}
    eligible_unapproved = sum(
        1 for row in rows if row["archive_eligible"] and row["path"] not in approved
    )
    # Independent of how the lifecycle was resolved: no eligible path may have a
    # positively detected consumer reference.
    eligible_with_consumer = sum(
        1 for row in rows if row["archive_eligible"] and row["path"] in consumer_targets
    )
    all_classified = len(rows) == n_tracked and len(unique) == n_tracked
    return {
        "n_tracked": n_tracked,
        "n_classified": len(rows),
        "n_unique_paths": len(unique),
        "all_classified": all_classified,
        "n_dependency_conflicts": sum(1 for row in rows if row["dependency_conflict"]),
        "n_archive_candidates": sum(1 for row in rows if row["archive_candidate"]),
        "n_archive_eligible": sum(1 for row in rows if row["archive_eligible"]),
        "n_archive_eligible_but_unapproved": eligible_unapproved,
        "n_archive_eligible_with_consumer": eligible_with_consumer,
        "ok": all_classified and eligible_unapproved == 0 and eligible_with_consumer == 0,
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
    stats = invariants(
        rows, len(snapshot.paths()), policy.get("approved_for_archive", []), graph.consumer_targets()
    )
    print(stats, file=sys.stderr)
    return 0 if stats["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
