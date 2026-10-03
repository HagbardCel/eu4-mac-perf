# WP11 — Tier-1 v4 training requalification

Training-only steady-state capture under the frozen **`tier1_causal_steady_state_hybrid_v4`** policy. Same WP8 harness protocol as WP9 (post-arm prime, completion timing, variant rotation) with **21 uniform paired trials** per training recipe. Hybrid CPU floors and completion-wall band are unchanged from v3.

## Mac capture

On a **clean** tree at the release commit:

```bash
python3 benchmark/eu4_frame_model.py wp11-tier1-v4-requalification --registry-json
```

Commit the emitted JSON under `analysis/evidence/` and append an entry to **`tier1_v4_requalification_archives`** in `analysis/profiler-overhead-diagnosis-manifest.json` (`work_package: WP11`). Do **not** use `summarize_wp1_diagnosis.py --register-latest`.

Validate locally (replays v4 admission from raw trials):

```bash
python3 benchmark/summarize_wp1_diagnosis.py analysis/evidence/<archive>.json
python3 benchmark/summarize_wp1_diagnosis.py analysis/evidence/<archive>.json --markdown
python3 benchmark/summarize_wp1_diagnosis.py --find
```

## Interpretation

- `overhead_gate` / `offline_tier1_v4_admission.status` is the binding v4 **training** outcome.
- Authoritative capture **`33339073`** (@ `7b7fc77`, registered on `main`): **`failed`**. Validator replay from raw trials matches embedded admission.
- **mesh:** only failure is `counters_cpu_ns` (median ~10.43 µs/frame; bootstrap upper bound ~11.14 µs above the 11 µs hybrid floor). **borders:** all gates passed. **text_ui:** CPU passed; both completion-wall gates **`unavailable`**.
- v3 authoritative capture **`a73ea57b`** remains the failed seven-pair result; v4 does not retroactively pass v3 evidence.
- **v2 remains production binding.** v4 training requalification **failed** — do not run held-out under v4; stop profiler qualification iteration and return to EU IV performance work (see `docs/tier1-v4-statistical-replication-proposal.md`).

### Scientific notes (non-admission)

- The 21-trial WP11 `text_ui` completion-wall result did not reproduce WP10's favorable 21×40f diagnostic; therefore increased replication alone does not resolve the small-workload completion-wall instability.
- Mesh counters CPU shows substantial within-session drift (7-trial block medians approximately 11.14 → 10.43 → 8.69 µs/frame); the frozen v4 failure is therefore an admission result, not evidence that steady-state median overhead exceeds 11 µs/frame (WP11 mesh median ~10.43 µs/frame; failure is the 95% CI upper bound ~11.14 µs vs the 11 µs hybrid allowance).
