# Profiler overhead diagnosis (post–Stage 2)

**Status:** in progress on `profiler-overhead-calibration`.  
**Prerequisite:** [Stage 2 validation outcome](stage-2-validation-outcome.md) — Tier-1 v2 **failed**; Stage 3 **blocked**.

## Goal

Explain and reduce **REFERENCE vs bare** and **counters vs REFERENCE** overhead on **training recipes** (`mesh`, `borders`, `text_ui`) using controlled diagnostics. Do **not** change held-out fixture, 3%/50 µs policy, or re-time held-out until training recipes pass offline causal admission on a **new** artifact.

## Constraints

| Rule | Why |
|------|-----|
| No held-out re-run for tuning | Anti-peeking |
| No `run --calibration-only` / live Tier 2 until offline causal admission passes | Roadmap Stage 3 |
| A–F diagnostic matrix is non-acceptance | Roadmap Stage 2 |
| Interposer/probe changes → new artifact hash → re-admission before live work | Stages 4–5 |

## Frozen comparison (do not overwrite)

| Role | Archive |
|------|---------|
| Primary failed Tier-1 v2 | `analysis/evidence/frame-model-offline-00924cb-20261002T195901.482894Z-f6b27087.json` |
| Replication | `analysis/evidence/frame-model-offline-8c71c47-20261002T203853.173597Z-2fff669c.json` |

Index: [`analysis/stage2-validation-manifest.json`](../analysis/stage2-validation-manifest.json).

## Work packages (execution order)

Technical detail: [`analysis/frame-model-next-iteration-plan.md`](../analysis/frame-model-next-iteration-plan.md).

| WP | Focus | Deliverable |
|----|--------|-------------|
| **0** | Branch + docs + CI | This plan; portable green |
| **1** | A–F decomposition (P0) | Mac archives via `diagnostic-matrix` subcommand; diagnosis memo |
| **2** | Draw path / pointers (P1) | Schema-2 manifest; parallel to WP1 |
| **3** | Causal vs forensic split | Gate tests; capture-free calibration |
| **5** | Reference fast path | After WP1 identifies reference cost |
| **4** | Calibration admission policy | After WP1 evidence; no bypass of failed Stage-2 |
| **6** | Reports / docs | Ongoing |

### WP1 — A–F matrix (training only)

Conditions (baseline `diag_A`):

| Stage | GPU timestamps | Forensic GL records | Cached metadata |
|-------|----------------|---------------------|-----------------|
| `diag_A` | off | off | off |
| `diag_B_gpu` | on | off | off |
| `diag_C_detail` | off | on | off |
| `diag_D_cached_detail` | off | on | on |
| `diag_E_full` | on | on | off |
| `diag_F_full_cached` | on | on | on |

Harness env: `EU4_TEST_GPU_TIMESTAMPS`, `EU4_TEST_FORENSIC_RECORDS`, `EU4_TEST_CACHED_METADATA` (see `benchmark/eu4_frame_model.py`).

**Mac capture:**

```bash
python3 benchmark/eu4_frame_model.py diagnostic-matrix
```

Writes immutable evidence under `analysis/evidence/` with `purpose: profiler_overhead_diagnosis_v1`. Does **not** require held-out; does **not** exit non-zero solely because causal gates still fail.

Register new archives in [`analysis/profiler-overhead-diagnosis-manifest.json`](../analysis/profiler-overhead-diagnosis-manifest.json).

## Re-qualification → Stage 3

1. Portable CI + native tests on new artifact.
2. Offline preflight / Tier-1 v2 on **training** recipes — all four causal gates per recipe.
3. If (2) passes: **one** new held-out timing (fixture unchanged).
4. Update manifest; do not rewrite `00924cb` / `8c71c47` archives.

Stage 3 ([profiler-roadmap.md](profiler-roadmap.md)) unlocks only when `offline_causal_admission` passes on qualified held-out + training.

## Explicit non-goals

- Stage 3 live calibration, Stage 4 hook loops, Metal prioritization from static analysis alone, policy revision from A–F alone.
