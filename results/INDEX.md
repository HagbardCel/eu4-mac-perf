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

For frame-rate and multithreaded GL work, see [analysis/frame-rate.md](../analysis/frame-rate.md).
