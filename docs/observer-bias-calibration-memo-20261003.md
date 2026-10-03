# Observer-bias calibration memo (2026-10-03)

**Authoritative archive:** `20261003T191305.493421Z-e86e33b3` @ `26af021`  
**Manifest:** `observer_bias_calibration_archives` in `analysis/profiler-overhead-diagnosis-manifest.json`  
**Policy:** `observer_bias_calibration_v1` (mesh, scales 1/2/4, 5 paired reps, all-scale OLS through origin)

## Outcome

Mac capture completed after merge of PR #27. Immutable evidence SHA256 `0e872718…`.

**Scientific conclusion:** the **aggregate** COUNTERS observer tax on the mesh surrogate is reproducible (~**9–10 µs/frame**). The attempted **component decomposition is not identifiable** enough for numerical bias correction in Phase C.

### Aggregate controls (ns/frame, all-scale paired OLS fit)

| Primitive | Slope (ns/frame) | Note |
|-----------|------------------|------|
| `reference_activation` | −177 (SE 431) | ~0 within noise; R is a valid low-intrusion baseline |
| `counters_incremental` | **+9247** (SE 448) | **~9.25 µs/frame**; consistent with WP8–WP11 mesh band |

Per-scale counter medians are noisier (1× ~9.8, 2× ~6.1, 4× ~10.1 µs/frame); the fitted aggregate plus historical captures support **9–10 µs/frame** as the robust prior.

### Base decomposition (not validated for subtraction)

| Primitive | Slope | Interpretation |
|-----------|-------|----------------|
| `scope_pair_clocks` | +63 ns/scope-pair call (bundle) | Unstable across scale; order-of-magnitude only |
| `per_frame_counter_flush` | **−1281 ns/frame** (SE 375) | Sign flips by scale; **must not** subtract in Phase C |

`consistency.explained_fraction` ≈ **−0.08** — scope+flush decomposition explains **none** of the aggregate counters tax on this capture.

### Forensic add-ons (not numeric correction)

| Primitive | Slope | Note |
|-----------|-------|------|
| `gpu_timestamp_call` | −2192 ns/call (scale-dependent) | Perturbation / driver interaction, not a stable CPU coefficient |
| `timed_gl_sample` | +26 ns/sample (SE 45) | Consistent with zero |

Do **not** apply forensic slopes via `estimate_bias_ns(..., allow_forensic=True)` for this archive.

### Instrumentation reference

`gl_interpose_dispatch` ≈ **2.95 ns** per synthetic enable+draw loop (stable); synthetic reference only, not a live per-intercept correction.

## Phase C guidance

See [`live-diagnostic-phase-c.md`](live-diagnostic-phase-c.md).

1. Measure **live R↔C** perturbation directly (primary).
2. Use intrusive profiler for **relative ranking/shares** in C1/C2, not bias-adjusted absolute µs.
3. Use offline **aggregate** prior ~9–10 µs/frame only as a sanity bracket.
4. **`bias_adjust_inclusive_cpu()` defaults to no component subtraction** when reconciliation fails (authoritative archive).

No further Phase B captures unless policy is deliberately reopened.
