# Metal init acceptance

Pinned executable SHA-256 `b3d38876abf4e61cdae57509186d7cb7dcb03bfeaca4c95c10c713794715141d`.

One process, pid 53881. Init thread and create thread are both `2491081`. `started_utc` is `2026-11-02T20:03:32.445969+00:00`, taken before launch. `init.json` records success 1. `create.json` records success 1 for `_GfxCreateVertexBuffer` at RVA `0x14d44e4`, and its pid matches the session. `stop.json` and `blocked_at.json` both name `glScissor` at RVA `0x15ea5ec` on that thread. Exit code 2, launcher did not signal. Classification is `blocked_at`. No draw receipt.

`_GfxAddPixelShader` (`0x15eb425`) returns 0 and writes nothing. This process did not stop inside that function and did not crash. The stop is inside original `_GfxClear` (`0x15ea552`). At RVA `0x15ea5cf` it loads `edi` from `+0xbc`, `esi` from `+0xc0`, `edx` from `+0xb0`, and `ecx` from `+0xac` of the object in `rbx`, then calls `glScissor`. The stop address `0x15ea5ec` is the return from that call. The caller is outside the replaced bodies.

This is the scissor in `_GfxClear`. It is not `_GfxLinkShader`, and it is not a draw.
