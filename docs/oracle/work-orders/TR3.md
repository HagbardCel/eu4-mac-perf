---
package: TR3
upstream: TR2 QBDI-to-fixture integration (unsatisfied)
contracts:
  - execution-observation/v1
owned_paths: []
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

# TR3 — preflight only

## Deliverable

This document. It records prerequisites and a proposed later experiment. It
authorizes no implementation, EU4 launch, injection, live trace, or
replacement.

`owned_paths` is empty. Instrumentation, fixture integration, replay, and any
launch path need a separately approved scope amendment or execution work order
that names those paths. Recording the experiment here is not that amendment.

Passing TR2 and passing the checks below grant neither later permission:

1. **Implementation authorization.** Instrumentation, fixture integration,
   replay, and any launch path, only after an approved scope amendment names
   the owned paths.
2. **Operator authorization.** One bounded EU4 execution, only after that
   implementation has been reviewed.

## Upstream

TR2 stays unsatisfied until both tests pass on a branch that contains the TR2
merge. The evaluation base is `origin/main` after that merge.

```
python3 -m unittest tests.test_trace_composition tests.test_trace_qbdi_fixture
```

`tests/test_trace_composition.py` must include the defective-callee mismatch.
`tests/test_trace_qbdi_fixture.py` must take a trace from the TR0 QBDI
producer, pass it through `reconstruction_trace/fixture.py`, and replay the
original synthetic function against the independent expected observations. A
Ghidra trace does not count. The blocked-because-unsupported outcome does not
count either: `reconstruction_trace/qbdi/receipt.json` already records the
decision `Reliable Rosetta traces with explicit gaps: continue with QBDI.` and
`interception.result` of `supported`.

## Frozen paths

These stay read-only for a later implementation stage:

- `docs/oracle/TRACE_V1.md`
- `oracle/**`, including E1/F1 certification policy and the oracle trust model
- `benchmark/eu4_gfx_live_capture.py`, with no new launch path
- `reverse_engineering/target.json`
- `analysis/reconstruction/**`

No new memory or calling-convention inference. Evidence from the later
experiment is not E1-admissible.

## Proposed later experiment

Not authorized by this document.

Exactly one preselected `_GfxSetConstantBuffer` invocation. Completeness
follows [TRACE_V1.md](../TRACE_V1.md): entry and exit state, memory events, and
no dropped events. One fixture is built from that trace and replayed through
the existing qualified reconstruction. The frozen RE0 contract records 21
instructions and zero selected direct callees, so this experiment does not add
a caller–callee target. Scaling to other graphics functions is a later stage.

## Overhead reference

The TR0 receipt is a measurement reference and a methodology. It is not a
numeric limit. `measurements.slowdown_threshold` stays `null`. The synthetic
probe recorded a native call of 83 ns, a QBDI VM call of 1,536,375 ns, and an
intercepted call of 7,708,375 ns. Those times do not bound
`_GfxSetConstantBuffer`. This preflight sets no performance threshold. A later
performance acceptance threshold needs its own authorization.

## Non-goals

- No instrumentation, no fixture bridge, no replay driver, and no launch path.
- No edit to `TRACE_V1.md`, E1/F1 policy, or the existing reconstruction.
- No EU4 process.

## Stopping rules

- **Contract conflict:** a required schema change stops for an amendment.
- **Scope expansion:** a second function, a caller–callee target, or treating
  this document as permission to implement stops.
- **Evidence limitation:** an unsatisfied TR2 upstream stays unsatisfied. Do
  not relabel a Ghidra trace or an unsupported TR0 outcome as a pass.

## Merge gate

This file may land while TR2 is unsatisfied. The preflight passes only when
the upstream tests pass. A passing preflight stops. It does not authorize
implementation or an EU4 execution.
