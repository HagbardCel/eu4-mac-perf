# PR1 go/no-go: mesh subrecord static RE

**Date:** 2026-10-04  
**Binary:** GOG EU IV 1.37.5 (`b3d38876…`)

## Deliverables completed

- [`sflushdata-0x50-layout.json`](sflushdata-0x50-layout.json) — full-byte classified layout
- [`mesh-draw-subrecord-0xe8-layout.json`](mesh-draw-subrecord-0xe8-layout.json) — full-byte classified layout
- [`mesh-draw-dependency-map.md`](mesh-draw-dependency-map.md) — per-draw inputs + engine elision inventory
- [`mesh-subrecord-hook-site.json`](mesh-subrecord-hook-site.json) — insertion before setup block; draw call landmark only
- [`benchmark/subrecord_equivalence.c`](../benchmark/subrecord_equivalence.c) — split predicates; CI parity via [`mesh_record_layout_tables.py`](../benchmark/mesh_record_layout_tables.py)

## Conclusion: **A (setup elision) with ROI gate; not B**

| Outcome | Status |
|---------|--------|
| **A — setup elision** | `setup_elision_eligible()` defined; pairs require identical barrier bytes + established equal fields + same parent/layer/flush context. Engine already skips effect-state replay on repeated effect pointers; remaining ROI targets object-constant/texture/buffer work ([`mesh-draw-dependency-map.md`](mesh-draw-dependency-map.md)). |
| **B — draw batch / physical submission reduction** | **`draw_batch_eligible()` returns false** (`EU4_PRED_GEOMETRY_UNPROVEN`). GfxDrawIndexed uses IBO-stored count with zero passed count; contiguous merge not established. |
| **C — full stop** | Not selected: structures and hook contract are understood enough for observer prototype, but batching branch is closed until geometry semantics are proven. |

## PR2 recommendation

1. Implement protocol v2 + `candidate_site_entries` at documented insertion site (assembly thunk).
2. Run **capability 1 observer smoke** (not full ABABA) measuring hit rate vs removed-work inventory.
3. Proceed to mutating Stage 2 only if hits are frequent **and** map to non-trivial remaining work (object constants / texture+buffer path), not effect-pointer caching alone.
