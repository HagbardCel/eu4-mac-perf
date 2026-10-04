# PR1 go/no-go: mesh subrecord static RE

**Date:** 2026-10-04 (revised after review)  
**Binary:** GOG EU IV 1.37.5 (`b3d38876…`)

## Deliverables

- Layout JSON with full-byte classification and generated C ranges (`codegen_subrecord_ranges.py` → `subrecord_equivalence_ranges.inc`)
- Split predicates with **strict barrier semantics** (unknown regions never enable generic `setup_elision_eligible`)
- Operation-specific elision: `buffer_bind_elision_eligible` on proven VBO/IBO/gate fields only
- [`mesh-subrecord-hook-site.json`](mesh-subrecord-hook-site.json) — 13-byte patch boundary, displaced insns, frame pointer map, resume offsets
- [`mesh-draw-dependency-map.md`](mesh-draw-dependency-map.md)

## Conclusion: **GO A (buffer-bind predicate only); not full setup elision; not B**

| Outcome | Status |
|---------|--------|
| **Full setup elision (`setup_elision_eligible`)** | **Not established.** Requires `texture_setup_elision_eligible` and `object_constants_elision_eligible`, both blocked on unresolved barrier fields (`EU4_PRED_BARRIER_UNRESOLVED`). |
| **Buffer-bind elision predicate** | `buffer_bind_elision_eligible()` compares only parent equal-fields + subrecord `0x30`/`0x38`/`0x40`/`0x7d`. Safe to observe hit rate; does not imply skipping GetGfxEffect/object-constant work. |
| **B — draw batch** | `draw_batch_eligible()` remains false (`EU4_PRED_GEOMETRY_UNPROVEN`). |
| **C — stop coalescing branch** | Not selected for the whole Gfx path: hook ABI and dependency map are sufficient for **observer** work on buffer-bind hits only. |

## PR2 recommendation

1. Install mesh observer at `0x14c81e6` per hook-site JSON; **do not** count CGL flush as `candidate_site_entries`.
2. Run capability 1 observer smoke with per-phase gates and scene validation; require `eligible_pair_hits > 0` on **each** B phase.
3. Compare hit rate to removed-work inventory — buffer-bind hits alone are unlikely to justify Stage 2 until texture/object-constant predicates are proven.

## PR2 observer smoke (2026-10-04, incomplete)

First Venice run after PR #31 merge (`2211e08`): [`results/20261004T111812Z-submission-experiment`](../results/20261004T111812Z-submission-experiment/validation.md).

| Result | Detail |
|--------|--------|
| Harness | **Incomplete** — `summarize()` required 25 swap samples while observer phases are 10 s (~10 samples); fixed on `main` after this run. |
| Hook traffic | Final mmap `candidate_site_entries` ≫ 0 (site hook firing). |
| Predicate ROI | Final `eligible_pair_hits` = 0 (no buffer-bind equivalence pairs this session; engagement gate not sealed). |
| Gates | `observer_gate` / scene checks **not computed** — re-run after harness fix. |

Registered evidence: [`analysis/evidence/gfx-subrecord-observer-smoke-20261004T111812Z.json`](evidence/gfx-subrecord-observer-smoke-20261004T111812Z.json).

## PR2 observer smoke (2026-10-04, complete — engagement failed)

Second run after summarize fix (`45b44fc`): [`results/20261004T112503Z-submission-experiment`](../results/20261004T112503Z-submission-experiment/validation.md).

| Result | Detail |
|--------|--------|
| Harness | **Complete** — gates computed; all scenes passed. |
| Hook traffic | Per-phase `candidate_site_entries_delta` ≈ 1.35–1.39M. |
| Predicate ROI | **`eligible_pair_hits_delta` = 0 on b1** (and all phases). |
| Gates | `observer_gate` / `engagement_gate` **failed**; Pareto skipped. |

**Conclusion for this scene:** buffer-bind observer is live but finds **no** eligible pairs on paused Venice; do not invest in capability-2 buffer-bind mutation without new predicates or a different workload.

Registered evidence: [`analysis/evidence/gfx-subrecord-observer-smoke-20261004T112503Z.json`](evidence/gfx-subrecord-observer-smoke-20261004T112503Z.json).
