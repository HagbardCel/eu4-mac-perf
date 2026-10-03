# Tier-1 v4 — statistical replication (frozen)

**Status:** Frozen in code on `main` (WP11). Informed by authoritative WP10 **`d07347df`** and failed v3 requalification **`a73ea57b`**. Capture: `docs/wp11-tier1-v4-requalification.md`.

## Motivation

WP10 rejected counters-lite/deferred-flush CPU optimizations and showed:

- mesh counters overhead ~**9.5 µs/frame** is stable across WP8/WP9/WP10 medians;
- v3 mesh **fail** on `a73ea57b` aligns with **N=7** bootstrap upper bound crossing 11 µs, not a shift in true cost;
- text_ui completion wall at **21×40f** yields CIs wholly inside ±5%; **7×400f** is weaker.

v4 should increase **replication**, not change hybrid floors or implementation.

## Frozen contract (v4)

Unchanged from v3 unless noted:

| Element | v3 | v4 (proposed) |
|---------|----|----------------|
| Policy identity | `tier1_causal_steady_state_hybrid_v3` | **`tier1_causal_steady_state_hybrid_v4`** |
| REFERENCE hybrid floor | 6 µs / 3% cap | **same** |
| Counters hybrid floor | 11 µs / 12% cap | **same** |
| Completion wall band | ±5% bootstrap CI | **same** |
| Post-arm prime | 1 | **same** |
| CPU measured frames | 4 (`four_frame_post_arm_prime_1`) | **same** |
| Wall measured frames | 40 (`forty_frame_post_arm_prime_1`) | **same** |
| Training recipes | mesh, borders, text_ui | **same** |
| **Paired trials per recipe** | **7** | **21** (uniform — no recipe-specific N) |
| Held-out | blocked until training requalification passes | **same** |

Drop WP10-style 400-frame completion experiments from qualification; WP10 found no advantage over 21×40f.

## Process

```text
frozen v4 evaluator + 21-trial harness (WP11 CLI)
    ↓
ONE clean-tree training requalification (`wp11-tier1-v4-requalification`)
    ↓
pass? → held-out under frozen v4
fail? → stop qualification work; profiler remains diagnostic; return to EU IV
```

**Do not** tune 6/11 µs thresholds using WP10 or pooled diagnostics.

## Non-goals

- Production counters-lite without a new engineering program and evidence.
- Replaying v3 archives as v4 passes.
- Held-out before a v4 training requalification pass.
