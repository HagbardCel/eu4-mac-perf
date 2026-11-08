"""Reviewer-authored adversarial cases for contract qualification.

The reviewer supplies these cases outside the candidate worktree. The trusted
runner resolves each forged evidence item against genuine original records and
requires the candidate validator to reject it for the stated semantic reason.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

SCHEMA = "eu4-gfx-adversarial-cases/v1"
MUTATION_CLASSES = frozenset(
    {
        "wrong_selected_object",
        "cross_resource",
        "wrong_pixels",
        "stale_call",
        "cross_session",
        "malformed_association",
        "wrong_state_decode",
        "wrong_scope",
        "wrong_field",
    }
)


class AdversarialCaseError(RuntimeError):
    pass


def load_cases(path: Path) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def cases_digest(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def validate_cases(
    document: dict,
    *,
    candidate_commit: str,
    contract_version: str,
    checkpoint_sha256: str,
    required_fields: set[str],
) -> list[str]:
    errors: list[str] = []
    if not isinstance(document, dict):
        return ["adversarial cases are not an object"]
    if document.get("schema") != SCHEMA:
        errors.append(f"schema must be {SCHEMA}")
    if document.get("candidate_commit") != candidate_commit:
        errors.append("adversarial cases are not bound to the reviewed contract commit")
    if document.get("contract_version") != contract_version:
        errors.append("adversarial cases version differs from the contract version")
    if document.get("checkpoint_sha256") != checkpoint_sha256:
        errors.append("adversarial cases are not bound to the checkpoint")
    fields = document.get("fields")
    if not isinstance(fields, list) or not fields:
        return errors + ["adversarial cases have no fields"]
    seen: set[str] = set()
    for index, case in enumerate(fields):
        prefix = f"fields[{index}]"
        if not isinstance(case, dict):
            errors.append(f"{prefix} is not an object")
            continue
        field = case.get("field")
        if not isinstance(field, str) or not field:
            errors.append(f"{prefix} has no field")
            continue
        seen.add(field)
        if case.get("structural_validity") != "valid":
            errors.append(f"{prefix} must be structurally valid")
        if case.get("mutation_class") not in MUTATION_CLASSES:
            errors.append(f"{prefix} has no meaningful mutation class")
        if "forged_value" not in case:
            errors.append(f"{prefix} has no forged value")
        if not isinstance(case.get("forged_evidence"), list) or not case["forged_evidence"]:
            errors.append(f"{prefix} has no forged evidence")
        if not isinstance(case.get("why_semantically_wrong"), str) \
                or not case["why_semantically_wrong"].strip():
            errors.append(f"{prefix} does not explain why the case is wrong")
        if not isinstance(case.get("expected_error_prefix"), str) \
                or not case["expected_error_prefix"].strip():
            errors.append(f"{prefix} has no expected error prefix")
    missing = sorted(required_fields - seen)
    if missing:
        errors.append("adversarial cases omit fields: " + ", ".join(missing))
    return errors
