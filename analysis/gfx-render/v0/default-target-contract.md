# Game-owned default render target (P5.1)

Bounded Metal support for a persistent game-owned intermediate `BGRA8Unorm` backbuffer, `_GfxSetRenderTarget(null)`, production clear onto that buffer, and `_GfxPresent` that blits to a drawable. Distinct from the P4 diagnostic offscreen target. Not a draw, shader, full frame, or live EU4 load.

## Acceptance claim

With game mode enabled, SetRT(null) selects the default backbuffer, a supported color clear produces GPU pixels on that buffer, and present copies those pixels to a drawable (or a host-injected destination) and completes. Unsupported and failed states fail closed.

## ABI and patch spans

Instruction-verified against the pinned x86_64 `eu4` image (`__TEXT.__text` file offset equals image RVA for these entries).

| Function | Entry | End (exclusive) | Replacement | Prefix |
| --- | --- | --- | --- | --- |
| `_GfxSetRenderTarget` | `0x15ed004` | `0x15ed09d` | `eu4_gfx_set_render_target` | `\x55\x48\x89\xe5\x41\x57` |
| `_GfxPresent` | `0x15ea6cb` | `0x15ea6df` | `eu4_gfx_present` | `\x55\x48\x89\xe5\x48\x8b\xbf\x78\x01\x00\x00` |

### `_GfxSetRenderTarget`

SysV: `void f(void *handle, void *target)`. `(%rdi)` is the master. `rsi` is the target object or null.

Null path (static-verified): bind framebuffer 0; write viewport from master window size at `+0xa0` (width) / `+0xa4` (height) into `+0xac` (height), `+0xb0` (width), preserve `+0xb4`/`+0xb8`, zero `+0xbc`/`+0xc0`; store null at `+0x78`. Original also calls `glViewport(0,0,w,h)`.

Non-null: unsupported in P5.1. Reject **before** mutating `+0x78` or the viewport image.

Allocate or validate the game backbuffer **before** those master writes.

### `_GfxPresent`

SysV: `int f(void *master)`. `rdi` is the master (loads window at `+0x178`). Original returns `1` after `SDL_GL_SwapWindow`.

The registered prefix is the complete 11-byte prologue (`push rbp; mov rsp,rbp; mov 0x178(%rdi),rdi`); the next byte is the call. Instruction boundaries inside `_GfxPresent` are 1, 4, 11, 16, 18, 20, so 11 is the shortest complete prefix that covers the body's own `push`/`mov` without splitting an instruction. The entry patcher therefore accepts 5–11 byte prefixes: a 5-byte `jmp rel32` plus `NOP` padding, with 11-byte saved/encoded slots. This is the single justified widening of the previous 5–8 range.

## Present dimension and format gate (F2)

Present fails closed **before** submit unless the destination is exactly the game backbuffer:

```
destination.width  == backend.gameWidth
destination.height == backend.gameHeight
destination.pixelFormat == MTLPixelFormatBGRA8Unorm
```

No upscale, crop, or larger-destination acceptance: the original `_GfxInit` sizes the backbuffer from `SDL_GetWindowSize` and `_GfxPresent` only calls `SDL_GL_SwapWindow`, so a destination of another size or format has no defined mapping. A pre-submit rejection preserves `gameContentsKnown`/`readyForPresent` so a retry after the destination matches is allowed.

Coverage policy: the native host asserts the exact-match success path (game size, `BGRA8Unorm`) through the production `eu4_gfx_set_render_target(NULL)` and `eu4_gfx_present` wrappers, and asserts rejection for unequal width, larger height, and a non-`BGRA8Unorm` pixel format, checking both flags survive each rejection.

## Present and drawable verification (F2)

Production: blit game → drawable → `presentDrawable` → commit → wait. No staging alloc.

Test-only opt-in (same command buffer): blit game → drawable (or injected destination texture) → blit destination → staging → present when using a real drawable → commit → wait → validate staging. Requires `layer.framebufferOnly = NO` on the Metal boot path. Host uses deterministic seams for nil-drawable and staging-alloc failure.

