# Gfx multi-hypothesis observer smoke (protocol v3) — complete, gates passed

**Run:** `20261004T131931Z-submission-experiment`  
**Git:** `main` @ `e593044` (PR #33 merged)  
**Command:** `python3 benchmark/submission_experiment.py --output results --multi-hypothesis-smoke`

## Outcome

| Gate | Status |
|------|--------|
| Harness | **Complete** (A–B–A, 10 s phases) |
| `multi_hypothesis_gate` | **passed** |
| `observer_scene_gate` | **passed** |
| `engagement_gate` | passed (v3 multi-hypothesis) |
| `observer_gate` | skipped (v3) |
| `stage2_recommended` | **false** |

All phases passed **absolute scene** checks. A1/A2 reference health: site + renderbuckets traffic, zero effective actions. B1: control passed, frozen-bank harness OK, `effective_actions_delta` = 0.

## B1 candidate bank (absolute ARM→FREEZE)

| Metric | Value |
|--------|------:|
| `candidate_swaps` | 564 |
| `adjacent_draw_pairs_total` | 55,836 |
| `same_parent_pairs` | 55,836 |
| `cross_parent_pairs` | 0 |
| `same_parent_buffer_signature` hits | 0 |
| `cross_parent_buffer_signature` | not exercised (0 cross-parent pairs) |
| `legacy_eligible_pair_hits` | 0 |

Pair partition and site/nonempty identities: **exact** (no invariant errors).

## Per-phase metrics (median)

| Phase | Swaps/s | EU IV CPU ms/s | Combined W |
|-------|--------:|---------------:|-----------:|
| a1 | 55.9 | 1060.3 | 9.60 |
| b1 | 55.9 | 1060.3 | 9.60 |
| a2 | 55.5 | 1142.0 | 9.80 |

Readiness: `swap_rate` 56.1, `cpu_ms_s` 1055.7.

## Interpretation

Two-hook v3 observer is **healthy** on paused Venice. Same-parent adjacent buffer signatures show **no recurrence** (0 / 55,836 pairs). **No cross-parent adjacent pairs** on this fixture window, so cross-parent structural hypotheses were **not exercised**. Safety-qualified mask is zero — **no Stage-2 recommendation**.

Evidence: [analysis/evidence/gfx-subrecord-multi-hypothesis-smoke-20261004T131931Z.json](../../analysis/evidence/gfx-subrecord-multi-hypothesis-smoke-20261004T131931Z.json)
