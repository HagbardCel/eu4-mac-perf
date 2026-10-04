# Gfx multi-hypothesis observer smoke (protocol v3.1 / kind v2) — complete, gates passed

**Run:** `20261004T152246Z-submission-experiment`  
**Git:** `main` @ `3b49bf5` (PR #34 merged)  
**Command:** `python3 benchmark/submission_experiment.py --output results --multi-hypothesis-smoke`

## Outcome

| Gate | Status |
|------|--------|
| Harness | **Complete** (A–B–A, 10 s phases) |
| `multi_hypothesis_gate` | **passed** |
| `observer_scene_gate` | **passed** |
| `engagement_gate` | passed (v3 multi-hypothesis) |
| `stage2_recommended` | **false** |

B1 frozen-bank harness OK; `effective_actions_delta` = 0. Schema v2 counters; experiment kind `submission_observer_multi_hypothesis_v2`.

## B1 candidate bank (absolute ARM→FREEZE)

| Metric | Value |
|--------|------:|
| `candidate_swaps` | 555 |
| `candidate_renderbuckets_invocations` | 1,110 |
| `candidate_nonempty_renderbuckets` | 555 |
| `candidate_site_entries` | 1,345,965 |
| `adjacent_within_invocation` | 1,345,410 |
| `same_parent_pairs` | 54,945 |
| `cross_parent_pairs` | 1,290,465 |
| `same_parent_buffer_signature` hits | 0 |
| `cross_parent_buffer_signature` hits | 0 |
| `legacy_eligible_pair_hits` | 0 |

Derived: `sites_per_nonempty_renderbuckets` ≈ 2425; `candidate_renderbuckets_invocations` / swaps = 2.0; `nonempty_fraction` = 0.5.

Pair partition and site/nonempty identities: **exact** (no invariant errors).

## Hypothesis summary

- Same-parent buffer signature: **0 / 54,945**
- Cross-parent buffer signature: **0 / 1,290,465**
- Same subrecord pointer cross-parent: **0 / 1,290,465**

Evidence: [analysis/evidence/gfx-subrecord-multi-hypothesis-smoke-20261004T152246Z.json](../../analysis/evidence/gfx-subrecord-multi-hypothesis-smoke-20261004T152246Z.json)
