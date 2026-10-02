# WP1 profiler overhead diagnosis memo (2026-10-02)

**Status:** **Partial closure** — forensic **B–F vs `diag_A`** and Tier-1 causal blocks are usable from the historical `dac4da4` capture; **`vs_counters` / Layer 2 are invalid** (log-path collision at policy v2). **Authoritative WP1 registration** awaits a **v3** `diagnostic-matrix` remeasurement after the `diag_counters` fix.

---

## Evidence inventory

| Role | Archive | Usable for |
|------|---------|------------|
| Stage-2 primary (frozen) | `00924cb` … `f6b27087.json` | Tier-1 causal baseline only |
| Stage-2 replication (frozen) | `8c71c47` … `2fff669c.json` | Tier-1 causal baseline only |
| Historical WP1 attempt | `dac4da4` … `27ab7b86.json` | Causal gates, **`vs_diag_A`**; **not** `vs_counters` |

### Provenance (`dac4da4` / `27ab7b86`)

| | SHA-256 |
|--|---------|
| Mac capture (parent worktree, pre-sanitize) | `0e87924dcb56795d5a2d42accefc0e661b41c042b477a624bd9b5de23a4aa27d` |
| Committed copy in this repo (path-sanitized) | `9e1c6689fcd23848aa38eb2fe770c1f4812bbcfe762a37edf73442c6fd1edfba` |

The committed file is a **derived copy** (sanitize_paths), not the raw Mac bytes. Manifest records both hashes under `diagnostic_archives_historical[]`.

**Policy:** `diag_matrix_counters_baseline_v2` — diagnostic trials reused `mesh-{trial}-counters.csv` after Tier-1 `counters`, so interposer `O_EXCL` failed and Python read **stale Tier-1 telemetry** while timing came from a second subprocess. See manifest `matrix_validity`.

---

## Layer 1 — Stage-2 causal gates (unchanged blocker)

From historical WP1 capture (`dac4da4`). **median_fraction** on `elapsed_ns`; Tier-1 **≤ 3%** → **failed** (expected).

| Recipe | `reference_elapsed_ns` | `counters_elapsed_ns` |
|--------|------------------------|------------------------|
| mesh | +12.4% | +10.3% |
| borders | +3.6% | +10.3% |
| text_ui | +2.1% | +13.5% |

**Takeaway:** Stage-3 remains blocked on **reference/counters** causal admission. Tier-1 failure does not invalidate the forensic matrix vs `diag_A`.

---

## Layer 2 — `diag_A` vs counters baseline

**Invalid for `dac4da4` — do not use.** Remeasure with policy **`diag_matrix_diag_counters_baseline_v3`** (`diag_counters` telemetry path). No equivalence/smallness claim is supported from the v2 capture.

---

## Layer 3 — Sampled minimal → GPU / detail / full (`vs_diag_A`)

**Usable** from `dac4da4` (unique log paths per stage). Wall **elapsed** median_fraction vs `diag_A`:

| Stage | mesh | borders | text_ui |
|-------|-----:|--------:|--------:|
| `diag_B_gpu` | +379% | +288% | +112% |
| `diag_C_detail` | +15.6% | +15.0% | +15.4% |
| `diag_D_cached_detail` | +13.0% | +12.8% | +15.9% |
| `diag_E_full` | +380% | +285% | +143% |
| `diag_F_full_cached` | +389% | +317% | +168% |

**CPU vs wall (mesh `diag_B_gpu`):** ~**+9.0%** `cpu_ns` vs `diag_A` (not ~6%); wall inflation remains far larger than CPU, consistent with GPU wait/poll on the structural harness.

**Cached metadata:** D vs C shows no large wall reduction when cached mode is **requested**; hit/fallback behavior is not yet metered — state as “no large reduction visible when cached-metadata mode is requested,” not as proven cache effectiveness.

**Forensic conclusion (credible without `vs_counters`):** **GPU timestamp machinery dominates** synthetic sampled/forensic wall overhead; **detail GL records** add a smaller but material ~13–16% vs `diag_A`.

---

## Next code PR (single focus)

**WP5 — reference fast path** (counters hot path follow-up). Layer 2 does not contradict this: causal reference/counters gates remain ~10–13% while forensic GPU cost is a separate stack.

---

## WP1 closure status

| Step | State |
|------|--------|
| 1. Lock authoritative v3 artifact | **Pending** — rerun `diagnostic-matrix` after `diag_counters` fix |
| 2. Manifest `diagnostic_archives[]` | **Empty** — `dac4da4` in `diagnostic_archives_historical` with validity flags |
| 3. This memo | **Updated** — partial validity explicit |
| 4. Next code PR | **WP5** (after v3 capture or in parallel with remeasure) |
| 5. Re-qualification | After fix PR + new Tier-1 artifact |

```bash
python3 benchmark/eu4_frame_model.py diagnostic-matrix --registry-json
python3 benchmark/summarize_wp1_diagnosis.py --register-latest
```

Do **not** update the Stage-2 rolling pointer or rewrite `00924cb` / `8c71c47` archives.
