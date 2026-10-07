# EU IV macOS Rendering — Strategy Assessment, Alternative Hypotheses, and Ranking

## Purpose

This document reviews the earlier plan, `eu4_rendering_strategy_90pct.md`, against evidence already in the repository. That plan has since been removed; it is available in git history, and its architecture is carried over into the canonical roadmap. This document also lists alternative hypotheses with effort and savings estimates, and ranks all candidates by expected performance gain per unit of effort, with a bias toward work that later steps can build on.

The resulting canonical roadmap is [`eu4_recommended_strategy.md`](eu4_recommended_strategy.md); its §1.2 defines the metrics used here. State of knowledge: [`eu4_macos_rendering_findings.md`](eu4_macos_rendering_findings.md).

**Evidence labels used below:**

- **Measured:** read directly from a repo artifact (linked).
- **Derived:** arithmetic on measured values.
- **Estimate:** engineering judgement; not measured. All effort and savings figures are estimates.

---

## 1. Baseline cost model (paused Venice)

### 1.1 Process-level baseline

Measured, profiler-off autonomous baseline ([`results/20260928T065747Z-autonomous/summary.md`](../results/20260928T065747Z-autonomous/summary.md)):

| EU IV CPU ms/s | CPU W | GPU W | Combined W | Swaps/s | CPU ms/swap |
|---:|---:|---:|---:|---:|---:|
| 1,113.69 | 7.47 | 2.01 | 9.50 | 52.78 | 21.1 |

The display runs at 120 Hz with VSync ([`analysis/frame-rate.md`](../analysis/frame-rate.md)), but the game delivers ~50–60 swaps/s. The main thread is the bottleneck, not VSync. GPU power (~2 W) is small next to CPU power (~7.5 W), so this is a CPU-side problem.

### 1.2 Main-thread decomposition

Measured from the paused-Venice `sample` capture [`results/20260926T210454Z-paused-cpu/cpu-sample.txt`](../results/20260926T210454Z-paused-cpu/cpu-sample.txt) (10 s, 477 main-thread stack samples, outermost-frame counting):

| Branch | Samples | Share of main thread |
|---|---:|---:|
| `CInGameIdler::Render` (all rendering) | 352 | **73.8%** |
| ↳ `CEU3GraphicalMap::Render` (map) | 291 | 61.0% |
| ↳↳ `CGraphics::RenderBuckets` (mesh objects) | 211 | **44.2%** |
| ↳↳↳ `CPdxMeshObject::RenderBuckets` | 198 | 41.5% |
| ↳↳↳↳ inside `glDrawElements` (driver) | 158 | **33.1%** |
| ↳↳ `CPdxMap::Render` (terrain + borders) | 71 | 14.9% |
| ↳↳↳ `CPdxMapBorderLayer::DrawBorders` | 59 | 12.4% |
| ↳↳ `CCountryNameCollection` (map text) | 4 | 0.8% |
| ↳ `CGraphics::Render2dObjectsToScreen` (UI) | 42 | 8.8% |
| ↳ `CGraphics::PresentScene` | 12 | 2.5% |
| Simulation / UI update inside `Idle` (non-render) | ~43 | ~9% |
| `CPdxProgram::UpdateInput` (event pump) | 82 | 17.2% |
| ↳ `-[NSCursor set]` → `SLSGetCursorScale` (WindowServer IPC) | 36 | **7.5%** |

Derived: stack frames entering GLEngine / libGL directly from `eu4` code account for **276 / 477 = 57.9%** of main-thread samples. Engine-side render work outside the driver is therefore roughly **352 − 276 ≈ 76 samples ≈ 16%**. The driver hot spots under `glDrawElements` are per-draw validation: `GLDContextRec::setRenderState`, `setRenderSamplersAndTextures`, `prepareResourceForGPUAccess` / `GLRResourceList::addResource` (with `malloc`), and uniform uploads via `setVertexBytes` / `setFragmentBytes`.

### 1.3 Per-call driver cost

Measured inclusive sampled time, full-telemetry phase of [`results/20260927T102247Z-diagnostic/root-cause.md`](../results/20260927T102247Z-diagnostic/root-cause.md) (intrusive; ratios are more trustworthy than absolutes):

