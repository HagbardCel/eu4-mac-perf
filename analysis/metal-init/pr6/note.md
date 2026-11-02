# PR6 viewport

Pinned Mach-O SHA-256 `b3d38876abf4e61cdae57509186d7cb7dcb03bfeaca4c95c10c713794715141d`. RVAs are from image base `0x100000000`.

## ABI

`_GfxSetViewPort` (`0x15ea4f8`) is the 13-instruction body `[0x15ea4f8, 0x15ea52c)`. The entry prefix is `55 48 89 e5 f3 0f 6f 06`: `push %rbp`, `mov %rbp, %rsp`, then `movdqu (%rsi), %xmm0`. A 5- or 6-byte patch would split that `movdqu`. `rdi` is the master. `rsi` points at 24 bytes. The body copies that image to `[rdi+0xac]` and tail-jumps at `0x15ea527` to the `_glViewport` thunk at `0x18c26ae`. The GL arguments are `x = [rsi+0x10]`, `y = [rsi+0x14]`, `width` at `[rsi+4]`, and `height` at `[rsi+0]`. The master keeps the source order.

Both direct callers pass that rectangle as a field of `CGraphics`. `CGraphics::InitGfx` at `0x149b21a` uses `lea r15+0x2c57c` and loads the master from `[r15+0x80]`. `CGraphics::Setup2dGraphics` at `0x149d041` does the same with `rbx`. Those regions are disjoint, so the replacement is `memcpy`. It does not call GL and it does not encode a `MTLViewport`.

`_GfxGetViewPort` (`0x15ea52c`) stays original. It copies the same 24 bytes back with qword moves from `+0xbc`, `+0xac`, and `+0xb4`.

## Offline result

`python3 tests/test_gfx_init.py`, `python3 tests/test_gfx_buffers.py`, and `python3 tests/test_metal_boot.py` passed. They cover the 24-byte image, the bytes outside `+0xac` through `+0xc3`, a second update, the getter's qword order, a call with no backend, the twelve-entry transaction with slot 0 at the 8-byte prefix, rejection of a flipped last prefix byte, rollback after eleven writes, and the diagnostic bounds `0x15ea4f8`, `0x15ea4f9`, `0x15ea52c`, `0x15ea52d`, and `0x149b21f`.

## Live result

[results/20261102T120220Z-metal-init](../../../results/20261102T120220Z-metal-init) is pid 20282. `init.json` records success 1 on thread 1856966. `create.json` records success 1 for `_GfxCreateVertexBuffer` at RVA `0x14d44e4` on that thread. `stop.json` is `glGenTextures` at RVA `0x15ec4ed`, `after_init`, reason `gl`, same thread and the pinned image. That address is the instruction after the call at `0x15ec4e8` in `_GfxInitializeTexture`. The process exited 2. The launcher did not signal it. `metadata.json` status is `complete`.

The particle create is inside `InitGfx` before the viewport call. From `0x149b1bd` through `call _GfxSetViewPort` at `0x149b21a` the tail has no branch, and `InitGfx` returns at `0x149b25c`. The `glGenTextures` stop is outside `InitGfx` and after the create receipt on the same thread, so the viewport replacement returned and `InitGfx` returned. The previous stop at `0x149b21f` did not recur. The next dependency is that `glGenTextures` call. Its direct callers are `_GfxCreateTexture` and `CTextureHandler::ReloadTexture`. This session does not identify which one ran.
