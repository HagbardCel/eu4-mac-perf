# WP9 — Tier-1 v3 training requalification

Training-only steady-state capture under the frozen **`tier1_causal_steady_state_hybrid_v3`** policy. Uses the same harness protocol as WP8 (post-arm prime, completion timing, variant rotation) but publishes under `profiler_overhead_diagnosis_v1` with `status: requalification_complete` for manifest registration.

## Mac capture

On a **clean** tree at the release commit:

```bash
python3 benchmark/eu4_frame_model.py wp9-tier1-v3-requalification --registry-json
```

Commit the emitted JSON under `analysis/evidence/` and append an entry to **`tier1_v3_requalification_archives`** in `analysis/profiler-overhead-diagnosis-manifest.json` (`work_package: WP9`). Do **not** use `summarize_wp1_diagnosis.py --register-latest` (requalification archives are manifest-listed only).

Validate locally (replays v3 admission from raw trials; fails if embedded summaries disagree):

```bash
python3 benchmark/summarize_wp1_diagnosis.py analysis/evidence/<archive>.json
python3 benchmark/summarize_wp1_diagnosis.py analysis/evidence/<archive>.json --markdown
python3 benchmark/summarize_wp1_diagnosis.py --find
```

The default path invocation runs full archive validation before printing a v3 gate summary.

## Interpretation

- `overhead_gate` / `offline_tier1_v3_admission.status` is the binding v3 **training** outcome (`passed`, `failed`, or `unavailable`).
- Authoritative capture **`a73ea57b`** (@ `1f4282a`, registered on `main` **`192aff5`**): **`failed`**. Validator replay from raw trials matches embedded admission (integrity OK).
- **v2 remains production binding.** v3 did **not** pass training requalification — **do not consume held-out evidence** until the training qualification issue is resolved and any replacement policy/implementation is frozen. Held-out is gated on **passing** training requalification first (see `docs/wp9-tier1-v3-design.md`).
- **mesh:** only failure is `counters_cpu_ns` (narrow: median under 11 µs/frame, bootstrap upper bound slightly above the hybrid floor). **borders:** all gates passed. **text_ui:** CPU passed; both completion-wall gates **`unavailable`** (high completion-window variance on a tiny workload — tri-state is correct).
