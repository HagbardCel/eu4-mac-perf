# P5.1.1 lifecycle remediation research note

Scope: repair the four lifecycle defects found by PR #163 review on pinned `4ff4add`. The
P5.1 game-owned `BGRA8Unorm` intermediate backbuffer, the single pending game command, and the
1:1 present blit are not redesigned. No shader, draw, scissored clear, Retina UI/font scaling,
or live EU4 run is part of this note. Claims are labelled `[SDK]`, `[Apple docs]`,
`[Project-derived]`, or `[Unresolved]`.

## Files and headers actually read

- `benchmark/eu4_gfx_init.h`, `benchmark/eu4_gfx_init.mm`
- `benchmark/eu4_metal_boot.m`
- `tests/gfx_hidpi_host.mm`, `tests/test_gfx_hidpi.py`
- `tests/gfx_default_target_host.mm`, `tests/test_gfx_default_target.py`
- `analysis/gfx-render/v0/hidpi-contract.md`,
  `analysis/gfx-render/v0/default-target-contract.md`,
  `analysis/gfx-render/v0/clear-contract.md`
- `analysis/metal-api/index.md`, `analysis/metal-api/textures.md`,
  `analysis/metal-api/render-pass.md`
- `analysis/automation/eu4-p511-hidpi/note.md` (captured prior checkpoint; left unchanged)
- SDK headers via `xcrun --sdk macosx --show-sdk-path`:
  `QuartzCore/CAMetalLayer.h`, `Metal/MTLTexture.h`, `Metal/MTLCommandBuffer.h`,
  `AppKit/NSView.h`, `AppKit/NSWindow.h`

## Established defects in the pinned implementation

1. `[Project-derived]` `clear_game_checked` and `eu4_gfx_set_render_target_checked` decide
   "drain vs reject" from the *current call's* `transitioned` flag. A pending clear whose
   generation is already older than `geometryGeneration` becomes `UNSUPPORTED` (fatal) as
   soon as another reconcile has advanced the generation. The correct predicate is a property
   of the pending command, not of this call: `pendingGameCommand != nil &&
   pendingGameGeneration != geometryGeneration`.
2. `[Project-derived]` `intern_generation` / `_seenGeometry` returns an earlier generation for
   a repeated tuple. `1x -> 2x -> 1x` therefore reuses the first generation, so a pending
   clear from the first `1x` looks current again after the GPU drain. That re-presents pixels
   that belong to a superseded layout.
3. `[Project-derived]` `EU4MetalChildView.syncDrawableGeometry` writes `layer.drawableSize`
   and publishes in one step. The renderer can observe the new size before Cocoa has actually
   resized the layer, or can acquire a drawable of the old size after the publish. There is
   no in-progress phase and no committed epoch distinct from the publication epoch.
4. `[Project-derived]` The only real-drawable test builds a detached `CAMetalLayer`, not the
   production `EU4MetalChildView` in a real `NSWindow`. Its availability answer therefore
   cannot decide whether the window-backed production path works.

## Locked fix 1: obsolete pending

- Add `pending_is_obsolete` =
  `pendingGameCommand != nil && pendingGameGeneration != geometryGeneration`.
- Game clear, after reconcile:
  - obsolete pending -> `EU4_GFX_CLEAR_GEOMETRY_STALE` immediately; no wait, no submit, and
    **no write to `pendingGameCommand`, `pendingGameGeneration`, `gameContentsKnown`,
    `readyForPresent`, or `awaitingFreshClear`**.
  - non-obsolete pending (same generation) -> `EU4_GFX_CLEAR_UNSUPPORTED` (P5.1 fatal).
  - no pending -> allocate/reuse and submit; capture `pendingGameGeneration =
    geometryGeneration`.
- `SetRT(null)`, after reconcile:
  - obsolete pending -> drain it (waiting is allowed here), then allocate the replacement
    before any master mutation.
  - non-obsolete pending -> `UNSUPPORTED` (unchanged P5.1 rejection).
- Production wrappers keep `GEOMETRY_STALE` nonfatal: game clear returns, present returns 1.
- `[Project-derived]` This stays inside genuine geometry transitions because the predicate can
  only be true after `geometryGeneration` advanced; an ordinary pending second clear keeps
  `pendingGameGeneration == geometryGeneration` and stays fatal.

## Locked fix 2: monotonic content generations

