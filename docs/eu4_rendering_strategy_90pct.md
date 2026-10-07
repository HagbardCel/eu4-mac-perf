# EU IV macOS Rendering Strategy — From Gfx Abstraction to a High-Efficiency Metal Renderer

## Mission

Our long-term ambition is to reduce the **graphical workload of EU IV on modern Apple Silicon by roughly 90% in static/paused-map scenarios**, while preserving visual correctness and normal gameplay behavior.

This is a **directional systems goal**, not a current measured prediction. The existing evidence does not show that 90% is already removable through one optimization. Reaching that scale would likely require several layers of improvement working together.

**State of knowledge (facts, closed hypotheses, evidence pointers):** [`eu4_macos_rendering_findings.md`](eu4_macos_rendering_findings.md). Border mode-0 MDE is closed for paused Venice — [`analysis/evidence/border-loop-head-roi-readiness-20261005.json`](../analysis/evidence/border-loop-head-roi-readiness-20261005.json) (`roi_gate_reason: MODE0_DOMAIN_EMPTY`); live census — [`analysis/evidence/border-mode-census-live-20261007T074355Z.json`](../analysis/evidence/border-mode-census-live-20261007T074355Z.json).

- cheaper rendering backend;
- removal of redundant state/submission work;
- batching of high-volume render loops;
- explicit Metal pipeline/resource handling;
- frame-level throttling or caching where the scene is static;
- selective movement from immediate rendering toward command collection.

The strategy is therefore **bottom-up first, then progressively upward**.

---

## 1. Core hypothesis

EU IV's macOS renderer is expensive not because the paused political map contains inherently difficult graphics, but because an old, submission-heavy OpenGL-era rendering architecture produces an enormous amount of CPU/driver work on the modern macOS/Rosetta/OpenGL-compatibility stack.

The current paused-Venice fixture issues roughly:

```text
5,863 GL draw submissions / frame
~55 rendered frames / second
~322,000 draw submissions / second
```

with most of the volume coming from ([`analysis/draw-path-decision.md`](../analysis/draw-path-decision.md), intrusive screening):

```text
mesh objects   ~2,774 / frame
borders        ~2,285 / frame
map text         ~381 / frame
UI/text          ~251 / frame
```

Border **2,285/swap** is live census-proven (mode-other only); the table above is from pre-census draw attribution. Our working hypothesis is that a significant part of this workload is **architectural overhead rather than necessary GPU work**.

---

## 2. Strategic insight: exploit the Gfx abstraction boundary

The executable already appears to funnel rendering through a relatively small Clausewitz graphics layer:

```text
high-level renderer
    ↓
GfxSet* / GfxDraw* / Gfx resource abstractions
    ↓
OpenGL
```

There are only 48 direct draw-helper sites ([`analysis/draw-callers.json`](../analysis/draw-callers.json)) and four main draw-helper families:

```text
GfxDraw
GfxDrawIndexed
GfxDrawIndexedInstanced
GfxDraw2dLines
```

This suggests a potentially powerful route:

> **Instead of patching hundreds of high-level renderers independently, identify the minimum Clausewitz Gfx API boundary needed to sever the OpenGL backend.**

That becomes the architectural foundation for a Metal migration.

---

## 3. The B→C migration model

We explicitly distinguish two stages.

### Option B — state-aware Metal backend behind existing Gfx semantics

Goal:

```text
EU IV renderer
    ↓
existing Clausewitz Gfx API
    ↓
our Metal backend
    ↓
Metal
```

The first backend should prioritize **correctness and containment**, while already avoiding obviously bad one-to-one OpenGL translation.

Instead of immediately emitting Metal commands for every legacy setter, maintain a shadow state:

```text
CurrentRenderState
    shader/effect
    vertex layout
    vertex buffers
    index buffer
    textures/samplers
    blend state
    depth/stencil state
    raster state
    constants
    render targets
```

At draw time:

```text
GfxDraw*
    ↓
derive PipelineKey
    ↓
lookup/cache MTLRenderPipelineState
    ↓
bind only changed resources/state
    ↓
encode Metal draw
```

This stage is intended to replace Apple's legacy OpenGL compatibility path without yet rewriting the entire high-level renderer.

### Option C — progressively native/optimized Metal renderer

Once B is functional, selected passes evolve from immediate translation toward command collection:

