Fix P5.1.1 lifecycle defects from PR #163 review on this pinned worktree (`4ff4add`). Do not redesign the renderer. Do not launch EU4. Do not broaden into shaders, draws, scissored clears, or Retina UI/font scaling.

Load the `opengl-metal-reconstruction` skill before editing implementation. Inspect current `eu4_gfx_init.mm` clear/SetRT/present paths, `eu4_metal_boot.m` `EU4MetalChildView`, and `analysis/gfx-render/v0/hidpi-contract.md`. Label claims `SDK`, `Apple docs`, `Project-derived`, or `Unresolved`.

Write `analysis/automation/eu4-p511-lifecycle/note.md` before changing replacement code. Record the four locked fixes below. A line starting with `BLOCKER:` stops the campaign when a faithful fix still requires an unsupported guess. Leave a captured research checkpoint alone; later corrections go under `## Follow-up`.

## Required fixes

1. **Obsolete pending.** Add `pending_is_obsolete` (`pendingGameCommand != nil && pendingGameGeneration != geometryGeneration`). After reconcile: game clear returns `GEOMETRY_STALE` when obsolete (no wait, no submit, **do not modify pending command/generation**); SetRT(null) drains when obsolete then allocate-before-master-mutation. Same-generation pending clear/SetRT stays `UNSUPPORTED`/fatal. Production wrappers keep nonfatal `GEOMETRY_STALE` skips.

2. **Monotonic content generations.** Remove `intern_generation` / `_seenGeometry` reuse. Effective-geometry change → `geometryGeneration += 1`; identical reobserve (including repeated layout) → unchanged. Cocoa publication epoch ≠ content generation. Update `hidpi-contract.md` (drop 1×→2×→1× identity reclaim). A→B→A with pending clear: strictly increasing gens; after GPU drain, present stays stale until a **new** clear completes.

3. **Two-phase Cocoa geometry transition.** Not publish-first alone. Phase 1: publish transition-in-progress with intended size → present returns `GEOMETRY_STALE`. Phase 2: set `layer.drawableSize`, then publish committed epoch → renderer reconciles. After acquire, recheck committed epoch + drawable size/format. Stable committed mismatch → `FAILED`. Test both interleavings (publish before layer resize; layer resize before commit).

4. **Production AppKit + mandatory real drawable.** Exercise the actual `EU4MetalChildView` used by `eu4_metal_boot.m` (or extract a shared production geometry-view component linked by boot + host—do not invent a test-only twin). NSWindow + production child view; change bounds on main; verify frame/`drawableSize`/published+committed geometry; real `CAMetalDrawable` present before and after with GPU readback. Availability is decided by this window-backed path (not a detached layer probe). If unavailable → checker must see `unavailable`, never `pass`.

Preserve clear no-wait. Do not expand `GEOMETRY_STALE` beyond genuine geometry transitions.

## Editable paths only

- `analysis/automation/eu4-p511-lifecycle/note.md`
- `analysis/automation/eu4-p511-lifecycle/progress.md`
- `progress.md`
- `analysis/gfx-render/v0/hidpi-contract.md`
- `analysis/gfx-render/v0/default-target-contract.md`
- `benchmark/eu4_gfx_init.h`
- `benchmark/eu4_gfx_init.mm`
- `benchmark/eu4_metal_boot.m`
- `benchmark/eu4_metal_geometry_view.h` (if extracted)
- `benchmark/eu4_metal_geometry_view.m` (if extracted)
- `tests/gfx_hidpi_host.mm`
- `tests/test_gfx_hidpi.py`
- `tests/gfx_hidpi_appkit_host.mm` (if needed)
- `tests/test_gfx_hidpi_appkit.py` (if needed)

Do not commit, reset, or push. Do not edit the campaign directory, the checker, or automation runner state. No new `GFX_ENTRY`.
