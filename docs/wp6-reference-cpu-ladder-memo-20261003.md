# WP6 — reference CPU ladder memo

## Pre–stage-validation capture (v2 contract)

**Archive:** `analysis/evidence/frame-model-offline-cb4b762-20261003T084522.537575Z-95f3513c.json` (`95f3513c`)  
**Git:** `cb4b762` (evidence committed separately on the WP6 branch)  
**Contract:** `wp6_loaded_disabled_v2` (passive synthetic hook fast paths; no hook accounting clocks in loaded-disabled)

This capture was taken **before** the ladder harness validated each `loaded-disabled` stage trace (no `F` rows, terminal `Z`, shutdown `X`). The implementation and native harness support passive v2; **one more offline ladder recapture with stage validation** should become the authoritative WP6 ladder. This archive stays immutable as the pre-validation v2 reference.

### Medians (seven paired trials)

| Recipe | bare cpu (µs) | loaded-disabled cpu | reference cpu | loaded−bare cpu | ref−loaded cpu |
|--------|---------------|---------------------|---------------|-----------------|----------------|
| mesh | 287 | 306 | 341 | **+4.1%** | **+8.1%** |
| borders | 89 | 91 | 124 | **+2.6%** | **+22.7%** |
| text_ui | 37 | 38 | 69 | **+4.5%** | **+79.2%** |

Wall and CPU `median_fraction` **broadly track** on all recipes (e.g. borders active step ~21.2% wall vs ~22.7% CPU).

### Active REFERENCE step — medians and 95% bootstrap CIs (`reference − loaded-disabled` CPU)

| Recipe | Median | 95% CI |
|--------|--------|--------|
| mesh | +8.1% | +4.7% … +16.0% |
| borders | +22.7% | −2.0% … +42.7% |
| text_ui | +79.2% | −0.7% … +83.9% |

**Mesh** is robust in this capture (CI excludes zero). **Borders** and **text_ui** show **wide CIs that cross zero** and heterogeneous trial pairs (e.g. text_ui: five trials with a large REFERENCE step, two near zero). That heterogeneity may be diagnostically interesting (profiler state / timing), not noise to hide.

### v2 conclusion (qualified)

With passive loaded-disabled, **`loaded-disabled − bare` is a clean fixed instrumentation / interposition tax** (roughly +2–5% CPU on these recipes). The **median** active REFERENCE step (`reference − loaded-disabled`) is **substantially larger than that fixed tax** on all three recipes. **Mesh** supports that claim in this capture; **borders** and **text_ui** rely more on medians and earlier WP5/WP5b/v1 evidence because of wide CIs and odd near-zero trials. This supports optimizing **active REFERENCE accounting** (scopes, events, publication, guards) rather than treating DYLD interposition as the irreducible bottleneck.

---

## Historical capture (v1)

**Archive:** `analysis/evidence/frame-model-offline-82922f3-20261003T083731.257302Z-e4e73118.json` (`e4e73118`)  
**Git:** `82922f3`  
**Contract:** `wp6_loaded_disabled_v1`

> v1 left unconditional `clock_gettime` in `hook_update` / `hook_render` while measurement was disabled (~7/frame). Its `loaded-disabled − bare` is a **disabled-REFERENCE baseline** (interposition + residual hook clocks), not a pure interposer tax. **Do not discard** — qualitative conclusion matched v2 (active REFERENCE dominates medians).

**Related:** [WP5b](wp5b-completion-diagnosis.md) (`f7cbf346`), [Phase A inventory](wp6-phase-a-reference-inventory.md)

## Timing boundary

Same harness as Tier-1 / WP5b: four `eu4_frame_model_test_frame` calls after warm-up, `glFinish()`, and `arm()`; `cpu_ns` is main-thread `CLOCK_THREAD_CPUTIME_ID` only.

## Ladder definitions (diagnostic)

| Quantity | Definition |
|----------|------------|
| Fixed instrumentation tax (v2) | `loaded-disabled` − `bare` with `wp6_loaded_disabled_v2` |
| Active REFERENCE tax | `reference` − `loaded-disabled` |

Comparisons use `median_fraction` with `status: diagnostic` (not Tier-1 gates).

**Capture harness:** after each `loaded-disabled` stage, the ladder validates the profiler trace (header `H`, no published `F` frames, shutdown `X`, terminal `Z` with `hook_failures=0` and `dropped_records=0`).

## v1 detail (historical)

| Recipe | loaded−bare cpu | ref−loaded cpu |
|--------|-----------------|----------------|
| mesh | +3.4% | +9.8% |
| borders | −1.4% | +27.4% |
| text_ui | +3.1% | +76.2% |

## Shared interpretation (v1 + v2)

1. **Fixed load is small** on mesh CPU (v2 **+4.1%**); borders/text_ui fixed tax remains a few percent — not the main Tier-1 story.
2. **Median `reference − loaded-disabled` exceeds fixed tax** on all recipes; mesh is clearest in `95f3513c`; borders/text_ui need CI/trial caveats above.
3. Does **not** replace WP5b (no post-`glFinish` drain); complements it on the same `cpu_ns` metric.

## Next engineering

- **One authoritative v2 ladder recapture** with loaded-disabled trace validation (then stop repeating the broad ladder).
- **`minimal-reference`** stage to bound residual active REFERENCE accounting (see [WP6 plan](wp6-reference-cpu-decomposition.md)).
- Marginal ablations only where `minimal-reference` leaves material residual; do not treat summed marginals as additive.
