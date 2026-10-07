# EU IV macOS Rendering Investigation — State of Knowledge

## Purpose

This document consolidates what we have learned so far about Europa Universalis IV's rendering path on macOS, using the pinned GOG 1.37.5 x86-64 binary and the paused-Venice fixture. It separates **measured facts**, **static reverse-engineering findings**, **closed hypotheses**, and **open architectural questions**.

The goal is not merely to explain why EU IV runs hot on modern Apple Silicon. The deeper goal is to understand the renderer well enough to decide where the best effort/performance trade-offs lie: local batching, higher-level render-loop changes, or ultimately replacing the legacy OpenGL backend with a Metal backend.

**Companion:** canonical roadmap — [`eu4_recommended_strategy.md`](eu4_recommended_strategy.md); cost-based assessment and ranking of alternatives — [`eu4_rendering_alternatives_ranked.md`](eu4_rendering_alternatives_ranked.md). The earlier `eu4_rendering_strategy_90pct.md` was superseded and removed (see git history).

**Note:** "Current conclusion" / "Still promising" items below that prioritize border mode-other RE or map-text batching predate the cost-based ranking. The roadmap deprioritizes both.

**Key evidence (repo):**

| Topic | Source |
|--------|--------|
| Draw decomposition (5,863/frame, intrusive screening) | [`analysis/draw-path-decision.md`](../analysis/draw-path-decision.md), trace `results/20260928T113359Z-draw-trace` (failed 5% intrusion gate; counts still useful) |
| Border mode census (live, schema v6) | [`analysis/evidence/border-mode-census-live-20261007T074355Z.json`](../analysis/evidence/border-mode-census-live-20261007T074355Z.json) |
| Mode-0 ROI closed (`MODE0_DOMAIN_EMPTY`) | [`analysis/evidence/border-loop-head-roi-readiness-20261005.json`](../analysis/evidence/border-loop-head-roi-readiness-20261005.json) |
| Mode-0 Gfx path / record-field corrections (mode-0 only) | [`analysis/border-mode0-basevertex-evidence-addendum.md`](../analysis/border-mode0-basevertex-evidence-addendum.md) |
| Mode-other draw site `0x1010cc3b8` / `GfxDrawIndexed` helper branch | [`analysis/border-draw-inner-loop-re.md`](../analysis/border-draw-inner-loop-re.md), [`analysis/border-gfxdrawindexed-helper-audit.md`](../analysis/border-gfxdrawindexed-helper-audit.md) |
| Direct `GfxDraw*` call/jump inventory (static) | [`analysis/draw-callers.json`](../analysis/draw-callers.json) |

---

## 1. Executive conclusion

The investigation has converged on a fairly coherent picture:

1. **EU IV is submission-heavy, not obviously geometry-heavy.** In the paused-Venice fixture we observe roughly **5,863 OpenGL draw submissions per rendered frame** ([draw-path screening](../analysis/draw-path-decision.md)), at roughly the display cadence (~55 fps; census swap rate ~55.8 Hz in run `074355Z`), or on the order of **322k draw submissions/s**.
2. A few renderer branches dominate the draw volume (same screening source):
   - mesh objects: ~2,774 draws/frame;
   - borders: ~2,285 draws/frame;
   - map text: ~381 draws/frame;
   - UI/text: ~251 draws/frame;
   - other/unclassified: ~172 draws/frame.
