# Border track wind-down (GOG EU IV 1.37.5, Venice fixture)

**Status:** reverse engineering complete; **mutation work deprioritized**. Further rendering investment follows [`eu4_recommended_strategy.md`](eu4_recommended_strategy.md).

## Mode-0 `glMultiDrawElements` / loop-head ROI

- Live schema-v6 census (`20261007T074355Z`): **`MODE0_DOMAIN_EMPTY`** on paused Venice — no mode-0 border draws to batch in the fixture.
- Evidence: [`analysis/evidence/border-mode-census-live-20261007T074355Z.json`](evidence/border-mode-census-live-20261007T074355Z.json), capsule in the same directory.
- Loop-head static feasibility @ `0x1010cbe55`: documented in [`analysis/border-loop-head-feasibility.md`](border-loop-head-feasibility.md) and [`analysis/evidence/border-loop-head-feasibility-20261004.json`](evidence/border-loop-head-feasibility-20261004.json).
- ROI readiness (`20261005`): observer instrumentation validated offline; live ROI did not justify mutation.

## Mode-other / `+0x10` / basevertex semantics

- Mode-0 record field `+0x04` is **not** the effective GL base vertex on the hot path; GfxDrawIndexed passes **zero** base vertex → `glDrawElements`. See [`analysis/border-mode0-basevertex-evidence-addendum.md`](border-mode0-basevertex-evidence-addendum.md).
- Mode-other multi-draw barriers and go/no-go: [`analysis/border-multidraw-go-nogo.md`](border-multidraw-go-nogo.md), [`analysis/border-multidraw-barriers.md`](border-multidraw-barriers.md).
- Estimated ceiling for border-only GL batching on macOS: ~3–7% main-thread wall time — superseded by the Metal / domain migration track.

## What was learned (kept for Metal / S8)

- Border draws are cheap per call (~0.6 µs) vs mesh (~2.2 µs); optimizing draw count alone mis-ranks work.
- Inner border walk structure, classifier parity tests, and GL interpose observer code remain in the repo for reference; do not extend mutation without a new cost-based gate.

## Next work

- R0-M measurement and R0-A / M0a architecture per the canonical rendering roadmap.
- No further border batching PRs unless a new fixture or map mode reopens mode-0/mode-other volume with a **≥3–5%** main-thread wall-time prediction.
