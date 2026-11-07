"""Machine-validated eu4-review/v1 reviewer results."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

SCHEMA = "eu4-review/v1"
OUTCOMES = frozenset({"qualified", "rejected", "unresolved"})
SEVERITIES = frozenset({"P0", "P1", "P2", "P3"})
KINDS = frozenset(
    {
        "behavioral_counterexample",
        "missing_dependency",
        "evidence_gap",
        "integrity",
        "other",
    }
)
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")


def load_review(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def validate_review(
    review: dict,
    *,
    expected_candidate_commit: str | None = None,
    expected_oracle_manifest_sha256: str | None = None,
) -> list[str]:
    errors: list[str] = []
    if review.get("schema") != SCHEMA:
        errors.append(f"schema must be {SCHEMA}")
    candidate = review.get("candidate_commit")
    if not isinstance(candidate, str) or not COMMIT_RE.match(candidate):
        errors.append("candidate_commit must be a 40-char lowercase hex git SHA")
    elif expected_candidate_commit and candidate != expected_candidate_commit:
        errors.append(
            f"candidate_commit mismatch: review has {candidate}, "
            f"expected {expected_candidate_commit}"
        )
    oracle = review.get("oracle_manifest_sha256")
    if not isinstance(oracle, str) or not SHA256_RE.match(oracle):
        errors.append("oracle_manifest_sha256 must be a 64-char hex digest")
    elif (
        expected_oracle_manifest_sha256
        and oracle != expected_oracle_manifest_sha256
    ):
        errors.append("oracle_manifest_sha256 mismatch")
    outcome = review.get("outcome")
    if outcome not in OUTCOMES:
        errors.append("outcome must be qualified|rejected|unresolved")
    findings = review.get("findings")
    if not isinstance(findings, list):
        errors.append("findings must be a list")
        return errors
    if outcome == "rejected" and not findings:
        errors.append("rejected requires at least one finding")
    if outcome == "qualified" and findings:
        # Allow empty or informational; P1+ findings on qualified are inconsistent.
        for finding in findings:
            if isinstance(finding, dict) and finding.get("severity") in {"P0", "P1"}:
                errors.append("qualified review cannot include P0/P1 findings")
                break
    for index, finding in enumerate(findings):
        errors.extend(_finding_errors(index, finding, outcome))
    return errors


def _finding_errors(index: int, finding: Any, outcome: str) -> list[str]:
    prefix = f"findings[{index}]"
    errors: list[str] = []
    if not isinstance(finding, dict):
        return [f"{prefix} must be an object"]
    for key in ("id", "severity", "kind"):
        if key not in finding:
            errors.append(f"{prefix} missing {key}")
    if finding.get("severity") not in SEVERITIES:
        errors.append(f"{prefix}.severity invalid")
    if finding.get("kind") not in KINDS:
        errors.append(f"{prefix}.kind invalid")
    if finding.get("kind") == "behavioral_counterexample":
        repro = finding.get("reproducer")
        if not isinstance(repro, str) or not repro:
            errors.append(f"{prefix}.reproducer required for behavioral_counterexample")
        prov = finding.get("expected_provenance")
        if prov not in {
            "original_execution",
            "instruction_static",
            "reconstructed_model",
            "unestablished",
        }:
            errors.append(f"{prefix}.expected_provenance invalid")
        if outcome == "rejected" and prov == "unestablished":
            # Still a valid rejection finding, but not promotable to oracle.
            pass
    return errors


def write_review(path: Path, review: dict) -> None:
    errors = validate_review(review)
    if errors:
        raise ValueError("invalid review: " + "; ".join(errors))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(review, indent=2, sort_keys=True) + "\n", encoding="utf-8")
