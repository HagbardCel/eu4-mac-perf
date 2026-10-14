#!/usr/bin/env bash
# Local merge gate: compile QueryFunctionEvidence.java and run six semantic headless ops.
# Requires Ghidra, pinned EU IV project (EU4_RE_EXPORT or default layout), and one inventory RVA.
set -euo pipefail
RVA="${1:-0x0000000000005000}"
BASELINE="${EU4_AGENT_BASELINE_REVISION:-auto-v1-20261013}"
OPS=(disassembly references signature type_layout data_accesses indirect_dispatch)
SCRIPT="reverse_engineering/ghidra/QueryFunctionEvidence.java"
echo "Baseline: $BASELINE  RVA: $RVA"
uv run python -m reverse_engineering.graphics_agent.ghidra_local_gate --baseline-revision "$BASELINE" --rva "$RVA" --operations "${OPS[@]}"
