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

## Reproducing the historical P0 baseline and the current optimized benchmark

`benchmark/eu4_gfx_stream_bench.py` builds the native live-draw host
(`tests/gfx_live_draw_host.mm`) with the real backend modules and drives a
deterministic synthetic draw stream through the same `_Gfx*` replacement route
the game uses.

The P0 baseline is **historical**: it requires commit `db69a62`, the P0 commit,
not current `main`. Running the benchmark on current `main` reproduces the
optimized result, not the baseline. Both commands use Python 3.14 and
`--no-debug-layer` (the measured configuration, without Metal validation) and
write only to ignored `.local/`; the tracked `p0-baseline.json`, `final.json`
and `stages.json` are evidence and are never overwritten.

### Historical P0 baseline (`db69a62`)

Use a detached worktree so the checkout you are reviewing is untouched:

```sh
# from the repository root; the P0 commit must be present in history
MAIN=$PWD
mkdir -p .local/perf
git worktree add --detach .local/perf/p0-worktree db69a62
(
  cd .local/perf/p0-worktree
  python3.14 benchmark/eu4_gfx_stream_bench.py \
    --draws 1000 --frames 30 --kinds simple,indexed,mixed \
    --no-debug-layer --summary \
    --output "$MAIN/.local/perf/p0-baseline-repro.json"
)
git worktree remove .local/perf/p0-worktree
```

Compare that output with the tracked [p0-baseline.json](p0-baseline.json).
`MAIN` deliberately points back at the checkout that created the worktree.

### Current optimized benchmark (`main`, including PR #192)

Run from the repository root at the reviewed merge or later:

```sh
python3.14 benchmark/eu4_gfx_stream_bench.py \
  --draws 1000 --frames 30 --kinds simple,indexed,mixed \
  --no-debug-layer --summary \
  --output .local/perf/current-repro.json
```

Compare that output with [final.json](final.json) and the per-stage
[stages.json](stages.json). `--records` retains every per-frame sample; the
tracked baseline and final files are compact summaries.

By default the runner sets `MTL_DEBUG_LAYER=1` and
`MTL_DEBUG_LAYER_ERROR_MODE=assert` for validation. `--no-debug-layer` disables
them for both performance measurements above. Three warm-up frames precede the
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
