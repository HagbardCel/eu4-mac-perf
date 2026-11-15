# EU4 Metal backend — submission and draw-overhead optimization

Final report for the P0–P4 sequence, updated after the PR #192 review. Work is
local; no branch was merged. PR #190 is present in `origin/main` (merge
`61e7795`), and every stage below is stacked on that merge plus the review-fix
commit.

## 1. PRs and commits

| PR | Branch | Commit | Scope |
|---|---|---|---|
| P0 | `perf/p0-baseline` | `db69a62` | Draw-stream benchmark and aggregate counters |
| P1 | `perf/p1-direct-index-buffer` | `7f5fc08` | Bind the engine index buffer directly |
| P2 | `perf/p2-command-batches` | `a483f8b` | One shared submitted command buffer per draw run |
| P3 | `perf/p3-render-pass-coalescing` | `8bde44a` | One render pass per compatible draw run |
| P4 | `perf/p4-cpu-hot-path` | `1ac02fa`, `37e9284` | Layout/depth/uniform caches; ordering remediations |
| Review | `perf/p4-cpu-hot-path` | `614a0da` | Geometry-thread, scissor, multi-RT conversion, GPU timing, cache hygiene |

The review commit addresses all four automated code-review findings plus the
additional geometry-publication thread finding. The staged commits preserve the
implementation history; this integrated PR is not split administratively.

## 2. Files and architecture changes

The `_Gfx*` entry patching, engine-state reads and shader-translation
architecture are unchanged. No scene graph, command IR, render graph, allocator
framework or second renderer was introduced.

- `benchmark/eu4_gfx_perf.{h,mm}` — opt-in aggregate counters; command-buffer
  GPU timing is registered once at the centralized submission boundary, is
  frame-epoch attributed and completion-handler safe.
- `benchmark/eu4_gfx_stream_bench.py` — deterministic synthetic streams,
  compact summaries by default (`--records` for raw samples), and a
  `--no-debug-layer` performance mode.
- `benchmark/eu4_gfx_buffers.{h,mm}` — borrowed index-buffer accessors resolved
  through the existing registry, plus the graphics-thread-bound predicate.
- `benchmark/eu4_gfx_generic_draw.mm` — direct indexed bind and prefix
  validation; shared active render encoder; immutable layout cache; depth-state
  cache; stack uniform packing.
- `benchmark/eu4_gfx_draw_route.mm` — acquires the active batch instead of
  creating/committing a command buffer per draw.
- `benchmark/eu4_gfx_init.{h,mm}` — minimal per-backend batch state, flush
  barriers, texture hooks, clear/geometry ordering policy, snapshot-only
  geometry publication, and batch thread ownership checks.
- `benchmark/eu4_gfx_simple_draw.mm`, `eu4_gfx_sprite_draw.mm` — shared batch
  encoder, explicit full-target scissor, thread-local refusal reason.
- `benchmark/eu4_gfx_texture.mm` — sampled-render-target preparation identity
  separated from per-conversion completion identity; conversion reuse and
  abandonment recovery.
- Tests: `tests/gfx_live_draw_host.mm`, `tests/test_gfx_live_draw.py`,
  `tests/test_gfx_stream_bench.py`; `test_gfx_shadow_worker.py` expectation
  aligned with the M6 `texture_lod=1` capability contract.
- Evidence: `analysis/perf/*.json`, `analysis/m6/review-live-acceptance.json`.

## 3. Review findings and fixes

1. **Cocoa geometry publication touched Metal batch state.** Both publication
   entry points are now snapshot-only under the existing mutex. Batch mutations
   reject non-graphics-thread callers (`eu4_gfx_buffers_thread_bound`), and the
   renderer observes the new epoch at clear/present/set-target and retires or
   rejects old-generation work before replacement. `--geometry-thread` publishes
   transition and physical geometry from another thread while a pass is open and
   verifies no encoder end, no submission, epoch observation, stale-present
   rejection, same-size re-observation and no deadlock.
