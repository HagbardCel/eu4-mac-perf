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
- provide the E1-v2 adapter entry point that re-reads sealed artifacts and ignores
  the reporter’s verdict. It must **not** call `oracle.certify.certify.certify_session`
  unchanged, because that path admits only v1 summaries. Reuse the existing
  candidate-binding, replay, coverage, and promotion helpers on **retained**
  transcripts, emit `eu4-oracle-tier1-certificate/v1`, and apply the unchanged
  [`gfx-set-constant-buffer.json`](../policies/gfx-set-constant-buffer.json). v2
  evidence does not upgrade `sealed-session-unverified` into independently attested
  live-grounded provenance. Check `established ⊆ qualified`;
- verify `audit-associations.json` (schema `eu4-gfx-live-audit-associations/v1`):
  the file is present and hashed by the v2 manifest even when `entries` is empty;
  reject duplicate audit paths, references outside the session, references that are
  not retained captures, rows that do not match the audit files, and hash mismatches.
  Recompute the producer match from the audit and reference transcripts; do not
  treat equal SHA-256 values as the match;
- differential-test native predicate and behavior key against
  `oracle.function.fixture.assess` and `oracle.certify.policy.behavior` on shared
  fixtures (ACQ-1 C must match). Parity-test the certificate shape against E1 on
  equivalent fixture sets where the v1 path applies.

## Required tests

- Contract acceptance matrix adversarial cases in `tests/test_acq_diversity_v1.py`.
- v1 reporter regression in `tests/test_gfx_live_capture_report.py` (historical summary
  header and accounting unchanged).
- Adapter: tampered manifest, audit hash mismatch, duplicate or out-of-session
  audit references, native over-claimed cells, broken v2 counter identities,
  `admission_invariant_lost` nonzero → structural admission fail. A producer-match
  disagreement fails even when the file hashes are internally consistent.
- Certificate parity: candidate binding and promotion match E1 on equivalent
  fixtures; v2 sessions are not reported as a stronger evidence tier.
- **First shared integration test** (when ACQ-1 exists): reconcile every native
  invocation outcome against the Python model, including failed reservations,
  `signature_read_contended`, `key_insert_contended`, an unreadable uniqueness
  scan, a reprobe that blocks reclamation, Phase B refusals that keep `e` or `u`
  advanced without incrementing `selected`, post-bind invariant failure, and
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