```text
legacy render calls
    ↓
collect DrawCommand objects
    ↓
group within safe render-pass/order domains
    ↓
batch / minimize pipeline & resource changes
    ↓
encode compact Metal command stream
```

Possible command representation:

```text
DrawCommand
    pipeline_key
    vertex/index geometry
    textures/samplers
    constant ranges
    depth/order class
    draw arguments
```

This allows the backend to become increasingly Metal-native without requiring an all-at-once renderer rewrite.

---

## 4. Why not build a raw OpenGL→Metal shim?

A direct compatibility layer at the GL API boundary is not the preferred architecture.

A naïve implementation would preserve behavior like:

```text
bind texture
set blend
set shader
upload uniform
draw
bind texture
upload uniform
draw
...
```

and merely map each operation to Metal.

That would remove Apple's OpenGL layer, but retain much of the same submission/state churn.

Metal is most effective when work is organized around:

- explicit render passes;
- cached immutable pipeline states;
- explicit resource ownership and synchronization;
- fewer redundant bindings;
- large command streams;
- batching/reuse across homogeneous work.

Therefore the Gfx boundary is a much more promising interception point than raw OpenGL calls.

---

## 5. 90% ambition: what it means

The **~90% target applies to avoidable graphical workload in favorable static/paused scenarios**, not to total process CPU usage and not necessarily to active gameplay (battles, animations, multiplayer, mod overlays).

**Primary metric for the ambition:** profiler-off **process CPU-ms/s** attributable to rendering / idler work (with draw submissions and swap cadence as structural corroboration). Power is supporting evidence.

A plausible decomposition of that ambition is:

### A. Eliminate legacy driver/backend overhead

A native Metal backend could remove substantial CPU cost currently spent in:

- Apple OpenGL validation/translation;
- repeated legacy state reconciliation;
- GL function-call dispatch;
- deprecated driver paths;
- redundant state changes.

### B. Collapse high-volume inner loops

Examples:

- border BaseVertex walks;
- map-text draws;
- future safe batching domains discovered by RE.

The border census alone exposes ([`074355Z`](../analysis/evidence/border-mode-census-live-20261007T074355Z.json)):

```text
border_structural_walks / candidate_swaps = 1,024 / 256 = 4 walks per swap
2,285 drawable mode-other loop-head entries per swap
```

which is an unusually attractive loop structure for command aggregation **after** static RE proves batchable subruns.

### C. Reduce frame frequency when visual state is static

A paused/static map need not necessarily redraw at ~55–60 Hz.

Possible policy:

```text
active camera / active UI     → full rate
paused but animated           → moderate rate
paused + visually static      → low rate or dirty-driven redraw
background                    → very low rate
```

A simple 30-fps paused cap already removes ~45% of frame-proportional rendering work versus 55 fps. Dirty-driven map caching could reduce static-scene rendering much further.

**Research policy only:** adaptive caps and dirty redraw affect UX, input, and mods — not silent production behavior without explicit product decisions.

### D. Avoid repeated resource/pipeline setup

Metal pipeline/resource caches should allow repeated draws to avoid reconstructing equivalent state.

### E. Eventually cache or reuse static map layers

Longer-term, selected map layers could render into reusable targets and only refresh when their inputs change.

Together, these mechanisms make a ~90% static-scene graphical-workload reduction an ambitious but conceptually coherent systems target.

---

## 6. Phased research and implementation plan

### Phase 0 — map the Gfx/OpenGL minimum cut

Before writing Metal code, build a complete inventory of the Clausewitz graphics backend.

Deliverable: a dependency table such as:

```text
Clausewitz function          OpenGL/API behavior           Primary callers
--------------------------------------------------------------------------
GfxDrawIndexed              draw elements variants       borders / mesh / text
GfxDraw                     draw arrays                   UI / postprocess / ...
GfxSetIndexBuffer           bind element buffer          many renderers
GfxSetVertexBuffers         vertex buffer/attrib state   many renderers
GfxSetTextures              texture/sampler state        mesh / map / UI
GfxSetShader / effect       program/shader state         ...
GfxUpdateConstantBuffer     uniform/constants            ...
render-target functions     FBO / viewport / clears      ...
present                     swap / flush                 frame end
```

Questions to answer:

1. How many Gfx/backend functions must be replaced to eliminate all reachable OpenGL use?
2. Which OpenGL calls bypass the Gfx layer?
3. Are resource creation/destruction paths centralized?
4. How are shaders stored and compiled?
5. Where are render-target/pass transitions represented?
6. Which functions contain OpenGL-specific cached state that higher layers depend on?

**Decision gate:** if the practical minimum cut of **Gfx functions that must emit or synchronize OpenGL for normal rendering** is on the order of a few dozen well-defined entry points, continue toward a backend prototype. A large total Gfx symbol count (debug, resource creation, edge cases) does not by itself fail the gate — only **reachable draw/state/present paths** matter. If OpenGL semantics are deeply embedded across hundreds of unrelated high-level routines with no clean seam, reassess.

---

### Phase 1 — continue the highest-value local RE in parallel

Proceed with mode-other/BaseVertex border analysis.

Goals:

- map per-record varying state;
- determine homogeneous run lengths;
- understand VBO/IBO/basevertex behavior;
- prove or reject `glMultiDrawElementsBaseVertex` equivalence;
- quantify non-draw setup that could disappear when consuming a run at loop head.

This work serves two purposes:

1. potential immediate performance improvement;
2. concrete understanding of what the future Metal backend must optimize.

---

### Phase 2 — prototype Metal backend skeleton

Only after the minimum-cut inventory is encouraging.

Start with the smallest viable slice:

```text
context/frame lifecycle
render target
one vertex format
one shader/effect path
vertex/index buffers
one texture path
GfxDrawIndexed
present
```

Do not attempt the entire game immediately.

Success criterion: render a deliberately narrow known path or synthetic harness through Metal with engine-compatible Gfx state semantics.

---

### Phase 3 — state shadow + pipeline cache

Implement:

```text
Clausewitz state changes
        ↓
shadow state only
        ↓
PipelineKey / ResourceKey
        ↓
cached Metal objects
```

Avoid creating/rebuilding Metal pipeline state during normal draws.

Core cache candidates:

- render pipeline states;
- depth/stencil states;
- sampler states;
- vertex descriptors;
- shader function combinations;
- resource binding layouts.

---

### Phase 4 — full draw-helper coverage

Support all observed draw families:

```text
GfxDraw
GfxDrawIndexed
GfxDrawIndexedInstanced
GfxDraw2dLines
```

Then close remaining reachable draw variants as needed.

Goal: make the Metal backend capable of reproducing the main paused-Venice scene without relying on OpenGL for draw submission.

---

### Phase 5 — resource backend

Implement/replace:

- textures;
- vertex/index buffers;
- dynamic/transient constant data;
- render targets;
- clears;
- uploads;
- lifetime management;
- synchronization.

Use ring buffers / suballocation for high-frequency constants rather than emulating repeated GL uniform uploads literally.

---

### Phase 6 — shader migration

Determine the least-cost shader path:

- recover original GLSL/ARB sources if available;
- identify engine shader metadata;
- evaluate GLSL → SPIR-V → MSL tooling;
- precompile/cache Metal shader functions where possible;
- map Clausewitz effect permutations to pipeline keys.

Shader feasibility may be the largest single determinant of the full backend effort.

---

### Phase 7 — evolve B toward C

Once Metal output is correct, move selected rendering domains from immediate calls to collected commands.

Priority domains:

1. borders;
2. map text;
3. opaque terrain/map overlays;
4. opaque mesh subsets where ordering permits;
5. UI subsets only if worthwhile.

Do not globally reorder transparent or order-sensitive work.

---

### Phase 8 — frame-level workload elimination

Add high-level scheduling/caching optimizations:

- paused/static adaptive frame limit;
- dirty-region / dirty-pass tracking;
- cached static map layers;
- separate high-frequency UI from low-frequency map redraw where feasible.

Scope: **paused-map / visually static** scenes only. Success is measured primarily against **profiler-off process CPU-ms/s** (and corroborating draw/swap reduction), not a single draw-count proxy.

This phase is likely necessary to approach the ~90% static-scene ambition.

---

## 7. Effort/performance trade-off ladder