2. **Scissor leaked into Simple/Sprite.** Both legacy encoders now explicitly set
   the full-target scissor in standalone and shared modes. `--scissor-handoff`
   runs generic-partial → full scissor → Simple → Sprite in one pass; pixels
   outside the old partial rectangle prove neither legacy draw was clipped.
3. **Multiple conversions in an external command shared only one token.** The
   code now separates a preparation identity (authorizes every shadow encoded for
   that command) from a per-conversion identity (supersedes stale completion
   handlers). `--sampled-multi` covers two render targets in one external
   command, source update, superseded completion, abandonment and repeated
   active-batch sampling.
4. **GPU timing was recorded once per draw.** Timing is now registered once per
   committed command buffer at `perf_commit`/`batch_commit`, runs under a lock,
   and is attributed to a frame epoch. `--gpu-timing` verifies exactly three
   records for both 1 and 1000 draws (clear + draw batch + present), rejects
   late records across a reset, and confirms GPU duration is below wall time.
5. **Unbounded descriptor-signature cache.** The map and signature construction
   were removed; it was not part of the pipeline key. The bounded layout cache
   and `descriptor.hash` pipeline key remain.

## 4. Performance measurements

Synthetic homogeneous streams, 1000 draws per frame, 30 measured frames after 3
warm-up frames, same machine/configuration, **without the Metal validation
layer**. Baseline is commit `db69a62` rebuilt in a temporary worktree; each stage
is a temporary worktree at its commit. GPU timing is reported only for the
reviewed head, whose accounting is corrected; the old per-draw accounting is not
a valid baseline.

| Stage | Simple CPU | Indexed CPU | Mixed CPU | Simple wall | Indexed wall | Mixed wall |
|---|---:|---:|---:|---:|---:|---:|
| P0 | 7.83 ms | 17.63 ms | 17.36 ms | 21.93 ms | 30.85 ms | 30.68 ms |
| P1 | 7.86 ms | 16.16 ms | 15.41 ms | 21.95 ms | 29.63 ms | 28.54 ms |
| P2 | 5.74 ms | 12.52 ms | 12.53 ms | 23.63 ms | 30.38 ms | 30.21 ms |
| P3 | 2.11 ms | 8.75 ms | 8.88 ms | 15.38 ms | 20.04 ms | 20.53 ms |
| P4 | 2.11 ms | 4.21 ms | 4.27 ms | 16.10 ms | 15.53 ms | 15.46 ms |
| **Final** | **2.00 ms** | **3.63 ms** | **3.69 ms** | **14.27 ms** | **14.37 ms** | **14.41 ms** |

P0 → final: CPU encoding −74.4% / −79.4% / −78.7% and wall time −34.9% /
−53.4% / −53.0% (simple / indexed / mixed). Per-draw CPU encoding is now
~2.0–3.7 µs.

Corrected GPU time per 1000-draw frame (sum over exactly three committed
command buffers): simple 1.20 ms, indexed 0.95 ms, mixed 0.84 ms. Every measured
frame reported exactly three timing records and zero pending records. Wall time
(~14 ms) is dominated by presentation and the two explicit completion waits, not
by CPU encoding or draw-stream GPU work.

These are synthetic backend-overhead measurements. **They are not gameplay or
FPS claims.**

## 5. Structural counter reductions (1000-draw frame)

| Metric | P0 baseline | Final |
|---|---:|---:|
| Draws encoded | 1000 | 1000 |
| Command buffers | 1002 | 3 |
| Draw-stream command buffers | 1000 | 1 |
| Render encoders | 1000 | 1 |
| Temporary indexed Metal buffers | 1000 | 0 |
| Temporary index bytes | 6000 | 0 |
| GPU timing records | invalid (per draw) | 3 |
| Pipeline misses after warm-up | 0 | 0 |

## 6. GPU validation and correctness

- `MTL_DEBUG_LAYER=1` / `MTL_DEBUG_LAYER_ERROR_MODE=assert` for every validation
  run.
