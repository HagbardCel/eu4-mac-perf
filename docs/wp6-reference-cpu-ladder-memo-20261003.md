# WP6 — reference CPU ladder memo

## Authoritative capture (v2 + stage validation)

**Archive:** `analysis/evidence/frame-model-offline-d9b77c4-20261003T093954.017662Z-4dc19f16.json` (`4dc19f16`)  
**Git:** `d9b77c4` (post–PR #14 merge; clean tree)  
**Contract:** `wp6_loaded_disabled_v2` — each `loaded-disabled` stage passed harness trace validation (no `F` rows; shutdown `X`; terminal `Z` with zero hook failures/drops).

### Medians (seven paired trials)

| Recipe | bare cpu (µs) | loaded-disabled cpu | reference cpu | loaded−bare cpu | ref−loaded cpu |
|--------|---------------|---------------------|---------------|-----------------|----------------|
| mesh | 262 | 284 | 312 | **+8.1%** | **+9.8%** |
| borders | 82 | 86 | 113 | **−0.3%** | **+30.8%** |
| text_ui | 33 | 36 | 63 | **+7.4%** | **+71.3%** |

Wall and CPU `median_fraction` **broadly track** on all recipes in this capture.

### Active REFERENCE step — medians and 95% bootstrap CIs (`reference − loaded-disabled` CPU)

| Recipe | Median | 95% CI |
|--------|--------|--------|
| mesh | +9.8% | +7.4% … +13.1% |
| borders | +30.8% | +3.5% … +33.0% |
| text_ui | +71.3% | +2.0% … +90.9% |

**Mesh** is robust (CI excludes zero). **Borders** and **text_ui** still show **wide CIs** and heterogeneous trial pairs (including near-zero active steps in some trials). Treat medians together with earlier captures (`95f3513c`, v1) when interpreting borders/text_ui; the odd trials may be diagnostically interesting.

### Conclusion (qualified)

With passive loaded-disabled, **`loaded-disabled − bare` is the fixed instrumentation / interposition tax** (roughly single-digit % CPU on mesh and text_ui in this capture; borders fixed tax ~0% median here). The **median** active REFERENCE step exceeds that fixed tax on all three recipes. **Mesh** is the clearest in `4dc19f16`; borders/text_ui need the CI/trial caveats above. No further broad ladder recaptures are planned — next experiment is **`minimal-reference`**.

---

## Pre–stage-validation capture (v2 contract)

**Archive:** `analysis/evidence/frame-model-offline-cb4b762-20261003T084522.537575Z-95f3513c.json` (`95f3513c`)  
**Git:** `cb4b762`  
**Contract:** `wp6_loaded_disabled_v2`

Captured before per-stage trace validation existed in the harness. Immutable reference; superseded for ladder authority by [`4dc19f16`](#authoritative-capture-v2--stage-validation).

| Recipe | loaded−bare cpu | ref−loaded cpu |
|--------|-----------------|----------------|
| mesh | +4.1% | +8.1% |
| borders | +2.6% | +22.7% |
| text_ui | +4.5% | +79.2% |

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

## v1 detail (historical)

| Recipe | loaded−bare cpu | ref−loaded cpu |
|--------|-----------------|----------------|
| mesh | +3.4% | +9.8% |
| borders | −1.4% | +27.4% |
| text_ui | +3.1% | +76.2% |

## Shared interpretation (v1 + v2 + authoritative)

1. **Fixed load is small** relative to active REFERENCE on mesh CPU (authoritative mesh fixed **+8.1%**, active **+9.8%**).
2. **Median `reference − loaded-disabled` exceeds fixed tax** on all recipes; mesh is clearest; borders/text_ui need CI/trial caveats.
3. Does **not** replace WP5b (no post-`glFinish` drain); complements it on the same `cpu_ns` metric.

## Next engineering

- **`minimal-reference`** stage to bound residual active REFERENCE accounting (see [WP6 plan](wp6-reference-cpu-decomposition.md)).
- Marginal ablations only where `minimal-reference` leaves material residual; do not treat summed marginals as additive.
