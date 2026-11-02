# Metal init acceptance

Pinned executable SHA-256 `b3d38876abf4e61cdae57509186d7cb7dcb03bfeaca4c95c10c713794715141d`.

One process, pid 62930. Init, create, and draw thread are all `2612664`. `started_utc` is `2026-11-02T21:37:09.611747+00:00`, taken before launch. `init.json` records success 1. `create.json` records success 1 for `_GfxCreateVertexBuffer` at RVA `0x14d44e4`, and its pid matches the session. `stop.json` and `draw_invocation.json` both name `_GfxDraw` at caller RVA `0xedfcd1` in `CEU3Graphics::ShowBasicLoadText`. Provenance is `game`. Exit code 2, launcher did not signal. Classification stays `draw_reached`.

The snapshot read all `0x171` master bytes. `shader_ptr` is `0x7f77e3c3a5d0`. `effective_count` is `0`. `attrib_dirty` is `1`. `rsi` is 0, so the count is the signed dword at master `+0x64`, which is also 0. The saved `edx` is 0, and the mode dword at `+8` is 5. The missing-shader return is not taken. The tail would call `_glDrawArrays` with mode 5, first 0, and count 0.

This invocation submits no primitives. The shader pointer is a captured nonzero value, so the failure-return stubs did not leave this draw without a master shader. A count of 0 is not `draw_nonvisible` by itself: this capture does not show whether a later call at `0xedfcd1` has a positive count. It is not a replayable packet.
