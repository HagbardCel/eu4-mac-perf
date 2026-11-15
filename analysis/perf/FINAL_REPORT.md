# EU4 Metal backend — submission and draw-overhead optimization

Final report for the P0–P4 sequence. All work is local; no branch was merged.
PR #190 is present in `origin/main` (merge `61e7795`), and every PR below is
stacked on that merge.

## 1. PRs and commits

| PR | Branch | Commit | Scope |
|---|---|---|---|
| P0 | `perf/p0-baseline` | `db69a62` | Draw-stream benchmark and aggregate counters |
| P1 | `perf/p1-direct-index-buffer` | `7f5fc08` | Bind the engine index buffer directly |
| P2 | `perf/p2-command-batches` | `a483f8b` | One shared submitted command buffer per draw run |
| P3 | `perf/p3-render-pass-coalescing` | `8bde44a` | One render pass per compatible draw run |
| P4 | `perf/p4-cpu-hot-path` | `1ac02fa`, `37e9284` | Layout/depth/uniform caches; ordering remediations |

The branches are stacked (P4 contains P0–P3). Merge recommendation is per PR in
the section summaries below; no automatic merge was performed.

## 2. Files and architecture changes

The `_Gfx*` entry patching, engine-state reads and shader-translation
architecture are unchanged. No scene graph, command IR, render graph, allocator
framework or second renderer was introduced.

- `benchmark/eu4_gfx_perf.{h,mm}` — opt-in aggregate counters (draws, command
  buffers, encoders, temporary index buffers, pipeline hits/misses, sampling
  conversions, completion waits, batch flushes, CPU encode and GPU durations).
- `benchmark/eu4_gfx_stream_bench.py` — builds the native live-draw host and
  drives deterministic synthetic streams; emits machine-readable per-frame JSON.
- `benchmark/eu4_gfx_buffers.{h,mm}` — adds borrowed
  `eu4_gfx_index_buffer_for_header` / `eu4_gfx_current_index_buffer` accessors
  resolved through the existing registry.
- `benchmark/eu4_gfx_generic_draw.mm` — direct indexed bind and validation; shared
  active render encoder; immutable vertex-layout cache; depth-state and pipeline
  signature caches; stack uniform packing.
- `benchmark/eu4_gfx_draw_route.mm` — acquires the active batch instead of
  creating/committing a command buffer per draw.
- `benchmark/eu4_gfx_init.{h,mm}` — minimal per-backend active batch
  (command buffer, generation, encoded-op count, serial, active render encoder)
  plus flush barriers and the clear/geometry ordering policy.
- `benchmark/eu4_gfx_simple_draw.mm`, `eu4_gfx_sprite_draw.mm` — encode into the
  caller-owned batch encoder when the route owns the command buffer.
- `benchmark/eu4_gfx_texture.mm` — sampled-render-target conversion reuse per
  command serial and abandonment recovery.
- `benchmark/eu4_gfx_logical_capture.mm` — flushes the batch before its own
  diagnostic submission.
- Tests: `tests/gfx_live_draw_host.mm`, `tests/test_gfx_live_draw.py`,
  `tests/test_gfx_stream_bench.py`; `test_gfx_shadow_worker.py` expectation
  aligned with the established `texture_lod=1` contract.
- Evidence: `analysis/perf/p0-baseline.json`, `p1.json`, `p2.json`, `p3.json`,
  `p4.json`, `final.json`.

## 3. Baseline and optimized measurements

Synthetic homogeneous streams, 1000 draws per measured frame, 30 measured
frames after 3 warm-up frames, same device, geometry and configuration.
`cpu` is CPU draw encoding; `wall` is clear → draws → present including waits;
`gpu` is the sum of command-buffer GPU start/end durations. Medians; p95 in the
JSON.

| Workload | P0 CPU | Final CPU | Δ | P0 wall | Final wall | Δ |
|---|---:|---:|---:|---:|---:|---:|
| Simple, 1000 draws | 33.01 ms | 9.32 ms | −71.8% | 49.85 ms | 23.02 ms | −53.8% |
| Indexed, 1000 draws | 44.41 ms | 12.70 ms | −71.4% | 59.72 ms | 23.98 ms | −59.8% |
| Mixed state, 1000 draws | 44.27 ms | 10.29 ms | −76.8% | 59.46 ms | 21.57 ms | −63.7% |

Per-draw CPU encoding fell from ~33–44 µs to ~9–13 µs. The Simple path is
unchanged by P4 (separate encoder); its gain comes from P2/P3.

GPU execution duration of the stream fell from 13.8/13.6/19.6 ms to
1.9/0.9/0.8 ms (simple/indexed/mixed). This is reported GPU time for 1000 tiny
command buffers versus one shared buffer; it is a structural measurement, not
an FPS claim. Metal System Trace was not run.

Run-to-run variance was observable mainly on the Simple path (final median
9.32 ms, p95 12.74 ms); indexed and mixed medians were stable within ~1 ms.
These are synthetic backend-overhead numbers. **They are not representative
gameplay and are not used to claim an EU4 FPS or power improvement.**

## 4. Structural counter reductions (1000-draw frame)

