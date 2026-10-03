# WP6 — reference CPU ladder memo

## Authoritative capture (v2)

**Archive:** `analysis/evidence/frame-model-offline-cb4b762-20261003T084522.537575Z-95f3513c.json` (`95f3513c`)  
**Git:** `cb4b762`  
**Contract:** `wp6_loaded_disabled_v2` (passive synthetic hook fast paths; no hook accounting clocks in loaded-disabled)

### Medians (seven paired trials)

| Recipe | bare cpu (µs) | loaded-disabled cpu | reference cpu | loaded−bare cpu | ref−loaded cpu |
|--------|---------------|---------------------|---------------|-----------------|----------------|
| mesh | 287 | 306 | 341 | **+4.1%** | **+8.1%** |
| borders | 89 | 91 | 124 | **+2.6%** | **+22.7%** |
| text_ui | 37 | 38 | 69 | **+4.5%** | **+79.2%** |

Wall `median_fraction` tracks CPU within ~0.1 pp on all recipes in this capture.

### v2 conclusion

With passive loaded-disabled, **`loaded-disabled − bare` is a clean fixed instrumentation / interposition tax** (single-digit % CPU on mesh; small on borders/text_ui). **`reference − loaded-disabled` remains the dominant term**, especially borders and text_ui. This supports optimizing **active REFERENCE accounting** (scopes, events, publication, guards) rather than treating DYLD interposition as the irreducible bottleneck.

---

## Historical capture (v1)

**Archive:** `analysis/evidence/frame-model-offline-82922f3-20261003T083731.257302Z-e4e73118.json` (`e4e73118`)  
**Git:** `82922f3`  
**Contract:** `wp6_loaded_disabled_v1`

> v1 left unconditional `clock_gettime` in `hook_update` / `hook_render` while measurement was disabled (~7/frame). Its `loaded-disabled − bare` is a **disabled-REFERENCE baseline** (interposition + residual hook clocks), not a pure interposer tax. **Do not discard** — qualitative conclusion matched v2 (active REFERENCE dominates).

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

## Shared interpretation (v1 + v2)

1. **Fixed load is small** on mesh CPU (v2 **+4.1%**); borders/text_ui fixed tax remains a few percent — not the main Tier-1 story.
2. **`reference − loaded-disabled` dominates** on borders and text_ui; target **active REFERENCE accounting** next (`minimal-reference`, then selective ablations).
3. Does **not** replace WP5b (no post-`glFinish` drain); complements it on the same `cpu_ns` metric.

## Next engineering

- **`minimal-reference`** stage to bound residual active REFERENCE accounting (see [WP6 plan](wp6-reference-cpu-decomposition.md)).
- Marginal ablations only where `minimal-reference` leaves material residual; do not treat summed marginals as additive.
