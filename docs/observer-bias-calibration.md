# Observer-bias calibration (Phase B)

Phase B estimates **offline slopes** (nanoseconds per synthetic operation) for profiler primitives that inflate live CPU during intrusive diagnostic capture. Slopes use **external harness thread CPU** (`cpu_ns` from the workload or GL harness subprocess), not profiler self-timers.

WP11 remains the terminal Tier-1 qualification outcome; this work does **not** reopen admission or Mac requalification.

## Command

```sh
python3 benchmark/eu4_frame_model.py observer-bias-calibration --registry-json
```

Requirements:

- Clean git tree (same evidence hygiene as other offline captures).
- Accelerated CGL pixel format (synthetic GL harness).
- Training **mesh** recipe only; scale factors **0, 1, 2, 4** on base frame/loop counts.

The command writes immutable evidence under `analysis/evidence/` (rolling pointer is **not** updated).

## Primitives

| ID | Differential pair | Unit |
|----|-------------------|------|
| `gl_interpose_dispatch` | bare GL harness → dylib interposed | synthetic draw loop |
| `reference_frame_hooks` | loaded-disabled → lean REFERENCE | published frame |
| `tls_event_accounting` | minimal-reference → reference | published frame |
| `scope_pair_clocks` | counters-lite → reference | published frame |
| `per_frame_counter_flush` | counters-lite → deferred flush | published frame |
| `sparse_gl_timestamp` | counters (GPU timestamps off) → on | published frame |
| `frame_publication_writer` | ablation writer → sampled | published frame |

At scale **0**, only the low path runs and the fitted marginal delta is defined as zero. Scales **1, 2, 4** multiply the base measured-frame count (4 frames) or base draw-loop count (4000 loops).

## Reporting helpers

Python module: `benchmark/frame_model_observer_bias.py`

- `build_bias_model()` — aggregate fitted slopes from a capture.
- `bias_adjust_inclusive_cpu(inclusive_cpu_ns, operation_counts, model)` — subtract estimated observer bias from inclusive CPU for Phase C tables.
- `bias_aware_interval()` — interval helper using fit residual spread.

Phase C live manifests may embed `observer_bias_calibration` from a registered Phase B archive; `report` surfaces `observer_bias_calibration` on intrusive diagnostic runs when present.

## Limitations

- Synthetic mesh workload only; EU IV live geometry and driver state differ.
- Slopes are **diagnostic**, not qualification gates.
- Negative fitted slopes can appear when a differential pair is dominated by noise or overlapping effects; treat marginal tables as bounds, not exact accounting.

See [live diagnostic roadmap](eu4-live-diagnostic-roadmap.md) and [measurement contract](live-diagnostic-measurement-contract.md).