| Metric | P0 baseline | Final | Notes |
|---|---:|---:|---|
| Draws encoded | 1000 | 1000 | No drops |
| Command buffers created | 1002 | 3 | clear + one draw batch + present |
| Draw-stream command buffers | 1000 | 1 | Capacity cap 4096 encoded ops |
| Render encoders created | 1000 | 1 | Compatible draws share the pass |
| Temporary indexed Metal buffers | 1000 | 0 | Engine buffer bound directly |
| Temporary index bytes | 6000 | 0 | 3×uint16 per draw |
| Explicit completion waits | 2 | 2 | Frame present path unchanged |
| Sampling conversions (synthetic) | 0 | 0 | Not exercised by this stream |
| Pipeline misses (measured frames) | 0 | 0 | Warmed before sampling |

## 5. GPU validation and correctness

- `MTL_DEBUG_LAYER=1` and `MTL_DEBUG_LAYER_ERROR_MODE=assert` for every GPU
  host run.
- Native graphics suite: **109 tests pass**, covering the live route, Simple,
  Sprite, texture, buffers, HiDPI lifecycle, shadow worker/render, startup
  diff, M5 and M6 trace/compare unit tests.
- New regressions:
  - `--indexed-resource`: resource reuse, update replacement, header recycle,
    negative base vertex, out-of-range indices, zero temporary allocations.
  - `--batch-sequences`: refusal after successful encodes, target switch,
    in-place vertex update, 1000-draw single submission, shutdown with
    unsubmitted work.
  - `--blend-order`: two distinct blend states in one pass with exact
    accumulated pixels; clear between draws splits the pass and discards the
    first draw.
  - `--sampled-batch`: one conversion serves all draws in a command buffer;
    abandonment forces reconversion.
  - `--layout-stability`: a changed dynamic slot offset creates no pipeline miss
    and no new encoder.
- One pre-existing failure remains: `tests.test_gfx_init` →
  `test_default_depth_stencil_contract` ("capability span was not otherwise
  zero"). It fails identically on `origin/main` once that host can link, and is
  unrelated to submission/draw changes.

## 6. M6 replay and live qualification

- **M6 captured-frame replay (final code):** 326/326 events encoded, 0 failed,
  command status completed; exported 1728×1117 BGRA8 frame. Differential against
  the canonical reference: **0 differing pixels of 1,930,176**, max channel
  error 0.
- **M6 live title scene:** not re-launched in this session. The three-start live
  acceptance is the established M6 evidence in `docs/status.md`; it was not
  invalidated by replay, but a fresh three-start live qualification is required
  before a production merge. No live FPS/power claim is made.

## 7. Remaining bottlenecks, ranked by evidence

1. **Generic indexed CPU encoding (~12.7 µs/draw).** Remaining work includes
   shader-stage dictionary lookups under a mutex, per-attribute
   `setVertexBuffer` calls, pipeline-key string construction, texture binding
   scans and the mandatory index-prefix validation. P4 removed ~4 µs/draw with
   layout/depth/uniform caches; the rest was not optimized further.
2. **Simple path CPU encoding (~9.3 µs/draw).** The Simple encoder still does a
   shared-pipeline lookup (locking), a 64-byte matrix copy, complete draw-state
   metadata fill and per-draw texture/sampler binding. P4 does not touch it.
3. **Mixed-state pipeline keys (~10.3 µs/draw).** Blend/mask changes allocate a
   new pipeline key string per draw even though the PSOs are warm. Numeric key
   construction is an untested improvement.
4. **Present/frame synchronization.** Wall time (21–24 ms) is roughly twice
   CPU+GPU in the synthetic stream; two completion waits and the present blit
   dominate the remainder. Async present completion and fewer present-side waits
   were not attempted.
5. **Sampled-render-target conversions.** Correct and reused per command, but a
   new conversion still closes the shared pass. Not present in the synthetic
   stream; impact on a shadow-heavy scene is unmeasured.
6. **Index validation scan.** O(index count) per indexed draw remains. A
   validated-range cache keyed by buffer version and requested prefix was
   explicitly not added without measurements.

## 8. Readiness for broader gameplay migration

The backend now has the expected structural execution model: one submitted
command buffer and one render encoder for a long compatible draw run, zero
transient indexed-draw buffers, and measured CPU reductions of ~71–77% on the
synthetic streams. It is ready for **measured gameplay trials**, not for an
overall performance claim.

Required before declaring a gameplay performance result:

- Run the paused-Venice reference workload through Metal and original OpenGL
  with matched resolution, settings, VSync, scene and visible content.
- Re-run three fresh live title-scene starts against the finalized branch.
- Compare process CPU-ms/frame, frame rate and power; treat any regression over
  ~5% as requiring investigation.
- Use Metal System Trace to confirm the synthetic GPU-duration and wall-clock
  observations under a real frame.

### Facts vs estimates vs hypotheses

- **Measured:** structural counters, CPU/wall/GPU stream durations, 109 passing
  tests, M6 replay pixel identity.
- **Estimated:** the synthetic structural gains will translate to fewer
  submissions/passes in gameplay; the magnitude of gameplay CPU/FPS gain is not
  established.
- **Untested:** gameplay FPS/power, live three-start qualification on these
  branches, async present gains, index-range caching, and behavior under
  multi-target/shadow-heavy frames beyond the M6 replay.
