# WP5 — reference fast path

**Branch:** `profiler-overhead-calibration`  
**Goal:** Reduce REFERENCE-mode overhead (vs bare) without counters, forensic shadow, or GL measurement hooks.

## Changes

- **REFERENCE render:** `hook_render` takes a dedicated path — `SCOPE_RENDER` + `real_render` only (no `save_gl_state` / `seed_gl_state` / forensic GPU prep).
- **GL shadow:** `shadow_tracking_active()` is false when `mode==REFERENCE` (no per-draw shadow/query work).
- **Context switches:** skip shadow seeding on `CGLSetCurrentContext` success in REFERENCE mode.
- **Workload control:** offline stages use `flags=3` (`INTERVENTION_ACTIVE|MEASURE_ENABLED`) so reference/counters gates measure consistently.

## Validation

```bash
python3 -m unittest discover -s tests -p 'test_*.py'
# Mac: compare Tier-1 reference_elapsed_ns before/after on training recipes (new evidence archive; do not rewrite WP1 v3).
```

Unowned worker threads still use `refresh_unowned_control()` via `measurement_active()` / `intervention_active()`; owned update still snapshots control once per frame in `hook_update`.
