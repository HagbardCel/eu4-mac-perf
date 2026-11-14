# Ghidra, agent tools and efficiency reporting (PR 5)

## Ghidra-first knowledge

`reverse_engineering/re_query.py` queries the existing corpus and sealed
renderer snapshot. It performs no disassembly and starts no Ghidra process.

| Operation | Source | Notes |
| --- | --- | --- |
| `resolve` | `index.sqlite` names/FTS | exact then partial names |
| `function` | functions + pseudocode | bounded pseudocode bytes |
| `callers`/`callees` | `call_edges` | call edges only |
| `xrefs` | `call_edges` | explicitly `partial` (no data xrefs offline) |
| `abi` | sealed `renderer_snapshot/v1/batches/*/static.jsonl` | `unavailable` rather than guessed |
| `struct` | tracked `analysis/**/*layout*.json` | returns the reviewed layout + evidence |
| `provenance` | index `provenance` table | binary SHA, revision, bundle digest |

The tool honours `EU4_RE_INDEX`, `EU4_RE_EXPORT` and `EU4_RE_REVISION`. From
this worktree the local corpus lives in the main checkout, so the operator (or
the agent environment) sets `EU4_RE_EXPORT`; without it the answer is
`unavailable` with the exact reason.

All reads open SQLite with `mode=ro&immutable=1`, so querying cannot create a
WAL sidecar or modify the corpus.

## Agent surfaces

The installed DSH version (0.2.2-rc.1) supports native typed tools through
`ctx.tools.register(definition)`; it does not produce the exact `eu4_*` names
through MCP (MCP tools are prefixed `mcp__<server>__<tool>`). The plugin is
therefore native, not MCP, and contains no backend logic: it maps typed
arguments to the Python core and returns its envelope.

`dsh/eu4-tools-plugin` registers exactly the seven tools. `eu4-web` mounts
`@eu4/dsh-tools`; the offline overlay leaves it enabled. Provisioning links the
plugin, and preflight requires the `eu4-tools` row. Plugin tests run without
DSH or EU4:

```sh
npm test --prefix dsh/eu4-tools-plugin
```

## Efficiency metrics (measured baseline)

From the local campaign root on 2026-11-14, including the feasibility and
failed-safety runs, **12 sessions used 4 ledger launches**: 646 observed frames,
186 paired, 172 aligned, 4 compared and 4 failed, 149 inconclusive
comparisons, and one diagnostic case. This is a baseline, not a success rate:
the 4 failures are dominated by the loading-stage correspondence bug, which is
now gated by `settle.min_draws`; the 149 inconclusive pairs are the honest
cost of refusing to call unaligned frames rendering failures.

The metric improves only by establishing better correspondence (scene/update
state), not by lowering the comparison threshold or counting inconclusive pairs
as passes.

## Operational defaults

The reconstruction instructions now default to:

1. query `eu4_re_query` / engine contracts;
2. run focused static/native tests;
3. replay known local cases (`eu4_replay`);
4. inspect differential observations (`eu4_diff_inspect`/`eu4_capture`);
5. run one targeted live diagnostic if needed;
6. run a dual campaign only when no adequate case exists;
7. run full qualification after corrections.

A new EU4 launch still needs a specific diagnostic objective but no separate
approval process; normal permissions are used, and sandbox escalation is only
requested after a genuine denial. After two inconclusive launches for the same
defect, the next step is instrumentation/experiment redesign, not a third
identical run.
