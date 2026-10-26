---
package: INV
upstream: trace-fixture contract amendment
contracts:
  - execution-observation/v1 (readiness labels only; no trace production)
owned_paths:
  - reverse_engineering/readiness.py
  - tests/test_trace_readiness.py
frozen_read_only:
  - docs/oracle/TRACE_V1.md
  - oracle/**
  - analysis/frame-model-static.json
  - analysis/renderer-corpus/scope/v1/renderer-functions.jsonl
  - analysis/reconstruction/**
  - reverse_engineering/target.json
stop_rules:
  - contract-conflict
  - scope-expansion
  - evidence-limitation
---

# INV — anchor readiness shortlist

## Deliverable

A deterministic shortlist of the graphics-anchor closure. Inputs are the
committed files [analysis/frame-model-static.json](../../../analysis/frame-model-static.json)
and
[analysis/renderer-corpus/scope/v1/renderer-functions.jsonl](../../../analysis/renderer-corpus/scope/v1/renderer-functions.jsonl).
Do not run Ghidra. Do not score all 4,450 corpus rows. Commit the shortlist,
not a second corpus.

Join by pinned RVA or address. Names are metadata. The static edge target
`__Z22GetOpenGLPrimitiveType13EGFXPrimitive` and the corpus name
`GetOpenGLPrimitiveType` are the same function only when the address matches.

For each function in the anchor closure, record RVA and binary sha, direct
callees, indirect sites, external API calls, whether a qualified RE package
already exists, and one label: `ready-leaf`, `blocked`, or `not-this-stage`.
An unresolved indirect edge, or an edge the bounded traversal did not visit, is
`blocked` or explicitly unvisited. It is not evidence of no dependencies, and
it cannot be `ready-leaf`. Collapse cycles in this small graph with a short
Tarjan implementation. Do not add a graph library.

`_GfxForceWireframe` and `_GfxSetConstantBuffer` are not reconstructed by this
package. The first is a state-write leaf. The second already has RE2.

## Non-goals

- No new export, no decompilation, and no trace or fixture.
- No change to the static graph or the corpus rows.

## Stopping rules

- **Contract conflict:** a symbol-only join stops. Addresses are the identity.
- **Scope expansion:** scoring the full corpus, or a new Ghidra run, stops.
- **Evidence limitation:** a missing address is unresolved. Do not guess a name
  match.

## Merge gate

`tests/test_trace_readiness.py` rebuilds the shortlist from the committed
inputs and checks the address join, including the primitive-type helper, and
checks that an unresolved or unvisited edge is not `ready-leaf`.
