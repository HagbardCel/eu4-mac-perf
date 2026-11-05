# Progress

## Objective

Reconstruct and implement `_GfxSetScissorRectangle` in `benchmark/eu4_gfx_init.{h,mm}`, register
it, and cover it with the gfx-init host and registry tests. The research checkpoint
`analysis/automation/pilot-gfx-set-scissor-rectangle/note.md` was captured by the checker
(sha256 `1c4790868a7356b577d08ed06e6f5263c43b8885a5e17ce8c591f7ebff36a463`).

## Findings

- Entry `0x15ed0aa`, body `[0x15ed0aa, 0x15ed0e2)`, 18 instructions; the last is the 5-byte
  tail `JMP 0x1018c2672` = `_glScissor`.
- Patchable prefix `55 48 89 e5 0f bf 56 04`, ending on the instruction boundary at
  `0x15ed0b2`.
- SysV `void f(void *handle, const int16_t rect[4])`: `RDI` handle, `RSI` rect; `[handle]` is
  the master and `[master+0xac]` is the viewport height.
- Rectangle fields are signed 16-bit `x1,y1,x2,y2` in top-left pixels. Width and height are
  32-bit differences clamped to zero when negative. Original GL effect:
  `glScissor(x1, viewport_height - y2, w, h)`. Original writes no memory.
- Callers: `RenderGfx2dList` (call `0x149d6e8`) and `Render` (call `0x14ae4bb`).

## Implementation

- `benchmark/eu4_gfx_init.h`: `EU4GfxBackend` now carries pending scissor validity plus x, y,
  width, height; declared `eu4_gfx_set_scissor_rectangle` and the
  `eu4_gfx_scissor_rectangle` getter.
- `benchmark/eu4_gfx_init.mm`: replacement validates the handle/rect, turns `[handle]` into
  the master, requires a published backend, clamps negative spans, and stores the
  top-left-origin rectangle. The encoder-facing consumer is
  `-[MTLRenderCommandEncoder setScissorRect:]`; the GL `viewport_height - y2` conversion is
  documented and not needed for Metal's top-left coordinates.
- `benchmark/eu4_gfx_entries.def`: registered
  `GFX_ENTRY(_GfxSetScissorRectangle, 0x15ed0aa, 0x15ed0e2, eu4_gfx_set_scissor_rectangle,
  "\x55\x48\x89\xe5\x0f\xbf\x56\x04")`.
- `tests/gfx_init_host.mm`: `test_scissor` covers unset -> set -> changed rect, negative
  coordinates, inverted/zero spans, viewport-height independence, null/no-backend no-ops, and
  feeds the stored rectangle to a real `MTLRenderCommandEncoder` on a 64x64 render target.
- `tests/test_gfx_entry_registry.py`: added the scissor baseline tuple and `_SCISSOR` prefix.

## Test results

- `python3 -m unittest tests.test_gfx_init tests.test_gfx_entry_registry`: 24 tests pass.
- `eu4_metal_boot.build_library('x86_64')`: builds `benchmark/.build/libeu4_metal_boot.dylib`.
- No EU4 launch, corpus rebuild, or reconstruction-directory read. No commit, reset, or push.

## Next

- Implementation and tests are green; hand the worktree to the external checker.
