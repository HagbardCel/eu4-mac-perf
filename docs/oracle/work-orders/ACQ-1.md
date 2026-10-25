---
package: ACQ-1
upstream: ACQ-0 merge commit (record the exact SHA in the branch's first commit)
contracts:
  - acq-diversity/v1
  - FUNCTION-CAPTURE v1 (retained transcripts only)
owned_paths:
  - benchmark/eu4_gfx_live_capture.c
  - benchmark/eu4_gfx_live_capture.h
  - benchmark/eu4_gfx_set_constant_buffer_capture.c
  - benchmark/eu4_gfx_set_constant_buffer_capture.h
  - tests/gfx_live_capture_harness.c
  - tests/test_gfx_live_capture.py
frozen_read_only:
  - docs/oracle/ACQ_DIVERSITY_V1.md
  - oracle/certify/**
  - oracle/function/**
  - docs/oracle/policies/**
  - benchmark/eu4_gfx_live_capture.py
stop_rules:
  - contract-conflict
  - scope-expansion
  - evidence-limitation
---

# ACQ-1 — native diversity-aware live capture (v2)

## Deliverable

Implement [ACQ_DIVERSITY_V1.md](../ACQ_DIVERSITY_V1.md) in the P5a native hook and live
recorder: screening snapshot, signature table, admission-word CAS, sampling, durable
commit, native `fixture.assess` / `policy.behavior` predicates in C, and
`EU4-GFX-LIVE-SUMMARY v2` emission.

The implementation must:

- reuse the screening dword as the entry pre-buffer snapshot on the buffer-length path;
- enforce `R + P_cov + P_div + L ≤ 64` through the single admission word;
- preserve `ANCHORED` across non-retained verifications per the transition table;
- never establish coverage without native predicate pass and durable rename of a
  retained transcript;
- classify `producer_matched_repeat` only against anchored records; persist ≤8 audit
  transcripts with `reference_capture_sha256` only (no A2 digest in native code);
- set sticky `admission_invariant_lost` per the contract; use `selected_invariant_failure`
  and `cas_exhausted` as specified;
- keep PR D v1 behavior available for historical replay tests (do not break v1 summary
  generation for existing code paths if still required for regression — v2 is the live
  diversity path).

## Required tests

All scenarios in the contract acceptance matrix, exercised via
`tests/gfx_live_capture_harness.c` and `tests/test_gfx_live_capture.py`, including:

- anchor preservation after `distinct_not_retained`, ineligibility, and `storage_failed`;
- signature-table starvation with reclamation for diversity keys;
- false coverage establishment (ineligible write does not clear missing cell);
- screening dword / snapshot consistency;
- concurrent admission and generation mismatch;
- rollback failure → `cas_exhausted` + flag, not in `selected`.

## Overhead gate (mandatory stop)

Before merge, run the contract overhead measurements. If the 4-byte Mach screen path
exceeds p50 1.0 µs or p99 2.5 µs, **stop and propose an ACQ-0 amendment** — do not
ship unsafe reads or silently relax limits.

## Non-goals

- No changes to `oracle/certify/`, E1 v1, or coverage policy JSON.
- No edits to `benchmark/eu4_gfx_live_capture.py` reporter (ACQ-2).
- No EU IV launch for acceptance.
- No generic multi-function collector framework.

## Stopping rules

- **Contract conflict:** cannot preserve accounting, anchor rule, or safe seal → amend
  ACQ-0; do not generalize the collector.
- **Scope expansion:** additional functions, hooks, or quota → architectural decision.
- **Evidence limitation:** ship harness-tested behavior; label live acceptance blocked.

## Merge gate

Native harness tests green; v2 summary schema matches the contract; differential
predicate tests agreed with ACQ-2 on shared fixtures; overhead gate passed or blocked
with documented amendment request. Integrated native-vs-model reconciliation is a
follow-on gate with ACQ-2, not a solo merge blocker unless both branches are ready.
