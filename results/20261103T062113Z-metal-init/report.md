# Metal init acceptance

Pinned executable SHA-256 `b3d38876abf4e61cdae57509186d7cb7dcb03bfeaca4c95c10c713794715141d`.

One process, pid 93416. Init and create thread are both `3246876`. `started_utc` is `2026-11-03T06:21:14.424175+00:00`, taken before launch. `init.json` records success 1. `create.json` records success 1 for `_GfxCreateVertexBuffer` at RVA `0x14d44e4`, and its pid matches the session. The launcher deadline then killed the process: exit code -9, launcher signaled. There is no `stop.json` and no `blocked_at.json`. The format-5 stop at `0x15ec663` did not recur.

The crash file `eu4_20261103_072119` records `SIGSEGV`. The fault address is `0xbc6a0ce4bf4c2646`, and the EU4 frame is the return from `call _GfxCreateIndexBuffer` at RVA `0x14bfa60` in `CreateIndexBuffer`. `CreateMeshFromPdxAssetFile` calls that function while `CPdxMeshType::InitForDevice` runs from `CGraphics::Init3DTypes`. Inside `_GfxCreateIndexBuffer`, the first indirect call is `callq *(%rax)` at RVA `0x15ec1a9` through `___glewGenBuffersARB`. The crash file does not capture that slot. This is separate from the texture upload.

There is no `draw_invocation.json`. Triage is `timeout`. `d1` is `not_started`. This session has no `selection`, invocation id, or skipped count. It is not a Decision 0003 candidate, and it is not `packet_ready`.
