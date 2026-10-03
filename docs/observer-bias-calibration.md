# Observer-bias calibration (Phase B)

Phase B estimates **offline slopes** (nanoseconds per primitive operation) for profiler components that distort live CPU during intrusive diagnostic capture. Slopes use **external harness thread CPU** (`cpu_ns` from the workload or GL harness subprocess), not profiler self-timers.

WP11 remains the terminal Tier-1 qualification outcome; this work does **not** reopen admission or Mac requalification.

## Command

```sh
python3 benchmark/eu4_frame_model.py observer-bias-calibration --registry-json
```

Requirements:

- Clean git tree (same evidence hygiene as other offline captures).
- Accelerated CGL pixel format (synthetic GL harness).
- Training **mesh** recipe; scale factors **1, 2, 4**; **5** paired repetitions per scale with alternating low→high / high→low order.

Immutable evidence is written under `analysis/evidence/` (rolling pointer is **not** updated). Metadata identifies `observer_bias_calibration_v1` explicitly.

## Model layers

### Aggregate controls (not additive)

Use for validation and coarse live R vs C checks; **never** sum with decomposition primitives.

| ID | Pair | Unit |
|----|------|------|
| `reference_activation` | loaded-disabled → reference | published frame |
| `counters_incremental` | reference → counters | published frame |

### Decomposition diagnostics (additive for Phase C)

Use with **observed operation counts** from the live capture:

| ID | Pair | Unit |
|----|------|------|
| `scope_pair_clocks` | counters-lite → counters | scope pair (from trace `Q` call counts) |
| `per_frame_counter_flush` | deferred flush → counters-lite | published frame |
| `gpu_timestamp_segment` | counters GPU timestamps off → on | GPU timestamp segment (`M` line) |
| `timed_gl_sample` | counters → sampled | timed GL sample (`D`/`S` records) |

### Instrumentation

| ID | Pair | Unit |
|----|------|------|
| `gl_interpose_dispatch` | bare harness → dylib interposed | intercepted draw loop |

## Phase C correction

Prefer:

\[
T_i^\text{adjusted} \approx T_i^\text{measured} - \sum_j N_{ij}\, c_j
\]

where \(N_{ij}\) are **observed** primitive counts on path \(i\) and \(c_j\) are **decomposition** slopes only (`bias_adjust_inclusive_cpu()` enforces this).

Reconciliation: `consistency.explained_fraction` compares the decomposition sum at mesh scale-1 counts to the aggregate `counters_incremental` slope × frames. If the sum explains far more than 100% of the aggregate tax, do not trust the decomposition for attribution.

## Reporting helpers

`benchmark/frame_model_observer_bias.py`:

- `build_bias_model()` — separates `additive_slopes` vs `aggregate_slopes`.
- `bias_adjust_inclusive_cpu()` — decomposition layer only.
- `bias_aware_interval()` — uses `slope_se_ns_per_op × operation_count` for margin.
- `reconcile_counters_decomposition()` — aggregate vs decomposition check.

## Limitations

- Synthetic mesh workload only; live EU IV mixtures differ.
- Slopes are diagnostic, not qualification gates.
- `timed_gl_sample` compares counters vs sampled stages and remains a coarse proxy for forensic draw timing cost.

See [live diagnostic roadmap](eu4-live-diagnostic-roadmap.md) and [measurement contract](live-diagnostic-measurement-contract.md).
