# WP6 — reference CPU ladder memo (first capture)

**Archive:** `analysis/evidence/frame-model-offline-82922f3-20261003T083731.257302Z-e4e73118.json` (`e4e73118`)  
**Git:** `82922f3` (WP6 Phase B ladder on `wp6-phase-b-reference-cpu-ladder`)  
**Contract:** `wp6_loaded_disabled_v1` (`EU4_TEST_LOADED_DISABLED`, `MODE_REFERENCE`, no `MEASURE_ENABLED` after arm)  
**Related:** [WP5b completion capture](wp5b-completion-diagnosis.md) (`f7cbf346`), [Phase A inventory](wp6-phase-a-reference-inventory.md)

## Timing boundary

Same harness as Tier-1 / WP5b: four `eu4_frame_model_test_frame` calls after warm-up, `glFinish()`, and `arm()`; `cpu_ns` is main-thread `CLOCK_THREAD_CPUTIME_ID` only.

## Ladder definitions (diagnostic)

| Quantity | Definition |
|----------|------------|
| Fixed instrumentation tax | `loaded-disabled` − `bare` |
| Active REFERENCE tax | `reference` − `loaded-disabled` |

Comparisons use `median_fraction` with `status: diagnostic` (not Tier-1 gates).

## Medians (seven paired trials)

Thread CPU (µs) and submission-window wall (ms):

| Recipe | bare cpu | loaded-disabled cpu | reference cpu | bare wall | loaded-disabled wall | reference wall |
|--------|----------|---------------------|---------------|-----------|----------------------|----------------|
| mesh | 264 | 273 | 302 | 11.00 | 11.41 | 12.57 |
| borders | 80 | 79 | 106 | 3.36 | 3.31 | 4.42 |
| text_ui | 36 | 36 | 64 | 1.48 | 1.49 | 2.68 |

## Paired `median_fraction` (vs baseline stage in each comparison)

| Recipe | loaded-disabled − bare (cpu) | reference − loaded-disabled (cpu) | loaded-disabled − bare (wall) | reference − loaded-disabled (wall) |
|--------|------------------------------|-----------------------------------|-------------------------------|-------------------------------------|
| mesh | **+3.4%** | **+9.8%** | +3.4% | +9.8% |
| borders | −1.4% | **+27.4%** | −1.3% | +27.8% |
| text_ui | +3.1% | **+76.2%** | +3.1% | +76.2% |

Wall and CPU fractions track together in this capture (no evidence that the ladder effect is wall-only).

## Interpretation

1. **`loaded-disabled − bare` on CPU is small** on mesh (~3%) and negligible on borders. Dylib presence + interposed GL fast paths alone do **not** explain most of the historical bare→REFERENCE CPU gap on mesh; they are not zero, but they are not the dominant term in this run.
2. **`reference − loaded-disabled` is the larger slice** on all training recipes, especially borders and text_ui. That points at **active REFERENCE measurement scaffolding** (scopes, events, `publish_frame`, and workload `gl_measurement_active()` guards on texture/state), not merely “profiler loaded.”
3. **text_ui** remains noisy in absolute wall time but shows a clear **active REFERENCE CPU** step-up (+76% vs loaded-disabled on CPU in this capture).
4. This capture **does not** replace WP5b: it does not measure post-`glFinish` drain. It **complements** WP5b by separating fixed load from active REFERENCE on the same `cpu_ns` metric.

## Next engineering

- **`minimal-reference`** stage to bound residual active REFERENCE accounting (see [WP6 plan](wp6-reference-cpu-decomposition.md)).
- Marginal ablations only where `minimal-reference` leaves material residual; do not treat summed marginals as additive.