3. The largest branch by count, mesh rendering, turned out to be a poor target for simple adjacency batching because per-object/per-subrecord state changes are real and recurrence was screened negative under the tested predicates.
4. The border renderer is structurally much more regular. The schema-v6 census ([`074355Z` evidence](../analysis/evidence/border-mode-census-live-20261007T074355Z.json)) showed **all** paused-Venice border loop-head traffic is on the **mode-other engine branch**: exactly **2,285 drawable mode-other entries per candidate swap**, with **zero mode-0** and **zero mode-1** iterations. Census did **not** measure `record+0x10` or the effective GL endpoint. Static RE shows the mode-other site loads `+0x10` into `%ecx` before `GfxDrawIndexed` (see §7). In this fixture, **candidate swap ≈ one rendered map frame** (not simulation tick).
5. The border work appears as roughly **4 large structural walks per swap**, averaging ~571 drawable records per walk (from census counters; see §7). Loop-head batching toward `glMultiDrawElementsBaseVertex` is a **candidate** only if RE establishes homogeneous subruns **and** a nonzero-BaseVertex GL path for those records — a more plausible direction than the closed mode-0 `glMultiDrawElements` hypothesis.
6. The executable already uses a recognizable **Clausewitz Gfx abstraction layer** (`GfxDraw*`, `GfxSet*`, buffer/texture/effect abstractions). The pinned binary has **48 direct `GfxDraw*` call/jump sites** in [`analysis/draw-callers.json`](../analysis/draw-callers.json) (direct call/jump inventory only), funneled into four main draw-helper families. This raises a credible long-term possibility: replace or progressively supersede the OpenGL backend at the Gfx boundary rather than rewriting every game renderer.
7. However, a naïve one-to-one Gfx→Metal translation would likely preserve much of the old immediate/state-machine behavior. A good Metal renderer requires pipeline-state caching, explicit resource management, command encoding, and eventually higher-level batching/collection. Therefore the promising architectural path is **B→C** (detailed in [`eu4_recommended_strategy.md`](eu4_recommended_strategy.md) §3):
   - **B:** implement a state-aware Metal backend behind the existing Gfx API;
   - **C:** progressively move batching, command collection, and pass-level optimization upward once the backend boundary is understood.

---

## 2. Pinned scope and evidence model

The current conclusions are primarily fixture-specific:

- binary: GOG EU IV 1.37.5, x86-64;
- execution: Apple Silicon under Rosetta / modern macOS OpenGL compatibility stack;
- main fixture: paused Venice save / stable map view;
- emphasis: rendering submission behavior, not gameplay simulation cost.

Important evidence discipline established during the work:

- heavily instrumented timings are not trusted as final performance numbers;
- call counts and structural topology from intrusive runs can still be useful;
- final performance decisions should use profiler-off A/B testing;
- static RE conclusions are separated from live-frequency evidence;
- mutation is not authorized until both semantic equivalence and workload opportunity are established.

---

## 3. High-level frame/render call graph

The closest identified top-level render path to a conceptual `draw_frame()` is:

```text
CApplication::UpdateOneFrame(bool)                    ~1 / rendered frame
│
├─ CInGameIdler::Idle(bool)                          ~1
│    ├─ simulation / UI / input work
│    ├─ CEU3GraphicalMap::Update(...)
│    └─ ...
│
└─ CInGameIdler::Render()                            ~1 / frame
     │
     ├─ CEU3GraphicalMap::PreRender(...)
     ├─ CGraphics::BeginScene(...)
     ├─ CGraphics::ClearScene(...)
     │
     ├─ CEU3GraphicalMap::Render(...)
     │    │
     │    ├─ map/terrain/water passes
     │    ├─ CPdxMapBorderLayer::DrawBorders(...)
     │    │     └─ ~2,285 draw-helper invocations/frame
     │    ├─ CCountryNameCollection::RenderNames(...)
     │    │     └─ ~381 draws/frame
     │    ├─ CGraphics::RenderBuckets(...)
     │    │     └─ CPdxMeshObject::RenderBuckets(...)
     │    │           └─ ~2,774 draws/frame
     │    ├─ trade routes / arrows / transparent buckets / etc.
     │    └─ ...
     │
     ├─ CEU3GraphicalMap::PostRender(...)
     ├─ CGraphics::Render2dObjectsToScreen(...)
     ├─ CGraphics::RenderAlwaysInFrontObjectsToScreen(...)
     │     └─ UI/text contribution ~251 draws/frame
     ├─ post-processing / miscellaneous
     │     └─ ~172 draws/frame
     ├─ CGraphics::EndScene()
     └─ CGraphics::PresentScene()
           └─ Cocoa_GL_SwapWindow / CGLFlushDrawable
```

