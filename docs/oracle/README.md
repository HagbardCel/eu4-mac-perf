# EU IV Oracle — ground-truth acquisition and reconstruction

**Status:** next-phase capability, design + audit stage. No recorder, runtime capture,
or OpenCode integration is implemented yet. Authoritative for **capture/validation
infrastructure** only; the function-level reconstruction lifecycle stays in the PR #44
contract `docs/renderer-reconstruction-and-differential-validation.md` (see
[open PR #44](https://github.com/HagbardCel/eu4-mac-perf/pull/44), not yet on `main`).

## Purpose

Establish correct ground truth for automated, agent-driven C++ reconstruction of large
parts of the EU IV graphics stack. The Oracle (capture + validation infrastructure)
supplies the fixtures, comparator and service that the function-level RE0–RE3 lifecycle
consumes. The project's power/heat objective is the downstream consumer of that work.

## Documents

| Document | Role |
| --- | --- |
| [PLAN_OF_RECORD.md](PLAN_OF_RECORD.md) | Authorized P0–P5 execution sequence and gates |
| [REUSE_MAP.md](REUSE_MAP.md) | REUSE / ADAPT / REPLACE / RETAIN_HISTORICAL decisions over existing assets |
| [BASELINE_P0.md](BASELINE_P0.md) | Frozen baseline record (revision, checks, pre-existing failures) |
| [../repo-map.md](../repo-map.md) | Tracked-path inventory and lifecycle labels (P1 audit) |

The design bundle that this program refines currently lives at
`docs/proposals/EU4_Oracle_v2/` (delivered artifact, migrated into `docs/oracle/` and
`oracle/` in P2, preserving its original bytes first). See
[PLAN_OF_RECORD.md](PLAN_OF_RECORD.md) for the phase sequence.

## Ownership model

| Layer | Owner document | Scope |
| --- | --- | --- |
| Overall strategy | [`../eu4_recommended_strategy.md`](../eu4_recommended_strategy.md) | Mission and priorities |
| Function lifecycle | `docs/renderer-reconstruction-and-differential-validation.md` (PR #44, not yet on `main`) | RE0–RE3 per-function reconstruction and oracle-strength comparison modes; RE3 optional |
| Capture/validation infrastructure | this directory (`docs/oracle/`) | Recorder, deduplication, fixtures, comparator, service, epochs |

Oracle infrastructure produces fixtures and a comparator that **RE2** consumes; RE0–RE3
remains the per-function lifecycle and does not define the recorder.

## Reconciliation actions

1. **PR #44** is open on branch `roadmap-renderer-reconstruction` (head `d321a18`), not on
   `main`. Its reconstruction contract must be reconciled on that branch or at merge —
   #43 has merged, its "open PR #43" dependency language is stale, and its scope should
   acknowledge the full-program corpus and the graphics-subsystem endpoint. Do **not**
   introduce a competing copy of the reconstruction contract on `main`.
2. **Canonical Mission update:** `../eu4_recommended_strategy.md` should name ground-truth
   acquisition + reconstruction as the primary next-phase capability. Coordinated with the
   PR #44 edit to avoid parallel edits to the same file.
3. **Seed migration (P2):** preserve the original `../proposals/EU4_Oracle_v2/` bytes
   (hash + manifest + provenance), then relocate design into `docs/oracle/` and prototype
   code into `oracle/`. PR-0-style cleanup is landing incrementally; see
   [REUSE_MAP.md](REUSE_MAP.md).
