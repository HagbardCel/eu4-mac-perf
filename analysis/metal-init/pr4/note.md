# PR4 post-init state

Pinned Mach-O SHA-256 `b3d38876abf4e61cdae57509186d7cb7dcb03bfeaca4c95c10c713794715141d`. RVAs are from image base `0x100000000`.

The live stop at RVA `0x15e9e20` is the instruction after the first `glGetIntegerv` (`0x15e9e1b`) in `GetOpenGLContextRasterizerState` (`0x15e9dfa`). That reader has three direct callers. `_GfxInit` is already replaced. `_GfxReset` calls blend first, and blend's first `glGetIntegerv` is `0x15e9fc3`, so this stop is not Reset. `_GfxOnResetMasterContext` calls the rasterizer reader first, at `0x15ea4a8`.

Its only caller is `CGraphics::OnResetDevice` (`0x149b43f`), which loads `CGraphics+0x80` into `rdi`. `CGraphics::InitGfx` calls `OnResetDevice` at `0x149b082`, after `_GfxInit`. The instruction after that call is `movaps`, so `OnResetDevice` does not read the return register. The replacement is `void`. `SetResolution` and `CheckDevice` also call `OnResetDevice`. `CheckDevice` calls `_GfxReset` first and skips `OnResetDevice` when `al` is 0.

## ABIs

`_GfxReset` (`0x15ea46b`): `rdi` is the master. The original queries blend at `+0xc`, rasterizer at `+0x54`, and depth at `+0x30`, then `mov al, 1`. It does not call a setter and it does not store a dirty byte. The replacement returns 1 and does not write the master. It does not rebuild the snapshots from `+0x80`, `+0x88`, or `+0x90`. Those slots are the stored objects. The snapshots are the current logical state.

`_GfxOnResetMasterContext` (`0x15ea49b`): `rdi` is the master. The original queries the three snapshots, then calls the setters with the objects at `+0x80`, `+0x88`, and `+0x90`, and tail-jumps to `_GfxSetRasterizerState`. The replacement calls the internal setter path with force 1, in that order, and returns. It does not query OpenGL. Force skips the snapshot `memcmp`. There is no render encoder or pipeline yet, so the force flag has no GPU-side effect. A later encoder has to honor the same flag.

Both prologues are `55 48 89 e5 53`, the same five bytes as `_GfxShutdown`.

Engine-facing state stays in the master snapshots. An unexpected GL call is an unmigrated dependency, not a value to invent.

## Offline result

`python3 tests/test_gfx_init.py` and `python3 tests/test_metal_boot.py` passed. They cover the return, a reset that keeps distinct snapshots and dirty sentinels, a forced restore of the default translations, the unsupported-object stop, the eight-entry commit, rollback after seven writes, and diagnostic addresses inside both new bodies.

## Live result

[results/20261101T224903Z-metal-init](../../../results/20261101T224903Z-metal-init) `stop.json` is not the game. A second process wrote `sdl_helper_unresolved` before the constructor checked the main executable.

[results/20261101T233006Z-metal-init](../../../results/20261101T233006Z-metal-init) is one LLDB session of pid 76944. `init.json` says success for that pid. There is no `stop.json`. Before continue, `AbortHandler` resolved in the pinned image at `0x100153e8c2` and was not hit. The fault stop is `EXC_BAD_ACCESS` at pc `0x0` on the init thread. The return address is `GfxCreateVertexBuffer + 79` at RVA `0x15ebd02`, called from `CPdxParticleObject::OnResetDevice + 128` (`0x14d44e4`), from `CGraphics::OnResetDevice` and `CGraphics::InitGfx`. That RVA is outside the eight replaced bodies, so both replacements had returned. A second continue stopped on the same exception. The debugger held the process; metadata status is `debugger_held`. This is the next dependency, not a GL diagnostic.
