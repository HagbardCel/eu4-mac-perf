---
package: TR0
upstream: trace-fixture contract amendment
contracts:
  - execution-observation/v1
owned_paths:
  - reconstruction_trace/qbdi/**
  - tests/test_trace_qbdi.py
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

# TR0 — QBDI under Rosetta

## Deliverable

A synthetic x86-64 probe and an auditable receipt. The probe answers whether
QBDI can record instruction-level memory accesses under Rosetta. It does not
build fixtures and it does not launch EU IV.

The subject is a small x86-64 C program with pointer chasing, a branch, a
memory write, and one helper call. Two integrations are both required: QBDI
calls the function itself, and a normal call is intercepted into QBDI. QBDI's
limits on threads, signals, and exceptions are recorded. Interception is an
experiment.

Entry regions come from the generic harness inventory in
[TRACE_V1.md](../TRACE_V1.md). TR0 does not edit the schema. If the package
root does not exist yet, this work order creates only `reconstruction_trace/qbdi/`.

## Receipt

The committed receipt includes the executable SHA, toolchain and QBDI version,
architecture, Rosetta status, the command, the declared expectations, the three
results below, and measured time and memory. CI skips the probe when the host
cannot run an x86-64 QBDI build. There is no slowdown threshold.

- `expected_accesses_match` compares a declared set of target accesses with
  independent expected values. Compiler-generated accesses are counted
  separately.
- `instrumentation_complete` accounts for every memory operation inside the
  declared instrumentation scope. A matching known-answer set does not set it.
- Unknown sizes, unknown values, unclassified accesses, and unsupported
  instrumentation stay explicit, keep QBDI access flags, and prevent
  `fixture_qualified`.

## Decision

The receipt fills one of these outcomes:

- Reliable Rosetta traces with explicit gaps: continue with QBDI.
- Incomplete values or sizes: those traces stay `incomplete`.
- Offline QBDI works and natural-call interception does not: keep QBDI for
  offline experiments. Report interception as unsupported. Do not substitute
  Ghidra traces and call TR0 complete.
- QBDI cannot run under Rosetta: report native instrumentation as unsupported
  and amend the milestone. The QBDI-to-fixture integration is blocked, not
  passed.
- A callee or environment that cannot be replayed: record an unresolved
  dependency and do not build a fixture.

## Non-goals

- No fixture builder, no EU IV process, and no live hook.
- No change to `TRACE_V1.md` or to historical evidence.

## Stopping rules

- **Contract conflict:** a required schema change stops for an amendment.
- **Scope expansion:** a general allocation tracer, an agent, or an EU IV
  integration stops.
- **Evidence limitation:** an unsupported result is the deliverable. Do not
  relabel another evidence tier as a pass.

## Merge gate

The receipt is committed, or the unsupported decision is written in the
receipt. Either outcome names the three results separately.
