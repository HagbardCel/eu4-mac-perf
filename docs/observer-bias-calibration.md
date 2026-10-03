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

Immutable evidence is written under `analysis/evidence/` (rolling pointer is **not** updated). Metadata identifies `observer_bias_calibration_v1`.

**Authoritative Mac capture:** `20261003T191305.493421Z-e86e33b3` @ `26af021` — registered under `observer_bias_calibration_archives` in `analysis/profiler-overhead-diagnosis-manifest.json`. Interpretation: [`observer-bias-calibration-memo-20261003.md`](observer-bias-calibration-memo-20261003.md).

## Model layers

### Aggregate controls (not additive)

| ID | Pair | Unit |
|----|------|------|
| `reference_activation` | loaded-disabled → reference | published frame |
| `counters_incremental` | reference → counters | published frame |

### Base additive decomposition (normal COUNTERS path)

Use with **observed operation counts** from live capture. `bias_adjust_inclusive_cpu()` subtracts **only** this layer.

| ID | Pair | Unit |
|----|------|------|
| `scope_pair_clocks` | counters-lite → counters | scope pair (approximate; includes Q-tree snapshot work) |
| `per_frame_counter_flush` | deferred flush → counters-lite | published frame |

`consistency.explained_fraction` reconciles **only** the two rows above against `counters_incremental` (not forensic add-ons).

### Instrumentation reference (not auto-subtracted)

| ID | Pair | Unit |
|----|------|------|
| `gl_interpose_dispatch` | bare harness ↔ dylib interposed (order alternates) | intercepted draw loop |

Stored in `instrumentation_slopes`; not used by `bias_adjust_inclusive_cpu()`.

### Forensic add-ons (detail/sample window only)

Reported in `forensic_slopes`; use `estimate_bias_ns(..., allow_forensic=True)` in Phase C when a phase arms forensic detail. **Not** summed into counters reconciliation.

| ID | Pair | Unit |
|----|------|------|
| `gpu_timestamp_call` | sampled, records off: GPU timestamps 0 → 1 | GPU timestamp insertion (`M[1]` stamp count) |
| `timed_gl_sample` | sampled, records/GPU off: `EU4_TEST_DRAW_TIMED_SAMPLES` 0 → 1 | `F.draw_timed_samples` |

## Phase C correction

\[
T_i^\text{adjusted} \approx T_i^\text{measured} - \sum_{j \in \text{base}} N_{ij}\, c_j - \sum_{k \in \text{forensic}} N_{ik}\, c_k
\]

Base slopes never include aggregate controls. Forensic slopes apply only when the live phase actually executes detail windows with the corresponding operations.

## Helpers

`benchmark/frame_model_observer_bias.py` — `build_bias_model()`, `bias_adjust_inclusive_cpu()`, `reconcile_counters_decomposition()`, `bias_aware_interval()`.

See [live diagnostic roadmap](eu4-live-diagnostic-roadmap.md) and [measurement contract](live-diagnostic-measurement-contract.md).
