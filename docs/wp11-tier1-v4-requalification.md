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
- v3 authoritative capture **`a73ea57b`** remains the failed seven-pair result; v4 does not replay v3 archives as passes.
- **v2 remains production binding** until v4 training requalification passes.
- On pass: one held-out evaluation under frozen v4, then stop qualification iteration or return to EU IV work. On fail: stop qualification work (see `docs/tier1-v4-statistical-replication-proposal.md`).
