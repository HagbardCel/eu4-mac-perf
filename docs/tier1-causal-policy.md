# Tier-1 offline causal admission policy

Policy identity (current): **`tier1_causal_rel3pct_abs50us_v2`**

Evidence schema for new immutable archives: **`stage2-split-v2`**

This document freezes the Stage-2 Tier-1 admission rule **before** interpreting held-out
measurements. Numeric thresholds (3%, 50 µs/frame) are an a priori error budget, not tuned
to mesh/borders/text_ui outcomes. **v2** additionally freezes evaluator semantics: seven-pair
raw trials, tri-state results, qualified held-out provenance, contaminated-SHA denylist, and
bootstrap parameters bound into computation.

## Historical: `tier1_causal_rel3pct_abs50us_v1` / `stage2-split-v1`

The exploratory Mac archive `ab00679` was recorded under **v1** before these semantics
were finalized (boolean gate results, unqualified held-out treated as ordinary failure, no
seven-pair enforcement in replay). **Do not re-interpret v1 archives under v2 without
recording both version IDs** — the rolling pointer stores `archive_recorded_under` vs
`current_replay_under`.

## Scope

- **Training recipes (in-sample):** `mesh`, `borders`, `text_ui` — reference and counters
  paired gates only.
- **Held-out recipe (mandatory for admission pass):** hash-frozen under
  `analysis/held-out/` before first timing; SHA must not appear in
  `EXPLORATORY_HELD_OUT_SHA256S`.
- **Forensic suitability:** sampled + ablation gates under legacy
  `reference_counters_3pct_sampled_5pct_v1`; reported in `release_gates` but **not**
  required for full causal or calibration-only progression.

## Gate metrics

For each causal gate (`reference_elapsed_ns`, `reference_cpu_ns`, `counters_elapsed_ns`,
`counters_cpu_ns`):

1. **Relative limit:** 3% — pass only if median paired fraction and the 95% bootstrap
   interval endpoints are all within ±3%.
2. **Absolute cap:** 50 µs per frame — pass only if per-frame overhead (median and 95%
   interval) is within ±50 µs/frame.

Both limits must pass (fail-closed).

## Statistical procedure (versioned with the policy)

Implemented in `frame_model_workload.paired_summary()` and invoked by
`Tier1CausalPolicy.summarize_gate()` during evaluation and archive replay:

| Parameter | Value |
|-----------|-------|
| Paired trials per gate | **7** (required; fewer or stored summaries alone → `unavailable`) |
| Bootstrap seed | 1729 |
| Bootstrap resamples | 2000 |
| CI indices (sorted resamples) | 50 / 1950 (95%) |
| Statistic | median of paired fractions |
| Frames per trial | 4 (offline harness default) |

Replay **must** recompute summaries from archived `pairs` with these parameters;
stored medians alone are not trusted.

## Tri-state outcomes

| Status | Meaning |
|--------|---------|
| `passed` | All required gates and qualified held-out validation satisfied |
| `failed` | Complete evidence present but limits exceeded |
| `unavailable` | Missing gates, missing pairs, unqualified/contaminated held-out, etc. |

## Changing the policy

Bump `tier1_causal_rel3pct_abs50us_v*` (and usually `stage2-split-v*`) when evaluator
semantics change. Do not alter immutable archives; record replay under the new version.
