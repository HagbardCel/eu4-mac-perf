# Metal init acceptance

Pinned executable SHA-256 `b3d38876abf4e61cdae57509186d7cb7dcb03bfeaca4c95c10c713794715141d`.

One process, pid 56537. Init thread and create thread are both `2518459`. `started_utc` is `2026-11-02T20:22:20.444217+00:00`, taken before launch. `init.json` records success 1. `create.json` records success 1 for `_GfxCreateVertexBuffer` at RVA `0x14d44e4`, and its pid matches the session. There is no `stop.json` and no draw receipt. Exit code -9, `stop_reason` `deadline`, launcher signaled. Classification is `timeout`, `d1: not_started`.

The crash file `crashes/eu4_20261102_212226/exception.txt` records:

```
Date/Time: 2026-11-02 21:22:26

Caught signal 11 (SIGSEGV)

Original Stack Trace:
  2   ???                                 0x000000000000000c 0x0 + 12
  3   eu4                                 0x00000001015ecc72 GfxSetTextures + 128
```

The recorded address is `0xc`, written as `0x0 + 12`. The EU4 frame is `_GfxSetTextures` (`0x15ecbf2`). RVA `0x15ecc72` is the instruction after `call InternalSetTextureAndSamplerState` at `0x15ecc6d`. The caller is `CEU3Graphics::ShowBasicLoadText`. That call is outside the replaced bodies.

The callee's first indirect call is `callq *(%rax)` at RVA `0x15ecd06`. That instruction loads the qword at `___glewActiveTextureARB`. The crash record does not show the contents of that qword. The `_GfxClear` stub had already returned; this fault is later, during texture binding.
