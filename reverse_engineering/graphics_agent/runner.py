"""PydanticAI investigation runner with mode-specific tools."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from reverse_engineering.graphics_agent.model_registry import model_string, profile_for_mode
from reverse_engineering.graphics_agent.session_checkpoint import connect as checkpoint_connect, load_messages, save_messages
from reverse_engineering.graphics_agent.tools import EvidenceService

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


def _build_agent(mode: str, service: EvidenceService, model_override: Any = None, with_tools: bool = True):
    from pydantic_ai import Agent
    from pydantic_ai.toolsets.function import FunctionToolset

    profile = profile_for_mode(mode)
    model = model_override if model_override is not None else model_string(profile)
    system = load_prompt("system.md") + "\n\n" + load_prompt(
        {
            "discovery": "discover.md",
            "reconstruct": "reconstruct.md",
            "verify": "verify.md",
            "report": "summarize.md",
        }.get(mode, "discover.md")
    )
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
) -> dict[str, Any]:
    service = EvidenceService.open(baseline_revision, investigation_id, config)
    session_dir = service.agent_dir() / "sessions" / session_id
    ckpt = checkpoint_connect(session_dir / "checkpoint.sqlite")

    test_mode = use_test_model or os.environ.get("ALLOW_MODEL_REQUESTS", "true").lower() == "false"
    if test_mode:
        from pydantic_ai.models.test import TestModel

        agent = _build_agent(mode, service, model_override=TestModel(), with_tools=False)
        result = agent.run_sync(user_prompt)
        save_messages(ckpt, [{"role": "result", "content": str(result.output)}])
        service.export_snapshots()
        return {"output": result.output, "test_model": True}

    agent = _build_agent(mode, service, with_tools=True)
    history = load_messages(ckpt)
    prompt = user_prompt
    if history:
        prompt = user_prompt + "\n\n(previous session messages restored: " + str(len(history)) + ")"
    result = agent.run_sync(prompt)
    save_messages(
        ckpt,
        history
        + [{"role": "user", "content": user_prompt}, {"role": "assistant", "content": str(result.output)}],
    )
    service.export_snapshots()
    return {"output": result.output, "model": model_string(profile_for_mode(mode))}