The important distinction is that functions such as `DrawBorders()` or `RenderBuckets()` are high-level routines called once or a small number of times per frame. The thousands of calls occur **inside their inner loops**, usually at the `GfxDraw*` layer.

---

## 4. Measured draw-call decomposition

For the paused-Venice fixture, the best current decomposition is:

| Branch | Draws/frame | Approx. share | Approx. draws/s at 55 Hz |
|---|---:|---:|---:|
| Mesh objects | 2,774 | 47.3% | 152.6k |
| Borders | 2,285 | 39.0% | 125.7k |
| Map text | 381 | 6.5% | 21.0k |
| UI/text | 251 | 4.3% | 13.8k |
| Other / unknown | 172 | 2.9% | 9.5k |
| **Total** | **5,863** | **100%** | **~322.5k** |

Source: [Phase II draw-path decision](../analysis/draw-path-decision.md) (intrusive attribution over 31 complete frames; 32nd frame partial). Trace failed the 5% intrusion gate — use for **topology and counts**, not timing claims.

This immediately suggests that optimization leverage is highly concentrated. Mesh + borders alone account for ~86% of all draw submissions.

---

## 5. Clausewitz draw abstraction

Static inventory found **48 direct `GfxDraw*` call/jump sites** in the pinned executable ([`analysis/draw-callers.json`](../analysis/draw-callers.json) — see `limits`; see also [`analysis/frame-model-draw-api.json`](../analysis/frame-model-draw-api.json)):

| Engine helper family | Direct call sites |
|---|---:|
| `GfxDraw` | 28 |
| `GfxDrawIndexed` | 12 |
| `GfxDrawIndexedInstanced` | 4 |
| `GfxDraw2dLines` | 4 |
| **Total** | **48** |

These call sites cover terrain, water, borders, trees, map text, mesh objects, particles, UI, post effects, debug paths, and related renderers.

Historically observed GL draw-family endpoints include:

```text
glDrawArrays
glDrawElements
glDrawElementsBaseVertex
glDrawArraysInstanced
glDrawElementsInstanced
glDrawElementsInstancedBaseVertex
```

This is a major architectural clue: draw submission is not scattered arbitrarily through thousands of engine functions. It is substantially funneled through a small Gfx layer.

The same pattern appears for state/resource setup through symbols and objects such as:

```text
GfxDeferredContextGFX
GfxMasterContextGFX
TextureGFX
VertexBufferGFX
ConstantBufferGFX
SGfxEffect
GfxSetTextures(...)
GfxSetVertexBuffers(...)
GfxSetIndexBuffer(...)
```

This does **not yet prove** that every OpenGL dependency is cleanly isolated behind one backend. Mapping that minimum cut is now an explicit research objective.

---

## 6. Mesh rendering path

`CGraphics::RenderBuckets()` dispatches into `CPdxMeshObject::RenderBuckets()` and related object types. The mesh path is nested:

```text
CGraphics::RenderBuckets
  └─ CPdxMeshObject::RenderBuckets
       └─ selected layer / opaque-or-transparent array
            └─ 80-byte SFlushData record
                 └─ 0xe8-byte mesh subrecord
                      ├─ effect selection
                      ├─ texture setup
                      ├─ vertex-buffer setup
                      ├─ index-buffer setup
                      ├─ object-constant update/bind
                      └─ GfxDrawIndexed
                           └─ glDrawElements / glDrawElementsBaseVertex
```

Key findings:

- roughly **2,774 mesh draws/frame** in the paused fixture;
- object-specific transforms/constants are updated immediately before draws;
- effect/texture/buffer state can vary across subrecords;
- transparent ordering may matter;
- same geometry handles are therefore insufficient to establish mergeability;
- same-/cross-parent buffer/subrecord recurrence was screened negative under the tested observer predicates.

