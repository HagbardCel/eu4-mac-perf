# Metal init acceptance

Pinned executable SHA-256 `b3d38876abf4e61cdae57509186d7cb7dcb03bfeaca4c95c10c713794715141d`.

One process, pid 20282. The process exited 2 on its own. The launcher did not send a signal. Init thread, create thread, and stop thread are all `1856966`. `init.json` records success 1. `metadata.json` status is `complete`.

`create.json` records success 1 for `_GfxCreateVertexBuffer` at RVA `0x14d44e4`, the instruction after the particle call at `0x14d44df`.

The stop is `glGenTextures` at RVA `0x15ec4ed`, the instruction after the call at `0x15ec4e8` in `_GfxInitializeTexture`. That call is outside `CGraphics::InitGfx`. The particle create is inside `InitGfx` before `call _GfxSetViewPort` at `0x149b21a`, and the tail from `0x149b1bd` to that call has no branch. `InitGfx` returns at `0x149b25c`. The later GL stop on the same thread shows that the viewport replacement returned and `InitGfx` returned. The previous stop at `0x149b21f` did not recur.

The next dependency is that `glGenTextures` call. Direct callers of `_GfxInitializeTexture` are `_GfxCreateTexture` and `CTextureHandler::ReloadTexture`. This session does not identify which one ran.
