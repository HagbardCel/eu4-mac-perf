# Metal init acceptance

Pinned executable SHA-256 `b3d38876abf4e61cdae57509186d7cb7dcb03bfeaca4c95c10c713794715141d`.

One process, pid 36020. The process exited 2 on its own. The launcher did not send a signal. Init thread, create thread, and stop thread are all `2134566`. `init.json` records success 1. `metadata.json` status is `blocked_at`.

`create.json` records success 1 for `_GfxCreateVertexBuffer` at RVA `0x14d44e4`, the same return as the previous session.

The stop is `_GfxInitializeTexture` with reason `blocked_at`. `blocked_at.json` names branch `format`, format 9, kind 0. The caller RVA is `0x15ec663`, the instruction after the call at `0x15ec662` in `_GfxCreateTexture`. Format 9 is 2D and not one of the compressed formats 1–7. The replacement accepts only BGRA formats `0xb` through `0xe`. The previous `glGenTextures` stop at `0x15ec4ed` did not recur. No `_GfxDraw*` receipt was written.

The next dependency is format 9 inside `_GfxInitializeTexture`.