### Current conclusion

Mesh remains the largest draw source, but **simple adjacency batching is deprioritized**. A future Metal backend may still reduce mesh overhead substantially through cheaper state translation and pipeline caching even if geometry-level batching remains hard.

---

## 7. Border rendering path

The border renderer has three direct `GfxDrawIndexed` sites and a 28-byte draw-record structure. Earlier work targeted the mode-0 path, where `%ecx == 0` selects `glDrawElements` (see [mode-0 addendum](../analysis/border-mode0-basevertex-evidence-addendum.md)). Mode-0 MDE ROI is **closed** for paused Venice: [`roi_gate_reason: MODE0_DOMAIN_EMPTY`](../analysis/evidence/border-loop-head-roi-readiness-20261005.json).

The schema-v6 live census (run `20261007T074355Z`, [evidence JSON](../analysis/evidence/border-mode-census-live-20261007T074355Z.json)) resolved the key uncertainty:

```text
border_loop_head_entries                         = 584,960
candidate_swaps                                 = 256
entries / swap                                  = 2,285
mode0 entries                                   = 0
mode1 entries                                   = 0
mode-other entries                              = 584,960
mode-other visible                              = 584,960
mode-other visible + triangle_count > 0         = 584,960
```

Therefore:

```text
mode 0     = 0%
mode 1     = 0%
mode other = 100%
```

The workload is also structurally regular (census window: **256** `candidate_swaps`, frozen end counters):

```text
border_structural_walks     = 1,024     → 1,024 / 256 swaps = 4 walks per swap
border_loop_head_entries    = 584,960   → 584,960 / 1,024 walks ≈ 571.25 records/walk
```

Schema-v6 `border_structural_draws = 0` reflects an **empty mode-0 drawable domain**, not “no border GL work.” All 584,960 loop-head entries are mode-other with visible nonzero triangles.

In this fixture, **candidate swap ≈ one rendered map frame** at paused cadence.

**What census measured:** mode partition (0 / 1 / other), visibility, triangle count, loop-head entry counts, structural walks.

**What census did not measure:** `record+0x10`, `%ecx` at the mode-other `GfxDrawIndexed` site (`0x1010cc3b8`), or whether draws reach `glDrawElements` vs `glDrawElementsBaseVertex`.

**Static RE (not census):** at `0x1010cc3b8`, `movl record+0x10, %ecx` then `call GfxDrawIndexed`; the helper uses `%ecx` to select `glDrawElements` when zero vs `glDrawElementsBaseVertex` when nonzero ([helper audit](../analysis/border-gfxdrawindexed-helper-audit.md)).

### Current conclusion

The mode-0 `glMultiDrawElements` hypothesis is closed for paused Venice because the domain is empty. The immediate track is **mode-other / helper-arg (+0x10) branch RE**: determine the distribution of `+0x10` and therefore how often the effective GL path is BaseVertex — **not** a global GL interposer. `glMultiDrawElementsBaseVertex` remains a **candidate** transformation only if RE finds batchable homogeneous subruns per walk **and** establishes nonzero-BaseVertex use for those records.

The key static questions are now:

- distribution and semantics of record `+0x10` as the fourth `GfxDrawIndexed` argument;
- VBO and IBO stability within walks;
- state mutations between successive records;
- whether counts/indices/basevertex arrays can be gathered cheaply;
- whether loop-head batching can preserve engine and CPU continuation state.

---

## 8. Map text and UI

### Map text

`CCountryNameCollection::RenderNames()` accounts for roughly **381 draws/frame** ([draw-path decision](../analysis/draw-path-decision.md)). Intrusive screening found ~11,744 map-text adjacent same-tracked-state pairs across 31 complete frames (~**379/frame**) — **screening only**, not census-proven.

