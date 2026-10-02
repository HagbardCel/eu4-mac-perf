# Tier-1 offline causal admission policy

Policy identity: **`tier1_causal_rel3pct_abs50us_v1`**

This document freezes the Stage-2 Tier-1 admission rule **before** interpreting held-out
measurements. Thresholds are an a priori error budget for representative GL structural
recipes, not tuned to mesh/borders/text_ui outcomes.

## Scope

- **Training recipes (in-sample):** `mesh`, `borders`, `text_ui` — reference and counters
  paired gates only.
- **Held-out recipe (mandatory for admission pass):** hash-frozen under
  `analysis/held-out/` before first timing. Exploratory archives measured without a frozen
  fixture (for example `ab00679`) are development evidence, not independent validation.
- **Forensic suitability:** sampled + ablation gates under legacy
  `reference_counters_3pct_sampled_5pct_v1`; non-blocking for `calibration_only` runs.

## Gate metrics

For each causal gate (`reference_elapsed_ns`, `reference_cpu_ns`, `counters_elapsed_ns`,
`counters_cpu_ns`):

1. **Relative limit:** 3% — pass only if median paired fraction and the 95% bootstrap
   interval endpoints are all within ±3%.
2. **Absolute floor:** 50 µs per frame — pass only if per-frame overhead (median and 95%
   interval) is within ±50 µs/frame.

Both limits must pass (fail-closed).

## Statistical procedure (versioned with the policy)

Implemented in `frame_model_workload.paired_summary()` and invoked by
`Tier1CausalPolicy.summarize_gate()` during evaluation and archive replay:

| Parameter | Value |
|-----------|-------|
| Bootstrap seed | 1729 |
| Bootstrap resamples | 2000 |
| CI indices (sorted resamples) | 50 / 1950 (95%) |
| Statistic | median of paired fractions |
| Frames per trial | 4 (offline harness default) |

Replay **must** recompute summaries from archived `pairs`; stored medians alone are not
trusted for policy audit.

## Tri-state outcomes

| Status | Meaning |
|--------|---------|
| `passed` | All required gates and held-out validation satisfied |
| `failed` | Complete evidence present but limits exceeded |
| `unavailable` | Missing gates, missing metrics, missing held-out fixture/run, or legacy archive without held-out |

Partial causal evidence never passes. Missing held-out validation never passes Stage-2
admission.

## Changing the policy

If limits or bootstrap settings change after observing training workloads, treat existing
archives as development evidence and introduce a **new** policy version plus a **new**
held-out workload frozen before remeasurement.
