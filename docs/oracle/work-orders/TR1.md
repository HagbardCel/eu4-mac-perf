---
package: TR1
upstream: trace-fixture contract amendment
contracts:
  - execution-observation/v1
owned_paths:
  - reconstruction_trace/schema.py
  - reconstruction_trace/fixture.py
  - reconstruction_trace/pcode.py
  - reconstruction_trace/harness/**
  - tests/test_trace_fixture.py
frozen_read_only:
  - docs/oracle/TRACE_V1.md
  - oracle/**
  - analysis/reconstruction/_GfxSetConstantBuffer/**
  - analysis/reconstruction/_GfxForceWireframe/**
  - reverse_engineering/target.json
  - benchmark/eu4_gfx_live_capture.py
stop_rules:
  - contract-conflict
  - scope-expansion
  - evidence-limitation
---

# TR1 — synthetic fixture builder

## Deliverable

The `execution-observation/v1` consumer, the frozen P-code importer, and one
reusable x86-64 harness for self-contained synthetic functions. Handwritten
traces are enough to merge. QBDI is not a merge dependency. Those traces still
use the generic harness inventory in [TRACE_V1.md](../TRACE_V1.md). They do not
name the function's expected fields, and their `evidence` is
`handwritten-synthetic`.

The harness restores the declared machine state and calls the original compiled
function. An ordinary C function pointer is not the harness. The P-code
importer checks the frozen `_GfxSetConstantBuffer` receipt. It does not execute
it, and it does not fabricate a load value. See the importer rules in
[TRACE_V1.md](../TRACE_V1.md).

## Required checks

- A conditional read derives a different initial dependency on each branch.
- A read after a write is not a new initial value.
- A write-only destination cannot satisfy a preservation claim from unknown
  initial bytes.
- A partial overlapping write is represented.
- Two pointers to the same allocation stay aliased.
- A missing input region fails qualification with that address and size.
- A fresh native replay matches the independent expected result.
- The importer marks receipt-present values `observed` and scenario-derived
  loads `derived-scenario`.

## Non-goals

- No QBDI probe, no EU IV replay, and no edit to the frozen receipt or scenarios.
- No change to `oracle/function/fixture.py`.

## Stopping rules

- **Contract conflict:** a second schema or a `FUNCTION-CAPTURE` emission stops
  for an amendment.
- **Scope expansion:** pointer typing, C++ object reconstruction, or a general
  heap tracer stops.
- **Evidence limitation:** an ambiguous P-code value stays incomplete. Do not
  fill it.

## Merge gate

`tests/test_trace_fixture.py` passes the checks above. Historical RE and oracle
tests are unchanged. This merge does not claim the QBDI integration.
