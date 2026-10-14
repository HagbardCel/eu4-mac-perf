# Graphics reconstruction agent (PR #51)

Local harness for evidence-backed investigation of the EU IV-owned graphics stack.

## Requirements

- Python **3.11+** for the agent runtime
- `uv sync --extra graphics-agent` at repo root
- Pinned corpus under `.local/eu4/<sha>/exports/<baseline>/` (see [reverse_engineering/README.md](../../reverse_engineering/README.md))
- `GHIDRA_INSTALL` for semantic Ghidra tools (optional for graph-only tests)

## Commands

```bash
export PYTHONPATH=benchmark:.

# Generate briefing package
uv run python -m reverse_engineering.graphics_agent.cli prepare --baseline-revision auto-v1-20261013

uv run python -m reverse_engineering.graphics_agent.cli doctor --baseline-revision auto-v1-20261013

# Bounded pilot (TestModel when ALLOW_MODEL_REQUESTS=False)
ALLOW_MODEL_REQUESTS=False uv run python -m reverse_engineering.graphics_agent.cli run \
  --baseline-revision auto-v1-20261013 --mode discovery --test-model

# MCP (stdio) — set investigation binding
export EU4_AGENT_BASELINE_REVISION=auto-v1-20261013
export EU4_AGENT_INVESTIGATION_ID=default-mcp
uv run python -m reverse_engineering.graphics_agent.mcp_server
```

## Artifacts

Under `exports/<baseline>/agent/`:

- `graphics-agent-context.json` — orientation briefing
- `research.sqlite` — authoritative findings, budgets, Ghidra op ledger
- `*.snapshot.json` — derived architecture map and backlog (regenerate via `cli report`)

## Pre-merge local gates

- Real Ghidra: `QueryFunctionEvidence.java` + decompile against retained project
- Live pilot with providers and human review template in `evidence/agent-pilot-review.md`

Codex using MCP still needs host sandbox configuration; EvidenceService does not grant shell access.
