# WP5 post-merge requalification — timing decomposition memo

**Archive:** `analysis/evidence/frame-model-offline-7a86153-20261003T064756.277190Z-37f57796.json`  
**Git:** `7a86153` (merge commit for PR #8)  
**Baseline for comparison:** WP1 v3 `9a063f0` (`20261002T220742.210844Z-129e5bf5`)  
**Purpose:** Falsify or support “REFERENCE fast path → Tier-1 bare→REFERENCE passes” without a new benchmark run.

## Harness timing boundary (unchanged)

From `tests/frame_model_workload_harness.c`:

1. `preparation_*` — one warm-up `frame(workload)` **before** `glFinish()` (not in Tier-1 `elapsed_ns`).
2. `arm()` — measurement window armed.
3. **`elapsed_ns` / `cpu_ns`** — four `frame(workload)` calls only.
4. `glReadPixels()` — validation **after** the timed window.

Tier-1 `elapsed_ns` is therefore a **submission-window wall clock**, not GPU completion. `non_cpu_ns = elapsed_ns - cpu_ns` approximates driver/GPU queueing and other non-thread-CPU wall time during that window.

## CPU vs non-CPU (medians, all seven trials)

CPU is ~**2.4%** of `elapsed_ns` in every stage/recipe (WP5 and WP1 v3). Differences between stages live almost entirely in **non_cpu_ns**.

### mesh

| Stage | elapsed (ms) | cpu (µs) | non_cpu (ms) |
|-------|-------------|----------|--------------|
| bare | 11.62 | 279 | 11.35 |
| reference | 13.46 | 323 | 13.13 |
| counters | 13.97 | 335 | 13.64 |

REFERENCE→counters: counters **slower** in 6/7 trials (+3–15% elapsed). No inversion.

### borders

| Stage | elapsed (ms) | cpu (µs) | non_cpu (ms) |
|-------|-------------|----------|--------------|
| bare | 3.83 | 92 | 3.74 |
| reference | 5.09 | 122 | **4.97** |
| counters | 4.32 | 104 | **4.21** |

REFERENCE→counters: counters **faster** in **7/7** trials (−11% to −19% elapsed).  
Pattern: REFERENCE **lower cpu** (+30 µs vs counters) but **much higher non_cpu** (+0.76 ms vs counters).

### text_ui

| Stage | elapsed (ms) | cpu (µs) | non_cpu (ms) |
|-------|-------------|----------|--------------|
| bare | 1.57 | 38 | 1.53 |
| reference | 2.78 | 67 | **2.71** |
| counters | 1.88 | 45 | **1.83** |

REFERENCE→counters: counters faster in **6/7** trials (−28% to −35%); trial 1 (reverse order) is the only exception (+5.7%).  
Same pattern: REFERENCE higher **non_cpu** (+0.88 ms median vs counters), not higher CPU.

## Interpretation

1. **Counters faster than REFERENCE is not explained by “less profiler CPU work”** on borders/text_ui. Counters has **more** CPU; it has **less** non-CPU wall during the timed window.
2. That matches **async GL submission + queue back-pressure**: a faster REFERENCE submit path can increase wall time inside the four-frame window; counters pacing can shorten it.
3. **WP5 did not clearly make REFERENCE CPU worse**; it may have changed **submission dynamics** relative to bare and counters.
4. The archive **does falsify** the simple hypothesis “drop REFERENCE shadow prep → bare→REFERENCE Tier-1 passes” on this metric, without proving WP5 regressed CPU logic.

## Order effects

Forward vs reverse stage order does not explain the borders inversion (all trials show counters &lt; REFERENCE). text_ui trial 1 (reverse) is an outlier where REFERENCE is faster than counters; worth watching in a completion-barrier diagnostic run.

## Next step (WP5b — proposed)

Diagnostic harness only: after the four-frame window, time `glFinish()` separately (`submission_elapsed_ns`, `post_window_drain_ns`, `submission_plus_drain_ns`). **Do not** change Tier-1 gates until that evidence exists.

Run bare / reference / counters only, three training recipes, seven paired trials — no A–F matrix, no held-out.

## Provenance

This capture is registered under `requalification_archives` in `analysis/profiler-overhead-diagnosis-manifest.json`, not as an authoritative WP1 `diagnostic_archives` entry.
