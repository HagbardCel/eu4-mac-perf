# HiDPI game-backbuffer lifecycle contract (PR5.1.1)

Bounded extension of [default-target-contract.md](default-target-contract.md). The P5.1
intermediate `BGRA8Unorm` backbuffer, its single pending game command, and the 1:1 present
blit stay in place. This contract adds a logical/physical geometry split and a geometry
transition state. It does not add draws, shaders, scissored clears, custom render targets,
Retina UI/font scaling, or a live EU4 run.

## Acceptance claim

With game mode enabled, the game backbuffer is allocated at the published physical drawable
size, EU4's master width/height and viewport remain logical, and present copies the physical
buffer 1:1 to an exactly matching `BGRA8Unorm` drawable. A geometry change between the
contents' generation and the current generation makes clear/present decline without waiting,
submitting, or presenting obsolete pixels. Unsupported and failed states fail closed as in
P5.1.

## Geometry

```c
effective = (logicalWidth, logicalHeight, physicalWidth, physicalHeight)
```

| Term | Source | Units |
| --- | --- | --- |
| `logicalWidth` | master `+0xa0` (`SDL_GetWindowSize` in `_GfxInit`) | window points |
| `logicalHeight` | master `+0xa4` | window points |
| `physicalWidth` | Cocoa/main publish, normally `drawableSize.width` | drawable pixels |
| `physicalHeight` | Cocoa/main publish, normally `drawableSize.height` | drawable pixels |

`[Project-derived]` `_GfxSetRenderTarget(null)` writes the logical viewport from
`+0xa0/+0xa4`; `_GfxClear` supplies that viewport to `glScissor`. The game clear's viewport
gate therefore compares the viewport image to the **logical** size, while the Metal render
pass clears the full **physical** attachment.

### Publication API

```c
void eu4_gfx_publish_physical_geometry(uint32_t width, uint32_t height);
```

- Cocoa/main only. Replaces an immutable `{width, height, epoch}` snapshot under a short
  mutex and increments the epoch. It never reads the engine master and never dispatches to
  main.
- The Gfx path never writes the snapshot. It reads the snapshot and the logical fields and
  combines them in `reconcile_game_geometry`.
- If no valid snapshot exists (older boot or a host), init uses `layer.drawableSize`; if that
  is zero, it uses the logical size. This fallback is project-derived, not a Metal promise.

### Effective generation

- A per-backend intern table maps an effective tuple to a generation. A new tuple gets the
  next counter; a tuple seen before returns its earlier generation.
- `1x -> 2x -> 1x` returns the original generation value. A logical-only change gets a new
  generation even though the physical texture size is unchanged.
- A generation change sets `awaitingFreshClear`, clears `readyForPresent`, and records the
  new tuple. It does not allocate, wait, or submit.

## Reconcile call sites

`reconcile_game_geometry(master)` is mandatory at:

| Call site | Transition handling |
| --- | --- |
| `_GfxInit` / game RT create | commit the initial tuple and allocate the physical backbuffer |
| `_GfxSetRenderTarget(null)` | may drain pending on a transition; allocate replacement before master writes |
| production game `_GfxClear` | transition + pending -> stale; transition + idle -> reconcile, allocate/reuse, clear new buffer |
| `_GfxPresent` | after pending drain; transition -> stale before drawable acquisition |

P4 diagnostic configure/clear/complete does not call it and never emits geometry stale.

## Status model

| Status | Meaning | Production game clear | Production present |
| --- | --- | --- | --- |
| `EU4_GFX_CLEAR_OK` | work submitted, or present completed | returns | returns `1` |
| `EU4_GFX_CLEAR_UNSUPPORTED` | P5.1 invalid state (e.g. ordinary pending second clear) | stops | stops |
| `EU4_GFX_CLEAR_FAILED` | real failure with unchanged geometry | stops | stops |
| `EU4_GFX_CLEAR_GEOMETRY_STALE` | effective generation changed; nothing submitted or presented | returns normally | returns `1` |

Checked helpers return the same values and never translate a skip into `OK`. A geometry
transition is never inferred from `readyForPresent` alone.

## State additions

Alongside the P5.1 flags (`gameContentsKnown`, `readyForPresent`, `pendingGameCommand`):

| Field | Meaning |
| --- | --- |
| cached logical/physical tuple | last reconciled effective geometry |
| `geometryGeneration` | interned generation of the current tuple |
| `pendingGameGeneration` | generation captured when the pending clear was submitted |
| `clearedGeneration` | generation of the last completed game clear |
| `awaitingFreshClear` | a transition occurred; current generation has no known contents |

`drain_pending_game` sets `clearedGeneration = pendingGameGeneration`,
`awaitingFreshClear = (pendingGameGeneration != geometryGeneration)`, and advertises
`readyForPresent` only when those are equal.

