# P5.1 remediation research note (`_GfxPresent`, `_GfxSetRenderTarget`, dimensions)

Scope: PR #162 P5.1 findings. The intermediate game backbuffer design is not changed. No
shader, draw, or EU4 run is part of this note. Claims are labelled `[SDK]`, `[Apple docs]`,
`[Project-derived]`, or `[Unresolved]`.

Pinned image: `reverse_engineering/target.json` (`eu4-gog-macos-1.37.5-x86_64`,
sha256 `b3d38876...715141d`). `__TEXT.__text` has `addr 0x100009d00`, `offset 0x9d00`, so the
file offset equals the image RVA (`rva - 0x100000000`) for the spans below. Instruction bytes
were recovered with `otool -tv -p <symbol>` and checked against the raw bytes at the same file
offset. `[Project-derived]`

## Recovered instruction spans

### `_GfxPresent`, entry `0x15ea6cb`, end `0x15ea6df` (exclusive)

Body bytes: `55 48 89 e5 48 8b bf 78 01 00 00 e8 55 0a ce ff b0 01 5d c3`.

| RVA | Bytes | Instruction |
| --- | --- | --- |
| `0x15ea6cb` | `55` | `push %rbp` |
| `0x15ea6cc` | `48 89 e5` | `mov %rsp,%rbp` |
| `0x15ea6cf` | `48 8b bf 78 01 00 00` | `mov 0x178(%rdi),%rdi` |
| `0x15ea6d6` | `e8 55 0a ce ff` | `call 0x1012cb130` = `_SDL_GL_SwapWindow` |
| `0x15ea6db` | `b0 01` | `mov $1,%al` |
| `0x15ea6dd` | `5d` | `pop %rbp` |
| `0x15ea6de` | `c3` | `ret` |

Complete-instruction boundaries measured from the entry are therefore lengths `1, 4, 11, 16,
18, 20`. The registered prefix `55 48 89 e5 48 8b` (6 bytes) ended at `0x15ea6d1`, inside the
7-byte `mov` at `0x15ea6cf`. It was not an instruction boundary. `[Project-derived]`

The original has no sizing, drawable, or pixel-format work. Its only non-logging call is
`SDL_GL_SwapWindow(master+0x178)` and it returns `1`. `[Project-derived]`

### `_GfxSetRenderTarget`, entry `0x15ed004`, end `0x15ed09d` (exclusive)

First instructions: `55` (`push %rbp`), `48 89 e5` (`mov %rsp,%rbp`), then
`41 57 41 56 41 54 53` (push `r15`, `r14`, `r12`, `rbx`). The registered 6-byte prefix
`55 48 89 e5 41 57` ends on the complete `push %r15` boundary. Null path: bind framebuffer 0;
write `+0xac` from `+0xa4` (height), `+0xb0` from `+0xa0` (width), preserve `+0xb4`/`+0xb8`,
zero `+0xbc`, call `glViewport(0,0,width,height)`, then store the null target at `+0x78`.
`[Project-derived]`

## `_GfxPresent` patch-prefix decision

No complete boundary exists between 5 and 8 bytes: the valid lengths are `1, 4, 11, ...`.
The registered prefix must therefore be the complete 11-byte prologue
`55 48 89 e5 48 8b bf 78 01 00 00`.

Narrowly justified alternative: raise the entry patcher maximum from 8 to 11 bytes for this
one entry rather than call a mid-`mov` prefix instruction-complete. The encoded patch is still
`e9 rel32` followed by `nop` up to the declared length, so the saved/encoded slot and the
prepare bound become 11 bytes. No other registry entry is longer than 8, and no other
replacement or patch mechanism changes. `[Project-derived]`

`benchmark/eu4_metal_boot.py` was outside the agent allowlist during the campaign, so the
agent recorded an 8-byte parser cap as `[Unresolved]`. Independent apply raises that parser
bound to 11 to match the C patcher and the registered Present prologue. `[Project-derived]`

## Dimension and present-coverage policy (frozen)

- `_GfxInit` calls `SDL_GetWindowSize(window, &w, &h)` and stores `w` at master `+0xa0`, `h`
  at `+0xa4`. It does not call `SDL_GL_GetDrawableSize` in that span. `[Project-derived]`
- `SDL_GetWindowSize` reports window coordinates; on a backing-scaled display these are
  points. `[Project-derived]` `[Apple docs]` A `CAMetalLayer.drawableSize` is in pixels,
  normally the layer size multiplied by `contentsScale`.
- `benchmark/eu4_metal_boot.m` sets `layer.drawableSize` from
  `[child convertRectToBacking:child.bounds].size`, i.e. backing pixels, not
  `SDL_GetWindowSize`. `[Project-derived]` Whether a given SDL window is itself backed at
  scale 2 is not established by the pinned instructions. `[Unresolved]`
- The original evidence supports window-size (`SDL_GetWindowSize`) game dimensions, not a
  drawable-pixel game backbuffer. The intermediate backbuffer is therefore left at window
  size. The layer is not shrunk to match it, and no drawable-pixel backbuffer is invented
  without evidence. `[Project-derived]`
- Until the point/pixel mapping is resolved, present fails closed before submit:
  `destination.width == gameWidth`, `destination.height == gameHeight`, and
  `destination.pixelFormat == MTLPixelFormatBGRA8Unorm` are all required. A mismatch returns
  failed with `gameContentsKnown` and `readyForPresent` unchanged. `[Project-derived]`
  This guarantees the blit covers every destination pixel whenever present reports success.
- Real-drawable present remains covered by the host-injected `MTLTexture` seam, not a live
  `CAMetalDrawable`. `[Project-derived]` The injected texture stands in for
  `drawable.texture`; the live drawable path and its swap timing remain `[Unresolved]`.

## Metal contracts and headers read

- `analysis/metal-api/index.md` and `analysis/metal-api/textures.md`. `[Project-derived]`
- `MTLBlitCommandEncoder.h`: `copyFromTexture:...toTexture:...` copies a rectangle of pixels
  between textures. `[SDK]`
- `MTLTexture.h`: `width`, `height`, and `pixelFormat` are read-only texture properties.
  `[SDK]`
- `CAMetalLayer.h`: `pixelFormat` supports `MTLPixelFormatBGRA8Unorm` and
  `MTLPixelFormatBGRA8Unorm_sRGB`; `drawableSize` is the drawable pixel size, typically layer
  size times `contentsScale`; `framebufferOnly = NO` permits sampling/read-write use. `[SDK]`
- A blit between different pixel formats is not described by the header as a conversion.
  `[Unresolved]` The replacement therefore requires the destination format to equal the
  game backbuffer `BGRA8Unorm` instead of relying on an implicit conversion.
- Blit coverage is the requested source rectangle. A smaller-than-destination copy leaves
  destination pixels unwritten, so exact size equality is the frozen gate. `[Project-derived]`

## Boundary tests derived from the mismatches

- Present with a destination wider/taller or narrower/shorter than the game backbuffer
  (`16x16` game vs `32x16`, `32x32`, `8x8`) fails before submit and preserves
  `gameContentsKnown`/`readyForPresent` (`unequal`, `oversize`).
- Present with a `RGBA8Unorm` injected destination fails before submit and preserves both
  flags (`bad_format`).
- Supported path: production `eu4_gfx_set_render_target(handle, NULL)` and
  `eu4_gfx_present(master)` both succeed; present returns `1` and the full injected
  destination is written.
- Patcher: an 11-byte complete prefix prepares, commits, restores, and rolls back like the
  existing 5- and 8-byte cases.
