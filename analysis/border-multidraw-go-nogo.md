# Border multidraw go/no-go (PR B)

**Date:** 2026-10-04 (revised)  
**Gate 0:** RUNTIME-ASSERT — [border-gl-multidraw-api-availability.md](border-gl-multidraw-api-availability.md)

## Three ceilings

| Ceiling | Value | Evidence basis |
|---------|------:|----------------|
| **Structural** | **~1,562** | Same-state border adjacencies / frame ([draw-screening.md](../results/20260928T113359Z-draw-trace/draw-screening.md)) |
| **Semantic** | **Unresolved** | Requires per-run classification (mode 0 vs 1/2, color fast vs slow, VBO/IBO transitions) on trace or loop-head classifier — **not numerically derived in this PR** |
| **Implementable** | **Unresolved** | Depends on **loop-head** topology; GL interpose mutation **rejected** ([mutation topology](border-multidraw-mutation-topology.md)) |

Do **not** treat ~1,350 / ~1,200 as conservative measured ceilings; they were estimates tied to an interpose topology that fails Gates 2/3a.

## Gross prototype screen (Gate 5)

Only the **structural** ceiling is evidence-backed today:

```text
structural_adjacent_pairs_per_frame ≈ 1,562
```

At ~55 swaps/s (v3.1 readiness), if every adjacent pair became one eliminated call (optimistic upper bound):

```text
upper_bound_draw_calls_eliminated/s ≈ 1,562 × 55 ≈ 86k/s
```

That is **not** an implementable claim without a valid mutation topology and batch-length distribution.

```text
implementable_draw_calls_eliminated/s: unresolved
gross_avoided_submission_ms/s: not computed (no implementable rate)

qualitative additional cost:
  gather: low at loop-head (linear over run length)
  runtime classification: required before mutation
  multidraw fixed overhead: PR C observer-only until loop-head

verdict: stop mutation — proceed static RE + observer; rework implementable ROI after loop-head design
```

## Decision

| Rule | Result |
|------|--------|
| GO-A (≥85k/s implementable) | **NO** — implementable rate unknown; interpose NO-GO |
| GO-B (40–85k/s) | **NO** |
| Venice **mutation** ABABA | **Do not run** until loop-head patch + engagement gate |
| Venice **observer** ABABA | **Not warranted / not authorized** — no decision-critical evidence before loop-head feasibility; see [border-loop-head-feasibility.md](border-loop-head-feasibility.md) |

Net ≥5% CPU is **PR C only** after a valid mutation topology ships.
