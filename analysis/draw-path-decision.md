# Phase II draw-path decision (GOG EU IV 1.37.5)

**Historical screening decision, superseded as the immediate next action by
the paused-frame causal investigation.** Border-specific multi-draw consolidation
and map text remain candidates to assess after profiler release gates pass.
The current profiler status is in [the verification record](frame-model-verification.md).

This is a **screening decision**, not a measured optimization result. The
unattended diagnostic stopped after one 32-frame window because its C caller
counter missed the fact that `GfxDrawIndexed` tail-jumps into GL. The recorded
return addresses still identify 99.4% of draws offline. The resulting
[screening report](../results/20260928T113359Z-draw-trace/draw-screening.md)
records 31 complete paused frames with exactly 5,863 draws each. The 32nd
recorded frame began mid-frame. The save was unchanged on cleanup.

| Draw path | Draws in one complete frame | Share |
|---|---:|---:|
| `CPdxMeshObject::RenderBuckets` | 2,774 | 47.3% |
| `CPdxMapBorderLayer::DrawBorders` | 2,285 | 39.0% |
| Map text | 381 | 6.5% |
| UI/text | 251 | 4.3% |
| Other/unknown | 172 | 2.9% |

Across the captured window, 60,290 adjacent draw pairs (32.4% of all draws)
share the **tracked** program, texture, uniform, vertex, buffer, framebuffer,
and render-state signatures. Of these, 48,418 are border pairs, 11,744 are map
text, and only 128 are mesh-object pairs. A 943-draw border run recurs in each
of the 31 complete frames. The border draws use one index buffer and zero
index offset, but varying base vertices; no adjacent pair has a strictly
contiguous index range. A multi-draw API that accepts per-draw base vertices
is therefore the structural candidate. Repeated mesh geometry with changed
uniforms accounts for only 6.1% of all draws, below the planned 15%
instancing threshold.

The trace itself ran at 47.48 swaps/s versus 52.54 swaps/s in its same-launch
control window, a 9.6% reduction. It therefore **failed the 5% intrusion
gate**, and the planned three-window consistency gate was not met. The
same-state count is an upper bound, not proof of legal merging: the tracer
does not model every GL state change or every engine-side constant-buffer
update, and ordering may matter. A prototype must preserve the unmodified
path as a control, check visual equivalence, measure CPU/GPU power and swap
rate in the same launch, and stop on any GL error or unexpected state change.
The static [mesh and border path note](mesh-render-path.md) records the
relevant record layouts and call sites.

No further broad diagnostic launch is warranted before that prototype. The
first captured window already separates the proposed branches by a wide
margin, and another trace would consume user time while leaving the causal
question unanswered. If a narrow border prototype does not improve
performance or cannot preserve rendering semantics, move to adaptive
`CInGameIdler::Render()` scheduling.
