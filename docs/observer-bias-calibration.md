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

## Phase C interpretation (authoritative capture `e86e33b3`)

The authoritative Mac capture **did not validate component-level additive correction** (`explained_fraction` ≈ −8%; negative flush slope; unstable scope bundle). Phase C must:

- treat **live R↔C perturbation** as the primary observer-effect measurement;
- use profiler output for **relative shares and rank stability**, not bias-adjusted absolute timings;
- cite the **aggregate** mesh prior **~9–10 µs/frame** (`counters_incremental`) only as a sanity bracket.

`bias_adjust_inclusive_cpu()` defaults to **no subtraction** unless `allow_component_correction=True` and reconciliation passes. Do **not** apply forensic slopes numerically for this evidence set.

See [`live-diagnostic-phase-c.md`](live-diagnostic-phase-c.md).

## Helpers

`benchmark/frame_model_observer_bias.py` — `phase_c_observer_interpretation()`, `evaluate_component_bias_correction()`, `mesh_counters_incremental_prior_ns_per_frame()`, `bias_adjust_inclusive_cpu(..., allow_component_correction=False)`.

See [live diagnostic roadmap](eu4-live-diagnostic-roadmap.md) and [measurement contract](live-diagnostic-measurement-contract.md).
