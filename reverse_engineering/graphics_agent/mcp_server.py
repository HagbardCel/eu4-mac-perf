#!/usr/bin/env python3
"""MCP v2 stdio server exposing EvidenceService tools."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

from reverse_engineering.graphics_agent.tools import EvidenceService


def load_investigation_config() -> dict[str, Any]:
    path = Path(__file__).resolve().parent / "config" / "investigation.json"
    return json.loads(path.read_text())


def main() -> int:
    if sys.version_info < (3, 11):
        print("MCP server requires Python 3.11+", file=sys.stderr)
        return 1
    from mcp.server.mcpserver import MCPServer

    cfg = load_investigation_config()
    inv_id = os.environ.get("EU4_AGENT_INVESTIGATION_ID", cfg.get("investigation_id", "default-mcp"))
    baseline = os.environ.get("EU4_AGENT_BASELINE_REVISION")
    if not baseline:
        print("Set EU4_AGENT_BASELINE_REVISION", file=sys.stderr)
        return 1

    service = EvidenceService.open(baseline, inv_id)
    mcp = MCPServer("eu4-graphics-agent")

    @mcp.tool()
    def search_functions(query: str, limit: int = 50) -> dict[str, Any]:
        return service.search_functions(query=query, limit=limit)

    @mcp.tool()
    def get_function_context(rva: str, include_pseudocode: bool = False) -> dict[str, Any]:
        return service.get_function_context(rva, include_pseudocode=include_pseudocode)

    @mcp.tool()
    def find_paths(source_rva: str, target_rva: str) -> dict[str, Any]:
        return service.find_paths(source_rva, target_rva)

    @mcp.tool()
    def get_pseudocode(rva: str) -> dict[str, Any]:
        return service.get_pseudocode(rva)

    @mcp.tool()
    def decompile_functions(rvas: list[str], timeout_seconds: int = 120) -> dict[str, Any]:
        return service.decompile_functions(rvas, timeout_seconds=timeout_seconds)

    @mcp.tool()
    def get_disassembly(rva: str) -> dict[str, Any]:
        return service.get_disassembly(rva)

    @mcp.tool()
    def get_references(rva: str) -> dict[str, Any]:
        return service.get_references(rva)

    @mcp.tool()
    def get_research_state(area: str | None = None) -> dict[str, Any]:
        return service.get_research_state(area)

    @mcp.tool()
    def submit_findings(claim_id: str, statement: str, evidence_ids: list[str]) -> dict[str, Any]:
        return service.submit_findings(
            {"claim_id": claim_id, "statement": statement, "evidence_ids": evidence_ids}
        )

    mcp.run(transport="stdio")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