Limitation (not a live-drawable claim): every automated size/format check uses a host-injected `MTLTexture` destination, because `CAMetalDrawable` texture dimensions are fixed by the layer (`drawableSize` from `convertRectToBacking:`) and cannot be authored at an arbitrary size by the test. Those tests prove the destination-blit path and exact size/`BGRA8Unorm` gates on that injected texture; they do not exercise `nextDrawable` acquisition or `presentDrawable` scheduling.

## Activation

`eu4_gfx_enable_game_window()` selects game mode **before** `_GfxInit`. `metal_init` calls it. Worker and default hosts stay diagnostic (flag off).

Allocation runs only after layer/device/queue exist inside init. Failure rolls back init; no success `init.json`. Dimensions are the window size already stored at master `+0xa0`/`+0xa4`.

## Production clear routing

| Mode | `eu4_gfx_clear` destination |
| --- | --- |
| Game enabled, `+0x78` null | Game backbuffer |
| Game disabled, diagnostic configured | P4 offscreen (preserve host wrapper) |
| Neither | Unsupported → stop |
| Game enabled, non-null RT | Unsupported; no diagnostic fallback |

`eu4_gfx_clear_checked` remains **diagnostic only**. Isolation: both targets may exist; checked clears never write the game buffer, and production game clears never write `colorTarget`.

## Viewport gate (game clear)

Authoritative for game clear: the master viewport image (`+0xac` height, `+0xb0` width, `+0xbc`/`+0xc0` origin) must equal full game backbuffer `(0,0,gameWidth,gameHeight)`. That image is written by SetRT(null) from window size—the original FB0 path—not invented solely for the P4 validator. Same color-mask, scissor, flags, and color gates as [clear-contract.md](clear-contract.md).

## State machine (F1)

Flags: `gameContentsKnown`, `readyForPresent`, at most one `pendingGameCommand` (separate from diagnostic `pendingClear`).

Present evaluation order:

```
if (pendingGameCommand != nil) {
    if (complete_game_command() != OK) return FAILED; // sets known+ready on OK
}
if (!gameContentsKnown || !readyForPresent) return UNSUPPORTED;
return present_game_backbuffer();
```

| State | Op | Behavior |
| --- | --- | --- |
| Clear pending | Second game clear | Reject |
| Clear pending | Present | Drain then present (order above) |
| Clear pending | Reconfigure / SetRT alloc replace | Reject |
| Not known/ready | Present | Reject (after drain) |
| Present OK | Repeat present | Reject (`readyForPresent` cleared; pixels remain known) |
| Present fail pre-submit | Retry | Preserve known/ready |
| GPU fail post-commit | Any further | Terminal (poisoned backend; production stops) |

## Flag reset (F3)

| Event | State |
| --- | --- |
| Target allocated / resize replace | Unknown, not ready |
| Clear submitted | Unknown, not ready, pending retained |
| Clear completed OK | Known, ready, no pending |
| Present rejected pre-submit | Previous known/ready preserved |
| Present completed OK | Known, **not** ready |
| Committed GPU failure | Poisoned; no further ops |
| Shutdown | Drain game and diagnostic pending, then release |

## Shutdown

Drain `pendingGameCommand` and `pendingClear` before releasing the backend. GPU failure during drain is terminal.

## Non-goals / non-claims

Draws, shaders, custom RTs, depth/stencil, worker protocol changes, multi-frame present, live EU4 as offline gate. GPU-failure poisoning is implemented; fault-injection coverage is host-seam based for pre-submit failures only.

HiDPI follow-up: [PR5.1.1](hidpi-contract.md) keeps the 1:1 present blit and this
fail-closed exact-match gate, and separates the engine's logical `+0xa0/+0xa4` window size
from the physical `drawableSize` backbuffer. Where the two contracts differ, the HiDPI
contract governs the dual geometry; the P5.1 present and state-machine invariants still
apply.
