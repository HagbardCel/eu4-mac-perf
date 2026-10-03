# WP9 — Tier-1 v3 causal admission (design)

Policy identity (proposed): **`tier1_causal_steady_state_hybrid_v3`**

Evidence basis: WP8 steady-state capture **`3c1e0db7`** @ `f62aa28`. WP7 requalification **`ee9f5484`** remains the authoritative **v2** failure under the legacy four-frame protocol.

**Status:** Design + offline replay evaluator in `frame_model_tier1_policy.py`. No new Mac capture, no held-out, no live EU IV until v3 is frozen and a `wp9-tier1-v3-requalification` command exists.

## Independence and calibration honesty

CPU hybrid floors (6 µs REFERENCE, 11 µs counters) were **chosen using WP8 training outcomes** (`3c1e0db7`, primed four-frame variant). That is legitimate use of training data for policy design.

**Replay of the same archive under v3 is not independent confirmation that v3 is correct.** It shows the stated policy accepts the capture it was calibrated against. Held-out (or a fresh Mac requalification capture after freeze) is required before treating v3 admission as validated.

Do not tune thresholds on held-out. Freeze on training + design rationale, then test generalization once.

## What v2 got wrong (empirically)

1. **Cold post-arm activation** inside the timed four-frame window inflated `reference − bare` CPU (WP8: ~7 µs/frame median drop after one prime).
2. **Submission-wall elapsed** is not a stable proxy for completed work (WP5b; WP8 `submission + drain` medians near neutral).
3. **Dual AND gate** (`±3%` **and** `±50 µs`) fails on tiny synthetic CPU denominators despite sub-microsecond absolute overhead.

## Steady-state measurement contract (mandatory for v3 replay)

```text
arm (MEASURE_ENABLED)
→ post_arm_prime_frames profiler-active frames (default 1)
→ glFinish()
→ timed measured_frames window (default 4)
→ post-window completion timing drain (WP5b fields)
```

**Replay validator** (`validate_wp8_steady_state_*`) requires before any gate math:

- Exactly **`mesh`**, **`borders`**, **`text_ui`**, each once, `role == training` (no held-out, no extras).
- Variant **`four_frame_post_arm_prime_1`** with `post_arm_prime_frames == 1`, `measured_frames == 4`.
- **Seven** trials per recipe; stages **`bare` / `reference` / `counters`** with alternating stage order.
- Every stage reports harness `post_arm_prime_frames` / `measured_frames` matching the contract.
- **REFERENCE** and **counters**: `frames == 4`, `published_frames == 5`.

Contract violations → **`unavailable`** (not `failed`).

## Admission gate set (v3)

| Gate | Pairing | Admission |
|------|---------|-----------|
| `reference_cpu_ns` | reference vs bare | **yes** (hybrid absolute) |
| `counters_cpu_ns` | counters vs reference | **yes** (hybrid absolute) |
| `reference_submission_plus_drain_elapsed_ns` | reference vs bare | **no** (diagnostic only) |
| `counters_submission_plus_drain_elapsed_ns` | counters vs reference | **no** (diagnostic only) |

Raw submission-window elapsed gates are not used.

### Completion wall (diagnostic only)

WP8 shows **wide bootstrap CIs** on four-frame completion wall despite small medians (especially text_ui). A ±20% (or any) relative **admission** band fit to training would be circular and fragile.

Replay therefore reports completion-wall summaries with `acceptance_gate: false` and a **±5% diagnostic reference band** (`within_diagnostic_band`). They do **not** affect `status`.

## Hybrid CPU admission rule

Recompute bootstrap summaries from archived `pairs` (same seed/samples/CI as v2).

```text
|overhead_us| ≤ allowed_us

allowed_us = min(
    50 µs/frame cap,
    max(relative_limit × baseline_us_per_frame, absolute_floor_us),
)
```

| Step | `relative_limit` | `absolute_floor_us` |
|------|------------------|---------------------|
| `reference_cpu_ns` | 3% | 6.0 µs/frame |
| `counters_cpu_ns` | 12% | 11.0 µs/frame |

Relative median/CI fractions are **diagnostic** for CPU gates under v3.

Floors are training-calibrated on **`3c1e0db7`** so steady-state REFERENCE and counters bands reflect WP8 steady-state separation, not v2 cold-start failure.

## Replay API

```python
import frame_model_tier1_policy as tier1

replay = tier1.evaluate_wp8_steady_state_training_v3(preflight)
```

Returns `unavailable` if recipe set or measurement contract fails.

## Counters qualification

After priming, **counters − reference** is the dominant repeatable CPU step (~11–12% on training denominators). v3 encodes a separate hybrid band; priming alone does not satisfy v2’s 3% counters gate.

See `docs/wp9-counters-fast-path-review.md` for bounded optimization options.

## Out of scope

- Held-out admission until fixture + v3 capture command exist.
- Mutating v2 archives or `requalification_archives`.
- EU IV live measurement.

## Implementation checklist

- [ ] `wp9-tier1-v3-requalification` CLI (steady-state harness defaults + embedded v3 gates).
- [ ] `stage2-split-v3` when archive schema for v3 captures is frozen.
- [ ] Manifest bucket for v3 requalification after Mac capture.
- [ ] Held-out replay under frozen v3 (no threshold tuning on held-out).
