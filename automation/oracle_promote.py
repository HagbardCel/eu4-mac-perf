"""Transactional, evidence-bound promotion of reviewer counterexamples."""

from __future__ import annotations

import json
import os
import shutil
from pathlib import Path

from automation.replay_primitive import (
    ALLOWLISTED_REPLAYS,
    binding_matches_primitive_evidence,
    mutant_fails_out_of_range,
)
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


class PromotionDecision:
    PROMOTE = "promote"
    REJECT_ONLY = "reject_only"
    UNRESOLVED = "unresolved"
    CONFLICT = "conflict"


def classify_finding(finding: dict, evidence: dict, evidence_path: Path) -> str:
    if finding.get("kind") != "behavioral_counterexample":
        return PromotionDecision.REJECT_ONLY
    if finding.get("expected_provenance") == "unestablished":
        return PromotionDecision.REJECT_ONLY
    binding = finding.get("binding")
    if not isinstance(binding, dict):
        return PromotionDecision.REJECT_ONLY
    repro = finding.get("reproducer")
    if not repro:
        return PromotionDecision.UNRESOLVED
    errors = binding_matches_primitive_evidence(binding, evidence, evidence_path)
    if errors:
        if any("does not match evidence" in e and "expected" in e for e in errors):
            return PromotionDecision.CONFLICT
        return PromotionDecision.REJECT_ONLY
    if binding.get("replay_id") not in ALLOWLISTED_REPLAYS:
        return PromotionDecision.REJECT_ONLY
    return PromotionDecision.PROMOTE


def promote_grounded_findings(
    *,
    repo: Path,
    review: dict,
    oracle_dir: Path,
    trusted_helpers: list[str],
    checker_relpath: str,
) -> dict:
    """Attempt transactional promotion. Returns a result object (never label-trusts)."""
    errors = validate_review(review)
    if errors:
        raise ValueError("review invalid: " + "; ".join(errors))
    if review.get("outcome") != "rejected":
        return {"promoted": False, "reason": "outcome_not_rejected"}

    evidence_path = oracle_dir / "primitive-table.evidence.json"
    if not evidence_path.is_file():
        raise FileNotFoundError("frozen primitive evidence missing")
    evidence = json.loads(evidence_path.read_text(encoding="utf-8"))

    decisions = []
    to_promote = []
    for finding in review.get("findings") or []:
        decision = classify_finding(finding, evidence, evidence_path)
        decisions.append({"id": finding.get("id"), "decision": decision})
        if decision == PromotionDecision.PROMOTE:
            to_promote.append(finding)

    if not to_promote:
        return {
            "promoted": False,
            "decisions": decisions,
            "reason": "no_promotable_findings",
        }

    # Mutant gate: allowlisted replay must reject a known bad candidate.
    if not mutant_fails_out_of_range(evidence):
        raise RuntimeError("allowlisted replay failed mutant detection gate")

    versions = oracle_dir / "oracle_versions"
    versions.mkdir(parents=True, exist_ok=True)
    prior_version = 0
    current = oracle_dir / "CURRENT"
    if current.is_file():
        prior_version = int(current.read_text(encoding="utf-8").strip().lstrip("v") or "0")
    new_version = prior_version + 1
    version_name = f"v{new_version}"
    staging = versions / f".staging-{version_name}"
    if staging.exists():
        shutil.rmtree(staging)
    staging.mkdir(parents=True)
    reg_dir = staging / "regressions"
    reg_dir.mkdir()

    for finding in to_promote:
        binding = finding["binding"]
        payload = {
            "schema": "eu4-promoted-regression/v1",
            "id": finding["id"],
            "replay_id": binding["replay_id"],
            "observation": binding["observation"],
            "expected": binding["expected"],
            "evidence_ref": binding["evidence_ref"],
            "function_rva": binding["function_rva"],
            "executable_sha256": binding["executable_sha256"],
            "sequence": binding.get("sequence") or ["load_candidate", "compare_obs"],
            "promoted_from_review_candidate": review.get("candidate_commit"),
        }
        (reg_dir / f"{finding['id']}.json").write_text(
            canonical_json(payload), encoding="utf-8"
        )

    # Copy evidence into version for self-contained oracle
    shutil.copy2(evidence_path, staging / "primitive-table.evidence.json")

    members = []
    for rel in trusted_helpers:
        members.append(member_from_file(repo, rel, "trusted_helper"))
    members.append(member_from_file(repo, checker_relpath, "checker"))
    members.append(
        member_from_file(
            repo,
            str((staging / "primitive-table.evidence.json").relative_to(repo))
            if staging.is_relative_to(repo)
            else str(staging / "primitive-table.evidence.json"),
            "frozen_oracle",
        )
    )
    # Prefer relative paths under oracle_dir for regressions once published
    published = versions / version_name
    if published.exists():
        # Idempotent: already published this version name
        return {
            "promoted": True,
            "oracle_version": version_name,
            "decisions": decisions,
            "reused": True,
            "manifest_sha256": digest_manifest(
                load_manifest(oracle_dir / "verification-manifest.json")
            ),
        }

    # Build manifest against final published paths: move staging → published first
    staging.rename(published)
    # Fix member paths for published regressions
    members = []
    for rel in trusted_helpers:
        members.append(member_from_file(repo, rel, "trusted_helper"))
    members.append(member_from_file(repo, checker_relpath, "checker"))
    members.append(
        member_from_file(
            repo,
            str((oracle_dir / "primitive-table.evidence.json").relative_to(repo)),
            "frozen_oracle",
        )
    )
    members.append(
        member_from_file(
            repo,
            "automation/replay_primitive.py",
            "trusted_helper",
        )
    )
    for path in sorted((published / "regressions").glob("*.json")):
        members.append(
            member_from_file(repo, str(path.relative_to(repo)), "regression")
        )

    parent_manifest = None
    if (oracle_dir / "verification-manifest.json").is_file():
        parent_manifest = load_manifest(oracle_dir / "verification-manifest.json")

    manifest = build_manifest(
        members=members,
        extra={
            "oracle_version": new_version,
            "oracle_version_name": version_name,
            "promoted_from_review_candidate": review.get("candidate_commit"),
            "parent_manifest_sha256": (
                digest_manifest(parent_manifest) if parent_manifest else None
            ),
        },
    )
    errs = verify_manifest(repo, manifest)
    if errs:
        # Roll back published version directory
        shutil.rmtree(published, ignore_errors=True)
        raise RuntimeError("promoted manifest invalid: " + "; ".join(errs))

    # Atomic publish: write manifest temp then replace; then CURRENT pointer
    save_manifest(oracle_dir / "verification-manifest.json", manifest)
    tmp_current = oracle_dir / "CURRENT.tmp"
    tmp_current.write_text(version_name + "\n", encoding="utf-8")
    os.replace(tmp_current, current)

    return {
        "promoted": True,
        "oracle_version": version_name,
        "decisions": decisions,
        "reused": False,
        "manifest_sha256": digest_manifest(manifest),
    }