## Game clear

1. Reconcile.
2. Geometry transition + pending -> `GEOMETRY_STALE` without waiting, submitting, or clearing
   the obsolete attachment.
3. Pending without a transition -> `UNSUPPORTED` (P5.1 fatal policy).
4. Allocate/reuse the backbuffer at the current physical size.
5. Existing logical viewport, color-mask, flags, scissor, and color gates apply.
6. Encode, commit, set `pendingGameGeneration = geometryGeneration`.

Waiting is prohibited inside clear. Allocation and encoding happen only after the stale and
pending rejections.

## Present

1. Observe the physical epoch.
2. Drain a pending game command.
3. Reconcile.
4. Awaiting a fresh clear or generation mismatch -> `GEOMETRY_STALE`, before acquiring a
   drawable.
5. Require `gameContentsKnown` and `readyForPresent`.
6. Acquire the injected destination or `nextDrawable`.
7. Recheck the epoch; if it moved, reconcile again. Transition/stale -> `GEOMETRY_STALE` and
   do not present the acquired drawable.
8. Require `destination.width == gameWidth`, `destination.height == gameHeight`,
   `destination.pixelFormat == MTLPixelFormatBGRA8Unorm` (physical), then blit 1:1.

A missing drawable, allocation failure, or size/format mismatch with unchanged geometry stays
`FAILED`. Repeated present after a transition stays `GEOMETRY_STALE` until a fresh game clear
completes at the current generation.

## `_GfxSetRenderTarget(null)`

- Non-null stays `UNSUPPORTED` and does not mutate the master.
- On a verified transition, waiting is allowed: drain a pending game command, then allocate
  the replacement before any master write.
- Allocation failure returns `FAILED`; the engine-visible master image is unchanged and the
  old texture is retained. Production stops.
- Without a transition, `pendingGameCommand != nil` keeps the P5.1 `UNSUPPORTED` rejection.
- Only after a successful allocation: write the logical viewport from `+0xa0/+0xa4` and
  store null at `+0x78`.

## Cocoa lifecycle

The boot host uses an `NSView` subclass for the Metal child view:

- `autoresizingMask = NSViewWidthSizable | NSViewHeightSizable` tracks parent content bounds.
- `setFrameSize:`, `layout`, and `viewDidChangeBackingProperties` recompute
  `[view convertRectToBacking:view.bounds]`.
- Each recompute sets `layer.drawableSize` and publishes physical dimensions + epoch.
- Creation publishes once before `_GfxInit` allocates the first game backbuffer.

No synchronous dispatch to main occurs while holding a renderer lock or waiting on the GPU.

## Invariants

1. Master `+0xa0/+0xa4` and the viewport image are never written with physical values.
2. The game backbuffer texture and the present destination are always the same physical size,
   `BGRA8Unorm`; the blit is 1:1.
3. A geometry transition cannot clear into, or present from, an attachment belonging to a
   different generation.
4. A stale outcome submits no command, waits on no GPU, and acquires no drawable.
5. A failed replacement leaves the previous texture and the engine-visible master intact.
6. The P4 diagnostic path is unchanged and never emits geometry stale.

## Test coverage

`tests/gfx_hidpi_host.mm` (run by `tests/test_gfx_hidpi.py`) covers, through the production
wrappers `eu4_gfx_clear`, `eu4_gfx_set_render_target`, and `eu4_gfx_present`:

- 1x and 2x physical backbuffers with unchanged master logical `+0xa0/+0xa4`
- scale-only physical change and a clear without SetRT
- resize between clear and present (present skips)
- pending clear plus resize, then production clear skips
- changed and idle: a clear without SetRT allocates and clears the new physical buffer
- repeated present remains stale
- SetRT(null) drains a pending resize and replaces the texture
- a failed SetRT replacement keeps the old texture and the master image
- ordinary pending second clear without a transition is still fatal (`exit 2`)
- logical-only generation bump
- `1x -> 2x -> 1x` generation identity
- missing drawable with unchanged geometry stays a failure
- a real `CAMetalDrawable` present before and after a geometry update when the host can
  acquire one; otherwise the test reports the host's `unavailable` path

P5.1 exact-match present remains the gate for all non-transition size/format failures.

## Non-goals / unresolved

- `[Unresolved]` The pinned SDL point-versus-pixel report is not proven; the dual model uses
  the published drawable size as the physical truth.
- `[Unresolved]` A headless host may return nil from `nextDrawable`; that path is reported as
  unavailable, never as a live-drawable pass.
- `[Unresolved]` Live EU4 logical-size changes without SetRT are handled by reconcile but are
  not demonstrated live.
