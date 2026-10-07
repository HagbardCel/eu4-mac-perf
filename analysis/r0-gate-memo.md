# R0 gate memo (fill after live session — PR B)

This memo records whether R0-M evidence supports **`candidate_for_lossless_skip`** scenarios and refreshes CPU/stack baselines. It does **not** authorize skipping `CInGameIdler::Render` — R1 must prove invalidation and correctness.

## Session

| Field | Value |
|---|---|
| Run directory | `results/<timestamp>-r0-measurement/` |
| Fixture / save SHA | |
| GOG executable SHA | `b3d38876abf4e61cdae57509186d7cb7dcb03bfeaca4c95c10c713794715141d` |
| Manifest | `manifest.json` |

## Evidence matrix

| Evidence | Result | Notes |
|---|---|---|
| Frame readback | | Synthetic harness + in-game positive control |
| Mode provenance | | ACK before census; transition frames discarded |
| Pixel equality | | memcmp series; epoch C only |
| Classification | | static / periodic / continuous / input-driven + confidence |
| Thread CPU | | main vs non-main; reconciliation tolerance |
| Stack attribution | | two-dimensional cross-tab |
| Positive control | | UI toggle detected localized change |
| R1 decision | | candidates listed; dirty-state **unresolved** |

## Candidates for lossless skip (screening only)

_List from `analysis/evidence/r0-frame-evolution-<run>.json` → `candidate_for_lossless_skip`._

## CPU baseline (epoch A)

_Process and main-thread CPU-ms/s from `epoch_a_thread_cpu.json`; reconciliation residual._

## Stack attribution (epoch B)

_Summary from `analysis/evidence/r0-main-thread-cost-model.json`._

## Explicit non-claims

- Pass-level map cacheability (R3) is **not** inferred from screen-space diffs.
- Captured pixels are **OpenGL back buffer before flush**, not the WindowServer-composited desktop image.

## R1 recommendation

- [ ] Proceed to implement lossless idle skip for scenario(s): …
- [ ] Do not implement whole-frame skip; consider map-layer cache only after temporal analysis.
- [ ] S2b (reduced idle rate) remains an explicit product decision.
