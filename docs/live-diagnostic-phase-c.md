# Live diagnostic Phase C (attribution run)

Phase B authoritative capture: [`e86e33b3`](observer-bias-calibration-memo-20261003.md) @ `26af021`. **Component-level bias subtraction is not validated** for that archive; Phase C must not present bias-adjusted absolute microseconds.

## Protocol (one EU IV launch)

```text
90 s warm-up (paused, fixture-stable)
R1 20 s → C1 20 s → R2 20 s → C2 20 s → R3 20 s
TAIL 20 s (isolated forensic detail; excluded from R/C observer brackets)
```

- **R** = lean REFERENCE (low intrusion baseline).
- **C** = counters/profile path (attribution window).
- External metrics throughout: EU IV thread CPU, swap rate, power, display cadence.

Preflight: `preflight --intrusive-diagnostic-contract` (WP11 terminal record + build integrity).

Live capture: `python3 benchmark/eu4_frame_model.py run --diagnostic-only --output results` (default output is `results/`).

## What to measure (priority order)

1. **Live R↔C observer effect** — bracketed **C1 vs (R1,R2)** and **C2 vs (R2,R3)** plus aggregate; lean REFERENCE-compatible frame fields (update/loop CPU, cadence), external EU IV CPU, swap rate, and system power. Compare aggregate update-CPU Δ to the offline ~9–10 µs/frame mesh prior.
2. **Relative attribution in C1/C2** — category/path **shares** and **rank stability** across the two counters windows. Report raw profiler-inclusive timings with explicit unqualified labeling.
3. **Offline mesh prior** — `counters_incremental` ≈ **9.25 µs/frame** (9247 ns/frame, SE ~448 ns) on the training mesh surrogate; use as a sanity bracket for live perturbation, not as a per-path correction.

Do **not** subtract `scope_pair_clocks`, `per_frame_counter_flush`, forensic slopes, or instrumentation slopes from live timings for this evidence set.

## Report shape (target)

```text
Live COUNTERS perturbation (R vs C):     +X% CPU, +Y% swaps  (measured)
Offline mesh prior (aggregate only):     ~9–10 µs/frame
Component-level bias correction:         unavailable (non-identifiable decomposition)

Category/path (exclusive scope path)   share (C1)   share (C2)   rank stable?
---------------------------------------------------------------------------
…                                      …%           …%           yes/no

Envelope residuals and semantic/update + semantic/render coverage are reported alongside shares.
Run `status` may be `complete_with_attribution_gap` when R/C succeeded but attribution or forensic tail evidence is incomplete.
```

Forensic/detail windows: emphasize **counts and structural relationships**; GPU timestamp and sparse draw-clock instrumentation may perturb execution (see Phase B forensic slopes).

## Code hooks

- `verify_authoritative_observer_bias_calibration()` — manifest + SHA + Phase C interpretation gate.
- `observer_bias.phase_c_observer_interpretation(bias_model)` — embed in intrusive `report.json`.
- `bias_adjust_inclusive_cpu(..., allow_component_correction=False)` — default refuses component subtraction unless reconciliation passes (authoritative archive does not).

## Outcome and raw evidence (forensically closed)

See [phase-c-outcome-memo.md](phase-c-outcome-memo.md). **No further live intrusive profiler captures are required for the archive.** Further EU IV runs belong only to profiler-off A/B tests of concrete renderer changes.

Canonical run [`20261003T210202Z-intrusive-diagnostic`](../results/20261003T210202Z-intrusive-diagnostic/) git capsule:

- Streams: `telemetry.csv.gz`, `power.samples.json.gz`, `powermetrics.pliststream.gz`
- Scene: `ready-scene.png`; readiness: `game-ready.log` when post-hoc extraction is time-anchored (else `game-ready.reconstructed.log` or metadata-only)
- Sidecar: `powermetrics.stderr` (helper warnings)
- Derived: `report.*`, `salvage-report.*`, `raw_evidence.json` (`capture_inventory` + logical gzip hashes)

Closure commands (order matters — seal after salvage):

```sh
python3 benchmark/eu4_frame_model.py intrusive-salvage results/20261003T210202Z-intrusive-diagnostic
python3 benchmark/eu4_frame_model.py seal-evidence results/20261003T210202Z-intrusive-diagnostic \
  --profile intrusive_complete_v1 --no-manifest-write
python3 benchmark/eu4_frame_model.py verify-evidence results/20261003T210202Z-intrusive-diagnostic
```

Clone-only analysis: `read_rows` accepts `.csv.gz` when uncompressed `telemetry.csv` is absent; `seal-evidence` preserves logical content SHA-256 after plain files are deleted.