- **113 native graphics tests pass** after the review fixes.
- New regressions: `--geometry-thread`, `--scissor-handoff`, `--sampled-multi`,
  `--gpu-timing`, in addition to `--indexed-resource`, `--batch-sequences`,
  `--blend-order`, `--sampled-batch` and `--layout-stability`.
- M6 captured-frame replay: **326/326 encoded, 0 failed, command completed**;
  differential **0 differing pixels of 1,930,176**, max channel error 0.
- One pre-existing failure remains: `tests.test_gfx_init` →
  `test_default_depth_stencil_contract` ("capability span was not otherwise
  zero"). It fails identically on `origin/main` once that host links, and is
  unrelated.

## 7. Live EU4 title-scene qualification

Three fresh, independent, settled launches on the reviewed head, using the
existing `automation.live_gfx probe` and launch ledger, the accepted
`shader-cache-fixed2` source set, `0x103b3c8` occurrence 300, and the 280 s
uptime guard. All three pass `automation.m6_live_accept --require-resolve-trace`:

| Run | UI correlations | Map mean | Map color similarity | Text upright/flipped |
|---|---|---:|---:|---|
| `review-live/run-2` | 0.882 / 0.946 / 0.835 | 72.27 | 0.965 | 0.905/0.486, 0.937/0.513 |
| `review-live2/run-1` | 0.882 / 0.946 / 0.836 | 72.24 | 0.967 | same |
| `review-live2/run-2` | 0.882 / 0.946 / 0.835 | 72.26 | 0.965 | same |

All checks true: draw reached/encoded, GPU completed, pixels captured,
presented, non-blank frame, no refused draws, no failed resolves, resolve trace
present, upright text, UI and map regions. `clean_starts=3`, distinct runs and
identities. Evidence: `analysis/m6/review-live-acceptance.json`.

One earlier launch (`review-live/run-1`) is excluded: it used the wrong cached
shader set (`shader-cache-lod`), producing a beige map. With the accepted
`shader-cache-fixed2` set, the three runs above reproduce the accepted M6 scene.

## 8. Remaining bottlenecks, ranked by evidence

1. **Presentation/wait wall time.** Wall ~14 ms versus CPU+GPU ~3 ms in the
   synthetic stream. The two explicit completion waits and present blit dominate.
   No async-present change was attempted in this PR.
2. **Generic indexed CPU (~3.6 µs/draw).** Remaining work includes shader-stage
   lookups under a lock, per-attribute binds, pipeline-key string construction,
   texture binding scans and mandatory index-prefix validation.
3. **Simple CPU (~2.0 µs/draw) and mixed (~3.7 µs/draw).** Pipeline lookup and
   state-key construction remain per draw; P4 does not cover the legacy encoder.
4. **Shadow-heavy conversion cost.** Correct and reused per command, but each
   conversion still closes the shared pass. Not present in the synthetic stream.
5. **GPU duration.** ~0.8–1.2 ms per 1000-draw frame in this synthetic stream;
   real-frame GPU time was not profiled with Metal System Trace.

## 9. Readiness

The backend now has the intended execution model: one submitted command buffer
and one render encoder for a long compatible draw run, zero transient indexed
buffers, corrected per-command GPU accounting, thread-safe geometry publication,
and three clean live title-scene starts. It is ready for **measured gameplay
trials**, not for a gameplay FPS/power claim.

Still required before declaring an overall performance result: paused-Venice
Metal versus original OpenGL CPU-ms/frame, frame rate and power under matched
settings, and Metal System Trace confirmation on a real gameplay frame.

### Facts vs estimates vs hypotheses

- **Measured:** structural counters, CPU/wall/GPU synthetic durations, 113 native
  tests, M6 replay pixel identity, three live title-scene acceptances.
- **Estimated:** the same submission/pass reductions reduce real-frame
  submission overhead; magnitude is not established.
- **Untested:** gameplay FPS/power versus OpenGL, async present gains, index-range
  caching, and behavior under shadow-heavy frames outside M6 replay.
