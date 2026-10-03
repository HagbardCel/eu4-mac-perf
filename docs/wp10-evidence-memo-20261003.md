# WP10 evidence memo — `d07347df` @ `a8200ea`

Authoritative diagnostic archive: `20261003T155416.972681Z-d07347df` (`bounded_remediation_archives`). **Not** Tier-1 admission. v3 requalification **`a73ea57b` remains failed**; held-out blocked.

## Integrity

Committed SHA-256 `7cdf17ea82988deaa37ccf761f474be30747295e7ffb62ac10ff16fadf303059` matches manifest. Clean Git identity, stable executed artifacts, `darwin/arm64`, expected WP10 contract.

## Mesh — counters-lite hypothesis **rejected**

Pre-specified engineering target: counters-lite minus reference **≤ 5 µs/frame**. **Not met.**

Medians vs reference (7 paired trials, prime=1, 4 frames):

| Stage | Median µs/frame | Bootstrap 95% CI |
|-------|----------------:|------------------|
| Full counters | 9.54 | 8.33–10.86 |
| Counters-lite | 9.60 | 7.40–10.44 |
| Lite + deferred flush | 11.02 | 7.69–13.28 |

The three “vs reference” medians are **separate** estimators. **Direct paired contrasts** (same trial, ns/frame) are the correct read for lite vs full vs deferred:

| Contrast | Median µs/frame | Bootstrap 95% CI |
|----------|----------------:|------------------|
| lite − full counters | +1.21 | −3.47 to +1.48 |
| deferred − lite | −0.15 | −2.34 to +5.88 |
| deferred − full | +0.39 | −0.86 to +2.42 |

All intervals span zero. **Neither scope-tree removal nor deferring per-frame `flush_counters()` produced a measurable CPU win.** Do not productionize counters-lite from this evidence.

### Cross-capture mesh counters stability (diagnostic only)

Primed four-frame mesh counters minus reference (training captures only):

| Capture | Median µs/frame | 95% CI | Note |
|---------|----------------:|--------|------|
| WP8 `3c1e0db7` | 9.37 | 6.92–10.24 | methodology diagnostic |
| WP9 `a73ea57b` | 9.63 | 7.14–**11.35** | v3 **fail** (CI upper > 11 µs) |
| WP10 `d07347df` | 9.54 | 8.33–10.86 | pass-like vs 11 µs floor |

Medians differ by ~0.26 µs/frame across three independent runs. **Pooled 21 pairs (WP8+WP9+WP10 mesh trials)** — not an admission replay: median **9.54 µs/frame**, CI **8.55–10.32**. This does **not** overturn `a73ea57b`; it suggests the v3 mesh failure may reflect **N=7 bootstrap instability** around a ~9.5 µs true overhead, not a new counters cost regime.

## text_ui completion wall — favors **more trials**, not longer windows

Raw completion totals still show **bimodal regimes** (~19–20 ms vs ~28–29 ms at 40f; ~182 ms vs ~191 ms at 400f). Longer windows do not remove environmental switching; **more independent 40-frame trials** tighten the median CI despite outliers.

**21 × 40-frame** (v3-style ±5% CI on signed fraction):

| Gate | Median fraction | 95% CI |
|------|----------------:|--------|
| reference submission+d drain vs bare | +0.67% | +0.24% to +3.09% |
| counters submission+d drain vs reference | +1.16% | +0.84% to +3.53% |

Both CIs lie wholly inside **[-5%, +5%]** (would pass v3 wall tri-state).

**7 × 400-frame**: intervals still inside ±5% but much wider and boundary-near — **less precise**, not more reassuring.

## WP10 conclusions

1. **Stop profiler implementation optimization** for qualification (no counters-lite production path).
2. **Do not** reinterpret v3 thresholds (6 / 11 µs) from WP10.
3. Next qualification iteration should be **statistical** (trial count), not architectural — see [`tier1-v4-statistical-replication-proposal.md`](tier1-v4-statistical-replication-proposal.md).
