# PR4 metal_init

Pinned executable SHA-256 `b3d38876abf4e61cdae57509186d7cb7dcb03bfeaca4c95c10c713794715141d`.

`init.json` records success 1 on thread `926894`. The process exited 11. The launcher did not send a signal.

Execution passed the previous `glGetIntegerv` stop at RVA `0x15e9e20`. `CGraphics::InitGfx` calls `CGraphics::OnResetDevice`, which returns from `_GfxOnResetMasterContext` and then calls `CPdxParticleObject::OnResetDevice`. That function calls `_GfxCreateVertexBuffer` at RVA `0x14d44df` (entry `0x15ebcb3`). The fault is inside that call. `AbortHandler` exits 11.

`stop.json` is a different process. Its constructor cannot resolve the SDL helpers, so it records `sdl_helper_unresolved` with phase `before_init` and exits 2. That file is why the launcher reports `post_init_failure`. The next migration frontier is `_GfxCreateVertexBuffer`, not that helper.
