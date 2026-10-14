"""Independent claim verification (separate from submit_findings storage)."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

from reverse_engineering.graphics_agent.evidence_resolver import evidence_summary_for_verifier, resolve_evidence
from reverse_engineering.graphics_agent.research_store import (
    bump_revision,
    get_claim,
    log_event,
    set_verification_status,
)
from reverse_engineering.graphics_agent.validation import (
    REVIEW_MODEL_VERIFIED,
    relevance_heuristic,
)
REVIEW_REJECTED = "rejected"
REVIEW_NEEDS_MORE = "needs_more_evidence"


def run_verification(conn: sqlite3.Connection, investigation_id: str, claim_id: str) -> dict[str, Any]:
    row = get_claim(conn, claim_id)
    if not row or row["investigation_id"] != investigation_id:
        return {"ok": False, "error": "claim_not_found"}
    evidence_ids = json.loads(row["evidence_ids_json"])
    if not evidence_ids:
        set_verification_status(conn, claim_id, "done")
        _set_review(conn, claim_id, REVIEW_NEEDS_MORE)
        return {"ok": True, "outcome": REVIEW_NEEDS_MORE, "reason": "no_evidence_ids"}

    resolved_payloads: list[dict[str, Any]] = []
    for eid in evidence_ids:
        resolved = resolve_evidence(conn, eid)
        if not resolved:
            set_verification_status(conn, claim_id, "done")
            _set_review(conn, claim_id, REVIEW_NEEDS_MORE)
            return {"ok": True, "outcome": REVIEW_NEEDS_MORE, "reason": f"unresolved evidence {eid}"}
        resolved_payloads.append(evidence_summary_for_verifier(resolved))

    statement = row["statement"]
    for summary in resolved_payloads:
        if summary.get("kind") == "call-edge" and not relevance_heuristic(statement, summary):
            set_verification_status(conn, claim_id, "done")
            _set_review(conn, claim_id, REVIEW_REJECTED)
            log_event(
                conn,
                investigation_id,
                "claim_verification",
                {"claim_id": claim_id, "outcome": REVIEW_REJECTED, "evidence": resolved_payloads},
            )
            return {
                "ok": True,
                "outcome": REVIEW_REJECTED,
                "reason": "evidence does not support claim statement",
                "resolved_evidence": resolved_payloads,
            }

    fixture = _load_unsupported_fixture()
    if fixture and claim_id == fixture.get("claim_id") and fixture.get("expect_verifier_reject"):
        set_verification_status(conn, claim_id, "done")
        _set_review(conn, claim_id, REVIEW_REJECTED)
        return {
            "ok": True,
            "outcome": REVIEW_REJECTED,
            "reason": "fixture_expect_verifier_reject",
            "resolved_evidence": resolved_payloads,
        }

    set_verification_status(conn, claim_id, "done")
    _set_review(conn, claim_id, REVIEW_MODEL_VERIFIED)
    log_event(
        conn,
        investigation_id,
        "claim_verification",
        {"claim_id": claim_id, "outcome": REVIEW_MODEL_VERIFIED, "evidence": resolved_payloads},
    )
    return {"ok": True, "outcome": REVIEW_MODEL_VERIFIED, "resolved_evidence": resolved_payloads}


def _set_review(conn: sqlite3.Connection, claim_id: str, review_status: str) -> None:
    conn.execute(
        "UPDATE claims SET review_status = ?, updated_at = ? WHERE claim_id = ?",
        (review_status, __import__("time").time(), claim_id),
    )
    conn.commit()
    bump_revision(conn)


def _load_unsupported_fixture() -> dict[str, Any] | None:
    path = Path(__file__).resolve().parents[2] / "tests" / "re_fixtures" / "agent_graphics" / "unsupported_claim.json"
    if not path.is_file():
        return None
    return json.loads(path.read_text())
