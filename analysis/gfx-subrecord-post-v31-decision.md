# Gfx subrecord program — post–v3.1 decision (paused Venice)

**Date:** 2026-10-04  
**Authority:** Venice smoke `20261004T152246Z` on `main` @ `3b49bf5` ([evidence JSON](evidence/gfx-subrecord-multi-hypothesis-smoke-20261004T152246Z.json)).

## Executive summary

The **mesh adjacency / buffer-bind recurrence** track is a **screened negative on the paused Venice fixture** under v3.1 semantics. No further Venice observer smokes are justified for bits 0–3. The next structural CPU hypothesis is **border `glMultiDrawElementsBaseVertex`**, gated by offline static RE ([border-multidraw-re.md](border-multidraw-re.md)) before any mutating launch.

## Draw mix (approximate, paused Venice)

| Path | ~draws / swap (sources) |
|------|-------------------------|
| Mesh subrecord (`RenderBuckets` site) | ~2,425 site entries / swap (v3.1 B1) |
| Border (`DrawBorders`) | ~2,285 / frame (complete-frame capture, [mesh-render-path.md](mesh-render-path.md)) |
| Map text | ~379 adjacency screen / frame ([draw-screening.md](../results/20260928T113359Z-draw-trace/draw-screening.md)) |

Intrusive draw trace (failed 5% gate): ~39% border draws; ~1,562 same-state border adjacencies / frame upper bound.

## v3.1 recurrence result (fixture-scoped)

On B1 ARM→FREEZE (555 swaps):

| Hypothesis | Evaluated | Hits |
|------------|------------:|-----:|
| `same_parent_buffer_signature` | 54,945 | **0** |
| `cross_parent_buffer_signature` | 1,290,465 | **0** |
| `same_subrecord_pointer_cross_parent` | 1,290,465 | **0** |

Infrastructure: `nonempty_fraction` 0.5, frozen-bank identities exact, `stage2_recommended: false`.

**Non-ruling:** Other map modes, camera motion, wartime overlays, or zoom levels may exhibit recurrence; this does not reopen the paused-Venice adjacency program without new semantics.

## CPU / power scenario (not measured net savings)

Profiler-off readiness on the same run: **~1,115 CPU-ms/s**, **~55 swaps/s**. Border multidraw gross screens use **~0.65 µs per eliminated draw call** as an avoided-submission scenario (PR B); **net ROI requires PR C**.

## Closed / deprioritized (this repo program)

| Track | Status |
|-------|--------|
| Same-/cross-parent mesh buffer elision | Screened negative (paused Venice v3.1) |
| Generic mesh coalescing / instancing | Deprioritized |
| Generic GL setter caches | Closed (prior validation) |
| GPU-bound / present-first | Out of scope |

## Next work

1. **Loop-head feasibility (offline)** — [border-loop-head-feasibility.md](border-loop-head-feasibility.md); semantic/implementable ceilings unresolved until classifier + live counters.  
2. **No Venice observer ABABA** — PR B mutation NO-GO; GL-tail interpose not decision-critical.  
3. **Map-text RE** — [map-text-multidraw-re.md](map-text-multidraw-re.md) remains fallback charter.

Mesh observer dylib remains **maintenance-only** (v3.1 entry + site hooks).
