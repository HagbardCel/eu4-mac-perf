# Metal init acceptance

Pinned executable SHA-256 `b3d38876abf4e61cdae57509186d7cb7dcb03bfeaca4c95c10c713794715141d`.

One process, pid 72943. Init, create, and stop thread are all `2706913`. `started_utc` is `2026-11-02T22:39:48.015170+00:00`, taken before launch. `init.json` records success 1. `create.json` records success 1 for `_GfxCreateVertexBuffer` at RVA `0x14d44e4`, and its pid matches the session. Exit code 2, launcher did not signal.

`_GfxSetDepthStencilState` at `0x149d061` returned. `Setup2dGraphics` then tail-jumps to `_GfxSetRasterizerState` at `0x149d07d` with the pointer at `CGraphics+0xa8`. That setter did not stop the process. There is no `depth_state.json`.

`stop.json` names `_GfxInitializeTexture` with reason `blocked_at`. The recorded return is RVA `0x15ec663`, the instruction after `call _GfxInitializeTexture` in `_GfxCreateTexture`. `blocked_at.json` records branch `compressed`, format 5, kind 0. There is no `draw_invocation.json`. Triage is `blocked_at`. `d1` is `blocked_at`.

This session has no `selection`, invocation id, or skipped count. It is not a Decision 0003 candidate, and it is not `packet_ready`.
