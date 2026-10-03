# Stage 2 validation outcome (Tier-1 v2)

**Status:** no-peeking experiment **complete**; Stage-2 **success checkpoint not passed**; **Stage 3 blocked**.

## What passed (methodology)

- Held-out fixture frozen at **`00924cb`** before first timing (`terrain_odd_ordinal_within_frame_v1`).
- Fixture SHA **`dbb0af0e7964777b7ab9f0fecde068692377719bde1670c2a082ecbb26281503`** (not the exploratory denylisted SHA).
- Qualified held-out provenance on both Mac runs (`stage2-split-v2` / `tier1_causal_rel3pct_abs50us_v2`).

## Scientific result

**`offline_causal_admission`: failed** on all training recipes and qualified held-out under frozen 3% / 50 µs/frame.

Do **not** change fixture, thresholds, or policy based on this outcome. Next work is [profiler overhead diagnosis](profiler-overhead-diagnosis-plan.md) (REFERENCE + counters on training workloads), not Stage 3 calibration-only.

## Canonical archives

| Role | Git @ run | Immutable archive |
|------|-----------|-------------------|
| **Primary no-peeking validation** | `00924cb` | `analysis/evidence/frame-model-offline-00924cb-20261002T195901.482894Z-f6b27087.json` |
| **Same-policy replication** | `8c71c47` | `analysis/evidence/frame-model-offline-8c71c47-20261002T203853.173597Z-2fff669c.json` |

Machine-readable index: [`analysis/stage2-validation-manifest.json`](../analysis/stage2-validation-manifest.json).

The rolling pointer [`frame-model-offline-evidence.json`](../analysis/frame-model-offline-evidence.json) summarizes the **replication** run (latest). Use the primary archive for the first independent held-out measurement.

## Infrastructure vs checkpoint

| Item | State |
|------|--------|
| Stage-2 gate split + Tier-1 v2 evaluator | **Complete** (PR #3) |
| No-peeking held-out validation executed | **Complete** (this PR) |
| Tier-1 admission passed | **No** |
| Stage 3 (`offline_causal_admission` prerequisite) | **Blocked** |
