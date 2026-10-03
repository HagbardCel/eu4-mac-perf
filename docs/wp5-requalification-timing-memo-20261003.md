# WP5 post-merge requalification — timing decomposition memo

**Archive:** `analysis/evidence/frame-model-offline-7a86153-20261003T064756.277190Z-37f57796.json`  
**Git:** `7a86153` (merge commit for PR #8)  
**Baseline for comparison:** WP1 v3 `9a063f0` (`20261002T220742.210844Z-129e5bf5`)  
**Purpose:** Falsify or support “REFERENCE fast path → Tier-1 bare→REFERENCE passes” without a new benchmark run.

## Harness timing boundary (unchanged)

From `tests/frame_model_workload_harness.c`:

1. `preparation_*` — one warm-up `frame(workload)` **before** `glFinish()` (not in Tier-1 `elapsed_ns`).
2. `arm()` — measurement window armed.
3. **`elapsed_ns` / `cpu_ns`** — four `frame(workload)` calls only (`CLOCK_UPTIME_RAW` / `CLOCK_THREAD_CPUTIME_ID`).
4. `glReadPixels()` — validation **after** the timed window.

Tier-1 `elapsed_ns` is a **submission-window wall clock**, not GPU completion. The window does not include an explicit completion barrier.

## CPU vs wall (medians, seven trials)

Thread CPU is ~**2.4%** of `elapsed_ns` in every stage/recipe (WP5 and WP1 v3). Because CPU is a small fraction of wall, **`non_cpu_ns = elapsed_ns - cpu_ns` carries most absolute nanoseconds by construction**; that does **not** by itself identify GPU queueing (it is everything not charged to thread CPU time).

### mesh

| Stage | elapsed (ms) | cpu (µs) | non_cpu (ms) |
|-------|-------------|----------|--------------|
| bare | 11.62 | 279 | 11.35 |
| reference | 13.46 | 323 | 13.13 |
| counters | 13.97 | 335 | 13.64 |

Tier-1 gates (counters vs REFERENCE): elapsed **+4.2%**, cpu **+4.3%** — same direction, similar magnitude. No inversion.

### borders

| Stage | elapsed (ms) | cpu (µs) | non_cpu (ms) |
|-------|-------------|----------|--------------|
| bare | 3.83 | 92 | 3.74 |
| reference | 5.09 | **122** | 4.97 |
| counters | 4.32 | **104** | 4.21 |

Tier-1 gates: counters vs REFERENCE elapsed **−15.4%**, cpu **−15.4%** (7/7 trials inverted on elapsed).  
REFERENCE has **higher** thread CPU than counters (~122 µs vs ~104 µs), not lower.

### text_ui

| Stage | elapsed (ms) | cpu (µs) | non_cpu (ms) |
|-------|-------------|----------|--------------|
| bare | 1.57 | 38 | 1.53 |
| reference | 2.78 | **66.7** | 2.71 |
| counters | 1.88 | **45.0** | 1.83 |

Tier-1 gates: counters vs REFERENCE elapsed **−31.6%**, cpu **−31.6%** (6/7 trials on elapsed; trial 1 reverse-order exception +5.7%).  
Wall and CPU ratios track together; REFERENCE CPU is **higher** than counters.

## Interpretation

1. **REFERENCE↔counters inversions (borders, text_ui) appear in both submission-window wall time and thread CPU time with very similar relative magnitudes.** The capture does **not** isolate the effect to “non-CPU waiting” or “less profiler CPU on counters.”
2. **Async GL submission / queue pacing** remains a **hypothesis** compatible with a submission-only window (faster submit might change wall without matching completion cost), but **this archive does not establish it** — especially while CPU co-moves with wall.
3. **WP5 vs WP1 v3 — absolute REFERENCE CPU (medians):**

   | Recipe | WP1 REFERENCE cpu | WP5 REFERENCE cpu | Δ |
   |--------|------------------:|------------------:|--:|
   | mesh | ~335 µs | ~323 µs | −4% |
   | borders | ~110 µs | ~122 µs | +11% |
   | text_ui | ~46 µs | ~67 µs | +45% |

   Counters CPU fell for borders/text_ui between runs while REFERENCE CPU rose. That warrants investigation of **REFERENCE-specific execution paths**, not only timing-boundary effects.
4. The archive **falsifies** the simple hypothesis “drop REFERENCE shadow prep → bare→REFERENCE Tier-1 passes” on the current metric, without proving whether the dominant miss is methodology or residual REFERENCE cost.

## Order effects

Borders inversion is stable across forward/reverse order (7/7). text_ui trial 1 (reverse) is an outlier; note in WP5b diagnostics.

## Next step (WP5b — proposed)

Extend the harness to time submission and a post-window `glFinish()` drain separately, recording **wall and thread CPU** for each:

- `submission_elapsed_ns` / `submission_cpu_ns`
- `post_window_drain_elapsed_ns` / `post_window_drain_cpu_ns`
- `submission_plus_drain_elapsed_ns` / `submission_plus_drain_cpu_ns`

Do **not** change Tier-1 gates until that evidence exists. Run bare / reference / counters only, three training recipes, seven paired trials.

## Provenance

Registered under `requalification_archives` in `analysis/profiler-overhead-diagnosis-manifest.json`, not authoritative WP1 `diagnostic_archives`. Use `summarize_wp1_diagnosis.py --find` (role `REQUALIFICATION`); do not register via `--register-latest`.
