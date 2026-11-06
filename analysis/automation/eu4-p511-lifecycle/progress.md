# P5.1.1 lifecycle remediation progress

## Objective

Fix the four lifecycle defects from the PR #163 review on pinned `4ff4add` without redesigning
the renderer: obsolete pending commands, monotonic content generations, a two-phase Cocoa
geometry transition, and a window-backed production-view test with a real `CAMetalDrawable`.
No shaders, draws, scissored clears, Retina UI/font scaling, new `GFX_ENTRY`, or live EU4 run.

## Locked fixes and evidence

1. **Obsolete pending.** `pending_is_obsolete` is
   `pendingGameCommand != nil && pendingGameGeneration != geometryGeneration`. Clear returns
   `GEOMETRY_STALE` without waiting, submitting, or modifying pending state. SetRT(null)
   drains an obsolete pending, then allocates before any master mutation. Same-generation
   pending stays `UNSUPPORTED`/fatal (separate process test asserts exit 2). `[Project-derived]`
2. **Monotonic generations.** `intern_generation`/`_seenGeometry` removed;
   `geometryGeneration += 1` only on effective tuple change; identical reobserve is a no-op.
   Publication epoch is separate from content generation. `A -> B -> A` yields increasing
   generations, so the first `A` pending clear stays stale after the drain until a new clear.
   `[Project-derived]` Contract updated. `hidpi-contract.md` no longer claims identity reclaim.
3. **Two-phase cocoa transition.** Phase 1 publishes transition-in-progress with the intended
   size; phase 2 commits only after `layer.drawableSize` is set. Clear/present decline while a
   transition is in progress. Present rechecks the transition flag and committed epoch after
   acquiring a drawable; a stable committed mismatch is `FAILED`. Both interleavings are
   covered. `[Project-derived]`, `[Apple docs]` `drawableSize` controls later drawable pixel
   dimensions.
4. **Production AppKit + real drawable.** `EU4MetalChildView` extracted to
   `benchmark/eu4_metal_geometry_view.{h,m}`; boot and host link the same component. The
   window-backed test uses an `NSWindow`, the production view, main-thread bounds changes,
   frame/`drawableSize`/snapshot checks, and real `[layer nextDrawable]` presents with GPU
   readback before and after. It lives in the dedicated `tests/test_gfx_hidpi_appkit.py`
   module so a nil drawable skips the only test and becomes `unavailable`, never `pass`.
   `[SDK]`, `[Project-derived]`

## Preserved policies

- Clear stays no-wait; stale and unsupported paths never call `waitUntilCompleted`.
- SetRT may wait only while draining an obsolete pending command.
- `GEOMETRY_STALE` only for a genuine transition or an obsolete pending generation.

## Blockers

None. No `BLOCKER:` line is required; see `note.md` for labels and unresolved runtime items.

## Test results

- `python3 -m unittest tests.test_gfx_hidpi -v`: 2/2 pass (lifecycle, fatal ordinary pending).
- `python3 -m unittest tests.test_gfx_hidpi_appkit -v`: window-backed gate passes here;
  skip-only would be `unavailable`.
- Related modules (`test_gfx_default_target`, `test_metal_boot`, `test_gfx_init`,
  `test_gfx_buffers`, `test_gfx_shadow_render`, `test_gfx_entry_registry`): all pass
  (45 tests total with the two hidpi modules).

## Next

- External checker qualification and independent PR review.
