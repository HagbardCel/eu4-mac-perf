# P5.1.1 HiDPI lifecycle research note

Scope: PR5.1.1 lifecycle on the P5.1 intermediate backbuffer. The P5.1 game-owned
`BGRA8Unorm` backbuffer, its pending-clear state machine, and the 1:1 present blit are not
redesigned. No shader, draw, scissored clear, Retina UI/font scaling, or live EU4 run is part
of this note. Claims are labelled `[SDK]`, `[Apple docs]`, `[Project-derived]`, or
`[Unresolved]`.

Pinned image: `reverse_engineering/target.json` (`eu4-gog-macos-1.37.5-x86_64`, sha256
`b3d38876...715141d`). `__TEXT.__text` has `addr 0x100009d00`, `offset 0x9d00`, so the file
offset equals the image RVA for the spans below. Instruction bytes were read from the pinned
binary with `otool -tv -p <symbol>` and checked against the raw file bytes at the same file
offset. `[Project-derived]`

## Files and headers actually read

- `analysis/gfx-render/v0/default-target-contract.md`, `analysis/gfx-render/v0/clear-contract.md`
- `analysis/metal-api/index.md`, `analysis/metal-api/textures.md`, `analysis/metal-api/render-pass.md`
- `analysis/automation/eu4-p51-remediation/note.md` (P5.1 instruction and dimension findings)
- `benchmark/eu4_gfx_init.h`, `benchmark/eu4_gfx_init.mm`, `benchmark/eu4_metal_boot.m`
- `tests/gfx_default_target_host.mm`, `tests/test_gfx_default_target.py`
- SDK headers: `QuartzCore/CAMetalLayer.h`, `Metal/MTLBlitCommandEncoder.h`,
  `Metal/MTLTexture.h`, `AppKit/NSView.h`, `AppKit/NSWindow.h`

## Recovered original instruction spans

### `_GfxPresent`, entry `0x15ea6cb`, end `0x15ea6df`

`push rbp; mov rsp,rbp; mov 0x178(%rdi),rdi; call SDL_GL_SwapWindow; mov $1,al; pop rbp; ret`.
The original does no sizing, drawable, or pixel-format work. It swaps the SDL window and
returns `1`. `[Project-derived]`

### `_GfxSetRenderTarget`, entry `0x15ed004`, null path

```
0x15ed047  glBindFramebuffer(GL_FRAMEBUFFER, 0)
0x15ed050  lea 0xa0(%rbx),%rax      ; width source
0x15ed057  lea 0xa4(%rbx),%rcx      ; height source
...
0x15ed05e  movl (%rcx),%ecx
0x15ed060  movl (%rax),%edx
           glViewport(0,0,width,height)
```

The null path preserves `+0xb4`/`+0xb8`, writes the height to `+0xac` and the width to
`+0xb0`, zeroes `+0xbc`/`+0xc0`, and stores null at `+0x78`. Width and height come from
master `+0xa0`/`+0xa4`, i.e. `SDL_GetWindowSize` in `_GfxInit`. `[Project-derived]`

### `_GfxClear`, entry `0x15ea552`

```
0x15ea5cf  movl 0xbc(%rbx),%edi     ; viewport x
0x15ea5d5  movl 0xc0(%rbx),%esi     ; viewport y
0x15ea5db  movl 0xac(%rbx),%ecx     ; viewport height
0x15ea5e1  movl 0xb0(%rbx),%edx     ; viewport width
0x15ea5e7  call glScissor
```

The original clear's scissor comes from the logical viewport image, not from the drawable.
`[Project-derived]` Therefore the replacement's viewport gate stays in logical units even
when the intermediate attachment is allocated in physical drawable pixels.

## Drawable size versus SDL window size

- `_GfxInit` stores `SDL_GetWindowSize` width/height at master `+0xa0`/`+0xa4`. It does not
  call `SDL_GL_GetDrawableSize` in that span. `[Project-derived]`
- `[Apple docs]` `SDL_GetWindowSize` reports window coordinates; on a backing-scaled display
  those are points, not drawable pixels. The pinned SDL build is not proven to report points
  in this process, so the point interpretation stays partly `[Unresolved]`.
- `[SDK]` `CAMetalLayer.drawableSize` "controls the pixel dimensions of the returned
  drawable objects. The most typical value will be the layer size multiplied by the layer
  contentsScale property."
