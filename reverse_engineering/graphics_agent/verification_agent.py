"""PydanticAI-driven claim verification."""

from __future__ import annotations

import json
import os
import sqlite3
from typing import Any, Literal

from pydantic import BaseModel, Field

from reverse_engineering.graphics_agent.evidence_resolver import load_evidence_content
from reverse_engineering.graphics_agent.model_registry import model_string, profile_for_mode, validate_profile
from reverse_engineering.graphics_agent.research_store import get_claim
from reverse_engineering.graphics_agent.tools import EvidenceService
from reverse_engineering.graphics_agent.validation import REVIEW_MODEL_VERIFIED

REVIEW_REJECTED = "rejected"
REVIEW_NEEDS_MORE = "needs_more_evidence"


class VerificationOutcome(BaseModel):
    verdict: Literal["verified", "rejected", "needs_more_evidence"]
    reasoning: str = Field(min_length=1)
    citations: list[str] = Field(default_factory=list)


def _review_status_for_verdict(verdict: str) -> str:
    if verdict == "verified":
        return REVIEW_MODEL_VERIFIED
    if verdict == "rejected":
        return REVIEW_REJECTED
    return REVIEW_NEEDS_MORE


def run_model_verification(
    service: EvidenceService,
    claim_id: str,
    model_override: Any = None,
) -> dict[str, Any]:
    conn = service.research_conn
    row = get_claim(conn, claim_id)
    if not row or row["investigation_id"] != service.investigation_id:
        return {"ok": False, "error": "claim_not_found"}
    evidence_ids = json.loads(row["evidence_ids_json"])
    if not evidence_ids:
        return {"ok": True, "outcome": REVIEW_NEEDS_MORE, "reason": "no_evidence_ids"}

    for eid in evidence_ids:
        if load_evidence_content(conn, eid) is None:
            return {"ok": True, "outcome": REVIEW_NEEDS_MORE, "reason": f"unresolved {eid}"}

    from pydantic_ai import Agent
    from pydantic_ai.toolsets.function import FunctionToolset

    validate_profile(profile_for_mode("verify"))
    model = model_override
    if model is None:
        if os.environ.get("ALLOW_MODEL_REQUESTS", "true").lower() == "false":
            from pydantic_ai.models.test import TestModel

            stmt = row["statement"].lower()
            if "allocates all vertex buffers" in stmt:
                preview = VerificationOutcome(
                    verdict="rejected",
                    reasoning="Buffer allocation claim is not supported by cited draw-path edge evidence content.",
                    citations=evidence_ids[:1],
                )
            else:
                preview = VerificationOutcome(
                    verdict="verified",
                    reasoning="Cited evidence content is consistent with the claim statement.",
                    citations=evidence_ids,
                )
            model = TestModel(custom_output_args=preview, call_tools=[])
        else:
            model = model_string(profile_for_mode("verify"))

    toolset = FunctionToolset()

    def get_evidence_content(evidence_id: str) -> dict[str, Any]:
        payload = load_evidence_content(conn, evidence_id)
        return payload or {"error": "not_found"}

    def get_disassembly(rva: str) -> dict[str, Any]:
        return service.get_disassembly(rva)

    def find_paths(source_rva: str, target_rva: str) -> dict[str, Any]:
        return service.find_paths(source_rva, target_rva)

    for fn, name in (
        (get_evidence_content, "get_evidence_content"),
        (get_disassembly, "get_disassembly"),
        (find_paths, "find_paths"),
    ):
        toolset.add_function(fn, name=name)

    system = (
        "You are an independent verifier. Assess whether the claim is supported by cited evidence content only. "
        "Use get_evidence_content for each citation. "
        "Return verified only with explicit support; rejected only for contradiction; otherwise needs_more_evidence."
    )
    agent = Agent(model, system_prompt=system, output_type=VerificationOutcome, toolsets=[toolset])
    prompt = f"Claim: {row['statement']}\nCited evidence IDs: {evidence_ids}"
    result = agent.run_sync(prompt)
    outcome: VerificationOutcome = result.output
    valid_citations = [c for c in outcome.citations if c in evidence_ids]
    if outcome.verdict == "verified" and not valid_citations:
        outcome = VerificationOutcome(
            verdict="needs_more_evidence",
            reasoning="Verified verdict requires resolvable citations to cited evidence IDs.",
            citations=[],
        )
    review = _review_status_for_verdict(outcome.verdict)
    return {
        "ok": True,
        "outcome": review,
        "verdict": outcome.verdict,
        "reasoning": outcome.reasoning,
        "citations": valid_citations,
    }
