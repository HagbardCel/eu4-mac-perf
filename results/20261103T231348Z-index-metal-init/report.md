# Index-buffer metal_init

Pinned executable SHA-256 `b3d38876abf4e61cdae57509186d7cb7dcb03bfeaca4c95c10c713794715141d`. One process, pid 85225. The graphics thread in `init.json` and `index_create.json` is `4506895`.

`index_create.json` records `_GfxCreateIndexBuffer` at RVA `0x14bfa60` in the pinned executable. The resource pointer is `0x7ff1f74f2970`, with `8922` indices and `17844` bytes. That is the 16-bit length. `create.json` is an earlier `_GfxCreateVertexBuffer` from the same pid and thread. `init.json` is also earlier and records success 1. `index_create.json` is not proof the caller resumed.

There is no `stop.json`. The launcher deadline later signaled pid 85225 (exit `-9`). There is no `draw_invocation.json`. This is a launcher-deadline session. It is not `packet_ready` or `draw_executed`, and it does not move Decisions 0001–0003 or the D1 gates.

Operator-reported excerpt. The crash report's own `Date/Time: 2026-11-04 00:14:01`. The crash file and its `meta.yml` contain no pid.

```
Caught signal 11 (SIGSEGV)

  2   ???                                 0x000000010e683000 0x0 + 4536676352
  3   eu4                                 0x000000010149d229 CGraphics::BeginScene(bool)              + 39
```

That fault is outside the four index-buffer replacements. The session stays a launcher deadline (exit `-9`, no `stop.json`).