- `[Project-derived]` `benchmark/eu4_metal_boot.m` currently sets
  `layer.drawableSize = [child convertRectToBacking:child.bounds].size` once at window
  creation. `[SDK]` `convertRectToBacking:` converts a rectangle to backing-store pixels.
- `[Project-derived]` The original evidence therefore has two different coordinate spaces:
  the engine master/viewport stays logical (window points), while the drawable is physical
  backing pixels. P5.1 incorrectly forced them to be equal. PR5.1.1 keeps both spaces.

## Dual logical/physical model

- Logical geometry: `L = (master+0xa0, master+0xa4)`. Written by `_GfxInit`; the viewport
  image `+0xac/+0xb0/+0xbc/+0xc0` is written from `L` by `_GfxSetRenderTarget(null)`.
  `[Project-derived]`
- Physical geometry: `P = (published drawable width, published drawable height)`. Published
  by Cocoa/main only, together with a monotonically increasing epoch. `[Project-derived]`
- Game backbuffer allocation: `P` in `BGRA8Unorm`, `MTLTextureUsageRenderTarget |
  MTLTextureUsageShaderRead`, shared storage. `[Project-derived]`
- Present destination: the exact `P`-sized drawable texture, `BGRA8Unorm`, copied 1:1 by a
  GPU blit. There is no scale, crop, or format conversion. `[Project-derived]`
- `[SDK]` `copyFromTexture:...toTexture:...` copies a source rectangle to a destination
  rectangle. The header does not promise format conversion, so equal format stays required.
  `[Unresolved]` Whether a blit between unlike formats converts is not stated by the header;
  this replacement does not rely on it.
- `[Apple docs]` A `CAMetalDrawable.texture` has the layer's drawable dimensions and
  `pixelFormat`; `[SDK]` `CAMetalLayer.pixelFormat` supports `MTLPixelFormatBGRA8Unorm`.

### Publication

Cocoa/main calls `eu4_gfx_publish_physical_geometry(width, height)`. It replaces an immutable
snapshot `{width, height, epoch}` under a plain mutex and increments the epoch. It reads no
engine master and takes no renderer lock. `[Project-derived]`

The Gfx path calls `reconcile_game_geometry`, which reads `+0xa0/+0xa4` from the master,
reads the published physical snapshot, and combines them into one effective tuple
`(logicalWidth, logicalHeight, physicalWidth, physicalHeight)`. `[Project-derived]`

## Effective generation and transition lifetime

- A small per-backend intern table maps an effective tuple to a generation. A new tuple gets
  the next counter. Returning to a previously seen tuple returns the same generation, so
  `1x -> 2x -> 1x` recovers the original generation identity. `[Project-derived]`
- A generation change is a geometry transition. It sets `awaitingFreshClear` and clears
  `readyForPresent`; it does not itself allocate, wait, or submit. `[Project-derived]`
- A game clear records `pendingGameGeneration` at submit. The pending drain records
  `clearedGeneration` and only advertises `readyForPresent` when the pending generation
  equals the current effective generation. `[Project-derived]`
- Logical-only changes (same physical, different `+0xa0/+0xa4`) produce a different tuple and
  therefore a new generation. Even though the texture size is unchanged, the logical
  viewport changed, so the prior presentable generation is invalid. `[Project-derived]`
- Contents validity is not inferred from `readyForPresent` alone; present checks the
  transition state and `clearedGeneration` explicitly. `[Project-derived]`

## Reconcile call sites (mandatory)

| Call site | Reconcile | Allocation |
| --- | --- | --- |
| `_GfxInit` / game RT create | initial tuple committed | physical game backbuffer |
| `_GfxSetRenderTarget(null)` | yes | may drain pending on transition, then allocate replacement before master writes |
| production game `_GfxClear` | yes | allocate/reuse before encoding |
| `_GfxPresent` | yes, after pending drain | none; a transition stays stale until a fresh clear |
| P4 diagnostic clear/configure | no | unchanged; never emits geometry stale |

## Failure classes

- `UNSUPPORTED`: invalid state that P5.1 already rejects, including an ordinary second game
  clear while one command is pending and no geometry changed. Production stops. `[Project-derived]`
- `FAILED`: a real failure with unchanged geometry (missing layer/drawable, allocation
  failure, GPU failure, size/format mismatch). Production stops. `[Project-derived]`
- `GEOMETRY_STALE`: a geometry transition makes the previous generation unpresentable. No
  wait, no submit, no clear into the obsolete attachment, no drawable acquisition. The
  production game clear returns normally; production present returns `1`; neither stops.
  Checked helpers return the distinct status. `[Project-derived]`

