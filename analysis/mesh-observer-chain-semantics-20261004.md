# Mesh observer chain semantics (v3.0 → v3.1)

**Venice run:** `results/20261004T131931Z-submission-experiment/`  
**Commit:** `ee31bab` (evidence registered post-merge)

## Dual verdict

| Layer | Verdict |
|-------|---------|
| **v3 infrastructure** (protocol v3, entry + site hooks, ARM/FREEZE, scene gates, frozen bank metadata) | **VALID** |
| **v3.0 pair segmentation** (within-invocation adjacency for cross-parent denominators) | **INVALID** |

Immutable run artifact [`validation.md`](../results/20261004T131931Z-submission-experiment/validation.md) records harness gates as passed under v3.0 comparator semantics. Post-hoc analysis amends interpretation only in evidence JSON.

## Root cause (v3.0)

[`benchmark/eu4_submission_mesh_site.c`](../benchmark/eu4_submission_mesh_site.c) reset the pair chain when `%r12d` (layer) or `-0xac(%rbp)` (flush selector byte) changed **within** a single `RenderBuckets` invocation. Those mid-loop sources were used as invocation boundaries. A full `memset` of chain state also cleared `epoch_nonempty_recorded`, inflating `candidate_nonempty_invocations` (~1.31M vs ~2 real invocations per frame interval).

Effects:

- **`cross_parent_pairs = 0` is inconclusive** — cross-parent adjacency was dropped at every parent transition.
- **`candidate_nonempty_invocations - adjacent ≈ site_entries`** was an **algebraic identity** with a **misleading nonempty** denominator.

## Narrow result still valid

Among pairs that survived v3.0 segmentation (same-parent only): **0 / 55,836** buffer-signature recurrences (`same_parent_buffer_signature`).

## v3.1 design

- **Epoch** (TLS, bumped at `RenderBuckets` entry @ `0x14c7da4`) = invocation boundary.
- **Entry TLS** captures `rcx` (layer), `r8b`, `r9b` — authoritative `layer` / flush selector for comparators (see [`renderbuckets-entry-abi-v31.md`](renderbuckets-entry-abi-v31.md)).
- **Parent pointer change** → classify `cross_parent_pair`; **do not** reset adjacency.
- **Epoch change** → `reset_pair_chain()` only; invocation accounting uses `counted_nonempty_epoch`.
- **ARM** → `reset_invocation_accounting()` + `reset_pair_chain()`.
- Experiment kind **`submission_observer_multi_hypothesis_v2`**; counter schema **v2** (`adjacent_within_invocation`, `candidate_renderbuckets_invocations`, `candidate_nonempty_renderbuckets`).

## Draw-mix note (approximate, separate captures)

~2,421 mesh site entries per swap (~41% of ~5,863 total draws/frame); borders ~39%; remainder ~20%. Percentages are approximate because mesh and draw-trace denominators come from different captures.
