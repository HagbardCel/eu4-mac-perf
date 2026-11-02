# Metal init acceptance

Pinned executable SHA-256 `b3d38876abf4e61cdae57509186d7cb7dcb03bfeaca4c95c10c713794715141d`.

One process, pid 69436. Init, create, and stop thread are all `2671899`. `started_utc` is `2026-11-02T22:13:30.708474+00:00`, taken before launch. `init.json` records success 1. `create.json` records success 1 for `_GfxCreateVertexBuffer` at RVA `0x14d44e4`, and its pid matches the session. Exit code 2, launcher did not signal.

`stop.json` names `_GfxSetDepthStencilState` with reason `unsupported`. The recorded return is RVA `0x149d061`, the instruction after `call _GfxSetDepthStencilState` in `CGraphics::Setup2dGraphics`. `Render2dObjectsToScreen` calls `Setup2dGraphics` at `0x149d0d3`. In `CEU3Graphics::ShowBasicLoadText`, the instruction after `call _GfxDraw` is the call to `Render2dObjectsToScreen`. There is no `draw_invocation.json`. The draw shim returned, and this later replacement stopped the process. Triage is `unsupported`. `d1` is `not_started`.

This session has no `selection`, invocation id, or skipped count, because the terminal stop is not a `_GfxDraw` receipt. It is not a Decision 0003 candidate, and it is not `packet_ready`.
