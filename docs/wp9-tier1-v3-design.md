# WP9 — Tier-1 v3 causal admission (design)

Policy identity (proposed): **`tier1_causal_steady_state_hybrid_v3`**

Evidence basis: WP8 steady-state capture **`3c1e0db7`** @ `f62aa28`. WP7 requalification **`ee9f5484`** remains the authoritative **v2** failure under the legacy four-frame protocol.

**Status:** Design + offline replay evaluator in `frame_model_tier1_policy.py`. No dedicated v3 Mac requalification command yet. **v2 remains binding** for existing archives.

## Independence and calibration honesty

CPU hybrid floors (6 µs REFERENCE, 11 µs counters) were **chosen using WP8 training data** for the **`four_frame_post_arm_prime_1`** variant. Completion-wall admission uses **`forty_frame_post_arm_prime_1`** with a **±5% bootstrap CI** rule because 40-frame completion data are substantially less noisy than four-frame (except text_ui reference, which remains unbounded).

**Replay of `3c1e0db7` under v3 is not independent validation** — it shows the stated policy accepts the capture it was calibrated against. A **fresh v3 requalification capture** and later **held-out** evaluation (without threshold tuning on held-out) are required before treating v3 as production admission.

## Steady-state measurement contract

### CPU admission (`four_frame_post_arm_prime_1`)

```text
prime = 1, measured = 4, published = 5 (reference/counters telemetry)
```

### Completion-wall admission (`forty_frame_post_arm_prime_1`)

```text
prime = 1, measured = 40, published = 41
```

Harness reports `post_arm_prime_frames` / `measured_frames` on **every** stage. Replay validates:

- Recipes exactly **`mesh`**, **`borders`**, **`text_ui`** (training only).
- Trial ids **`0…6`**, unique; stage order alternates per trial; **`variant_order`** rotates per WP8 generator.
- Variant contracts for **both** CPU and wall variants on all trials/stages.

Contract violations → **`unavailable`**.

## Admission gates (v3)

Pairs are **derived from `trials[].variants[…].stages`** at replay time (not from archived `variant_comparisons`).

| Gate | Variant | Pairing | Rule |
|------|---------|---------|------|
| `reference_cpu_ns` | 4f + prime | reference vs bare | Hybrid absolute CPU |
| `counters_cpu_ns` | 4f + prime | counters vs reference | Hybrid absolute CPU |
| `reference_submission_plus_drain_elapsed_ns` | 40f + prime | reference vs bare | ±5% bootstrap CI on fraction |
| `counters_submission_plus_drain_elapsed_ns` | 40f + prime | counters vs reference | ±5% bootstrap CI on fraction |

If a completion-wall gate’s 95% CI exceeds ±5%, that gate is **`unavailable`** (perturbation not bounded), not `failed`.

Raw submission-window elapsed gates are not used.

### Hybrid CPU rule

```text
|overhead_us| ≤ min(50 µs, max(relative_limit × baseline_us, absolute_floor_us))
```

| Gate | `relative_limit` (diagnostic) | `absolute_floor_us` |
|------|-------------------------------|---------------------|
| `reference_cpu_ns` | 3% | 6.0 |
| `counters_cpu_ns` | 12% | 11.0 |

For the **CPU admission variant** (`four_frame_post_arm_prime_1`), training medians are roughly **~12–16%** and **~1.3–9.4 µs/frame** counters-over-reference (mesh ~13.5% / 9.4 µs; text_ui ~16.3% / 1.3 µs). The **11 µs floor** dominates pass/fail; the 12% relative limit is not the binding constraint on this variant.

## Replay API

```python
import frame_model_tier1_policy as tier1

replay = tier1.evaluate_wp8_steady_state_training_v3(preflight)
```

On archive **`3c1e0db7`**: **mesh** and **borders** pass all gates; **text_ui** is **`unavailable`** on `reference_submission_plus_drain_elapsed_ns` (wide 40-frame CI). Overall training replay → **`unavailable`** until text_ui completion-wall measurement is qualified or the gate policy is revised with held-out evidence.

## Counters qualification

After priming, counters — not lean REFERENCE — is the dominant repeatable CPU step. v3 encodes a separate hybrid band for counters CPU; priming alone does not satisfy v2’s 3% counters gate.

See `docs/wp9-counters-fast-path-review.md`.

## Checklist

- [ ] `wp9-tier1-v3-requalification` CLI (steady-state harness + embedded v3 gates).
- [ ] Fresh Mac capture; register manifest bucket.
- [ ] Held-out replay under **frozen** v3 (no threshold retuning on held-out).
