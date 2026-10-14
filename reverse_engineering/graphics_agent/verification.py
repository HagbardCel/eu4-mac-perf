"""Claim verification orchestration."""

from __future__ import annotations

import sqlite3
from typing import Any

from reverse_engineering.graphics_agent.research_store import (
    bump_revision,
    get_claim,
    log_event,
    set_verification_status,
)
from reverse_engineering.graphics_agent.tools import EvidenceService
from reverse_engineering.graphics_agent.validation import REVIEW_MODEL_VERIFIED
from reverse_engineering.graphics_agent.verification_agent import (
    REVIEW_NEEDS_MORE,
    REVIEW_REJECTED,
    run_model_verification,
)

REVIEW_REJECTED = REVIEW_REJECTED
REVIEW_NEEDS_MORE = REVIEW_NEEDS_MORE


def run_verification(
    conn: sqlite3.Connection,
    investigation_id: str,
    claim_id: str,
    service: EvidenceService | None = None,
    model_override: Any = None,
) -> dict[str, Any]:
    if service is None:
        return {"ok": False, "error": "verification_requires_evidence_service"}
    result = run_model_verification(service, claim_id, model_override=model_override)
    if not result.get("ok"):
        return result
    outcome = result["outcome"]
    set_verification_status(conn, claim_id, "done")
    _set_review(conn, claim_id, outcome)
    log_event(
        conn,
        investigation_id,
        "claim_verification",
        {
            "claim_id": claim_id,
            "outcome": outcome,
            "verdict": result.get("verdict"),
            "reasoning": result.get("reasoning"),
            "citations": result.get("citations"),
        },
    )
    return result


def _set_review(conn: sqlite3.Connection, claim_id: str, review_status: str) -> None:
    import time

    conn.execute(
        "UPDATE claims SET review_status = ?, updated_at = ? WHERE claim_id = ?",
        (review_status, time.time(), claim_id),
    )
    conn.commit()
    bump_revision(conn)
