"""PydanticAI investigation runner with mode-specific tools."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from reverse_engineering.graphics_agent.model_registry import model_string, profile_for_mode
from reverse_engineering.graphics_agent.session_checkpoint import (
    connect as checkpoint_connect,
    load_session_meta,
    parse_message_history,
    persist_run_messages,
    sha256_text,
    validate_resume_meta,
    write_session_meta,
)
from reverse_engineering.graphics_agent.tools import EvidenceService
from reverse_engineering.graphics_agent.verification import run_verification

PROMPTS_DIR = Path(__file__).resolve().parent / "prompts"

MODE_TOOLS: dict[str, list[str]] = {
    "discovery": [
        "search_functions",
        "get_function_context",
        "get_subgraph",
        "find_paths",
        "get_api_endpoints",
        "get_architecture_overview",
        "get_runtime_evidence",
        "submit_findings",
        "propose_investigation",
    ],
    "reconstruct": [
        "get_function_context",
        "get_pseudocode",
        "get_disassembly",
        "get_references",
        "get_function_signature",
        "get_type_layout",
        "get_data_accesses",
        "get_indirect_dispatch",
        "find_paths",
        "decompile_functions",
        "submit_findings",
    ],
    "verify": [
        "get_research_state",
        "get_disassembly",
        "get_references",
        "get_data_accesses",
        "find_paths",
        "request_verification",
        "submit_findings",
    ],
    "report": [
        "get_research_state",
        "get_architecture_overview",
        "get_coverage_report",
        "get_unresolved_questions",
    ],
}


def load_prompt(name: str) -> str:
    return (PROMPTS_DIR / name).read_text()


def _system_prompt(mode: str) -> str:
    return load_prompt("system.md") + "\n\n" + load_prompt(
        {
            "discovery": "discover.md",
            "reconstruct": "reconstruct.md",
            "verify": "verify.md",
            "report": "summarize.md",
        }.get(mode, "discover.md")
    )


def _inject_briefing(service: EvidenceService, system: str) -> str:
    ctx_path = service.agent_dir() / "graphics-agent-context.json"
    briefing = json.loads(ctx_path.read_text()) if ctx_path.is_file() else {}
    summary = briefing.get("known_architecture") or briefing.get("briefing") or {}
    return system + "\n\n## Corpus briefing\n" + json.dumps(summary, indent=2)[:12000]


def _checkpoint_meta(service: EvidenceService, mode: str, system: str, session_id: str) -> dict[str, str]:
    inv = service.inventory_digest()
    return {
        "session_id": session_id,
        "mode": mode,
        "model_profile": str(profile_for_mode(mode).get("model", "unknown")),
        "investigation_id": service.investigation_id,
        "baseline_revision": service.baseline_revision,
        "binary_sha256": __import__("reverse_engineering.paths", fromlist=["binary_sha256"]).binary_sha256(),
        "inventory_bundle_digest": inv,
        "system_prompt_sha256": sha256_text(system),
    }


def _build_agent(mode: str, service: EvidenceService, system: str, model_override: Any = None, with_tools: bool = True):
    from pydantic_ai import Agent
    from pydantic_ai.toolsets.function import FunctionToolset

    profile = profile_for_mode(mode)
    model = model_override if model_override is not None else model_string(profile)
    toolset = FunctionToolset()
    allowed = set(MODE_TOOLS.get(mode, MODE_TOOLS["discovery"]))

    if with_tools:

        def search_functions(query: str, limit: int = 50) -> dict[str, Any]:
            return service.search_functions(query=query, limit=limit)

        def get_function_context(rva: str, include_pseudocode: bool = False) -> dict[str, Any]:
            return service.get_function_context(rva, include_pseudocode=include_pseudocode)

        def get_subgraph(seeds: list[str], node_budget: int = 100) -> dict[str, Any]:
            return service.get_subgraph(seeds, node_budget=node_budget)

        def find_paths(source_rva: str, target_rva: str) -> dict[str, Any]:
            return service.find_paths(source_rva, target_rva)

        def get_api_endpoints(api_family: str = "opengl", symbol_filter: str | None = None) -> dict[str, Any]:
            return service.get_api_endpoints(api_family=api_family, symbol_filter=symbol_filter)

        def get_architecture_overview() -> dict[str, Any]:
            return service.get_architecture_overview()

        def get_pseudocode(rva: str) -> dict[str, Any]:
            return service.get_pseudocode(rva)

        def get_disassembly(rva: str) -> dict[str, Any]:
            return service.get_disassembly(rva)

        def get_references(rva: str) -> dict[str, Any]:
            return service.get_references(rva)

        def get_function_signature(rva: str) -> dict[str, Any]:
            return service.get_function_signature(rva)

        def get_type_layout(rva: str) -> dict[str, Any]:
            return service.get_type_layout(rva)

        def get_data_accesses(rva: str) -> dict[str, Any]:
            return service.get_data_accesses(rva)

        def get_indirect_dispatch(rva: str) -> dict[str, Any]:
            return service.get_indirect_dispatch(rva)

        def decompile_functions(rvas: list[str], timeout_seconds: int = 120) -> dict[str, Any]:
            return service.decompile_functions(rvas, timeout_seconds=timeout_seconds)

        def get_runtime_evidence(subsystem: str | None = None) -> dict[str, Any]:
            return service.get_runtime_evidence(subsystem)

        def submit_findings(
            claim_id: str, statement: str, evidence_ids: list[str], scientific_status: str = "hypothesis"
        ) -> dict[str, Any]:
            return service.submit_findings(
                {
                    "claim_id": claim_id,
                    "statement": statement,
                    "evidence_ids": evidence_ids,
                    "scientific_status": scientific_status,
                }
            )

        def propose_investigation(domain: str, question: str) -> dict[str, Any]:
            return service.propose_investigation(domain, question)

        def get_research_state(area: str | None = None) -> dict[str, Any]:
            return service.get_research_state(area)

        def request_verification(claim_id: str) -> dict[str, Any]:
            return service.request_verification(claim_id)

        def get_coverage_report() -> dict[str, Any]:
            return service.get_coverage_report()

        def get_unresolved_questions() -> dict[str, Any]:
            return service.get_unresolved_questions()

        registry = {
            "search_functions": search_functions,
            "get_function_context": get_function_context,
            "get_subgraph": get_subgraph,
            "find_paths": find_paths,
            "get_api_endpoints": get_api_endpoints,
            "get_architecture_overview": get_architecture_overview,
            "get_pseudocode": get_pseudocode,
            "get_disassembly": get_disassembly,
            "get_references": get_references,
            "get_function_signature": get_function_signature,
            "get_type_layout": get_type_layout,
            "get_data_accesses": get_data_accesses,
            "get_indirect_dispatch": get_indirect_dispatch,
            "decompile_functions": decompile_functions,
            "get_runtime_evidence": get_runtime_evidence,
            "submit_findings": submit_findings,
            "propose_investigation": propose_investigation,
            "get_research_state": get_research_state,
            "request_verification": request_verification,
            "get_coverage_report": get_coverage_report,
            "get_unresolved_questions": get_unresolved_questions,
        }
        for name in allowed:
            if name in registry:
                toolset.add_function(registry[name], name=name)

    return Agent(model, system_prompt=system, toolsets=[toolset] if with_tools else [])


def run_investigation(
    baseline_revision: str,
    investigation_id: str,
    mode: str,
    user_prompt: str,
    session_id: str,
    config: dict[str, Any] | None = None,
    use_test_model: bool = False,
    claim_id_to_verify: str | None = None,
) -> dict[str, Any]:
    service = EvidenceService.open(baseline_revision, investigation_id, config)
    session_dir = service.agent_dir() / "sessions" / session_id
    ckpt = checkpoint_connect(session_dir / "checkpoint.sqlite")

    if mode == "verify" and claim_id_to_verify:
        outcome = run_verification(service.research_conn, investigation_id, claim_id_to_verify)
        service.export_snapshots()
        return {"verification": outcome}

    system = _inject_briefing(service, _system_prompt(mode))
    meta = _checkpoint_meta(service, mode, system, session_id)
    history = parse_message_history(ckpt)
    if history:
        mismatches = validate_resume_meta(ckpt, meta)
        if mismatches:
            raise ValueError("session resume refused: " + "; ".join(mismatches))
    elif not load_session_meta(ckpt):
        write_session_meta(ckpt, meta)

    test_mode = use_test_model or os.environ.get("ALLOW_MODEL_REQUESTS", "true").lower() == "false"
    if test_mode:
        from pydantic_ai.models.test import TestModel

        agent = _build_agent(
            mode,
            service,
            system,
            model_override=TestModel(call_tools=[]),
            with_tools=True,
        )
        result = agent.run_sync(user_prompt, message_history=history or None)
        persist_run_messages(ckpt, result)
        service.export_snapshots()
        return {"output": result.output, "test_model": True}

    agent = _build_agent(mode, service, system, with_tools=True)
    result = agent.run_sync(user_prompt, message_history=history or None)
    persist_run_messages(ckpt, result)
    service.export_snapshots()
    return {"output": result.output, "model": model_string(profile_for_mode(mode))}
