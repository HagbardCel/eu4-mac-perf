"""Evidence existence and claim workflow validation."""

from __future__ import annotations

import re
from typing import Any

from reverse_engineering.graphics_agent.evidence_ids import digest_prefix


class ValidationError(Exception):
    pass


REVIEW_PROPOSED = "proposed"
REVIEW_EVIDENCE_VALIDATED = "evidence_validated"
REVIEW_MODEL_VERIFIED = "semantic_review_passed"
REVIEW_HUMAN_ACCEPTED = "human_accepted"

SCIENTIFIC_STATUSES = frozenset(
    {
        "observed_static",
        "supported_inference",
        "hypothesis",
        "contradicted",
        "unresolved",
    }
)

MEMBERSHIP_STATUSES = frozenset(
    {"graphics_owned", "shared_dependency", "non_graphics", "unknown"}
)


def evidence_exists(evidence_id: str, inventory_digest: str, known_ids: set[str]) -> bool:
    if evidence_id in known_ids:
        return True
    prefix = digest_prefix(inventory_digest)
    if evidence_id.startswith(f"call-edge:{prefix}:") or evidence_id.startswith(f"fn:{prefix}:"):
        return evidence_id in known_ids
    if evidence_id.startswith(f"ghidra:{prefix}:"):
        return evidence_id in known_ids
    return False


def validate_claim_evidence_refs(
    claim: dict[str, Any],
    inventory_digest: str,
    known_evidence_ids: set[str],
) -> list[str]:
    """Return list of errors; empty if evidence IDs resolve."""
    errors: list[str] = []
    refs = claim.get("evidence_ids") or []
    if not refs:
        errors.append("claim has no evidence_ids")
        return errors
    for eid in refs:
        if not evidence_exists(eid, inventory_digest, known_evidence_ids):
            errors.append(f"unknown or stale evidence_id: {eid}")
    return errors


def can_advance_review(current: str, target: str) -> bool:
    order = [
        REVIEW_PROPOSED,
        REVIEW_EVIDENCE_VALIDATED,
        REVIEW_MODEL_VERIFIED,
        REVIEW_HUMAN_ACCEPTED,
    ]
    try:
        return order.index(target) == order.index(current) + 1
    except ValueError:
        return False


def relevance_heuristic(claim_statement: str, evidence_meta: dict[str, Any]) -> bool:
    """Cheap deterministic filter before model verifier (not proof of relevance)."""
    stmt = claim_statement.lower()
    sym = (evidence_meta.get("ultimate_external_symbol") or evidence_meta.get("callee") or "").lower()
    if not sym:
        return True
    if "draw" in stmt and "draw" in sym:
        return True
    if "shader" in stmt and ("shader" in sym or "gl" in sym):
        return True
    if "buffer" in stmt and "buffer" in sym:
        return True
    # Irrelevant pairing: claim about buffer allocation citing unrelated draw edge only
    if "buffer" in stmt and "alloc" in stmt and re.search(r"draw", sym):
        return False
    return True
