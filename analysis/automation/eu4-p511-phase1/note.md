# eu4-p511-phase1 research note

Pinned tip: `5b5545e` (post-#164 lifecycle). Remediation for Cocoa phase-1 pending clear/SetRT termination and deferred SetRT after logical resize.

## Provenance

- `[Project-derived]` `clear_game_checked` / `eu4_gfx_set_render_target_checked` order and production stop wrappers in `benchmark/eu4_gfx_init.mm`.
- `[Project-derived]` two-phase publication in `eu4_metal_geometry_view.m` (`eu4_gfx_publish_geometry_transition` then commit).
- `[Project-derived]` `viewport_covers_attachment` / `clear_gates` require viewport == logical window after SetRT(null) `write_default_viewport`.
- `[SDK]` `CAMetalLayer.drawableSize` / `NSView` layout callbacks (AppKit/Metal headers via installed macOS SDK).

Contracts/headers read: `analysis/gfx-render/v0/hidpi-contract.md`, `analysis/metal-api/index.md` (drawable/layer), `benchmark/eu4_gfx_init.h`.

## Fixes

1. Clear: after reconcile, `pending_is_obsolete` then **`geometry_transition_in_progress`** then same-gen pending `UNSUPPORTED`. Phase-1 with pending is `GEOMETRY_STALE`; pending identity unchanged.
2. SetRT(null): phase-1 first → `GEOMETRY_STALE`, set `deferredDefaultTargetSelect`, no master/RT mutation. Production returns without stop.
3. Deferred apply on clear when committed and no pending: viewport + `+0x78` + allocate as SetRT(null). No GPU wait inside clear.

## Tests

Phase-1 pending clear; phase-1 SetRT checked+production; deferred logical resize without second SetRT; ABA keeps pending through both transitions before drain; AppKit parent resize without child `setFrameSize`/`syncDrawableGeometry`.
