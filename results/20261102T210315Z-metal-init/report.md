# Metal init acceptance

Pinned executable SHA-256 `b3d38876abf4e61cdae57509186d7cb7dcb03bfeaca4c95c10c713794715141d`.

One process, pid 59605. Init thread, create thread, and draw thread are all `2565568`. `started_utc` is `2026-11-02T21:03:19.015944+00:00`, taken before launch. `init.json` records success 1. `create.json` records success 1 for `_GfxCreateVertexBuffer` at RVA `0x14d44e4`, and its pid matches the session. `stop.json` and `draw_invocation.json` both name `_GfxDraw` at caller RVA `0xedfcd1` on that thread. Provenance is `game`. Invocation id is 1. `context_complete` is true with `context_want` 0. Exit code 2, launcher did not signal. Classification is `draw_reached`.

`CEU3Graphics::ShowBasicLoadText` calls `_GfxSetTextures` at RVA `0xedfcc0` and `_GfxDraw` at RVA `0xedfccc`. The stop address `0xedfcd1` is the instruction after that draw call. The texture stub had returned. The receipt registers are `rsi` 0, `rdx` 0, and `rcx` 1.

This is the original game call into `_GfxDraw`. The shader failure returns, the clear stub, and the texture stub did not issue a GPU draw, so the receipt does not reconstruct the original binding workload. D1b and D1c remain open.
