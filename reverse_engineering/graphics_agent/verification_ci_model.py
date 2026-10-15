"""FunctionModel for CI verification: must call get_evidence_content before verdict."""

from __future__ import annotations

import json
import re
from typing import Any

from pydantic_ai.messages import ModelResponse, TextPart, ToolCallPart, ToolReturnPart
from pydantic_ai.models.function import FunctionModel

from reverse_engineering.graphics_agent.verification_agent import VerificationOutcome


def _claim_text(messages: list[Any]) -> str:
    for msg in messages:
        for part in getattr(msg, "parts", ()) or ():
            text = getattr(part, "content", None)
            if isinstance(text, str) and "Claim:" in text:
                return text
    return ""


def _evidence_payload_from_messages(messages: list[Any]) -> dict[str, Any] | None:
    for msg in reversed(messages):
        for part in getattr(msg, "parts", ()) or ():
            if isinstance(part, ToolReturnPart):
                content = part.content
                if isinstance(content, dict):
                    return content
                if isinstance(content, str):
                    try:
                        return json.loads(content)
                    except json.JSONDecodeError:
                        pass
    return None


def _verdict_from_content(claim: str, payload: dict[str, Any], evidence_ids: list[str]) -> VerificationOutcome:
    claim_l = claim.lower()
    artifact = payload.get("artifact") or payload.get("content") or {}
    if isinstance(artifact, list) and artifact:
        artifact = artifact[0]
    symbol = str(
        artifact.get("ultimate_external_symbol")
        or artifact.get("callee_external_symbol")
        or ""
    )
    eid = payload.get("evidence_id") or (evidence_ids[0] if evidence_ids else "")
    if "malloc" in claim_l and "never" in claim_l:
        if symbol and "_gl" in symbol.lower():
            return VerificationOutcome(
                verdict="rejected",
                reasoning="Cited call-edge content names an OpenGL symbol, contradicting a malloc-only claim.",
                citations=[eid] if eid else [],
            )
        if artifact.get("callee_internal_rva") or artifact.get("caller_rva"):
            return VerificationOutcome(
                verdict="rejected",
                reasoning="Cited call-edge documents a graphics call chain, contradicting a malloc-only claim.",
                citations=[eid] if eid else [],
            )
    if re.search(r"exclusively|immediate-mode|without index", claim_l):
        return VerificationOutcome(
            verdict="needs_more_evidence",
            reasoning="Cited edge evidence does not establish the exclusive rendering mode asserted in the claim.",
            citations=[eid] if eid else [],
        )
    if "opengl" in claim_l or "draw" in claim_l:
        if symbol and "_gl" in symbol.lower():
            return VerificationOutcome(
                verdict="verified",
                reasoning="Cited call-edge content names an OpenGL symbol consistent with the draw-path claim.",
                citations=[eid] if eid else [],
            )
        if artifact.get("caller_rva") and (
            artifact.get("callee_internal_rva") or artifact.get("ultimate_external_symbol")
        ):
            return VerificationOutcome(
                verdict="verified",
                reasoning="Retrieved call-edge content documents a caller/callee step along the cited draw path.",
                citations=[eid] if eid else [],
            )
    return VerificationOutcome(
        verdict="needs_more_evidence",
        reasoning="Retrieved evidence content does not clearly support or contradict the claim.",
        citations=[eid] if eid else [],
    )


def build_ci_verification_model(evidence_ids: list[str]) -> FunctionModel:
    """Scripted model: always requests evidence content, then decides from tool return."""

    def model_fn(messages: list[Any], info: Any) -> ModelResponse:
        payload = _evidence_payload_from_messages(messages)
        if payload is None and evidence_ids:
            return ModelResponse(
                parts=[
                    ToolCallPart(
                        tool_name="get_evidence_content",
                        args={"evidence_id": evidence_ids[0]},
                        tool_call_id="ci-ev-1",
                    )
                ]
            )
        claim = _claim_text(messages)
        outcome = _verdict_from_content(claim, payload or {}, evidence_ids)
        return ModelResponse(parts=[TextPart(outcome.model_dump_json())])

    return FunctionModel(function=model_fn)
