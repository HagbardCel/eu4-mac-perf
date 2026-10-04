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
