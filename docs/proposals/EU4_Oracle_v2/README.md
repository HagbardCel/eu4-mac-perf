# EU IV Oracle v2 — revised design and offline contract prototype

## Start here

- `design/MASTER_DESIGN_V2.md`: full architectural proposal, PR-0 cleanup, deduplication, acquisition, diagnostics, OpenCode single-agent architecture and acceptance gates.
- `design/OPENCODE_FEASIBILITY.md`: API mapping and version/sandbox limitations.
- `examples/opencode.json`, `examples/.opencode/agents/eu4-reconstructor.md`, `examples/.opencode/tools/oracle.ts`: OpenCode configuration **templates only**; the trusted bridge and compiler sandbox are NOT included/implemented.
- `examples/diagnostic.schema.json`, `examples/diagnostic.example.json`: structured feedback schema and illustrative sample.
- `oracle_proto/core.py` and `tests/test_core.py`: intentionally small, executable *offline* demonstrations of count reconciliation, collision-checked canonical deduplication, and first-divergence classification. They are not a runtime collector, ABI emulator or full comparator.

## Validate offline

```sh
PYTHONPATH=. python3 -m unittest discover -s tests -v
python3 -m json.tool examples/opencode.json >/dev/null
python3 -m json.tool examples/diagnostic.schema.json >/dev/null
```

## Project integration

This archive is a design handoff, not a ready-to-merge PR. The sample API targets OpenCode's currently documented `/docs` generation, not the separate `/v2/docs` schema. Pin and validate against the installed OpenCode version before integration. The sample `oracle.ts` requires a trusted `EU4_ORACLE_BRIDGE` binary; candidate source execution must be sandboxed outside the agent's process.

## Immutable source identity

Static corpus baseline `auto-v1-20261013`; binary SHA-256 `b3d38876abf4e61cdae57509186d7cb7dcb03bfeaca4c95c10c713794715141d`. Nothing in this package captures or patches EU IV.
