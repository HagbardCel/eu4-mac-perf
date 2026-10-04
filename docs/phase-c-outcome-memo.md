# Phase C outcome memo

Phase C ([`20261003T210202Z-intrusive-diagnostic`](../results/20261003T210202Z-intrusive-diagnostic/)) closes live intrusive profiling as a path to quantitative qualification. Use it for **structural** evidence only.

## High confidence

- **REFERENCE (R)** is a credible low-intrusion baseline (process CPU, power, swaps within a few percent of uninstrumented autonomous reference).
- **COUNTERS (C)** cut throughput ~33% and move the system into a different regime; C absolute timings are not uninstrumented truth.
- The paused **update thread waits** (~98% of update wall is not thread CPU); Map::Render is the dominant envelope residual.
- GL workload is **submission/state heavy** (~86k state calls, ~5.9k draws per rendered frame in C).
- Exhaustive per-call forensic logging is **not viable** (hundreds of thousands of detail records per frame; ~192k dropped).

## Non-goals

- No immediate Phase C rerun.
- No profiler requalification or larger detail queues.
- No default optimization of Present, CGLFlush, AddToBucket, or Append.

## Forensic capsule vs salvage (PR #29)

- **Forensic capsule:** committed gzip telemetry/power, `raw_evidence.json` with `capsule_profile: intrusive_complete_v1`, and unchanged historical `manifest.json`.
- **Salvage:** bounded TAIL ordinal hints (`usable_ordinal_hint`); Spearman/Kendall and count-complete S/U ranks are **reporting only**, not admission gates.
- **Verify:** `eu4_frame_model.py verify-evidence <dir>` enforces the sealed profile hermetically (no GOG/`$HOME`).

## Next work

1. Static RE of `_FlushData` / 0xe8 subrecords.
2. One **Gfx-layer** submission transaction experiment, validated profiler-off via autonomous probe + bracket-normalized ABABA.

See [live-diagnostic-phase-c.md](live-diagnostic-phase-c.md) and [map-render-optimization-hypothesis.md](../analysis/map-render-optimization-hypothesis.md).
