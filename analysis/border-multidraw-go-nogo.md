# Border multidraw go/no-go (PR B)

**Date:** 2026-10-04  
**Gate 0:** RUNTIME-ASSERT — [border-gl-multidraw-api-availability.md](border-gl-multidraw-api-availability.md)

## Three ceilings (draw_calls_eliminated / frame)

| Ceiling | Value | Notes |
|---------|------:|-------|
| Structural | ~1,562 | Same-state adjacent pairs / frame (screening) |
| Semantic | ~1,350 | Mode-0 fast-path runs; excludes slow color / mode 1–2 |
| Implementable | ~1,200 | Interpose batching + fail-closed; conservative |

`draws_covered_by_multidraw/frame` ≈ implementable + batch count (~1,201).

## Cadence → gross screen

Paused Venice v3.1 readiness: **55.1 swaps/s** ([evidence](evidence/gfx-subrecord-multi-hypothesis-smoke-20261004T152246Z.json)).

```text
implementable_draw_calls_eliminated/s ≈ 1,200 × 55.1 ≈ 66,100/s
gross_avoided_submission_ms/s ≈ 66,100 × 0.65 µs ≈ 43.0 ms/s
```

| Calls eliminated/frame | @ 55 swaps/s |
|------------------------|-------------:|
| 1,200 | 66k/s |
| 1,400 | 77k/s |
| 1,562 | 86k/s |

```text
batch_count/s (static estimate): ~55 (one dominant 943-run batch per swap plus fragments)
mean_batch_size: ~400–943 (mode-0 dominated)
median / min / max: 50 / 2 / 943 (screening-informed)

qualitative additional cost:
  gather: negligible
  runtime classification: low (return-address filter)
  multidraw fixed overhead: unknown until PR C

verdict: strong prototype candidate (GO-B borderline GO-A on cadence sensitivity)
```

## Decision

| Rule | Result |
|------|--------|
| GO-A (≥85k/s) | **Marginal** at 55 swaps/s unless implementable ≥1,545/frame |
| GO-B (40–85k/s, simple impl) | **YES** — interpose + linear gather; exceptional simplicity |
| Hard gates 1–4 | **PASS** (mode-0 subset) |

**Proceed to PR C** with fail-closed runtime API assert and ABABA (`B−A` primary).

Net ≥5% CPU **not** claimed from this document.