This is a promising secondary batching candidate, although engine-side constants/geometry/order still need auditing before mutation.

### UI/text

UI/text contributes roughly **251 draws/frame** across many renderers (`CBitmapFont`, masked sprites, progress bars, tiled sprites, etc.). The workload is more fragmented and therefore currently lower priority.

---

## 9. What has been ruled out or deprioritized

### Closed / deprioritized

- generic GL setter caches as primary strategy;
- simple same-parent/cross-parent mesh adjacency batching for paused Venice;
- mode-0 border `glMultiDrawElements` for paused Venice ([readiness evidence](../analysis/evidence/border-loop-head-roi-readiness-20261005.json));
- more intrusive profiler qualification as the main path;
- global GL interposition as the preferred mutation point.

### Still promising

- mode-other / helper-arg (+0x10) border RE; conditional MDEBV if GL path and subruns support it;
- map-text batching;
- state/uniform reduction where counts justify it;
- frame-level throttling/caching for paused/static scenes;
- Clausewitz Gfx backend replacement / Metal migration.

---

## 10. Why a Metal backend is plausible

The current renderer appears to have a meaningful graphics abstraction boundary:

```text
high-level game renderer
        ↓
Clausewitz Gfx API
        ↓
OpenGL backend
        ↓
Apple OpenGL compatibility stack
        ↓
Metal driver / GPU
```

A possible future architecture is:

```text
high-level game renderer
        ↓
Clausewitz Gfx API
        ↓
state-aware Metal backend
        ↓
Metal
```

This is much more plausible than rewriting every high-level render function.

However, simple one-to-one translation would likely preserve too much of the old OpenGL-era state-machine behavior. A good Metal backend should instead:

- maintain a shadow render state;
- derive/cache Metal pipeline-state objects from Clausewitz state;
- manage buffers/textures explicitly;
- translate/update shader resources efficiently;
- encode commands within explicit render passes;
- eventually collect/batch work where ordering permits.

Thus the realistic path is incremental: first preserve semantics behind the Gfx API, then optimize upward. **Implementation** should migrate **complete render domains vertically** (shader + resources + pass + draw), not bring up all four `GfxDraw*` families horizontally while the rest of the stack remains OpenGL — see [`eu4_recommended_strategy.md`](eu4_recommended_strategy.md) §3.5.

---

## 11. Open questions

The highest-value unanswered architectural questions are now:

1. **What is the complete Clausewitz Gfx backend surface?**
2. How many Gfx functions form the practical minimum cut between renderer and OpenGL?
3. Are texture, buffer, shader, render-target, and present operations as centralized as draw submission?
4. Where are shaders represented/compiled, and how difficult would GLSL/ARB → MSL translation be?
5. Can old Gfx state be converted into stable Metal pipeline keys without touching most game render code?
6. Which render passes permit deferred command collection/reordering?
7. How much of the current CPU cost sits in engine-side setup versus OpenGL driver submission?
8. How much work can be removed by frame-level throttling or static-scene caching independently of backend migration?

---

## 12. Current best mental model

The renderer should currently be thought of as a hierarchy of leverage:

```text
LEVEL 1 — frame scheduling
CInGameIdler::Render
    ↓
LEVEL 2 — render passes / loops
DrawBorders / RenderBuckets / RenderNames / UI
    ↓
LEVEL 3 — per-object / per-record setup
textures / buffers / constants / effect state
    ↓
LEVEL 4 — Clausewitz Gfx API
GfxSet* / GfxDraw*
    ↓
LEVEL 5 — OpenGL
bind / uniform / attrib / draw calls
    ↓
LEVEL 6 — Apple OpenGL translation
    ↓
GPU
```

The higher in this graph we can safely eliminate or aggregate work, the greater the expected payoff.

At present, the most promising engineering path is to continue bottom-up enough to understand the Gfx backend boundary, while using selected high-value loops (especially borders) as concrete optimization laboratories.
