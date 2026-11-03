# Index-buffer metal_init

Pinned executable SHA-256 `b3d38876abf4e61cdae57509186d7cb7dcb03bfeaca4c95c10c713794715141d`. One process, pid 85225. The graphics thread in `init.json` and `index_create.json` is `4506895`.

`index_create.json` records `_GfxCreateIndexBuffer` returning to RVA `0x14bfa60` in the pinned executable. The resource pointer is `0x7ff1f74f2970`, with `8922` indices and `17844` bytes. That is the 16-bit length. `create.json` is an earlier `_GfxCreateVertexBuffer` from the same pid and thread. `init.json` is also earlier and records success 1.

The process continued and then caught `SIGSEGV` in `CGraphics::BeginScene(bool)+39` at `0x10149d229`. The frame above it is `0x10e683000`, outside the executable. The crash file is `eu4_20261104_001401`, timestamped `2026-11-04 00:14:01`, during `CEU3Graphics::ShowBasicLoadText` from `CEU4Application::InitBase`. That call is not one of the four index-buffer replacements. The session stops on that fault.

There is no `stop.json`. The launcher deadline later signaled pid 85225 (exit `-9`). There is no `draw_invocation.json`. This is not `packet_ready` or `draw_executed`, and it does not move Decisions 0001–0003 or the D1 gates.
