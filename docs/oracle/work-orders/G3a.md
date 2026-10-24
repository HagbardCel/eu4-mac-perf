---
package: G3a
upstream: amendment merge commit (record the exact SHA in the branch's first commit)
contracts:
  - P3 five-tool surface
  - shadow-invocation/v0 (consumer-side result shape only)
owned_paths:
  - oracle/function_agent/**
  - tests/test_oracle_function_agent_*.py
frozen_read_only:
  - oracle/slice/**
  - oracle/function/**
  - oracle/recorder/**
  - reverse_engineering/**
stop_rules:
  - contract-conflict
  - scope-expansion
  - evidence-limitation
---

# G3a — function-agent adapter seam on `synth:apply`

## Deliverable

A new adapter that reuses the certified P3 primitives by import — budget accounting,
process supervision, workspace isolation, receipts and the pinned OpenCode binding —
and demonstrates the seam against the existing `synth:apply` package.

The adapter must:

- preserve the five-tool surface (`inspect`, `fixtures`, `coverage`, `test`, `diff`);
- keep `certify` out of the agent-facing catalog and controller-only;
- keep held-out fixtures, Oracle source and the controller token outside the agent
  workspace;
- reuse the existing budget, abort, termination and receipt behaviour;
- refuse honestly when no function package is present;
- label any mocked or synthetic downstream result.

## Required tests

- The adapter runs the existing `synth:apply` conformance case end to end.
- The tool catalog is exactly the five tools; `certify` is undiscoverable.
- Held-out material is unreachable from the agent workspace.
- Budget, abort, process-group termination and receipt sealing still hold.
- A missing package yields a refusal, not a fabricated success.
- No certified P3 source file is modified by the adapter.
- The adapter does not claim to support arbitrary reconstructed C++ functions.

## Non-goals

- No real second-function package, C++ workspace, G2 harness or G3b pilot.
- No generic agent SDK, generic controller, new sandbox or new MCP server.
- No edits to `oracle/slice/**`; reuse it by import only.
- No claim that `oracle/slice/workspace.py` is a general C++ function workspace.

## Stopping rules

- **Contract conflict:** if the seam cannot be built without editing certified P3,
  stop and propose a focused amendment.
- **Scope expansion:** if substantial controller duplication is unavoidable, stop
  and review that specific obstacle; do not preemptively generalize.
- **Evidence limitation:** if the conformance case cannot be run offline, report it
  as blocked rather than weakening the boundary.

## Merge gate

The adapter passes the `synth:apply` conformance test, the five-tool/held-out/receipt
checks, and review confirms certified P3 is byte-identical.
