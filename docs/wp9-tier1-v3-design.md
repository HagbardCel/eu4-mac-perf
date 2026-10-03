# WP9 — Tier-1 v3 causal admission (design)

Policy identity (proposed): **`tier1_causal_steady_state_hybrid_v3`**

Evidence basis: WP8 steady-state capture **`3c1e0db7`** @ `f62aa28`. WP7 requalification **`ee9f5484`** remains the authoritative **v2** failure under the legacy four-frame protocol.

**Status:** Policy frozen in `frame_model_tier1_policy.py`; WP9 requalification CLI shipped (PR #23). Authoritative Mac training requalification **`a73ea57b`** @ `1f4282a` **failed** (`tier1_v3_admission` / `overhead_gate`: **failed**). **v2 remains binding** for production admission.

## Independence and calibration honesty

CPU hybrid floors (6 µs REFERENCE, 11 µs counters) were **chosen using WP8 training data** for the **`four_frame_post_arm_prime_1`** variant. Completion-wall admission uses **`forty_frame_post_arm_prime_1`** with a **±5% bootstrap CI** rule because 40-frame completion data are substantially less noisy than four-frame (except text_ui reference, which remains unbounded).

**Replay of `3c1e0db7` under v3 is not independent validation** — it shows the stated policy accepts the capture it was calibrated against. The intended path was: freeze v3 → **fresh training requalification** → on **pass**, **held-out** validation (no threshold tuning on held-out). Fresh requalification **`a73ea57b`** did **not** pass; **held-out must not be consumed** to rescue v3.

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

Completion-wall tri-state (95% bootstrap CI on signed fraction vs **[-5%, +5%]**):

| CI vs band | Status |
|------------|--------|
| Wholly inside `[-5%, +5%]` | **passed** |
| Wholly above `+5%` or wholly below `-5%` | **failed** |
| Overlaps band but extends outside | **unavailable** |

Example: text_ui reference 40f CI ~`[-24%, +4%]` → **unavailable**; a tight CI ~`[+7%, +11%]` → **failed**.

Raw submission-window elapsed gates are not used.

### Hybrid CPU rule

```text
|overhead_us| ≤ min(50 µs, max(relative_limit × baseline_us, absolute_floor_us))
```

| Gate | Relative component (not independently gated) | `absolute_floor_us` |
|------|---------------------------------------------|---------------------|
| `reference_cpu_ns` | 3% of baseline µs/frame in `max(·, floor)` | 6.0 |
| `counters_cpu_ns` | 12% of baseline µs/frame in `max(·, floor)` | 11.0 |

The legacy ±relative `paired_summary` outcome is preserved as `relative_diagnostic_status` on each gate; authoritative v3 admission is `gates[].status` == `results[]`.

For the **CPU admission variant** (`four_frame_post_arm_prime_1`), training medians are roughly **~12–16%** and **~1.3–9.4 µs/frame** counters-over-reference (mesh ~13.5% / 9.4 µs; text_ui ~16.3% / 1.3 µs). The **11 µs floor** dominates pass/fail; the 12% relative limit is not the binding constraint on this variant.

## Replay API

```python
import frame_model_tier1_policy as tier1

replay = tier1.evaluate_wp8_steady_state_training_v3(preflight)
```

On archive **`3c1e0db7`**: **mesh** and **borders** pass all gates; **text_ui** is **`unavailable`** on `reference_submission_plus_drain_elapsed_ns` (wide 40-frame CI). Overall training replay → **`unavailable`** (calibration replay only).

On authoritative requalification **`a73ea57b`**: overall **`failed`**. **mesh** `counters_cpu_ns` **failed** (median ~9.63 µs/frame within 11 µs floor, but bootstrap upper bound ~11.35 µs/frame — policy correctly fails the whole CI). **borders** and **text_ui** CPU gates pass. **text_ui** completion-wall gates (**reference** and **counters**) are **`unavailable`** (wide CIs; 40-frame windows jump between ~19–20 ms and ~27–32 ms regimes — environmental completion variability, not credible ±30–40% profiler overhead). See manifest `tier1_v3_requalification_archives`.

## Counters qualification

After priming, counters — not lean REFERENCE — is the dominant repeatable CPU step. v3 encodes a separate hybrid band for counters CPU; priming alone does not satisfy v2’s 3% counters gate.

See `docs/wp9-counters-fast-path-review.md`.

## Checklist

- [x] `wp9-tier1-v3-requalification` CLI (steady-state harness + embedded v3 gates). See `docs/wp9-tier1-v3-requalification.md`.
- [x] Fresh Mac capture; register `tier1_v3_requalification_archives` manifest bucket (`a73ea57b` @ `1f4282a`).
- [x] Fresh training requalification executed — **outcome: failed** (not a registration bug; replay matches stored admission).
- [ ] **Blocked:** held-out replay under frozen v3 — **do not run** until training qualification is resolved and any replacement policy/implementation is frozen. Failed training requalification is not a license to tune on held-out.

## After `a73ea57b` (bounded next work — not another methodology WP)

v3 did not pass fresh training requalification: **mesh counters CPU failed**; **text_ui completion-wall qualification unavailable**. v2 remains binding.

1. **Mesh counters CPU (only genuine overhead failure):** A tightly bounded **counters-lite** experiment on mesh is justified (see `docs/wp9-counters-fast-path-review.md`). Goal: materially cut ~9–10 µs/frame stack cost — not to shave ~0.35 µs off a confidence bound via repeated captures.
2. **text_ui completion-wall (measurement problem):** Do not optimize the profiler from these gates. Either design a more stable completion-wall measurement for tiny workloads or revise policy in a **new version** — do not silently reinterpret frozen v3.

Do **not** repeat the same capture until one passes without a pre-specified aggregation rule (that would be qualification-by-luck). Do **not** loosen 11 → 12 µs because the independent run landed at 11.35 µs on the CI upper bound.
