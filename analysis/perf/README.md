# Metal backend performance measurements

Final qualification and PR/commit list: [FINAL_REPORT.md](FINAL_REPORT.md).

## Final result (1000-draw synthetic frame, no Metal validation layer)

| Metric | P0 baseline | Optimized |
|---|---:|---:|
| Draw calls | 1000 | 1000 |
| Command buffers | 1002 | 3 |
| Render encoders | 1000 | 1 |
| Temporary index buffers | 1000 | 0 |
| CPU encoding, simple / indexed / mixed | 7.83 / 17.63 / 17.36 ms | 2.02 / 3.31 / 3.37 ms |
| Wall time, simple / indexed / mixed | 21.93 / 30.85 / 30.68 ms | 13.81 / 13.90 / 13.79 ms |
| GPU time (3 command buffers/frame) | unavailable | 1.36 / 0.95 / 0.85 ms |

Baseline and every stage were rebuilt in temporary worktrees and measured
without `MTL_DEBUG_LAYER`; see [FINAL_REPORT.md](FINAL_REPORT.md). GPU timing is
only reported for the reviewed head because the earlier per-draw accounting was
invalid. These are synthetic backend-overhead measurements, not gameplay or FPS
claims.

Saved evidence: [p0-baseline.json](p0-baseline.json),
[final.json](final.json) and the combined per-stage
[stages.json](stages.json). Raw per-frame samples are reproducible with
`--records`.

## PR P0 baseline

`benchmark/eu4_gfx_stream_bench.py` builds the native live-draw host
(`tests/gfx_live_draw_host.mm`) with the real backend modules and drives a
deterministic synthetic draw stream through the same `_Gfx*` replacement route
the game uses.

Reproduce (from the repository root). This writes to gitignored `.local/`, so
the tracked baseline is not overwritten:

```sh
python3.14 benchmark/eu4_gfx_stream_bench.py \
  --draws 1000 --frames 30 --kinds simple,indexed,mixed \
  --no-debug-layer --summary \
  --output .local/perf/p0-baseline-repro.json
```

`--no-debug-layer` matches the measured configuration; omit it only for a
validation run. Add `--records` to retain every per-frame sample (the tracked
`p0-baseline.json` is the compact summary).

By default the runner sets `MTL_DEBUG_LAYER=1` and
`MTL_DEBUG_LAYER_ERROR_MODE=assert` for validation. `--no-debug-layer` disables
them for the performance measurement above. Three warm-up frames precede the
measured frames, and the instrumented build runs the same workload, so pipeline
compilation and Metal shader compilation are warm before sampling.

The saved baseline is [p0-baseline.json](p0-baseline.json).

### Workloads

| Kind | Stream |
|---|---|
| `simple` | 1000 non-indexed `_GfxDraw` calls through the admitted Simple encoder; the bound texture alternates each draw. |
| `indexed` | 1000 `_GfxDrawIndexed` calls through the generic encoder using one unchanged engine index buffer; signed base vertex alternates 0/4 (red/green fetched vertices). |
| `mixed` | 1000 generic indexed draws that additionally alternate blend enable/disable, colour-write mask and cull mode inside the stream. |

Each measured frame is `clear → N draws → present`; present waits for GPU
completion, so per-frame counters and GPU durations are final when sampled.

### Metrics reported

Per frame, from `benchmark/eu4_gfx_perf.mm`:

- draws encoded (generic/simple/sprite)
- Metal command buffers created/committed
- render encoders created/reused
- indexed-draw temporary Metal buffer allocations and bytes
- pipeline cache hits/misses
- render-target sampling conversions
- explicit GPU completion waits
- CPU draw-encoding nanoseconds
- GPU execution nanoseconds (sum of command-buffer `GPUStartTime`/`GPUEndTime`)
- wall-clock frame nanoseconds

Aggregates report median and p95 over the measured frames. The JSON keeps every
per-frame sample.

### What the numbers do and do not mean

- These are **backend overhead measurements on synthetic geometry**. They are
  not representative gameplay and must not be used to claim an EU4 FPS
  improvement.
- `cpu_encode_ns` covers the draw route from command-buffer acquisition through
  encoder completion, excluding `commit` and GPU execution.
- `wall_ns` covers clear, draws, present and the completion wait, so it includes
  GPU and presentation work.
- Instrumentation is opt-in. With `eu4_gfx_perf_set_enabled(0)` every hook is a
  predictable branch; the engine path leaves it disabled.
