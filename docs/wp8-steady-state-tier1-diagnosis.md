# WP8 — steady-state Tier-1 methodology diagnostic

## Context

WP7 requalification archive `ee9f5484` is a valid **Tier-1 v2-policy failure**, but the four-frame training protocol may mix **post-arm lazy producer initialization** and **submission-wall placement** with steady-state REFERENCE CPU. Do not interpret that capture as proof of ~14–28% steady-state overhead.

This work package measures whether REFERENCE CPU perturbation collapses once cold initialization is excluded, and records completion timing alongside the existing submission window.

## Scope

- Training recipes only: `mesh`, `borders`, `text_ui`
- Stages: `bare` → `reference` → `counters` (seven alternating trials)
- **No** held-out fixture, **no** EU IV, **no** Tier-1 acceptance gate change

## Measurement variants (per stage)

| Variant | Post-arm prime frames | Measured frames |
|---------|----------------------:|----------------:|
| `four_frame_baseline` | 0 | 4 (current Tier-1 window) |
| `four_frame_post_arm_prime_1` | 1 | 4 |
| `forty_frame_post_arm_prime_1` | 1 | 40 |

Each variant records:

- Tier-1 submission `elapsed_ns` / `cpu_ns`
- WP5b completion fields (`submission_*`, `post_window_drain_*`, `submission_plus_drain_*`)

Harness: after `MEASURE_ENABLED` arm, optional profiler-active prime frames run **outside** the timed window (`EU4_TEST_POST_ARM_PRIME_FRAMES`).

## Command

Clean tree on `main`:

```bash
python3 benchmark/eu4_frame_model.py wp8-steady-state-diagnosis --registry-json
```

Archive `purpose`: `wp8_steady_state_tier1_diagnosis_v1`. Register under a new manifest bucket or memo when capture exists; **not** authoritative WP1 or `requalification_archives` unless explicitly decided later.

## How to read results

1. Compare `four_frame_baseline` vs `four_frame_post_arm_prime_1` on **reference_cpu_ns** and completion axes.
2. If CPU gap collapses after one prime frame, Tier-1 should measure **steady-state** (prime before the timed window), not change REFERENCE implementation first.
3. If CPU remains ~1–10 µs/frame but still fails ±3% on tiny denominators, design **Tier-1 v3** before held-out.
4. Use `forty_frame_post_arm_prime_1` to see whether residual cost amortizes (per-frame CPU vs four-frame window).

## Related evidence

- WP7 requalification: `ee9f5484` (`requalification_archives`, `work_package: WP7`)
- WP5b completion diagnosis: `f7cbf346`
