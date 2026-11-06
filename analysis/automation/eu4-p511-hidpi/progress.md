# PR5.1.1 HiDPI lifecycle — progress

## Objective

Separate the engine's logical window size (master `+0xa0/+0xa4`, logical viewport) from the
physical `CAMetalLayer.drawableSize`, so the game-owned `BGRA8Unorm` intermediate backbuffer is
allocated and presented at physical pixels without changing the 1:1 blit. Add a distinct
geometry-stale status so a logical/physical transition is a normal no-op instead of a fatal
clear or present, and reconcile the tuple at every mandatory site.

## Implementation

- `benchmark/eu4_gfx_init.h`: `EU4_GFX_CLEAR_GEOMETRY_STALE = 3`; per-backend logical/physical
  geometry, `geometryGeneration`, `pendingGameGeneration`, `clearedGeneration`,
  `awaitingFreshClear`; `eu4_gfx_publish_physical_geometry` and
  `eu4_gfx_geometry_generation`.
- `benchmark/eu4_gfx_init.mm`:
  - Mutex-guarded immutable physical snapshot `{width, height, epoch, valid}`.
  - Per-backend intern table (`1x -> 2x -> 1x` recovers the same generation) and a pure
    `reconcile_game_geometry` that reads master `+0xa0/+0xa4` plus the snapshot and never
    allocates or waits.
  - Init allocates the physical size and commits the initial tuple; `awaitingFreshClear` is
    cleared so P5.1 present-before-first-clear still reports `UNSUPPORTED`.
  - Game clear: transition + pending -> `GEOMETRY_STALE` with no wait/submit; unchanged
    geometry + pending stays `UNSUPPORTED`; otherwise allocate/reuse physical, gate on the
    logical viewport, and record `pendingGameGeneration`.
  - Pending drain sets `clearedGeneration`; `readyForPresent` only when it matches the current
    generation.
  - SetRT(null) drains only on a transition and allocates the replacement before any master
    write; allocation failure keeps the old texture and the master image.
  - Present: observe epoch, drain, reconcile, stale check, acquire, epoch recheck, exact
    physical size/format gate, blit.
  - Production `eu4_gfx_clear`/`eu4_gfx_present` return normally on stale; P4 diagnostic path
    untouched.
- `benchmark/eu4_metal_boot.m`: `EU4MetalChildView` recomputes the backing rectangle and
  publishes physical size on create, `setFrameSize:`, `layout`, and
  `viewDidChangeBackingProperties`; `NSViewWidthSizable | NSViewHeightSizable`.

## Findings

- The original `_GfxClear` scissor reads the logical viewport image (`+0xbc/+0xc0/+0xac/+0xb0`),
  so the replacement's clear gate must stay logical while the attachment is physical.
- `_GfxPresent` only swaps the SDL window; P5.1's exact-size `BGRA8Unorm` 1:1 blit remains the
  right present policy once the backbuffer is physical.
- The initial physical size falls back to `layer.drawableSize`, then the logical size, when no
  snapshot was published (old boot or host). This keeps the existing default-target host green.

## Hypotheses / blockers

- None blocking. `[Unresolved]` Live EU4 may change `+0xa0/+0xa4` without SetRT; reconcile at
  clear/present covers it but is not demonstrated live.
- `[Unresolved]` A headless host may return nil from `nextDrawable`; the real-drawable test
  reports that as skipped (`exit 77`) rather than a pass.

## Test results

- `python3 -m unittest tests.test_gfx_hidpi -v`: 3 tests pass (deterministic lifecycle,
  ordinary pending second clear is fatal with `exit 2`, real drawable scale change).
- `MTL_DEBUG_LAYER=1 MTL_DEBUG_LAYER_ERROR_MODE=assert python3 -m unittest
  tests.test_gfx_default_target tests.test_gfx_init tests.test_metal_boot -v`: 7 tests pass.
- `MTL_DEBUG_LAYER=1 MTL_DEBUG_LAYER_ERROR_MODE=assert python3 -m unittest tests.test_gfx_buffers
  tests.test_gfx_entry_registry tests.test_gfx_shadow_render -v`: 35 tests pass.
- No EU4 launch, no commit/reset/push.

## Next

- Independent PR review is the merge gate. If it asks for live coverage, add a measured
  backing-scale run on real hardware; nothing else is outstanding.
