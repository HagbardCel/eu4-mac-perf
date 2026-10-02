# WP1 profiler overhead diagnosis memo (2026-10-02)

**Status:** **WP1 capture registered** — `diagnostic-matrix` immutable archive at commit `dac4da4` (see [Closure](#wp1-closure-status)).

**Authoritative WP1 source** (training-only, `purpose: profiler_overhead_diagnosis_v1`):

| Field | Value |
|-------|--------|
| Archive | `analysis/evidence/frame-model-offline-dac4da4-20261002T213942.617917Z-27ab7b86.json` |
| `evidence_id` | `20261002T213942.617917Z-27ab7b86` |
| `archive_sha256` | `9e1c6689fcd23848aa38eb2fe770c1f4812bbcfe762a37edf73442c6fd1edfba` |
| `validation_scope` | `training_only` |
| `status` | `diagnostic_complete` |

**Frozen Stage-2 baselines (read-only; do not rewrite):** `00924cb` / `8c71c47` in [`profiler-overhead-diagnosis-manifest.json`](../analysis/profiler-overhead-diagnosis-manifest.json).

---

## Layer 1 — Stage-2 causal gates (unchanged blocker)

From the **WP1 capture** (`dac4da4`). **median_fraction** on `elapsed_ns`; 95% CI; Tier-1 **≤ 3%** → all **failed**.

| Recipe | Gate | median_fraction | confidence_interval_95 |
|--------|------|-----------------|-------------------------|
| mesh | `reference_elapsed_ns` | +12.4% | [10.1%, 12.5%] |
| mesh | `counters_elapsed_ns` | +10.3% | [6.1%, 11.5%] |
| borders | `reference_elapsed_ns` | +3.6% | [-1.7%, 11.5%] |
| borders | `counters_elapsed_ns` | +10.3% | [3.0%, 15.8%] |
| text_ui | `reference_elapsed_ns` | +2.1% | [-12.4%, 17.1%] |
| text_ui | `counters_elapsed_ns` | +13.5% | [6.6%, 21.7%] |

Forensic suitability (same capture): `sampled_elapsed_ns` vs reference **+386% to +470%** (mesh/borders/text_ui) — still far above the 5% forensic gate.

**Takeaway:** Stage-3 remains blocked on **offline causal admission** for reference/counters. Tier-1 **failed** on this capture does **not** invalidate the diagnostic matrix.

---

## Layer 2 — Counters → sampled minimal (`diag_A` vs `counters`)

Paired in-trial via `diagnostic_matrix.vs_counters["diag_A"]`.

| Recipe | elapsed median_fraction | elapsed CI95 | cpu median_fraction | cpu CI95 |
|--------|------------------------:|--------------|--------------------:|---------|
| mesh | +3.8% | [-3.6%, 8.2%] | +4.0% | [-1.3%, 7.7%] |
| borders | -1.2% | [-8.2%, 13.4%] | -0.3% | [-5.5%, 11.3%] |
| text_ui | -2.7% | [-3.0%, 6.2%] | -1.4% | [-2.0%, 5.0%] |

**Takeaway:** **Sampled-minimal (`diag_A`) on top of counters is small** (roughly within noise vs the 3% causal gate). The causal gap is **not** explained by turning on sampled draw clocks alone. Per plan guardrails: **next engineering is WP5 (reference fast path) / counters hot path**, not more matrix runs.

---

## Layer 3 — Sampled minimal → GPU / detail / full

### vs `diag_A` (`diagnostic_matrix.vs_diag_A`) — elapsed median_fraction

| Stage | mesh | borders | text_ui |
|-------|-----:|--------:|--------:|
| `diag_B_gpu` | +379% | +288% | +112% |
| `diag_C_detail` | +15.6% | +15.0% | +15.4% |
| `diag_D_cached_detail` | (see archive) | | |
| `diag_E_full` | +380% | +285% | +143% |
| `diag_F_full_cached` | (see archive) | | |

Representative **elapsed** CI95 (mesh): `diag_B_gpu` [~370%, ~403%]; `diag_C_detail` [~6%, ~18%] (full table in archive).

### vs `counters` (`diagnostic_matrix.vs_counters`) — selected elapsed median_fraction

| Stage | mesh | borders | text_ui |
|-------|-----:|--------:|--------:|
| `diag_A` | +3.8% | -1.2% | -2.7% |
| `diag_B_gpu` | +382% | +332% | +111% |
| `diag_C_detail` | +17.4% | +19.1% | +14.2% |
| `diag_E_full` | +404% | +296% | +147% |

**Dominant forensic knob (wall):** **GPU timestamps** (`diag_B_*`, `diag_E_*`, `diag_F_*`) drive **~2–4×** over `diag_A` on mesh/borders and **~2×** on text_ui. **Detail records** add **~15–19%** vs counters. Cached metadata does not materially change detail cost (D ≈ C in archive).

**CPU vs wall:** GPU legs inflate **elapsed** much more than **cpu_ns** on mesh (e.g. `diag_B_gpu` +379% elapsed vs +~6% cpu in vs_diag_A), consistent with GPU wait/poll on the structural harness.

---

## Interpretation guardrails

1. Matrix explains **sampled/forensic** stack cost; it does **not** pass Stage-2 causal gates.
2. **`diag_A vs counters` is small** but **`reference_*` / `counters_*` gates remain large** → prioritize **WP5 / counters hot path** before GPU/detail tuning for Tier-1 admission.
3. GPU/detail work remains **WP3 / post-admission** forensic mitigation, not the current Stage-3 blocker.
4. Do **not** re-run held-out, change 3%/50 µs, or run `run --calibration-only` until training passes offline Tier-1 on a **new** artifact after a fix.

---

## Next code PR (single focus)

**WP5 — reference fast path** (and counters hot-path follow-up).

---

## WP1 closure status

| Step | State |
|------|--------|
| 1. Lock artifact | **Done** — `dac4da4` / `27ab7b86`, `diagnostic_complete`, `training_only` |
| 2. Manifest `diagnostic_archives[]` | **Done** — see `analysis/profiler-overhead-diagnosis-manifest.json` |
| 3. This memo | **Done** (tables from registered capture) |
| 4. Next code PR | **WP5** |
| 5. Re-qualification | After fix PR + new artifact |

**Note:** The capture was written under the parent worktree `~/Projects/MiniProjects/eu4-mac-perf/analysis/evidence/`; the git submodule/nested clone `eu4-mac-perf/eu4-mac-perf/` now holds a copy for version control. Re-run discovery with:

```bash
python3 benchmark/summarize_wp1_diagnosis.py --find
python3 benchmark/summarize_wp1_diagnosis.py --register-latest
```

Do **not** update the Stage-2 rolling pointer or rewrite `00924cb` / `8c71c47` archives.
