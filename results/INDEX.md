# Landmark runs

| Directory | Focus | Key artifact |
|-----------|--------|--------------|
| `20260926T194101Z-paused-t1` | Early paused baseline (Lübeck save era) | `metadata.json` |
| `20260926T220508Z-paused-interpose` | Frame interpose experiment | `metadata.json` |
| `20260927T102247Z-diagnostic` | Root-cause analysis | [root-cause.md](20260927T102247Z-diagnostic/root-cause.md) |
| `20260927T135005Z-state-cache` | State-cache validation (failed engage) | [validation.md](20260927T135005Z-state-cache/validation.md) |
| `20260927T141040Z-state-cache` | State-cache validation (six phases) | [validation.md](20260927T141040Z-state-cache/validation.md) |
| `20260928T065747Z-autonomous` | Unattended Venice paused baseline | [summary.md](20260928T065747Z-autonomous/summary.md) |
| (aggregate) | Autonomous reproducibility | [autonomous-reproducibility.json](autonomous-reproducibility.json) |
| `20260928T112439Z-draw-trace` | Draw tracer baseline gate stopped before capture | `manifest.json` |
| `20260928T113359Z-draw-trace` | One paused draw window; screening only | [draw-screening.md](20260928T113359Z-draw-trace/draw-screening.md) |
| `20261003T210202Z-intrusive-diagnostic` | Phase C forensic capsule (R–C–R–C–R, Venice paused); gzip telemetry/power/plist + scene + [salvage-report.md](20261003T210202Z-intrusive-diagnostic/salvage-report.md) | [report.md](20261003T210202Z-intrusive-diagnostic/report.md), [raw_evidence.json](20261003T210202Z-intrusive-diagnostic/raw_evidence.json), `ready-scene.png`, `game-ready.log` |
| `20261004T111812Z-submission-experiment` | Gfx PR2 capability-1 observer smoke (A–B–A); incomplete — summarize threshold vs 10 s phases | [validation.md](20261004T111812Z-submission-experiment/validation.md), [analysis/evidence/gfx-subrecord-observer-smoke-20261004T111812Z.json](../analysis/evidence/gfx-subrecord-observer-smoke-20261004T111812Z.json) |
| `20261004T112503Z-submission-experiment` | Gfx PR2 observer smoke complete; engagement failed (0 buffer-bind pair hits on Venice) | [validation.md](20261004T112503Z-submission-experiment/validation.md), [analysis/evidence/gfx-subrecord-observer-smoke-20261004T112503Z.json](../analysis/evidence/gfx-subrecord-observer-smoke-20261004T112503Z.json) |
| `20261004T131931Z-submission-experiment` | Gfx v3 multi-hypothesis smoke (kind v1); harness valid; **v3.0 pair segmentation invalid** — cross-parent inconclusive; 0/55,836 same-parent buffer-signature hits among surviving pairs | [validation.md](20261004T131931Z-submission-experiment/validation.md), [analysis/evidence/gfx-subrecord-multi-hypothesis-smoke-20261004T131931Z.json](../analysis/evidence/gfx-subrecord-multi-hypothesis-smoke-20261004T131931Z.json), [analysis/mesh-observer-chain-semantics-20261004.md](../analysis/mesh-observer-chain-semantics-20261004.md) |
| `20261004T152246Z-submission-experiment` | Gfx v3.1 multi-hypothesis smoke (kind v2); gates passed; 1.29M cross-parent pairs; 0 buffer-signature hits (same- and cross-parent) | [validation.md](20261004T152246Z-submission-experiment/validation.md), [analysis/evidence/gfx-subrecord-multi-hypothesis-smoke-20261004T152246Z.json](../analysis/evidence/gfx-subrecord-multi-hypothesis-smoke-20261004T152246Z.json) |

For frame-rate and multithreaded GL work, see [analysis/frame-rate.md](../analysis/frame-rate.md).