| Call | Calls/s | Inclusive ms/s | Derived µs/call |
|---|---:|---:|---:|
| `glDrawElements` | 107,815 | 234.7 | **~2.2** |
| `glDrawElementsBaseVertex` | 98,708 | 60.7 | **~0.6** |
| `glDrawArrays` | 16,130 | 33.9 | ~2.1 |
| all state calls combined (`glUniform*`, attrib, bind, tex) | ~4.1 M | ~165 | ~0.04 |

Derived: per frame in the same run's lighter "Paused Idle" phase (132,636 and 121,422 calls/s at 48.0 swaps/s), `glDrawElements` ≈ 2,760/frame, which matches mesh (~2,774/frame); `glDrawElementsBaseVertex` ≈ 2,530/frame, which matches borders (2,285) plus text/UI. **Inference:** mesh draws are ~3.5× more expensive per call than border draws. State calls are individually cheap; cost is concentrated at draw time, when the driver revalidates dirty state.

### 1.4 Caveats

- `sample` stacks are wall-clock snapshots. A sample can include blocking time; e.g. the cursor path ends in `mach_msg`, so its share is main-thread latency, not necessarily EU IV CPU.
- The CPU sample (2026-09-26) predates the current harness; the scene was the same paused Venice fixture at 3456×2234 / 120 Hz, but camera state was not independently controlled.
- The per-call table comes from an intrusive run that lost ~20% swap rate; treat absolute µs as upper bounds.
- Other threads (Metal completion queue, audio, GOG Galaxy SDK, Rosetta) make up the gap between main-thread time and ~1,110 CPU-ms/s. They are not broken down yet.
- **Metric mismatch:** all shares in §1.2 are **main-thread wall-clock** shares, while 21.1 ms/swap in §1.1 is **process CPU** across all threads. A 44% mesh share therefore does not directly imply 44% of process CPU-ms/swap. Per-frame savings estimates below refer to **main-thread wall ms/frame** and stay provisional until per-thread CPU counters exist (S1).

---

## 2. Assessment of the earlier strategy

**What is right.** Replacing the OpenGL backend at the Clausewitz Gfx boundary, with shadow state and cached pipeline objects, is the correct long-run architecture. A raw GL→Metal shim would preserve the per-draw churn the driver is already paying for. Metal is also the only lever with a high ceiling for **active** scenes (unpaused, camera moving), where frames genuinely differ.

**What is not the most promising, as sequenced:**

