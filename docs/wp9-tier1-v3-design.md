# WP9 — Tier-1 v3 causal admission (design)

Policy identity (proposed): **`tier1_causal_steady_state_hybrid_v3`**

Evidence basis: WP8 steady-state capture **`3c1e0db7`** @ `f62aa28`. WP7 requalification **`ee9f5484`** remains the authoritative **v2** failure under the legacy four-frame protocol.

**Status:** Design + offline replay evaluator implemented in `frame_model_tier1_policy.py`. No new Mac capture, no held-out, no live EU IV until v3 is frozen and a `wp9-tier1-v3-requalification` (or equivalent) command exists.

## What v2 got wrong (empirically)

1. **Cold post-arm activation** was inside the timed four-frame window, inflating `reference − bare` CPU (WP8: ~7 µs/frame median reduction after one prime).
2. **Submission-wall elapsed** gates are not stable proxies for completed work (WP5b, reinforced by WP8 primed 40-frame `submission + drain` medians near zero).
3. **Dual AND gate** (`±3%` **and** `±50 µs`) fails on tiny synthetic denominators even when absolute overhead is sub-microsecond (text_ui REFERENCE after priming).

## Steady-state measurement contract (mandatory for v3 captures)

```text
arm (MEASURE_ENABLED)
→ post_arm_prime_frames profiler-active frames (default 1)
→ glFinish()   # GPU completion boundary
→ timed measured_frames window (default 4)
→ post-window completion timing drain (WP5b fields)
```

Harness must report `post_arm_prime_frames` and `measured_frames` on **every** stage (including bare). Archive must record `published_frames` where telemetry includes prime frames.

Default admission variant: **`four_frame_post_arm_prime_1`** (1 prime, 4 measured). Longer windows (e.g. 40 frames) remain diagnostic, not admission, until explicitly adopted.

## Gate set (v3)

| Gate | Pairing | Role |
|------|---------|------|
| `reference_submission_plus_drain_elapsed_ns` | reference vs bare | Completed wall (primary wall admission) |
| `reference_cpu_ns` | reference vs bare | Steady-state REFERENCE CPU |
| `counters_submission_plus_drain_elapsed_ns` | counters vs reference | Completed wall for counters step |
| `counters_cpu_ns` | counters vs reference | Steady-state counters CPU |

Raw `reference_elapsed_ns` / `counters_elapsed_ns` (submission window only) are **reported** in captures but **not** v3 admission gates.

Completion-wall gates use a **±20% bootstrap CI** relative check only (no hybrid µs cap — denominators are orders of magnitude larger than CPU). Tighten after held-out protocol is frozen; WP8 text_ui counters completion CI is wide despite small medians.

## Hybrid admission rule

For each gate, recompute bootstrap summaries from archived `pairs` (same seed/samples/CI indices as v2).

Let `baseline_us` = median `reference / (1000 × measured_frames)` from the pair list (the reference stage of that comparison).

**Pass** iff per-frame absolute overhead and its 95% bootstrap interval satisfy:

```text
|overhead_us| ≤ allowed_us

allowed_us = min(
    absolute_cap,
    max(relative_limit × baseline_us, absolute_floor_us),
)
```

| Step | `relative_limit` | `absolute_floor_us` |
|------|------------------|---------------------|
| `reference_*` (CPU) | 3% | 6.0 µs/frame |
| `counters_*` (CPU) | 12% | 11.0 µs/frame |
| `*_submission_plus_drain_elapsed_ns` | 20% CI | (relative only) |

`absolute_cap` remains **50 µs/frame** (safety ceiling, unchanged from v2).

Relative median/CI fractions are **diagnostic** under v3; they do not independently fail admission once the hybrid absolute test passes.

Floors are calibrated from **`3c1e0db7`** primed windows so that lean REFERENCE steady-state passes while counters steady-state (~11–12% on tiny denominators, ~0.9–8 µs/frame absolute) is explicitly qualified rather than failing v2’s 3% counters step.

## Replay API

```python
import frame_model_tier1_policy as tier1

replay = tier1.evaluate_wp8_steady_state_training_v3(preflight)
```

Expect `training_recipes[].gates[].hybrid_allowed_us_per_frame` in replay output.

## Counters qualification (explicit v3 decision)

WP8 shows that after priming, **counters — not lean REFERENCE — is the dominant repeatable CPU perturbation**. v3 encodes that as a **separate hybrid band** (12% + 10 µs floor), not by reusing the REFERENCE 3% gate.

Engineering follow-up (bounded): see `docs/wp9-counters-fast-path-review.md`. If a cheap fast-path removes most of the ~11–12% band, revisit floors before held-out.

## Out of scope for WP9 design

- Held-out admission (still requires frozen fixture + v3 capture command).
- Changing immutable v2 archives or `requalification_archives` outcomes.
- EU IV live measurement.

## Implementation checklist (follow-on PRs)

- [ ] `wp9-tier1-v3-requalification` CLI mirroring WP7 but with steady-state harness defaults and v3 gate embedding.
- [ ] Bump `stage2-split-v3` only when archive schema for v3 captures is frozen.
- [ ] Register v3 requalification under a new manifest bucket when capture exists.
