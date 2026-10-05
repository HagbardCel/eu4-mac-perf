# Border path multidraw RE charter

**Binary:** GOG EU IV 1.37.5 x86-64 (`b3d38876abf4e61cdae57509186d7cb7dcb03bfeaca4c95c10c713794715141d`)  
**Owner:** `CPdxMapBorderLayer::DrawBorders` — three `GfxDrawIndexed` sites @ `0x1010cc2e8`, `0x1010cc352`, `0x1010cc3b8`.

## Motivation

Paused Venice: ~2,285 border draws/frame (single IBO, zero GL index offset on mode-0).
Intrusive screening: ~1,562 same-state border adjacencies/frame; recurring **943-draw**
runs (`943×31` frames). Record `+0x04` varies but is **not** GL basevertex on mode-0
([addendum](border-mode0-basevertex-evidence-addendum.md)). Loop-head target:
**`glMultiDrawElements`** with shared mode/type/IBO.

Mesh adjacency recurrence is a **screened negative** on paused Venice v3.1 — see [gfx-subrecord-post-v31-decision.md](gfx-subrecord-post-v31-decision.md).

## Gate chain (PR B → PR C)

| Gate | Deliverable |
|------|-------------|
| 0 API viability | [border-gl-multidraw-api-availability.md](border-gl-multidraw-api-availability.md) |
| 1 Semantic mergeability | [border-multidraw-barriers.md](border-multidraw-barriers.md) |
| 2 Realizability | [border-multidraw-mutation-topology.md](border-multidraw-mutation-topology.md) |
| 3a Temporal / 3b Post-loop | topology + barriers |
| 4 Argument reconstruction | [border-draw-record-layout.json](border-draw-record-layout.json) |
| 5 Gross prototype screen | [border-multidraw-go-nogo.md](border-multidraw-go-nogo.md) |

**Terminology:** `draw_calls_eliminated = draws_covered_by_multidraw - multidraw_calls`. GO thresholds use **draw_calls_eliminated/s** (gross scenario); **net CPU is PR C only**.

## PR sequence

| PR | Scope |
|----|--------|
| A | Mesh screened-negative closure |
| B | Offline RE artifacts (stacked PR #36) |
| C | Capability-2 **observer** harness; mutation blocked pending loop-head |
| D | Map-text RE fallback |

## Status

PR B/C/D merged on `main`. **Mutation: NO** for GL interpose. **Active track:** [border-loop-head-feasibility.md](border-loop-head-feasibility.md) @ `0x1010cbe55` (PR #39 offline RE).
