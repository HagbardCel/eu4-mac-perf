# Gfx subrecord observer smoke (capability 1) — complete, engagement failed

**Run:** `20261004T112503Z-submission-experiment`  
**Git:** `main` @ `45b44fc` (post summarize floor fix)  
**Command:** `python3 benchmark/submission_experiment.py --output results --engagement-smoke`

## Outcome

| Gate | Status |
|------|--------|
| Harness | **Complete** (A–B–A, 10 s phases) |
| `observer_gate` | **failed** |
| `engagement_gate` | **failed** |
| `pareto_gate` | skipped (capability 1) |

All three phases passed **absolute scene** checks. Reference phases passed **control** (site traffic, zero hits/actions). **B1 failed control:** `eligible_pair_hits_delta` = 0.

## Per-phase control (deltas)

| Phase | Role | Site entries Δ | Pair hits Δ | Actions Δ | Control |
|-------|------|----------------:|------------:|----------:|---------|
| a1 | reference | 1,391,003 | 0 | 0 | passed |
| b1 | candidate | 1,355,860 | 0 | 0 | **failed** |
| a2 | reference | 1,355,280 | 0 | 0 | passed |

Failure reason (b1): observer candidate requires mesh site entries and pair hits without mutation.

## Per-phase metrics (median)

| Phase | Swaps/s | EU IV CPU ms/s | Combined W |
|-------|--------:|---------------:|-----------:|
| a1 | 57.7 | 1180.5 | 10.18 |
| b1 | 55.7 | 1140.1 | 9.64 |
| a2 | 55.5 | 1142.0 | 9.80 |

Readiness: `swap_rate` 55.6, `cpu_ms_s` 1138.0.

## Interpretation

The mesh hook at `0x14c81e6` is **live** (`candidate_site_entries` ≫ 0 every phase). On the pinned paused Venice fixture, **`buffer_bind_elision_eligible` never matched** (`eligible_pair_hits` stayed 0 in candidate mode). That is a **negative ROI signal** for buffer-bind-only mutation on this scene: the observer path works, but the predicate does not surface removable work here.

Registered evidence: [`analysis/evidence/gfx-subrecord-observer-smoke-20261004T112503Z.json`](../../analysis/evidence/gfx-subrecord-observer-smoke-20261004T112503Z.json).
