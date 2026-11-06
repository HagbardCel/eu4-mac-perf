Fix P5.1.1 Cocoa phase-1 pending clear/SetRT termination on this pinned worktree (`5b5545e`, post-#164 tip). Do not redesign the renderer. Do not launch EU4. Do not broaden into shaders or draws.

Load the `opengl-metal-reconstruction` skill before editing implementation. Inspect `clear_game_checked`, `eu4_gfx_set_render_target_checked`, two-phase geometry publication, and `hidpi-contract.md`. Label claims `SDK`, `Apple docs`, `Project-derived`, or `Unresolved`.

Write `analysis/automation/eu4-p511-phase1/note.md` before changing replacement code. A `BLOCKER:` line stops the campaign when a faithful fix still requires an unsupported guess.

## Required fixes

1. **Game clear order.** After reconcile: `pending_is_obsolete` → `GEOMETRY_STALE`; then **`geometry_transition_in_progress()` → `GEOMETRY_STALE` even with same-gen pending**; only then same-gen pending → `UNSUPPORTED`. Stale skip must not wait, submit, or modify pending command/generation. Production clear already treats stale as nonfatal.

2. **SetRT(null) phase 1.** After reconcile, before obsolete drain / same-gen reject: if transition in progress → `GEOMETRY_STALE` with **no** drain, allocate, `write_default_viewport`, or `+0x78` write. Production SetRT must return normally on `GEOMETRY_STALE` (no `eu4_gfx_stop`).

3. **Deferred default-target SetRT.** On that stale SetRT(null), set `deferredDefaultTargetSelect` (or equivalent). Once geometry is **committed** and safe (no undrained obsolete pending), apply deferred selection before the next game clear encodes: viewport from logical `+0xa0/+0xa4`, `+0x78` null, physical BB allocate/reconcile as SetRT(null) would. **No wait inside clear**—if obsolete pending remains, clear stays stale; drain only on SetRT/present. Document in `hidpi-contract.md`.

4. **Tests.** Phase-1 pending clear (production no-stop, pending intact); checked+production SetRT phase-1; deferred recovery: `logical A → phase 1 → logical B → skipped SetRT → commit B → clear without SetRT → success` with viewport matching new logical; ABA pending through both transitions before drain; AppKit parent resize without test calling child `setFrameSize`/`syncDrawableGeometry`; stable-geometry fatal paths unchanged.

## Editable paths only

- `analysis/automation/eu4-p511-phase1/note.md`
- `analysis/automation/eu4-p511-phase1/progress.md`
- `progress.md`
- `analysis/gfx-render/v0/hidpi-contract.md`
- `benchmark/eu4_gfx_init.h`
- `benchmark/eu4_gfx_init.mm`
- `benchmark/eu4_metal_geometry_view.h`
- `benchmark/eu4_metal_geometry_view.m`
- `tests/gfx_hidpi_host.mm`
- `tests/test_gfx_hidpi.py`
- `tests/test_gfx_hidpi_appkit.py`
- `tests/gfx_hidpi_appkit_host.mm`

Do not commit, reset, or push. Do not edit the campaign directory or checker. No new `GFX_ENTRY`.
