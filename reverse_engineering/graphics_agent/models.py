"""Pydantic schemas for agent tools and research records."""

from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class QueryScopeStatus(str, Enum):
    COMPLETE = "complete_for_requested_scope"
    PARTIAL = "partial"
    UNRESOLVED = "unresolved"
    UNSUPPORTED = "unsupported"


class EvidenceProvenance(str, Enum):
    MACHINE_INSTRUCTION = "machine_instruction"
    GHIDRA_DISASSEMBLY = "ghidra_disassembly"
    GHIDRA_REFERENCE = "ghidra_reference"
    GHIDRA_TYPE_INFERENCE = "ghidra_type_inference"
    GHIDRA_PCODE = "ghidra_pcode"
    INVENTORY_CALL_GRAPH = "inventory_call_graph"
    RUNTIME_MEASUREMENT = "runtime_measurement"


class SemanticQueryResult(BaseModel):
    function_rva: str
    query: str
    status: QueryScopeStatus
    status_detail: str | None = Field(
        default=None,
        description="e.g. no_accesses_identified vs recovery_failed",
    )
    facts: list[dict[str, Any]] = Field(default_factory=list)
    interpretations: list[dict[str, Any]] = Field(default_factory=list)
    evidence_artifact_ids: list[str] = Field(default_factory=list)


class SubmitFindingRequest(BaseModel):
    claim_id: str
    statement: str
    scientific_status: str = "hypothesis"
    evidence_ids: list[str] = Field(default_factory=list)
    limitations: list[str] | None = None
    membership_status: str | None = None
    entry_rva: str | None = None
