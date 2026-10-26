---
package: TR2
upstream: TR1 schema implementation accepted
contracts:
  - execution-observation/v1
owned_paths:
  - tests/test_trace_composition.py
  - tests/test_trace_qbdi_fixture.py
frozen_read_only:
  - docs/oracle/TRACE_V1.md
  - oracle/**
  - analysis/reconstruction/**
  - reverse_engineering/target.json
  - benchmark/eu4_gfx_live_capture.py
stop_rules:
  - contract-conflict
  - scope-expansion
  - evidence-limitation
---

# TR2 — synthetic composition and QBDI integration

## Deliverable

Two owned tests. No new package and no third producer.

`tests/test_trace_composition.py` may land from handwritten traces once TR1 is
accepted. One synthetic caller and one synthetic callee. One direct call and
one indirect call. Each function is checked against its own original execution.
The reconstructed caller links the qualified callee and does not re-trace the
callee body. The reference side runs the original callee. A deliberately wrong
callee fails with a reproducible mismatch.

The validation identity is the hash defined in [TRACE_V1.md](../TRACE_V1.md):
caller candidate, callee implementation digests, callee qualification record,
comparator version, fixtures, and harness. Changing the callee invalidates that
result. It does not invalidate the original input fixture. The indirect call
records the resolved target.

`tests/test_trace_qbdi_fixture.py` is the integration. It merges only with a
supported TR0 trace, or with the explicit blocked decision when QBDI is
unsupported. A Ghidra trace does not pass it. When supported, the test:

1. Takes a trace from the TR0 producer.
2. Passes it to `reconstruction_trace/fixture.py`.
3. Builds the fixture without a handwritten function-specific memory map.
4. Replays the original synthetic function in a fresh process.
5. Matches the independent expected observations.
6. Fails qualification when a required input or memory effect is removed.

## Non-goals

- No real EU IV edge. `_GfxSetPrimitiveType` to `GetOpenGLPrimitiveType` may be
  named by INV as a later candidate. This package does not reconstruct it.
- No agent and no change to the schema.

## Stopping rules

- **Contract conflict:** a new event kind or a second validation identity stops
  for an amendment.
- **Scope expansion:** a calling-convention inference system, or reconstruction
  of an EU IV pair, stops.
- **Evidence limitation:** if TR0 is unsupported, report the integration blocked
  and amend the definition of done. Do not skip the test silently.

## Merge gate

The composition test passes, including the defective callee. The integration
test passes on a TR0 trace, or the same commit records that it is blocked
because TR0 is unsupported.
