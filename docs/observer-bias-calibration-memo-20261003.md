# Observer-bias calibration memo (2026-10-03)

**Authoritative archive:** `20261003T191305.493421Z-e86e33b3` @ `26af021`  
**Manifest:** `observer_bias_calibration_archives` in `analysis/profiler-overhead-diagnosis-manifest.json`  
**Policy:** `observer_bias_calibration_v1` (mesh, scales 1/2/4, 5 paired reps)

## Outcome

Mac capture completed after merge of PR #27. Immutable evidence SHA256 `0e872718…`.

### Aggregate controls (µs/frame, mesh scale-1 fit)

| Primitive | Slope (ns/frame) | Note |
|-----------|------------------|------|
| `reference_activation` | −177 | Noisy (large SE); not for subtraction |
| `counters_incremental` | **+9247** | ~9.25 µs/frame reference→counters |

### Base decomposition (additive layer)

| Primitive | Slope | Interpretation |
|-----------|-------|----------------|
| `scope_pair_clocks` | +63 ns/scope-pair call (bundle) | Positive; high relative SE |
| `per_frame_counter_flush` | **−1281 ns/frame** | Negative on mesh; **do not** subtract blindly in Phase C |

`consistency.explained_fraction` ≈ **−0.08** — scope+flush decomposition does **not** explain `counters_incremental` on this capture (flush dominates with wrong sign).

### Forensic add-ons (detail window)

| Primitive | Slope |
|-----------|-------|
| `gpu_timestamp_call` | −2192 ns/call (noisy; treat as diagnostic bound) |
| `timed_gl_sample` | +26 ns/timed sample |

### Instrumentation reference

`gl_interpose_dispatch` ≈ **2.95 ns** per synthetic enable+draw loop (stable); reference only.

## Phase C guidance

1. Prefer **aggregate** `counters_incremental` for coarse R vs C observer effect checks.
2. Use **decomposition** slopes only when operation counts are observed and signs are physically plausible; skip negative `per_frame_counter_flush` subtraction unless live counts justify it.
3. Apply **forensic_slopes** only in phases with forensic detail armed.

No further Phase B infrastructure unless a new capture shows a concrete gap.