### Game clear ordering

1. Reconcile the observed tuple.
2. Transition and a pending command -> `GEOMETRY_STALE` immediately. Do not wait or submit.
3. Pending command without a transition -> `UNSUPPORTED`.
4. Allocate/reuse the current physical backbuffer.
5. Apply the existing logical viewport, flags, mask, scissor, and color gates.
6. Encode and commit; `pendingGameGeneration = geometryGeneration`.

### Present ordering

1. Observe the physical epoch.
2. Drain a pending game command if present.
3. Reconcile the effective tuple.
4. Awaiting a fresh clear or a generation mismatch -> `GEOMETRY_STALE`, before acquiring a
   drawable.
5. Check known/ready and the target/queue.
6. Acquire the destination (injected texture or `nextDrawable`).
7. Re-check the physical epoch; if it moved, reconcile again. A transition or stale
   generation -> `GEOMETRY_STALE`; the acquired drawable is not presented.
8. Require exact physical size and `BGRA8Unorm`, then encode the 1:1 blit.

A missing drawable or a size/format mismatch with unchanged geometry remains `FAILED`
(P5.1 exact-match policy). `[Project-derived]`

### `_GfxSetRenderTarget(null)` ordering

1. Reject non-null before mutating `+0x78` or the viewport (P5.1).
2. Reconcile.
3. On a transition, a pending command may be drained (waiting is allowed here, not in clear).
4. Allocate the replacement before any master write. Allocation failure is `FAILED`, leaves
   the master image and the old texture intact, and production stops.
5. Without a transition, a pending command keeps the P5.1 `UNSUPPORTED` rejection.
6. Write the logical viewport, then store null at `+0x78`.

## Cocoa lifecycle

`[SDK]` `NSView.autoresizingMask` (`NSViewWidthSizable | NSViewHeightSizable`) keeps the child
view matched to the parent content bounds. `[SDK]` `viewDidChangeBackingProperties` is the
AppKit notification point for a backing-scale change. `[SDK]` `convertRectToBacking:` returns
backing-store pixels.

The boot child view is a small `NSView` subclass that, on create, `setFrameSize:`, `layout`,
and `viewDidChangeBackingProperties`, recomputes the backing rectangle, sets
`layer.drawableSize`, and publishes physical dimensions + epoch. It never reads the engine
master and never dispatches synchronously to main while a renderer lock is held or the GPU is
being waited on. `[Project-derived]`

## Unresolved assumptions

- `[Unresolved]` Whether the pinned SDL reports window points or pixels for a given display
  is not proven by the pinned instructions. The model treats `+0xa0/+0xa4` as logical and
  uses the published drawable size as physical; when no physical snapshot exists (old boot
  or host), init falls back to `layer.drawableSize`, then to the logical size.
- `[Unresolved]` `CAMetalLayer.nextDrawable` may return nil in a headless host (the SDK says
  nil is allowed for an invalid property combination, drawable timeout, or OOM). The host
  test reports that path as unavailable rather than claiming a live-drawable pass.
- `[Unresolved]` The exact AppKit delivery order for live resize versus backing-scale change
  is not specified here. Both paths recompute from the current view bounds, so a duplicate
  publication only advances the epoch and does not change the effective tuple.
- `[Unresolved]` Live EU4 may change `+0xa0/+0xa4` without calling `_GfxSetRenderTarget(null)`.
  Reconcile reads them at every mandatory site and advances the generation, so the
  replacement does not depend on SetRT being the only logical-change path.

No `BLOCKER:` was required: the required dual-coordinate policy is fully stated by the
contract above and does not need a guess about an unsupported Metal value.

## Boundary tests derived from the mismatches

- 1x and 2x physical backbuffers with master logical `+0xa0/+0xa4` unchanged.
- Scale-only physical change (no SetRT): present returns stale without acquiring a drawable;
  the next clear allocates the new physical buffer and present then succeeds.
- Resize between a completed clear and present: present skips as stale.
- Pending clear plus physical resize: production clear returns normally, submits nothing, and
  leaves the original pending command for present to drain.
- Repeated present after a transition remains stale.
- SetRT(null) after a pending resize drains, replaces the physical texture, and keeps the
  logical viewport.
- Logical-only change advances the effective generation.
- `1x -> 2x -> 1x` returns to the same effective generation value.
- Missing drawable with unchanged geometry remains `FAILED`.
- Real `CAMetalDrawable` present before and after a geometry update, when the host can
  acquire one.
