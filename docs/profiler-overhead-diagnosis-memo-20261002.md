# WP1 profiler overhead diagnosis memo (2026-10-02)

**Status:** analysis complete for embedded A–F matrix; **dedicated `diagnostic-matrix` immutable archive not yet registered** (see [Closure](#wp1-closure-status)).

**Sources (read-only; do not rewrite):**

| Role | Archive |
|------|---------|
| Primary Stage-2 failed Tier-1 v2 | `analysis/evidence/frame-model-offline-00924cb-20261002T195901.482894Z-f6b27087.json` |
| Replication | `analysis/evidence/frame-model-offline-8c71c47-20261002T203853.173597Z-2fff669c.json` |

Both archives embed the same interleaved A–F diagnostic trials under `preflight.representative_workloads.recipes[]`, captured at commits `00924cb` and `8c71c47` respectively. They predate the PR #6 harness change that adds **`counters` inside each diagnostic trial** and publishes `diagnostic_matrix.vs_counters` plus paired **`diag_A` vs `counters`**. Until a Mac `python3 benchmark/eu4_frame_model.py diagnostic-matrix` artifact is registered, **`vs_counters` / sampled-on-counters decomposition is not available in-repo**.

---

## Layer 1 — Stage-2 causal gates (unchanged blocker)

Comparisons vs **bare** (`reference_*`) and vs **reference** (`counters_*`). Values are **median_fraction** on `elapsed_ns`; 95% CI on the same axis. Tier-1 policy: **≤ 3%** → all **failed** (expected for this phase).

### Primary (`00924cb`)

| Recipe | Gate | median_fraction | confidence_interval_95 | status |
|--------|------|-----------------|-------------------------|--------|
| mesh | `reference_elapsed_ns` | +10.0% | [7.6%, 14.3%] | failed |
| mesh | `counters_elapsed_ns` | +13.1% | [8.7%, 14.1%] | failed |
| borders | `reference_elapsed_ns` | +2.6% | [-1.2%, 7.1%] | failed |
| borders | `counters_elapsed_ns` | +12.8% | [10.0%, 15.3%] | failed |
| text_ui | `reference_elapsed_ns` | +13.5% | [10.6%, 16.4%] | failed |
| text_ui | `counters_elapsed_ns` | +12.4% | [4.8%, 18.0%] | failed |

Forensic suitability (informational, same archive): `sampled_elapsed_ns` vs reference remains **+247% to +476%** across training recipes — far above the 5% forensic gate. That confirms the **full sampled/forensic stack** is not admission-ready, independent of the matrix.

### Replication (`8c71c47`)

Same qualitative story: **reference** and **counters** wall overhead sit roughly **+6% to +13%** per recipe (all failed vs 3%). **Sampled** vs reference stays **+199% to +470%**. Replication does not overturn the primary causal failure.

**Takeaway:** Stage-3 remains blocked on **offline causal admission** for reference/counters on training recipes. A strong A–F story does not remove this gate.

---

## Layer 2 — Counters → sampled minimal (`diag_A` vs `counters`)

**Not present** in the frozen Stage-2 archives: diagnostic trials list only `diag_A` … `diag_F_full_cached` (no `counters` stage in the same trial block). The hardened harness records this as `diagnostic_matrix.vs_counters["diag_A"]` (and siblings) in a dedicated `profiler_overhead_diagnosis_v1` capture.

**Interim inference (weak):** causal `counters_elapsed_ns` (+10–13% vs reference) is modest relative to `sampled_elapsed_ns` (+200–470% vs reference), which strongly suggests **most forensic wall cost lives above the counters baseline**, but the paired **diag_A vs counters** median required by the plan must come from a new `diagnostic-matrix` run.

---

## Layer 3 — Sampled minimal → GPU / detail / full (`vs_diag_A`)

Paired within the same diagnostic trial block. **median_fraction** on `elapsed_ns` and `cpu_ns` vs **`diag_A`** (sampled-minimal: sampled draw clocks on, GPU timestamps off, forensic records off, cached metadata off). Policy: **non-acceptance** diagnostics only.

### Primary (`00924cb`) — `elapsed_ns` / `cpu_ns` median_fraction

| Stage | mesh elapsed | mesh cpu | borders elapsed | borders cpu | text_ui elapsed | text_ui cpu |
|-------|-------------:|---------:|----------------:|------------:|----------------:|------------:|
| `diag_B_gpu` | +397% | +5.6% | +309% | +59.7% | +223% | +133% |
| `diag_C_detail` | +16.3% | +16.4% | +23.9% | +24.1% | +19.1% | +19.1% |
| `diag_D_cached_detail` | +15.4% | +15.4% | +24.5% | +24.4% | +15.0% | +15.0% |
| `diag_E_full` | +399% | +20.6% | +317% | +65.8% | +176% | +152% |
| `diag_F_full_cached` | +403% | +18.1% | +326% | +73.4% | +245% | +145% |

95% CIs (elapsed, mesh): `diag_B_gpu` [370%, 403%]; `diag_C_detail` [6.6%, 17.5%]; `diag_E_full` [373%, 406%].

### Replication (`8c71c47`) — same columns (elapsed)

| Stage | mesh | borders | text_ui |
|-------|-----:|--------:|--------:|
| `diag_B_gpu` | +403% | +315% | +201% |
| `diag_C_detail` | +16.1% | +21.3% | +23.2% |
| `diag_D_cached_detail` | +15.2% | +19.0% | +23.5% |
| `diag_E_full` | +410% | +337% | +208% |
| `diag_F_full_cached` | +413% | +336% | +192% |

**Dominant forensic knob (wall `elapsed_ns`):** enabling **GPU timestamps** (`diag_B_gpu`, and again in `diag_E_full` / `diag_F_full_cached`) drives **~3–4×** wall time over `diag_A` on mesh/borders and **~2–2.5×** on text_ui. **Detail forensic records** (`diag_C_*`) add a stable **~15–25%** on both wall and CPU vs `diag_A`. **Cached metadata** does not materially change detail cost (D vs C).

**CPU vs wall:** GPU-timestamp legs inflate **elapsed** far more than **cpu_ns** on mesh (e.g. `diag_B_gpu` +397% elapsed vs +5.6% cpu), consistent with **GPU-side waiting / availability polling** on the structural harness rather than pure CPU accounting.

---

## Interpretation guardrails

1. The matrix explains **sampled/forensic stack** cost (especially GPU timestamps + detail records). It does **not** pass Stage-2 causal gates by itself.
2. **Reference/counters** failures (~10–13%) persist in both primary and replication while `diag_A` forensic legs are isolated. Even after a future `diag_A vs counters` capture, if that pair is **small** but `reference_*` / `counters_*` gates remain **large**, the next engineering focus is **WP5 (reference fast path) / counters hot path**, not more matrix runs.
3. If **`diag_A vs counters`** (once measured) is **large**, prioritize **sampled-clock / minimal sampled path** work before tuning GPU/detail knobs.
4. Do **not** re-run held-out, change 3%/50 µs policy, or run `run --calibration-only` until training passes offline Tier-1 on a **new** artifact after a fix.

---

## Next code PR (single focus)

**Choose: WP5 — reference fast path** (and counters hot-path follow-up in the same vein).

**Rationale:** Training recipes still fail **reference** and **counters** causal gates at ~6–13% with tight replication, while the matrix localizes **forensic** inflation to GPU timestamps and detail records on top of `diag_A`. Fixing forensic knobs first does not address the Tier-1 blocker; WP5 targets the remaining **reference/counters** gap. GPU-timestamp mitigation remains **WP3 / matrix-driven forensic work** after causal admission improves.

**Explicitly not next:** held-out re-timing, policy changes, `run --calibration-only`, or WP2 draw-path completeness (matrix already explains overhead; completeness is not the current gate).

---

## WP1 closure status

| Step | State |
|------|--------|
| 1. Lock artifact (`purpose: profiler_overhead_diagnosis_v1`, `status: diagnostic_complete`, `validation_scope: training_only`) | **Pending Mac capture** — cloud agent cannot run workloads without `results/.../trace.bin` (gitignored local trace). |
| 2. Register `analysis/profiler-overhead-diagnosis-manifest.json` → `diagnostic_archives[]` | **Empty** — run `python3 benchmark/summarize_wp1_diagnosis.py --register …` after capture. |
| 3. This memo | **Done** (interim tables from frozen Stage-2 embedded matrix). |
| 4. Next code PR | **WP5** (above). |
| 5. Re-qualification | After fix PR + new artifact only. |

**Mac capture (unchanged):**

```bash
python3 benchmark/eu4_frame_model.py diagnostic-matrix | tee /tmp/wp1-diagnostic.json
python3 benchmark/sanitize_paths.py analysis/evidence/<archive-from-immutable_evidence>.json
python3 benchmark/summarize_wp1_diagnosis.py --register analysis/evidence/<archive>.json
git add analysis/evidence/<archive>.json analysis/profiler-overhead-diagnosis-manifest.json
```

Do **not** update the Stage-2 rolling pointer or rewrite `00924cb` / `8c71c47` archives.
