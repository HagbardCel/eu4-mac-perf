# Live diagnostic Phase C (attribution run)

Phase B authoritative capture: [`e86e33b3`](observer-bias-calibration-memo-20261003.md) @ `26af021`. **Component-level bias subtraction is not validated** for that archive; Phase C must not present bias-adjusted absolute microseconds.

## Protocol (one EU IV launch)

```text
90 s warm-up (paused, fixture-stable)
R1 20 s → C1 20 s → R2 20 s → C2 20 s → R3 20 s
```

- **R** = lean REFERENCE (low intrusion baseline).
- **C** = counters/profile path (attribution window).
- External metrics throughout: EU IV thread CPU, swap rate, power, display cadence.

Preflight: `preflight --intrusive-diagnostic-contract` (WP11 terminal record + build integrity).

Live capture: `python3 benchmark/eu4_frame_model.py run --diagnostic-only <output-dir>`.

## What to measure (priority order)

1. **Live R↔C observer effect** — frame cadence, process CPU, swap rate between R and C phases. This dominates the offline ~9–10 µs/frame mesh prior.
2. **Relative attribution in C1/C2** — category/path **shares** and **rank stability** across the two counters windows. Report raw profiler-inclusive timings with explicit unqualified labeling.
3. **Offline mesh prior** — `counters_incremental` ≈ **9.25 µs/frame** (9247 ns/frame, SE ~448 ns) on the training mesh surrogate; use as a sanity bracket for live perturbation, not as a per-path correction.

Do **not** subtract `scope_pair_clocks`, `per_frame_counter_flush`, forensic slopes, or instrumentation slopes from live timings for this evidence set.

## Report shape (target)

```text
Live COUNTERS perturbation (R vs C):     +X% CPU, +Y% swaps  (measured)
Offline mesh prior (aggregate only):     ~9–10 µs/frame
Component-level bias correction:         unavailable (non-identifiable decomposition)

Category/path          share (C1)   share (C2)   rank stable?
-----------------------------------------------------------
…                      …%           …%           yes/no
```

Forensic/detail windows: emphasize **counts and structural relationships**; GPU timestamp and sparse draw-clock instrumentation may perturb execution (see Phase B forensic slopes).

## Code hooks

- `verify_authoritative_observer_bias_calibration()` — manifest + SHA + Phase C interpretation gate.
- `observer_bias.phase_c_observer_interpretation(bias_model)` — embed in intrusive `report.json`.
- `bias_adjust_inclusive_cpu(..., allow_component_correction=False)` — default refuses component subtraction unless reconciliation passes (authoritative archive does not).
