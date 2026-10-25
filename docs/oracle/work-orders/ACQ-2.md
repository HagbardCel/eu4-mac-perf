---
package: ACQ-2
upstream: ACQ-0 merge commit (record the exact SHA in the branch's first commit)
contracts:
  - acq-diversity/v1
  - capture-evidence/v2 (structural admission)
  - FUNCTION-CAPTURE v1
owned_paths:
  - benchmark/acq_diversity_v1.py
  - tests/test_acq_diversity_v1.py
  - benchmark/eu4_gfx_live_capture.py
  - tests/test_gfx_live_capture_report.py
frozen_read_only:
  - docs/oracle/ACQ_DIVERSITY_V1.md
  - oracle/certify/**
  - docs/oracle/policies/**
  - benchmark/eu4_gfx_live_capture.c
  - benchmark/eu4_gfx_live_capture.h
  - benchmark/eu4_gfx_set_constant_buffer_capture.c
  - benchmark/eu4_gfx_set_constant_buffer_capture.h
stop_rules:
  - contract-conflict
  - scope-expansion
  - evidence-limitation
---

# ACQ-2 — independent model, v2 reporter, and E1-v2 adapter

## Deliverable

Portable reference for [ACQ_DIVERSITY_V1.md](../ACQ_DIVERSITY_V1.md), v2 session
reporting, and the **E1-v2 adapter** that admits v2 sealed sessions for coverage
evaluation without modifying E1 v1.

The package must:

- implement `benchmark/acq_diversity_v1.py` as the authoritative Python model of
  selection, accounting, signature transitions, and commit outcomes;
- add a **v2-only** branch in `benchmark/eu4_gfx_live_capture.py` for
  `EU4-GFX-LIVE-SUMMARY v2` validation (v1 path and tests unchanged);
- provide the E1-v2 adapter entry point that re-reads sealed artifacts, ignores the
  reporter’s verdict, materializes **retained** transcripts through A2, replays through
  the existing comparator, applies the unchanged
  [`gfx-set-constant-buffer.json`](../policies/gfx-set-constant-buffer.json), verifies
  audit files via `reference_capture_sha256` and A2, and checks
  `established ⊆ qualified`;
- differential-test native predicate and behavior key against
  `oracle.function.fixture.assess` and `oracle.certify.policy.behavior` on shared
  fixtures (ACQ-1 C must match).

## Required tests

- Contract acceptance matrix adversarial cases in `tests/test_acq_diversity_v1.py`.
- v1 reporter regression in `tests/test_gfx_live_capture_report.py` (historical summary
  header and accounting unchanged).
- Adapter: tampered manifest, audit hash mismatch, native over-claimed cells, broken v2
  counter identities, `admission_invariant_lost` nonzero → structural admission fail.
- **First shared integration test** (when ACQ-1 exists): reconcile every native
  invocation outcome against the Python model, including failed reservations and
  unsuccessful selected captures.

## Non-goals

- No edits to ACQ-1 C/H files to make tests pass.
- No changes to `oracle/certify/` or E1 v1 admission for v1 summaries.
- No multi-tier certification framework.
- No EU IV launch.

## Stopping rules

Same as ACQ-1: contract conflict → amend ACQ-0; do not patch native code.

## Merge gate

`test_acq_diversity_v1` and v1 report tests pass; adapter tests pass on synthetic v2
sessions. E1-v2 adapter tests are a **precondition for the next operator-authorized live
session** (documented in PLAN_OF_RECORD). Merging ACQ-2 does not authorize launch.
