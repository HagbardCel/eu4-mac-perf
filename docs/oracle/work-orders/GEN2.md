---
package: GEN2
upstream: GEN1 native raw JSON
contracts:
  - trace-target/v1
  - execution-observation/v1
owned_paths:
  - reconstruction_trace/tr3/project.py
  - tests/test_trace_gen2.py
frozen_read_only:
  - docs/oracle/TRACE_V1.md
  - docs/oracle/GENERIC_TRACE_V1.md
  - reconstruction_trace/schema.py
  - reconstruction_trace/fixture.py
  - reconstruction_trace/tr3/native.py
  - reconstruction_trace/tr3/gfx.py
  - reconstruction_trace/tr3/adapter.py
  - reconstruction_trace/tr3/insert.c
  - reconstruction_trace/tr3/insert_stub.S
  - reconstruction_trace/tr3/startup.py
  - reconstruction_trace/tr3/run.py
  - oracle/**
  - analysis/reconstruction/**
  - reverse_engineering/target.json
  - benchmark/eu4_gfx_live_capture.py
stop_rules:
  - contract-conflict
  - scope-expansion
  - evidence-limitation
---

# GEN2 — generic offline observation processing

## Deliverable

One pure-Python `project(raw, descriptor, *, synthetic)` in
`reconstruction_trace/tr3/project.py`. It accepts a sealed GEN1 raw record,
its `trace-target/v1` descriptor, and an explicit synthetic flag. It returns
a dictionary that `reconstruction_trace.schema.validate()` accepts.

`synthetic=True` sets `binary` to null. `synthetic=False` uses the
descriptor-bound binary identity after it matches the raw record. Synthetic
status is not inferred from a function name. `function_id` is
`descriptor["function"]["name"]`.

Reuse `native.observation_from_run` for registers, events, recorded regions,
and recorded pointer edges. Do not call `subject_gap`. Exit state stays `rax`
only. FXSAVE images stay in the raw record. This projection is not full
architectural replay.

Three private helpers are enough: identity, one linear serialized-path scan,
and incomplete-reason aggregation. No new schema, decoder, inventory engine,
or pipeline.

## Identity

`ProjectError` is the only projector exception. Wrap `RuntimeError`,
`ValueError`, `TraceError`, and a missing key at the `project` boundary, and
keep the underlying diagnostic.

Reject, with no observation, when the capture is unsealed, when a refusal has
no observed entry, or when the descriptor schema, image base, entry RVA, or
contiguous range disagrees with the raw record. File and window hashes
reported by the capture must match the descriptor. The mapped window must
hash to the declared window hash. Recorded instruction bytes must match that
window. Do not rebuild a matching identity from the raw bytes.

A true `execution_complete` must agree with every checkable serialized GEN1
condition: null `failure`; `ran`, `entered`, and `memory_recording`; all four
drop counters zero; null `rejected_boundary`; empty `exec_transfers`; a
nonempty instruction list that starts at the runtime entry and follows
`next_pc`; a final return with no earlier return or out-of-range successor;
authenticated bytes of the declared size; and every retained access a
read or write with a known positive size of at most 8, a known value, and no
unsupported QBDI flag. A contradiction raises `ProjectError`.

The native return-site check is not in the raw record. Do not reconstruct it.
This scan is receipt consistency. It does not prove the executable was
instrumented.

## Completeness

The raw `execution_complete` flag is never modified and is never promoted to
`fixture.qualify`.

| Raw result | Projection |
| --- | --- |
| Complete and internally consistent | Evaluate replay dependencies |
| Complete and internally contradictory | `ProjectError` |
| Incomplete, specific known gap | Preserve the gap and mark incomplete |
| Incomplete, no reconstructible reason | Incomplete, with an explicit native-completeness reason |

A false flag forces an incomplete observation even when the serialized path
and the entry inventory look complete. If no more specific gap is recorded,
name native execution completeness. Regions and pointer edges come only from
the raw record. An access outside that inventory is an unresolved dependency.
A known QBDI integer of width 1..8 is little-endian `value` with
`value_source: observed`. An unknown value, unknown size, unsupported width,
or unsupported access type is not fabricated and is not omitted silently.

`fixture.qualify` is a downstream check. Tests must not force it to succeed.

## Acceptance

1. The committed GEN1 startup capture, with a descriptor taken from the
   receipt's top-level hashes and window, projects to a schema-valid
   observation. Recorded cell and stack regions and pointer edges are kept.
   `fixture.qualify` accepts it. That is receipt consistency, not an
   independent executable authentication.
2. A fresh generic capture of `tr3_leaf_b` has observed registers, ordered
   events, and that function id. Missing memory prestate leaves the
   observation incomplete while native execution stays complete.
3. Removing a recorded input region does not discover or rebuild it. The
   projection names unresolved dependencies and does not qualify.
4. An unknown QBDI value or unsupported width is not an observed value. The
   observation stays incomplete.
5. Instruction drops, access drops, byte-budget overflow, a rejected call,
   and an out-of-range transfer cannot become complete observations. A false
   `execution_complete` with an otherwise complete serialized path stays
   incomplete.
6. A mismatched file hash, window hash, instruction byte, or entry RVA fails
   closed. A malformed descriptor raises `ProjectError`.
7. The projector has no `_GfxSetConstantBuffer`, `tr3_subject`, or
   `tr3_leaf_b` branch.
8. On supported Darwin, one dylib captures both controlled leaves. Both
   project. The legacy subject can qualify. The generic leaf stays
   replay-incomplete. Existing GEN1 continuation and execution-completeness
   tests stay green. A handwritten second leaf is a test double, not that
   native run. An unsupported host skips explicitly and is not a pass.

## Non-goals

No edit to the frozen paths above. No new C, assembly, hook, or receipt
format. No EU4 process. No second raw-trace schema. No instruction decoder
or memory-provenance engine.

## Stopping rules

- **Contract conflict:** a change to `execution-observation/v1`, a second raw
  schema, or a weaker `fixture.qualify()` stops for an amendment.
- **Scope expansion:** a file not named above, a live EU4 run, or a generic
  inventory or decoder stops.
- **Evidence limitation:** an inferred region or pointer is not observed. Raw
  execution completeness is not fixture qualification. Do not implement a
  deferred capability as a workaround.

## Merge gate

Run the new portable tests, the existing trace tests, and the Darwin
integration with zero unexpected skips. Require two projected controlled
functions, at least one replay-qualified controlled fixture, at least one
execution-complete but replay-incomplete generic trace, and fail-closed
negative cases. GEN1 receipts and native code stay unchanged. No EU4
execution. Report handwritten lines. Past 650 non-blank lines across the
work order, the doc edits, and the two new files needs a failed acceptance
test or a named correctness rule.