| Option | Effort | Likely payoff | Strategic value |
|---|---:|---:|---|
| Border MDEBV batching (post-census RE + proof) | Medium (RE first) | Moderate if feasible | Immediate + informs backend |
| Map-text batching | Low–medium | Small–moderate | Useful secondary win |
| Targeted state/uniform elision | Medium | Unknown/moderate | Backend-relevant |
| Paused adaptive frame cap | Low–medium | Large for static scenes | High practical value |
| Gfx API minimum-cut RE | Low–medium | Information gain | **Critical decision step** |
| State-aware Metal backend (B) | Very high | Potentially large | Strategic platform shift |
| Command-collecting Metal renderer (C) | Extremely high | Very large ceiling | Long-term architecture |
| Static-layer caching / dirty rendering | High | Extremely large static-scene win | Likely needed for ~90% goal |

---

## 8. Quantitative milestones

The project should avoid a single all-or-nothing 90% criterion. Use staged targets.

### Near term

- demonstrate one local optimization with **≥2–5% CPU improvement** profiler-off;
- quantify border BaseVertex removable calls;
- map the Gfx minimum cut.

### Medium term

- reduce draw/state submissions materially without lowering visual fidelity;
- demonstrate a Metal-backed subset of rendering;
- reach **20–40% lower rendering-side CPU/power** in paused scenes through backend + batching + state reduction.

### Long term

- native Metal path for dominant rendering passes;
- adaptive/static frame scheduling;
- cached static map work;
- target **~90% reduction in avoidable graphical workload** in fully static paused-map conditions.

Again, this last target is a design ambition, not a currently demonstrated forecast.

---

## 9. Measurement principles

All optimizations should preserve the evidence discipline established so far.

### Performance

Prefer profiler-off A/B or ABABA comparisons.

Primary metrics:

- process CPU-ms/s;
- CPU-ms/frame when cadence changes;
- swap/frame cadence;
- combined power as supporting evidence.

### Correctness

- visual comparison against normal-render variation envelope;
- no silent fallback misclassification;
- fail closed when state equivalence is uncertain.

### Structural telemetry

Use cheap count-only telemetry where possible:

- calls/frame;
- state changes/frame;
- resource binds/frame;
- pipeline changes/frame;
- batch-size histograms;
- eliminated submissions/frame.

Avoid repeating heavily intrusive timing instrumentation unless absolutely necessary.

---

## 10. Architectural principles

1. **Optimize above the driver when possible.** Removing work before it reaches OpenGL/Metal is more valuable than making the same work slightly cheaper.
2. **Use the Gfx abstraction as the migration seam.** Do not start with a general-purpose GL compatibility layer.
3. **Preserve semantics first, then loosen compatibility.** B should work before C becomes aggressive.
4. **Cache immutable Metal state aggressively.** Pipeline creation belongs outside hot loops.
5. **Do not globally reorder rendering.** Respect pass, transparency, UI, and postprocess ordering.
6. **Prefer run/pass-level transformations over individual-call wrappers.**
7. **Use local RE experiments as architectural probes.** Border batching is valuable even if the eventual backend supersedes it.
8. **Keep the project incremental.** Every phase should produce either a measurable win or a decisive feasibility answer.

---

## 11. Immediate next deliverables

### Deliverable A — Gfx backend minimum-cut map

Produce:

- complete Gfx symbol inventory;
- direct/indirect OpenGL callees;
- callers by renderer subsystem;
- resource/shader/frame-lifecycle functions;
- list of OpenGL bypasses;
- estimate of backend replacement surface.

### Deliverable B — mode-other/BaseVertex static RE

Produce:

- exact call/state path;
- per-walk state invariants/barriers;
- MDEBV feasibility verdict;
- expected eliminated calls/frame;
- expected eliminated setup work/frame.

### Deliverable C — architecture decision

After A+B, choose among:

```text
continue local batching only
        vs
start Metal backend prototype B
        vs
run both tracks in parallel
```

The current expectation is that **parallel work is likely optimal**: local border/text optimizations provide near-term wins and improve our understanding of exactly what the future Metal backend must do.

---

## 12. Long-run success state

The desired end state is not merely “EU IV uses Metal.”

It is:

```text
high-level Clausewitz renderer
        ↓
clean state/resource abstraction
        ↓
pass-aware command collection
        ↓
pipeline/resource caching
        ↓
batched Metal encoding
        ↓
adaptive/static frame scheduling
        ↓
GPU
```

For static paused-map scenes, the renderer should ideally do work **only when something visually relevant changes**.

That is the architectural route by which a ~90% reduction in avoidable graphical workload becomes a meaningful target rather than a collection of isolated micro-optimizations.
