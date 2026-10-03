# WP8 — steady-state Tier-1 methodology memo

## Authoritative capture

**Archive:** `analysis/evidence/frame-model-offline-f62aa28-20261003T123219.715675Z-3c1e0db7.json` (`3c1e0db7`)  
**SHA256:** `c9e94a3934d715181c7c70a5e843c23ec4e46469c1353330891cf6ccd0d1ef3b`  
**Git:** `f62aa28` (clean tree)  
**Baseline context:** WP7 requalification `ee9f5484` (four-frame v2-policy failure; not steady-state overhead claim)

Registered under `steady_state_tier1_diagnosis_archives` in `analysis/profiler-overhead-diagnosis-manifest.json`. Diagnostic only — does not change Tier-1 v2 gates or WP1 authority.

## Question

Does apparent REFERENCE CPU overhead in short Tier-1 windows reflect **post-arm cold activation** vs **steady-state** cost?

## Method (this capture)

- Training recipes: `mesh`, `borders`, `text_ui`
- Seven trials; stage order alternates; **variant order rotates** per trial
- Variants: `four_frame_baseline` (0 prime, 4 measured), `four_frame_post_arm_prime_1` (1 prime, 4 measured), `forty_frame_post_arm_prime_1` (1 prime, 40 measured)
- Post-arm prime + `glFinish()` before timed window; WP5b completion timing on every stage

## Reference − bare CPU (`reference_cpu_ns`, µs/frame medians)

| Recipe | four_frame_baseline | four_frame_post_arm_prime_1 | forty_frame_post_arm_prime_1 |
|--------|--------------------:|----------------------------:|-----------------------------:|
| mesh | 12.63 | 5.25 | 3.20 |
| borders | 6.50 | 0.89 | 0.73 |
| text_ui | 7.31 | 0.39 | 0.30 |

Median **relative** fractions on the same axis drop sharply after one post-arm prime (e.g. text_ui ~0.91 → ~0.05 on four measured frames). Longer 40-frame windows show modest further per-frame reduction vs primed four-frame (amortization / lower variance), not a separate order-of-magnitude effect.

## Interpretation (qualified)

1. A large share of the **short-window** REFERENCE CPU gap vs bare in WP7-like protocols is consistent with **measurement placement / first timed frames after arm**, not multi-µs steady-state tax at the magnitudes implied by raw v2 failure fractions on tiny denominators.
2. After one profiler-active prime outside the timed window, remaining REFERENCE CPU overhead is **sub-µs to low single-digit µs/frame** on these training recipes in this capture — still often **above ±3% relative** where bare CPU is tiny (especially text_ui).
3. **Completion / wall** axes remain dominated by submission-window vs drain semantics (WP5b). Primed 40-frame REFERENCE `submission + drain` wall medians stay near neutral (mesh ~−0.11%, borders ~+0.03%, text_ui ~−2.33% with wide CI). Submission-window wall is not a suitable hard proxy for completed frame cost.
4. After steady-state priming, **`counters − reference` is the dominant repeatable CPU perturbation**, not lean REFERENCE. Prime + 40 frames: mesh **+12.23%** (~8.15 µs/frame), borders **+10.92%** (~2.19 µs/frame), text_ui **+11.62%** (~0.87 µs/frame). Primed four-frame variants show the same ~11–16% band — not a startup artifact. Steady-state ladder: bare → lean REFERENCE (tiny tax) → counters (~11–12% on synthetic denominators, ~0.9–8 µs/frame absolute).

## Next steps (not executed here)

- Design **Tier-1 v3** before held-out or live EU IV. WP8 supports at least: (1) explicit steady-state protocol (`arm → profiler-active prime → glFinish → timed window`); (2) demote or replace raw submission-wall gates with completed-wall semantics; (3) hybrid admission using an absolute noise floor on tiny CPU denominators, not bare ±3% alone.
- **Counters qualification** must be an explicit v3 decision: further counters fast-path work vs accepting ~1–8 µs/frame under a hybrid policy. Priming alone will not make the existing ±3% counters-vs-reference gate pass.
- Inspect counters cost with existing profiling before another broad decomposition program.
- Do **not** treat this archive as requalification pass/fail; compare qualitatively to `ee9f5484` only for methodology.
