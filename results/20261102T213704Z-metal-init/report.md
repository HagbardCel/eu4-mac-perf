# Metal init acceptance

Pinned executable SHA-256 `b3d38876abf4e61cdae57509186d7cb7dcb03bfeaca4c95c10c713794715141d`.

One process, pid 62930. Init, create, and draw thread are all `2612664`. `started_utc` is `2026-11-02T21:37:09.611747+00:00`, taken before launch. `init.json` records success 1. `create.json` records success 1 for `_GfxCreateVertexBuffer` at RVA `0x14d44e4`, and its pid matches the session. `stop.json` and `draw_invocation.json` both name `_GfxDraw` at caller RVA `0xedfcd1` in `CEU3Graphics::ShowBasicLoadText`. Provenance is `game`. Exit code 2, launcher did not signal. Classification stays `draw_reached`.

The snapshot read all `0x171` master bytes. `shader_ptr` is `0x7f77e3c3a5d0`. `effective_count` is `0`. `attrib_dirty` is `1`. `rsi` is 0, so the count is the signed dword at master `+0x64`, which is also 0. The saved `edx` is 0, and the mode dword at `+8` is 5. The missing-shader return is not taken. The tail would call `_glDrawArrays` with mode 5, first 0, and count 0.

Invocation 1 is `draw_nonvisible`. Its effective count is 0, so `_glDrawArrays(5, 0, 0)` submits no primitives. `d1` remains `draw_reached`. This session does not show a later positive count at `0xedfcd1`, and it does not assign a count to pid 59605. D1b is not `packet_ready`.