- Delete `intern_generation`, `_seenGeometry`, `_seenGeometryCount`, `_nextGeneration`, and the
  `eu4_seen_geometry` type.
- `commit_game_geometry` assigns `geometryGeneration += 1` when the effective tuple changes,
  and does nothing when the tuple is identical. This applies to repeated layout and repeated
  publication of the same size.
- The Cocoa publication epoch is not the content generation. It advances on publication; the
  content generation advances only when `(logical, physical)` changes.
- `[Project-derived]` `A -> B -> A` produces strictly increasing generations. A pending clear
  from the first `A` is obsolete at the second `A`; after the GPU drain, present stays stale
  until a **new** clear completes at the new generation.
- `hidpi-contract.md` must drop the `1x -> 2x -> 1x` identity-reclaim claim and state strict
  monotonicity.

## Locked fix 3: two-phase Cocoa geometry transition

- Publication carries a committed snapshot plus a transition-in-progress phase:
  - `eu4_gfx_publish_geometry_transition(width, height)` (phase 1) records the intended size
    and marks a transition in progress. The renderer treats this as a genuine geometry
    transition: present/clear return `GEOMETRY_STALE` without acquiring or submitting.
  - `eu4_gfx_publish_physical_geometry(width, height)` (phase 2) commits the intended size
    after `layer.drawableSize` has been set. It advances the committed epoch and clears the
    in-progress flag; the next reconcile advances the content generation.
- `EU4MetalChildView.syncDrawableGeometry` order:
  1. compute `backing = [self convertRectToBacking:self.bounds]`;
  2. identical reobserve (frame, layer `drawableSize`, and committed snapshot all already at
     `backing`) -> return with no publication and no generation change;
  3. phase 1 publish transition-in-progress at the intended size;
  4. `layer.drawableSize = backing.size`;
  5. phase 2 publish committed epoch at the same size.
- Present after acquisition rechecks the committed epoch and the transition flag. If the
  epoch changed or a transition is in progress, reconcile and return `GEOMETRY_STALE`
  without presenting the acquired drawable. If the committed geometry is stable and the
  acquired drawable's size/format does not match it exactly, return `FAILED` (P5.1
  exact-match policy).
- Both interleavings are exercised: phase 1 before `layer.drawableSize`, and
  `layer.drawableSize` before phase 1/commit.

## Locked fix 4: production AppKit view + mandatory real drawable

- Extract the production `EU4MetalChildView` from `eu4_metal_boot.m` into
  `benchmark/eu4_metal_geometry_view.{h,m}`. Boot and the host link the same component; there
  is no test-only twin.
- The window-backed host creates an `NSApplication`, an `NSWindow`, and the production child
  view, changes bounds on the main thread, and verifies frame, `layer.drawableSize`, and the
  published/committed geometry.
- Before and after the bounds change it presents to a real `CAMetalDrawable` acquired by
  `[layer nextDrawable]` and reads pixels back through a GPU blit to a shared staging
  texture. The injected-destination texture path is not used for this availability claim.
- `[SDK]` `CAMetalLayer.nextDrawable` may return nil for an invalid property combination,
  timeout, or OOM. `[Apple docs]` changes to `layer.drawableSize` control the pixel dimensions
  of subsequently returned drawables.
- If the window-backed path cannot acquire a drawable, the Python test raises
  `unittest.SkipTest`; a skipped-only suite maps to `unavailable`, never `pass`.

## Preserved policies

- Clear stays no-wait: stale and unsupported clear paths never call `waitUntilCompleted`.
- `SetRT(null)` may wait only on the obsolete-pending drain; allocation precedes master
  writes.
- `GEOMETRY_STALE` is emitted only for a genuine logical/physical transition or an obsolete
  pending generation, never for a same-generation failure.

## Unresolved

- `[Unresolved]` The pinned SDL point-versus-pixel report is not proven; the dual model keeps
  engine master/viewport logical and the published drawable physical.
- `[Unresolved]` `nextDrawable` availability on a given headless Mac is a runtime property.
  The production window-backed test reports that as unavailable instead of claiming a pass.
- `[Unresolved]` Live EU4 logical changes without `SetRT(null)` are covered by reconcile at
  clear/present but are not demonstrated live.

No unsupported guess is required for the four fixes, so no `BLOCKER:` line is present.
