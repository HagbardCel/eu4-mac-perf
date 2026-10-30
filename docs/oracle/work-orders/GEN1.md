---
package: GEN1
upstream: GEN0 trace-target/v1
contracts:
  - trace-target/v1
  - execution-observation/v1
owned_paths:
  - reconstruction_trace/tr3/host.c
  - reconstruction_trace/tr3/subject.S
  - reconstruction_trace/tr3/insert.c
  - reconstruction_trace/tr3/insert_stub.S
  - reconstruction_trace/tr3/startup.py
  - reconstruction_trace/tr3/run.py
  - reconstruction_trace/tr3/startup_receipt.json
  - reconstruction_trace/tr3/startup_observation.json
  - reconstruction_trace/tr3/receipt.json
  - reconstruction_trace/tr3/observation.json
  - tests/test_trace_tr3.py
frozen_read_only:
  - docs/oracle/TRACE_V1.md
  - reconstruction_trace/schema.py
  - reconstruction_trace/fixture.py
  - reconstruction_trace/tr3/adapter.py
  - reconstruction_trace/tr3/gfx.py
  - oracle/**
  - analysis/reconstruction/**
  - reverse_engineering/target.json
  - benchmark/eu4_gfx_live_capture.py
stop_rules:
  - contract-conflict
  - scope-expansion
  - evidence-limitation
---

# GEN1 — one dylib, two controlled functions

## Deliverable

Parameterize the existing one-shot QBDI hook from a `trace-target/v1`
descriptor. Merging this work order authorizes a later implementation PR to
edit only the owned paths. It does not authorize launching or attaching to
EU4, and it does not authorize a claim that the tracer is ready for live use.

No new source file is authorized. A required loader module stops for an
amendment that names that one file. `run.py` may change only so offline
configuration reads a target descriptor. It gains no EU4 execution.

The two controlled functions use one compiled instrumentation dylib. Only
their descriptors change. Two separately compiled tracers do not pass. Add
the second leaf to the existing host in `host.c` and `subject.S`, and select
which function that host invokes.

`startup_receipt.json` and `startup_observation.json` are regenerated when
source hashes change. They stay the controlled-host receipts.
`receipt.json` and `observation.json` are regenerated only to record the
unchanged 10-byte subject again.

`docs/oracle/GENERIC_TRACE_V1.md` may be amended solely to specify pre-hook
refusal when `max_bytes` cannot hold the mandatory capture envelope. All
other contract content remains frozen.

## Acceptance

1. Two materially different controlled x86-64 leaf functions are captured
   with the same compiled instrumentation dylib, only their descriptors
   changing.
2. Executed instruction addresses and bytes match the authenticated mapped
   code window. Memory accesses, entry/exit state, and control flow are
   accounted for without function-specific semantics.
3. Actual CPU continuation after the stub is verified for the supported
   register and flag state, including caller-saved registers and flags. The
   test reads the CPU after return from the stub. Inspecting the saved QBDI
   exit structure is not that test. An unsupported register or
   instruction-state requirement is a refusal, not a transparency claim.
4. Budget overflow, a call, and an out-of-range jump each produce a retained,
   explicitly incomplete raw trace. The out-of-range jump is reported; a
   range-limited instrumentation window does not hide it. Intra-range
   branches stay supported. Recursion, an exception, or an exit other than
   the expected return is likewise incomplete.
5. Existing TR3 controlled-host regression tests and receipts remain valid.
   No EU4 process is started.

`max_bytes` is the raw capture payload size, excluding serialization
overhead, as defined in [GENERIC_TRACE_V1.md](../GENERIC_TRACE_V1.md). The
engine enforces a safety ceiling before allocation. Overflow keeps the record
and a drop count.

Before any later claim that the tracer is ready for live use, condition 3
has already passed on the controlled host.

## Non-goals

- No edit to `adapter.py`, `gfx.py`, `fixture.py`, `schema.py`, or
  `TRACE_V1.md`.
- No new raw-trace schema, provenance analysis, Frida backend, or second
  function in one session.
- No EU4 process in tests or CI.

## Stopping rules

- **Contract conflict:** a change to `execution-observation/v1`, a new raw
  schema, or a weaker `fixture.qualify()` stops for an amendment.
- **Scope expansion:** a file not named above, a live EU4 run, or
  function-specific C for either controlled subject stops.
- **Evidence limitation:** an incomplete trace stays incomplete. A QBDI exit
  dump is not the continuation test.

## Live launcher

`run.py` is unchanged. It does not supply the budgets this dylib now requires,
so a normal environment refuses after EU4 has started. A budget variable
already in the environment is not a guarantee that the launch will refuse,
because the launcher copies that environment. This note does not repair that
incompatibility. Live acquisition is repaired and revalidated only under a
separate authorization.

## Merge gate

The five acceptance conditions pass, with zero skips. `run.py` does not
launch EU4. A green run does not authorize a live session.
