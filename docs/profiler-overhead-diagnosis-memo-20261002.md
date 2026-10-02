# WP1 profiler overhead diagnosis memo (2026-10-02)

**Status:** **Authoritative WP1 closure** on policy **`diag_matrix_diag_counters_baseline_v3`** (`diag_counters` telemetry path). Historical **`dac4da4` / v2** retained for forensic `vs_diag_A` only; **`vs_counters` on v2 is invalid**.

---

## Authoritative WP1 source (v3)

Sanitized **committed copy** of the Mac immutable capture (measurement payload unchanged; path strings may differ).

| Field | Value |
|-------|--------|
| Archive (committed) | `analysis/evidence/frame-model-offline-9a063f0-20261002T220742.210844Z-129e5bf5.json` |
| `evidence_id` | `20261002T220742.210844Z-129e5bf5` |
| `source_archive_sha256_mac_capture` (pre-sanitize emit) | `a5198332167edf4198c1d349e170400b2788513b91cf8c140717278d68eb75b9` |
| `archive_sha256_committed` (post–path-sanitize) | `ce3198535c1944e8b3825f87f4f82b5e6a4aef02fe5a107893ae7c59bf26d6ae` |
| `git_commit` | `9a063f09a26918011ea34c98b156954d30b6c454` |
| `validation_scope` | `training_only` |
| `status` | `diagnostic_complete` |

Frozen Stage-2 baselines (unchanged): `00924cb`, `8c71c47` — see manifest.

### Historical v2 capture (`dac4da4` / `27ab7b86`)

Listed under `diagnostic_archives_historical[]`. **Usable:** Tier-1 causal block, **`vs_diag_A`**. **Invalid:** `vs_counters`, Layer-2 conclusions. Mac capture SHA `0e87924d…`; committed sanitized copy `9e1c6689…`.

---

## Layer 1 — Stage-2 causal gates (unchanged blocker)

From **v3** capture. **median_fraction** on `elapsed_ns`; Tier-1 **≤ 3%** → **failed**.

| Recipe | `reference_elapsed_ns` | `counters_elapsed_ns` |
|--------|------------------------|------------------------|
| mesh | +5.7% | +6.7% |
| borders | ~0% | +6.5% |
| text_ui | +7.6% | +3.5% |

Stage-3 remains blocked on **offline causal admission** for reference/counters.

---

## Layer 2 — `diag_A` vs `diag_counters` (v3 paired baseline)

**median_fraction**; 95% CI on same axis. This is **not** a Tier-1 equivalence test; wide CIs do not establish “small” under ±3%.

| Recipe | elapsed median | elapsed CI95 | cpu median | cpu CI95 |
|--------|-------------:|--------------|-----------:|---------|
| mesh | +4.4% | [0.5%, 7.6%] | +4.4% | [0.6%, 7.4%] |
| borders | +5.3% | [3.4%, 9.0%] | +5.3% | [3.8%, 8.7%] |
| text_ui | +6.6% | [-0.6%, 9.0%] | +6.6% | [-0.8%, 9.0%] |

**Takeaway:** Sampled-minimal clocks on top of **matrix** counters add roughly **+4–7%** point estimates with **material uncertainty**. That does **not** explain the full **reference/counters** causal gap (~3.5–7.6% on this run). Per guardrails: prioritize **WP5 reference fast path / counters hot path**; do not treat Layer 2 as proof that sampled clocks are negligible for Tier-1.

---

## Layer 3 — Forensic decomposition (`vs_diag_A`, v3)

Wall **elapsed** median_fraction vs `diag_A`:

| Stage | mesh | borders | text_ui |
|-------|-----:|--------:|--------:|
| `diag_B_gpu` | +377% | +275% | +122% |
| `diag_C_detail` | +14.1% | +14.7% | +17.7% |
| `diag_E_full` | (see archive) | | |

**GPU timestamps** dominate wall overhead vs `diag_A`; **detail records** ~14–18%. **Cached metadata:** no large wall reduction is visible when cached mode is **requested** (D vs C in archive); cache hit/fallback is not metered.

Mesh `diag_B_gpu` **cpu_ns** vs `diag_A` ~**+9%** (qualitative wall ≫ CPU unchanged).

---

## Next code PR

**WP5 — reference fast path** (counters hot-path follow-up).

---

## WP1 closure status

| Step | State |
|------|--------|
| 1. Authoritative v3 artifact | **Done** (`9a063f0` / `129e5bf5`) |
| 2. Manifest `diagnostic_archives[]` | **Done** |
| 3. This memo | **Done** |
| 4. Next code PR | **WP5** |
| 5. Re-qualification | After fix PR + new Tier-1 artifact |

Do **not** update the Stage-2 rolling pointer or rewrite `00924cb` / `8c71c47` archives.
