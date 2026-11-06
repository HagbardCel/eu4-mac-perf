Implement PR5.1.1 HiDPI lifecycle on this pinned worktree (`d873e5c`). Do not redesign the P5.1 intermediate backbuffer model. Do not launch EU4. Do not broaden into shaders, draws, scissored clears, or Retina UI/font scaling.

Load the `opengl-metal-reconstruction` skill before editing implementation. Inspect original `_GfxPresent` / `_GfxSetRenderTarget` / `_GfxClear` instructions, Metal drawable/blit/format contracts under `analysis/metal-api/`, and how `CAMetalLayer.drawableSize` relates to SDL window size in `benchmark/eu4_metal_boot.m`. Label claims `SDK`, `Apple docs`, `Project-derived`, or `Unresolved`.

Write `analysis/automation/eu4-p511-hidpi/note.md` and `analysis/gfx-render/v0/hidpi-contract.md` before changing replacement code. Record dual logical/physical geometry, `EU4_GFX_CLEAR_GEOMETRY_STALE` production policy, reconcile call sites, generation/transition lifetime, SetRT drain-on-transition, and unresolved assumptions. A line starting with `BLOCKER:` stops the campaign when a faithful fix still requires an unsupported guess. Leave a captured research checkpoint alone; later corrections go under `## Follow-up`.

## Required behavior

1. **Dual coordinates.** Keep EU4 master `+0xa0/+0xa4` and viewport logical. Allocate the game backbuffer at physical `drawableSize` pixels. Present remains a 1:1 GPU blit (exact size + BGRA8Unorm).

2. **Physical snapshot vs logical master.** Cocoa/main publishes immutable physical dimensions + epoch only. Gfx path reads logical dims from master and combines them in `reconcile_game_geometry`. Do not read master from Cocoa callbacks. Logical-only changes must advance effective generation.

3. **`reconcile_game_geometry` call sites.** Mandatory: `_GfxInit` / game RT create, `_GfxSetRenderTarget(null)`, production game `_GfxClear`, and `_GfxPresent`.

4. **`EU4_GFX_CLEAR_GEOMETRY_STALE`.** Distinct from `UNSUPPORTED`/`FAILED`. Checked helpers must not return `OK` for a skip.
   - Game clear: geometry changed + pending → `GEOMETRY_STALE` (no wait, no submit, no clear into obsolete attachment). Geometry changed + idle → reconcile then clear the **new** backbuffer. Ordinary pending second clear without geometry change stays `UNSUPPORTED`/fatal.
   - Present order: observe → drain pending → reconcile → if awaiting fresh clear return `GEOMETRY_STALE` (no drawable) → else acquire, verify, recheck epoch, blit.
   - Production `_GfxClear` (game) and `_GfxPresent`: on `GEOMETRY_STALE` return normally / return `1` without `eu4_gfx_stop`. Do not infer stale from `readyForPresent` alone.
   - P4 diagnostic path unchanged (never emits geometry-transition stale).

5. **SetRT(null).** On verified geometry transition may drain pending, allocate replacement before mutating master. Alloc failure is fatal with engine-visible state unchanged and old texture retained. Unrelated SetRT while pending keeps P5.1 rejection. Waiting is prohibited inside clear only.

6. **Cocoa lifecycle.** Update child view + `drawableSize` on create, resize, backing-scale, and parent content bounds (autoresizing or explicit layout). No sync dispatch to main while holding renderer locks or waiting on GPU.

7. **Tests.** Add `tests/gfx_hidpi_host.mm` + `tests/test_gfx_hidpi.py`. Exercise **production** wrappers (`eu4_gfx_clear`, `eu4_gfx_set_render_target`, `eu4_gfx_present`), not only `*_checked`. Cover: 1×/2× physical BB with unchanged master logical fields; scale-only change without SetRT; resize between clear and present (present skip); pending+resize then clear skip; repeated present stays stale; SetRT drain after pending resize; logical-only gen bump; 1×→2×→1× gen identity; unexpected missing drawable with unchanged geometry stays failure class; real `CAMetalDrawable` path before and after geometry update (else document host `unavailable` path—checker maps skipped-only suite to `unavailable`, never `pass`).

Keep P5.1 exact-match present for non-transition failures. Cross-link `default-target-contract.md` only; do not weaken it.

## Editable paths only

- `analysis/automation/eu4-p511-hidpi/note.md`
- `analysis/automation/eu4-p511-hidpi/progress.md`
- `progress.md`
- `analysis/gfx-render/v0/hidpi-contract.md`
- `analysis/gfx-render/v0/default-target-contract.md`
- `benchmark/eu4_gfx_init.h`
- `benchmark/eu4_gfx_init.mm`
- `benchmark/eu4_metal_boot.m`
- `tests/gfx_default_target_host.mm`
- `tests/test_gfx_default_target.py`
- `tests/gfx_hidpi_host.mm`
- `tests/test_gfx_hidpi.py`

Do not commit, reset, or push. Do not edit the campaign directory, the checker, or automation runner state. This worktree is not a sandbox: shell commands run as the same OS user as the runner. No new `GFX_ENTRY`.