1. **Prioritization by draw count instead of cost.** Borders are 39% of draws but ~12% of main-thread time; mesh is 47% of draws but ~44% of time. The border multi-draw track (Phase 1, and the recent census work) has a realistic **ceiling of ~3–7% per frame** (Estimate: borders' ~12% share, minus the irreducible per-sub-draw driver work that `glMultiDrawElementsBaseVertex` still does). Since the border harness work began (2026-10-04), several Venice runs have produced zero eliminations ([`border-loop-head-roi-live-20261007T055904Z.json`](../analysis/evidence/border-loop-head-roi-live-20261007T055904Z.json)). Map text, the planned second batching target, is <1% of main-thread time.
2. **Open question 7 is already answered by existing data.** ~58% of the main thread is inside the GL driver, ~16% is engine-side render work (§1.2). This also explains why the GL setter caches failed: they eliminated ~1.1 M calls/s of cheap state calls ([`analysis/state-cache-validation.md`](../analysis/state-cache-validation.md): ~1% CPU change; [`analysis/sampler-uniform-validation.md`](../analysis/sampler-uniform-validation.md): no gain) without reducing the per-draw validation the driver does anyway.
3. **The static-scene 90% target is out of reach of making frames cheaper alone.** Simulation/UI update (~9%) and the event pump (~17%) remain however rendering is done. Engine-side render work (~16%) also remains **if callers keep their current OpenGL-oriented structure**, though it is not necessarily irreducible once callers emit commands instead of mutating state (see S9). The working estimate for backend + command collection with today's callers is ~50–60% less main-thread wall time per frame; that is not a ceiling. 90% in a static scene still requires *not rendering unchanged frames*, which the earlier strategy deferred to Phase 8, the last phase.
4. **The outcome metric hides per-frame wins.** The game is CPU-bound below the 120 Hz VSync ceiling. Making a frame cheaper raises swaps/s; CPU-ms/s stays roughly flat until fps reaches 120. Batching or Metal results must be reported as **CPU-ms per frame** (and swaps/s), or paired with a cadence-holding cap, otherwise real wins look like zero.
5. **GL/Metal coexistence is the hidden crux, and the plan doesn't yet say where it will be.** Interleaving Metal mesh draws into a GL pass that shares depth with terrain and borders is the hardest integration problem in the plan. A map-layer cache (S4 below) creates a clean seam: the whole map is produced into an offscreen target and composited under the UI. Building that seam first, in GL, gives Metal a well-defined place to plug in later.
6. **A cheap non-rendering cost was missed.** ~7.5% of main-thread time is AppKit re-setting the cursor on every event pump (`NSCursor set` → WindowServer IPC).

**Verdict:** keep the Gfx→Metal architecture as the long-term destination, but reorder by measured cost. Measure first, and start the architecture RE (Gfx minimum cut, shaders, pass/depth graph) **immediately, in parallel**. Take cheap wins, and eliminate redundant frames and layers only where measurement proves the content static. Enter Metal through an offscreen map seam with a **mesh-anchored** self-contained domain. Stop investing in border/text batching beyond writing up the RE already done.

---

## 3. Candidate strategies

Savings are given for two scene types:

- **Static:** paused, camera still, no input (the fixture).
- **Active:** camera moving or game unpaused, frames genuinely change.

"Per frame" savings mean **main-thread wall ms/frame** (provisional, see §1.4); they convert into higher fps below 120 Hz. "CPU-ms/s" means **process CPU-ms/s** and assumes cadence is held.

### S1 — Cheap measurement probes

- **Mechanism:** three probes, no mutation.
  1. **Frame-evolution census:** run just before `CGLFlushDrawable`, over several seconds of consecutive paused frames, across map modes and zoom levels, with the UI closed and open.
     - The **equality predicate** is an exact hash/CRC of the **full-resolution** back buffer (PBO readback). Only this can authorize lossless skipping; downscaling can hide a small flag, highlight, or pulse.
     - Once frames differ, per-region hashes and downscaled difference maps **localize** the change, and each region is classified as static, periodic, continuous, or input-driven.
  2. **Per-thread CPU accounting:** actual thread CPU-time counters (Mach `task_threads` + `thread_info` user/system time deltas, or an equivalent counter-based tool) to split the ~1,110 CPU-ms/s into main thread vs Metal completion, audio, Galaxy SDK, Rosetta. `sample` cannot do this: it records where each thread was, including blocked threads.
  3. **Fresh stack attribution at Gfx granularity:** a new `sample` on the current fixture, attributing main-thread wall time to `GfxDrawIndexed` / `GfxSetTextures` / `GfxSetShader` call sites. It is a separate experiment from CPU accounting.
- **Evidence:** [`analysis/diagnostic.md`](../analysis/diagnostic.md) states the map is animated; the diagnostic screenshot pair showed ~60% of pixels changed, but those shots were minutes apart and not controlled for camera.
- **Falsifies:** S2 lossless viability (probe 1); "non-render threads are negligible" (probe 2).
- **Effort:** 1–3 days. **Savings:** none directly; it decides between S2 and S2b and validates §1.
- **Building-block value:** high; the frame hash becomes the correctness oracle for S2 and S4.
- **Risk:** low.

### S2 — Idle render-skip (dirty-driven frames)

- **Mechanism:** when the game reports paused (reuse `pause_verified()` from [`benchmark/eu4_idle_pacer.c`](../benchmark/eu4_idle_pacer.c)), no input has arrived, and no dirty signal fired, skip `CInGameIdler::Render` entirely and sleep to hold cadence. The window keeps showing the last presented frame. Any input, tick, UI change, or dirty signal renders immediately. **Note:** the existing pacer only caps at 60 Hz, so it has no headroom at ~55 fps; true render-skipping has never been tested.
- **Variant S2b (lossy, needs your approval):** if S1 shows the paused map is animated, render idle frames at a reduced rate (e.g. 15–30 Hz) instead of skipping them entirely.
- **Evidence:** rendering is 74% of the main thread (§1.2); the GPU does nothing on skipped frames.
- **Falsifies:** S1 frame-identity probe; ABABA with phase screenshots.
- **Effort:** 1–2 weeks (Estimate). Hooking `Render` from the `Idle` call site; making sure nothing is presented from an undefined back buffer; defining dirty signals.
- **Savings (Estimate):**
  - Static, ≥90% of frames skipped: **50–65% process CPU-ms/s, 80–90% GPU power.**
  - Active: 0.
  - S2b at 20 Hz: ~35–50% process CPU-ms/s.
- **Building-block value:** high; the dirty-signal model and render gate are reused by S4 and by any future pass-level caching.
- **Risk:** input latency on wake-up (one frame); missed dirty signals leave a stale screen. A maximum skip interval (e.g. 250–500 ms) is only a safety net in a proven-static scene: if anything animates without producing a dirty signal (shader time, water, flags, trees), showing one frame for 500 ms simply lowers its frame rate. S2 is therefore **conditional** on the S1 census, not an expected win.

### S3 — Redundant cursor-set elision

- **Mechanism:** on every event pump, AppKit's display-cycle observer calls `-[NSCursor set]` via `cursorUpdate:`, which IPCs to WindowServer (`SLSGetCursorScale`). Interpose so it becomes a no-op when the same cursor is already set, or fix the SDL tracking-area / cursor-rect setup that triggers it.
- **Evidence:** 36/477 main-thread samples (7.5%) (§1.2).
- **Falsifies:** a counter on `-[NSCursor set]` calls/s, then ABABA on swaps/s and CPU-ms/frame; check the cursor is still correct over UI elements and the map.
- **Effort:** 1–3 days. **Savings (Estimate):** 2–8% main-thread frame time in all scenes; mostly appears as fps and WindowServer load, since the time is partly blocking IPC.
- **Building-block value:** low (independent of rendering).
- **Risk:** low; cursor appearance regressions are immediately visible.

### S4 — Map-layer cache (offscreen map, live UI)

- **Mechanism:** render `CEU3GraphicalMap::Render` and `RenderPostEffects` into an offscreen color target (or copy the default framebuffer after the map pass). While a dirty key is unchanged, skip the map pass, blit the cached map, and draw the UI live at full rate.
  - **Dirty key:** camera matrices, map mode, hovered/selected province, game date/tick, `UpdateProvinceGraphics` activity, drag selection, plus any time uniform the map shaders use.
- **Why it matters:** players spend much paused time interacting with menus and tooltips. Input keeps arriving, so S2 never engages, but the map doesn't change.
- **Evidence:** the map is 61% of the main thread, the UI 8.8% (§1.2). Static note: `Render` clears the default framebuffer and then draws map and UI in sequence, so a composition path is needed ([`analysis/diagnostic.md`](../analysis/diagnostic.md)).
- **Falsifies:** a pixel comparison of cached vs fresh composition (S1 hash) across scripted UI interaction.
- **Effort:** 4–8 weeks (Estimate). **Savings (Estimate):**
  - Static map with active UI: ~55–60% per frame (~45–55% CPU-ms/s at held cadence).
  - Fully static: subsumed by S2.
  - Active camera: 0.
- **Building-block value:** split into two parts.
  - The **offscreen map seam** (S4a) is very high value regardless of caching: a Metal map renderer can later write into the same offscreen target (IOSurface-backed), composited by the existing GL UI path.
  - The **cache** (S4b) is only worth building if temporal-dependency analysis shows the map is static during UI interaction.
- **Risk:** proving the dirty key is complete (every input that can change map pixels: time uniforms, water, flags, vegetation, province effects) in a closed-source engine with mods; post-effects ordering; memory for a full-resolution target (3456×2234 RGBA ≈ 31 MB, acceptable).

### S5 — Invisible-work culling

- **Hypothesis:** a material fraction of mesh and border draws produce zero visible pixels. Candidates: wrap-world duplicates (`CWrapWorldShadowMap`), objects outside the frustum, sub-pixel distant objects, or some of the **4 border walks per frame** ([census](../analysis/evidence/border-mode-census-live-20261007T074355Z.json)).
- **Mechanism:**
  - **Test:** an occlusion-query census (`GL_SAMPLES_PASSED` per draw, counts only) or a CPU frustum check of per-object bounds, grouped by caller.
  - **Implement:** skip zero-pixel objects at the `RenderBuckets` / border-walk level.
- **Falsifies (cost-based):** proceed only if the census predicts **≥3–5% lower main-thread wall ms/frame**. Each rejectable draw is weighted by its caller's measured cost: mesh draws are ~3.5× border draws per call, so 5% of mesh draws can outweigh 15% of border draws. The visibility test must also be substantially cheaper than the saving.
- **Effort:** test 2–4 days; implementation 1–3 weeks if positive (Estimate).
- **Savings (Estimate):** unknown, 0–30% of map work, in **both** static and active scenes; lossless.
- **Building-block value:** low-medium. Culling reduces **runtime** workload, but not Metal **implementation surface**: culled objects become visible from other camera positions, so their effects, shader variants, and resources must still be supported.
- **Risk:**
  - A zero-sample draw may be hidden behind another object; predicting that before rendering can cost more than the draw. Only trivially rejectable cases (frustum, wrap-world duplicates) are cheap, so prefer a CPU frustum/wrap check over per-draw occlusion queries.
  - Thousands of occlusion queries are intrusive.
  - Shadow passes may legitimately draw off-screen geometry.

### S6 — Border mode-other `glMultiDrawElementsBaseVertex` batching (earlier Phase 1)

- **Mechanism:** as in the earlier strategy: RE `record+0x10`, find homogeneous subruns, collapse each run into one multi-draw call at the loop head.
- **Evidence:** borders ~12% of main-thread time; per-draw cost ~0.6 µs (§1.3); zero eliminations so far.
- **Effort:** 2–6 more weeks (Estimate). **Savings (Estimate):** 3–7% per frame, all scenes, if feasible.
- **Building-block value:** low-medium (RE knowledge informs a Metal border domain; the mutation itself will be superseded by Metal).
- **Risk:** medium; engine continuation-state preservation at the loop head.

### S7 — Map-text batching

- **Evidence:** 381 draws/frame but only 4/477 main-thread samples (<1%).
- **Effort:** 2–4 weeks (Estimate). **Savings (Estimate):** ≤1–3%.
- **Building-block value:** low.

### S8 — First Metal vertical slice: mesh-anchored self-contained domain, via the S4a seam

- **Mechanism:** shadow state + cached pipeline objects + MSL ports of the mesh effects, rendering the map's mesh objects in Metal. Preferred variant: render the **whole map layer** into the S4a offscreen target in Metal, starting with mesh plus whatever must share its depth buffer. This avoids interleaving GL and Metal inside one pass. In the roadmap this is stage **M2**, after the S13/S14 probes (M0a/M0b) and the GL↔Metal interop proof (M1). The domain gets both a minimal compatibility path (B0) and a command-oriented path (C0), with callers emitting commands where S14 shows it pays. Caller redesign means detouring closed-source x86-64 code above Gfx, which is harder to patch and more exposed to mods than the backend; B0 is the fail-closed fallback.
- **Evidence:** mesh is 44% of main-thread wall time, of which ~75% is driver time.
- **Effort:** 3–6 months (Estimate; shader translation likely dominates). **Savings (Estimate):** 25–35% main-thread wall ms/frame, in all scenes; provisional until S13 measures actual GL vs Metal per-draw CPU. The C0 command path adds a further reduction that S14 will bound.
- **Building-block value:** very high; it is the core of backend B.
- **Risk:** high. Depends on the S12 minimum cut and pass/depth graph and on shader feasibility; if terrain and mesh share depth, the slice must include terrain.

### S9 — Full Gfx→Metal: backend (B) as compatibility substrate, command collection (C) per domain

- **Evidence:** driver ≈ 58% of the main thread; engine render work ≈ 16% remains.
- **Effort:** 6–18 months (Estimate). **Savings (Estimate, main-thread wall ms/frame):** 35–45% with B; 50–60% with C on today's caller structure; all scenes.
  - These are working estimates, **not a ceiling**: the ~16% engine-side render work reflects today's OpenGL-oriented callers and may shrink once callers emit compact commands (`Submit(DrawCommand)` instead of setter sequences).
  - The roadmap develops B (as compatibility substrate) and C (as performance architecture) together per domain, rather than B broadly first.
- **Building-block value:** it is the destination.
- **Risk:** very high (scope, shaders, mods, UI, Rosetta-translated Metal calls still pay translation cost).

### S12 — Architecture RE: Gfx minimum cut, shaders, pass/depth graph

- **Mechanism:** the earlier strategy's Phase 0, not deferred:
  - map reachable Gfx/OpenGL entry points and bypasses;
  - inventory shaders and effects;
  - map which map passes share color/depth/stencil targets.
  
  The GL↔Metal interop proof is **not** part of S12. It needs the S4a seam to exist and follows it (roadmap R2b / M1).
- **Evidence:** every Metal option (S8, S9) depends on these answers; the [findings](eu4_macos_rendering_findings.md#11-open-questions) open questions 1–6 are exactly these.
- **Effort:** 2–4 weeks (Estimate). **Savings:** none directly; very large option value.
- **Building-block value:** very high. It decides the Metal slice boundary (mesh alone vs terrain + mesh vs the whole opaque map pass) and whether Metal is feasible at all.
- **Risk:** low-medium (static RE).

### S13 — Early Metal single-draw probe (M0a)

- **Mechanism:**
  1. Trace one representative mesh draw end to end (`CPdxMeshObject::RenderBuckets` → effect selection → `GfxSet*` → `GfxDrawIndexed` → GL state → `glDrawElements`), counting Gfx and GL calls per draw.
  2. Capture its real vertex/index data, textures, constants, and shader, and reproduce it in a standalone x86-64 Metal harness (MSL translation, `MTLRenderPipelineState`, `drawIndexedPrimitives` into an offscreen texture), comparing output with GL.
  3. Microbenchmark N draws with EU4-like buffer/texture/constant/pipeline churn, GL vs Metal, in CPU µs/draw from thread CPU counters.
- **Why early:** it measures what is currently only inferred, namely how much of the per-draw driver work (`setRenderState`, texture/sampler binding, `prepareResourceForGPUAccess`, uniform uploads) actually disappears under Metal. It also forces a semantic model of the draw: what is pipeline state, what varies per material/object/frame. That model is the draft `DrawCommand` for command collection and shows where callers above Gfx could be restructured. The S12 shader-translation sample comes out of this naturally.
- **Effort:** 1–2 weeks, no game integration (Estimate). **Savings:** none directly.
- **Decision value:** it decides *where* the gains must come from, not whether Metal is worth pursuing.
  - Metal per-draw CPU ≤~25% of GL → API replacement carries much of the saving.
  - Metal at 50–70% of GL → most value must come from changing the work presented to Metal (S14), which makes early command-oriented work more important.
  - Reassess Metal only if this **and** S14 are both weak.
- **Building-block value:** very high (harness code is reused by the interop proof and S8).
- **Risk:** low-medium; a single draw may not be representative, so choose a common mesh effect and test realistic churn.

### S14 — Command-stream census (M0b)

- **Mechanism:** capture 1–10 complete `RenderBuckets` sequences (count-only observer) and convert them offline into candidate `DrawCommand`s using the S13 command shape. Measure:
  - distinct pipeline keys;
  - how often only geometry, only constants, or one texture changes between commands;
  - same-pipeline run lengths;
  - ordering constraints that block regrouping;
  - the achievable command count after legal grouping.
  
  Classify per-draw caller work as essential semantics, OpenGL-induced bookkeeping, or submission-order artifact.
- **Why:** upstream optimization opportunities live in the *sequence* of draws, not in one draw. This turns the bottom-up RE into a concrete map of what callers above Gfx could stop doing.
- **Evidence:** existing screening found low geometry recurrence for mesh under GL-level predicates ([`draw-path-decision.md`](../analysis/draw-path-decision.md)). A command-level view (pipeline + resources + constants) asks a different question: what can be grouped or encoded compactly, not which GL calls repeat.
- **Effort:** 1–2 weeks after S13 (Estimate). **Savings:** none directly; it bounds the C0 gain for the first Metal domain and ranks caller-redesign opportunities.
- **Building-block value:** very high (input to S8's command path).
- **Risk:** low (offline analysis of observer captures); caller redesign itself carries the patching risk noted under S8.

### S10 — GL-only static mesh pre-merging

- **Mechanism:** merge static world meshes that share an effect into large vertex buffers, with texture arrays/atlases, to collapse the ~2,774 mesh draws.
- **Evidence against:** repeated-geometry screening found only 6.1% of draws ([`analysis/draw-path-decision.md`](../analysis/draw-path-decision.md)); buffer-signature recurrence 0 ([`results/20261004T152246Z-submission-experiment`](../results/20261004T152246Z-submission-experiment/validation.md)); textures and constants vary per object.
- **Effort:** 2–3 months, with shader changes (Estimate). **Savings (Estimate):** 20–30% per frame if it worked.
- **Building-block value:** low-medium (throwaway once on Metal). **Risk:** high.

### S11 — Outside reference: Windows build under CrossOver

- **Mechanism:** run the Windows DX9 build under CrossOver/Wine with DXVK→MoltenVK (or D3DMetal, if it supports DX9) and compare CPU-ms/frame on the same save.
- **Effort:** 1–2 days. **Savings:** unknown (could be better or worse; Wine adds its own overhead, also under Rosetta).
- **Building-block value:** none for this project; useful only as a reference point for what a Metal path can achieve.
- **Risk:** licensing/installation; not a deliverable.

### Closed or low value (for completeness)

| Candidate | Status | Evidence |
|---|---|---|
| GL texture/vertex setter caches | Closed: ~1% CPU | [`state-cache-validation.md`](../analysis/state-cache-validation.md) |
| `SShaderOpenGL::SetAll` sampler-uniform suppression | Closed: no gain | [`sampler-uniform-validation.md`](../analysis/sampler-uniform-validation.md) |
| Multithreaded GL engine (`kCGLCEMPEngine`) | Closed: not reproducible, +52% power | [`frame-rate.md`](../analysis/frame-rate.md) |
| 60 Hz idle cap (existing pacer) | No headroom at ~55 fps | [`diagnostic.md`](../analysis/diagnostic.md) |
| Corrected uniform dedup | Never run in-game; low value, since state calls are cheap (§1.3) | [`state-cache-validation.md`](../analysis/state-cache-validation.md) |
| Mesh adjacency batching | Closed: recurrence negative | [`draw-path-decision.md`](../analysis/draw-path-decision.md) |
| Border mode-0 `glMultiDrawElements` | Closed: `MODE0_DOMAIN_EMPTY` | [readiness](../analysis/evidence/border-loop-head-roi-readiness-20261005.json) |

---

## 4. Ranking

**Score** = (expected gain × confidence) / effort, then multiplied by a building-block factor (×1.5 for strong foundations, ×1.0 neutral, ×0.7 for work that a later step supersedes). Gains are a blend of static and active scenes, weighted toward static (the fixture and the stated mission). The scores are relative and not precise.

| Rank | Strategy | Effort | Expected gain (Estimate) | Confidence | Foundation | Why this rank |
|---:|---|---|---|---|---|---|
| 1 | **S1** Measurement probes | 2–5 d | decides S2/S4b/S2b; validates model | high | ×1.5 | Near-zero cost; gates the elimination levers |
| 2 | **S12** Architecture RE | 2–4 wk | decides Metal feasibility and slice boundary | high | ×1.5 | Large option value; runs in parallel from day one |
| 3 | **S13** Metal single-draw probe (M0a) | 1–2 wk | measures GL vs Metal per-draw CPU; drafts `DrawCommand` | high | ×1.5 | Turns the Metal case from inference into measurement |
| 3b | **S14** Command-stream census (M0b) | 1–2 wk | bounds command/caller-level gains; ranks redesign targets | high | ×1.5 | Where most of the Metal-era value may come from |
| 4 | **S3** Cursor-set elision | 1–3 d | −2–8% main-thread wall ms/frame, all scenes | medium | ×1.0 | Tiny effort, independent |
| 5 | **S2** Idle render-skip (conditional) | 1–2 wk | static: −50–65% process CPU-ms/s **if** full-res census shows static | low-medium until S1 | ×1.0 | Huge if the map is static; zero if it animates |
| 6 | **S4a** Offscreen map seam, then interop proof (M1) | 3–6 wk | none directly | high | ×1.5 | Metal coexistence boundary; value independent of caching |
| 7 | **S8** Metal mesh-anchored domain via S4a (M2: B0 + C0) | 3–6 mo | −25–35% main-thread wall ms/frame from B0, plus C0 (provisional) | medium-low | ×1.5 | First real per-frame lever for active scenes |
| 8 | **S4b** Map-layer cache (conditional) | 2–6 wk | UI-active static: −45–55% process CPU-ms/s **if** map static | low until S1 | ×1.0 | Dirty-key completeness is hard to prove |
| 9 | **S5** Culling census (opportunistic) | 2–4 d test | 0–20% of map work | unknown | ×1.0 | Independent; cost-weighted gate |
| 10 | **S9** Full B + C, domain by domain | 6–18 mo | −50–60% main-thread wall ms/frame or more (not a ceiling) | low | ×1.5 | Destination; too large to rank higher on ratio |
| 11 | **S6** Border multi-draw | 2–6 wk | −3–7% per frame | medium-low | ×0.7 | Small ceiling; superseded by Metal |
| 12 | **S10** Static mesh pre-merge | 2–3 mo | −20–30% per frame | low | ×0.7 | Evidence against; throwaway under Metal |
| 13 | **S7** Map-text batching | 2–4 wk | ≤1–3% | medium | ×0.7 | Negligible share of CPU |
| — | **S11** CrossOver reference | 1–2 d | reference only | — | — | Not a deliverable |

```mermaid
flowchart LR
    S1[S1_Probes] -->|"full-res static only"| S2[S2_IdleRenderSkip]
    S1 -.->|"needs approval"| S2b[S2b_ReducedIdleRate]
    S1 -->|"temporal analysis"| S4b[S4b_MapLayerCache]
    S1 -.-> S5[S5_CullingCensus_Opportunistic]
    S3[S3_CursorElision]
    S12[S12_ArchitectureRE] --> S13[S13_SingleDrawProbe_M0a]
    S13 -->|"draft DrawCommand"| S14[S14_CommandStreamCensus_M0b]
    S14 -->|"caller redesign targets"| S8
    S12 --> S4a[S4a_OffscreenMapSeam]
    S4a --> M1[M1_GLMetalInteropProof]
    S4a --> S4b
    S13 -->|"harness and command shape"| M1
    M1 --> S8[S8_MetalDomain_MeshAnchored_M2_B0_C0]
    S12 -->|"slice boundary"| S8
    S13 -->|"per-draw cost evidence"| S8
    S8 --> S9[S9_FullMetal_B_to_C]
    S6[S6_BorderMultiDraw] -.->|"RE knowledge only"| S8
```

---

## 5. Recommended revised sequencing

1. Finish the current border RE step as a write-up; **do not** start S6 mutation work.
2. Start **S1**, **S12**, and the **S13** single-draw Metal probe now, in parallel; follow S13 with the **S14** command-stream census.
3. Implement **S3**. Implement **S2** only if the full-resolution S1 census shows static idle frames; S2b stays an explicit fidelity decision.
4. Build the **S4a** seam, then the GL↔Metal interop proof (M1). Add the **S4b** cache only if temporal-dependency analysis supports it.
5. Enter Metal (**S8**, M2) through the S4a target with the highest-cost self-contained map domain, mesh-anchored, with the boundary chosen by the S12 pass/depth graph. Build B0 (compatibility) and C0 (command path, informed by S14) together, and measure their combined effect.
6. Run the **S5** census opportunistically, with a cost-weighted gate.
7. Use the four metrics defined in the roadmap (main-thread wall ms/frame, process CPU-ms/swap, process CPU-ms/s, swaps/s), with per-thread CPU counters for accounting.

Phases, gates, and milestones are in [`eu4_recommended_strategy.md`](eu4_recommended_strategy.md).

---

## 6. Decision for you

If S1 shows that paused-idle frames are **not** full-resolution pixel-identical (animated map shaders, trees, etc.), is a **reduced idle redraw rate** (S2b, e.g. 15–30 Hz after a few seconds without input, full rate on any input) acceptable? [`analysis/profiling.md`](../analysis/profiling.md) records an earlier full-experience requirement against lowering the frame rate. Without approval, S2 is limited to provably identical frames, and S4 carries the static-scene savings instead.
