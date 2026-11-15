# M7 PR 2 implementation log

## Stack and base

- PR 1 (`m7/pr1-frame-seal`, commit `7517b9c`, PR #199) is the base. PR 2 is an
  explicitly documented stacked branch `m7/pr2-inprocess-metal`.
- PR #198 is merged to `main` as `b4c3955`; PR 1 targets `main`.

## Replay-core extraction

- Replaced the replay executable's file-scope mutable globals with an
  `EU4MetalReplayState` session object (device, queue, pipelines, textures,
  samplers, render targets, provenance, checkpoints, transfer records).
- A thread-local active-session pointer selects the executing session; the
  session owns every resource and cache. No renderer operation was
  reimplemented.
- Added `eu4_gfx_metal_replay.h` (opaque session, fixed-layout config/result)
  and kept `run_replay` as the single implementation used by the CLI and the
  bridge.
- Moved argument parsing to `eu4_gfx_frame_replay_cli.mm`; the CLI contract is
  unchanged.
- Added bounded GPU waits using an off-thread `waitUntilCompleted` and a
  dispatch semaphore. An earlier `addCompletedHandler`-after-commit attempt
  triggered a Metal failure and was discarded.
- Added `state coverage` for `gl_dither`/`multisample` rather than silently
  treating them as supported.

## In-process bridge

- Compiled the core and `eu4_gfx_shadow_bridge.mm` as ARC x86_64 objects and
  linked them into the existing instrumentation dylib with Metal.
- The bridge subscribes to PR 1's sealed callback only when
  `EU4_GFX_SHADOW_MODE=single-frame`; it never runs in the default mode.
- It waits for the external coordinator's `shaders/ready` marker, then calls
  the C ABI exactly once. Translation is on demand and never launches EU4.
- It writes `shadow-report.json` with GL/Metal digests, operation counts,
  command status, provenance paths and errors, and catches Objective-C
  exceptions.

## Coordinator and capture wiring

- Added `m6_frame shadow-coordinator`: waits for the seal, invokes the existing
  translator, writes `ready`, waits for the report, and runs the existing
  comparator.
- Added `m6_frame capture --shadow`: starts the coordinator, enables the seal,
  sets bounded wait/GPU timeouts, and includes the shadow verdict in
  `capture-report.json`. A pixel difference is a completed diagnostic, not a
  command failure.

## Verification

- Canonical title: 326/326, exact `ff2f4e2b…`, CLI and ABI agree, repeated
  sessions agree.
- Copy-bearing scene 75: 283/283, one transfer, known divergence preserved
  (1,792,005 / 1,930,176, max 23).
- Invalid trace/config rejected; timeout bounded; core has no OpenGL linkage.
- One live launch (pid 40775): one sealed frame, 325/325 Metal operations,
  readback ok, one GL reference and one Metal output. Pixel verdict `fail`
  (1,161,038 / 1,930,176, max 68) with `state_coverage=partial` for dither and
  multisampling; the amplified difference is quantization noise, not a missing
  region.
