# WP9 — Tier-1 v3 training requalification

Training-only steady-state capture under the frozen **`tier1_causal_steady_state_hybrid_v3`** policy. Uses the same harness protocol as WP8 (post-arm prime, completion timing, variant rotation) but publishes under `profiler_overhead_diagnosis_v1` with `status: requalification_complete` for manifest registration.

## Mac capture

On a **clean** tree at the release commit:

```bash
python3 benchmark/eu4_frame_model.py wp9-tier1-v3-requalification --registry-json
```

Commit the emitted JSON under `analysis/evidence/` and append an entry to **`tier1_v3_requalification_archives`** in `analysis/profiler-overhead-diagnosis-manifest.json` (`work_package: WP9`). Do **not** use `summarize_wp1_diagnosis.py --register-latest` (requalification archives are manifest-listed only).

Validate locally:

```bash
python3 benchmark/summarize_wp1_diagnosis.py analysis/evidence/<archive>.json
python3 benchmark/summarize_wp1_diagnosis.py --find
```

## Interpretation

- `overhead_gate` / `offline_tier1_v3_admission.status` is the binding v3 training outcome (`passed`, `failed`, or `unavailable`).
- v2 remains production binding until v3 is accepted via this archive and follow-up held-out replay (see `docs/wp9-tier1-v3-design.md`).
- WP8 replay on `3c1e0db7` showed **text_ui** completion-wall **`unavailable`**; a fresh capture may still land there until measurement variance improves.
