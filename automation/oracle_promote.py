"""Append-only promotion of grounded reviewer counterexamples into the oracle."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

from automation.review_result import validate_review
from automation.verification_manifest import (
    build_manifest,
    canonical_json,
    digest_manifest,
    file_sha256,
    load_manifest,
    member_from_file,
    save_manifest,
    verify_manifest,
)


def promote_grounded_findings(
    *,
    repo: Path,
    review: dict,
    oracle_dir: Path,
    trusted_helpers: list[str],
    checker_relpath: str,
) -> dict | None:
    """If review has grounded behavioral counterexamples, append regressions.

    Returns the new manifest dict, or None when nothing was promotable.
    Findings with ``expected_provenance`` of ``unestablished`` are skipped.
    """
    errors = validate_review(review)
    if errors:
        raise ValueError("review invalid: " + "; ".join(errors))
    if review.get("outcome") != "rejected":
        return None

    regressions_dir = oracle_dir / "regressions"
    regressions_dir.mkdir(parents=True, exist_ok=True)
    added: list[Path] = []
    for finding in review["findings"]:
        if finding.get("kind") != "behavioral_counterexample":
            continue
        if finding.get("expected_provenance") == "unestablished":
            continue
        if finding.get("expected_provenance") == "reconstructed_model":
            # Model-only expectations are not promoted to ground truth.
            continue
        repro = finding.get("reproducer")
        src = Path(repro)
        if not src.is_absolute():
            src = repo / repro
        if not src.is_file():
            raise FileNotFoundError(f"reproducer missing: {repro}")
        # Require a companion expected-obs file next to the reproducer when present,
        # or a JSON reproducer that embeds expected observation with provenance.
        dest = regressions_dir / f"{finding['id']}.json"
        payload = json.loads(src.read_text(encoding="utf-8"))
        if payload.get("expected_provenance") not in {
            "original_execution",
            "instruction_static",
        }:
            continue
        if "expected" not in payload:
            raise ValueError(f"{repro} missing expected observation")
        dest.write_text(canonical_json(payload), encoding="utf-8")
        added.append(dest)

    if not added:
        return None

    prior = oracle_dir / "verification-manifest.json"
    prior_manifest = load_manifest(prior) if prior.is_file() else None
    version = 1
    if prior_manifest:
        version = int(prior_manifest.get("oracle_version", 1)) + 1
        # Preserve prior member paths that still exist.
        archive = oracle_dir / f"verification-manifest.v{version - 1}.json"
        shutil.copy2(prior, archive)

    members = []
    for rel in trusted_helpers:
        members.append(member_from_file(repo, rel, "trusted_helper"))
    members.append(member_from_file(repo, checker_relpath, "checker"))
    evidence = oracle_dir / "primitive-table.evidence.json"
    if evidence.is_file():
        members.append(
            member_from_file(
                repo, str(evidence.relative_to(repo)), "frozen_oracle"
            )
        )
    for path in sorted(regressions_dir.glob("*.json")):
        members.append(
            member_from_file(repo, str(path.relative_to(repo)), "regression")
        )

    manifest = build_manifest(
        members=members,
        extra={
            "oracle_version": version,
            "promoted_from_review_candidate": review.get("candidate_commit"),
            "parent_manifest_sha256": (
                digest_manifest(prior_manifest) if prior_manifest else None
            ),
        },
    )
    errors = verify_manifest(repo, manifest)
    if errors:
        raise RuntimeError("promoted manifest invalid: " + "; ".join(errors))
    save_manifest(prior, manifest)
    return manifest
