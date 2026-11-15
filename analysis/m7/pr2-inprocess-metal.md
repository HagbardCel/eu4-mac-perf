# M7 PR 2 — in-process Metal shadow renderer

Status: implementation and one authentic same-process launch complete. This PR
is stacked on PR 1 (`m7/pr1-frame-seal`, commit `7517b9c`, PR #199), which is
still open for review. Do not merge PR 2 before PR 1.

## Architecture

The reusable Metal replay core was extracted from the standalone
`benchmark/eu4_gfx_frame_replay.mm`:

- `benchmark/eu4_gfx_metal_replay.h` — stable C ABI (`create`, `frame`,
  `destroy`, `last_error`) and fixed-layout config/result structs.
- `benchmark/eu4_gfx_frame_replay.mm` — replay core. All mutable device,
  queue, pipeline, texture, sampler, render-target, provenance and checkpoint
  state now lives in a session-owned `EU4MetalReplayState`; the thread-local
  active-session pointer only selects the executing session. The standalone
  executable no longer contains a `main`.
- `benchmark/eu4_gfx_frame_replay_cli.mm` — thin CLI frontend preserving the
  existing `--frame/--shaders/--out/--stop-after/--only-event/...` contract.
- `benchmark/eu4_gfx_shadow_bridge.mm/.h` — opt-in in-process bridge compiled
  into the existing instrumentation dylib. It subscribes to PR 1's
  `frame-sealed` callback, waits (bounded) for immutable translated shader
  artifacts, executes exactly one replay through the core, and writes
  `shadow-report.json`.
- `automation/m6_frame.py` — `--shadow` capture mode and
  `shadow-coordinator`, which waits for the seal, runs the existing shader
  translator, publishes the `ready` marker, waits for the Metal report, and
  runs the existing `m6_compare` differential.

No GL/Metal interop exists. The bridge never opens a GL context, never touches
the engine framebuffer, never re-invokes an engine draw/clear/copy function and
never starts another EU4 process. It runs after PR 1 has sealed the frame and
released the tracer mutex, so the GL frame remains the displayed frame.

### Configuration

- `EU4_GFX_SHADOW_MODE=single-frame` (opt-in; absent by default)
- `EU4_GFX_SHADOW_OUTPUT_DIR` (default `<run>/shadow`)
- `EU4_GFX_SHADOW_SHADER_DIR` (default `<shadow>/shaders`)
- `EU4_GFX_SHADOW_WAIT_MS` (bounded shader-ready wait, default 120 s)
- `EU4_GFX_SHADOW_TIMEOUT_MS` (bounded GPU command wait, default 120 s)

The capture CLI's `--shadow` implies `--seal`, starts the coordinator, and
reports `shadow.status`, `shadow.pixel_verdict`, `shadow.state_coverage` and
the comparison metrics. A Metal failure/session timeout is `shadow_error` and
does not overwrite the GL reference. A completed comparison that finds a
difference is a completed diagnostic with `pixel_verdict=fail`, not a tool
failure.

## Shader preparation

Translation is on demand by the external coordinator after the seal, while the
game is blocked at the frame boundary. The coordinator is the existing
`m6_frame translate` command; it does not start EU4. The bridge waits for an
immutable `shaders/ready` marker and a source-identity-bearing
`shaders.json`; a missing/stale translation is an explicit incomplete result.

## Resource provenance

The core keeps the existing Strategy B distinction:

- external captured inputs (textures/vertex bytes from the trace) are uploaded
  into Metal resources;
- renderer-generated targets are allocated and written by the Metal replay;
- in-frame copy/resolve reads the corresponding Metal source;
- no captured OpenGL intermediate is seeded to force agreement.

`replay.json` reports binding counts, external/render-target texture sets and
`first_sample_before_any_write`. The live frame had 2,739 bindings: 274
Metal-render-target and 2,465 external-capture bindings, no
first-sample-before-write entries, and one in-frame transfer.

## Offline verification (Stage A/B)

- Canonical M6 title frame: 326 events, 326 encoded, 0 failed, command status 4,
  Metal frame `ff2f4e2b…` = GL reference exactly. Library and CLI agree; two
  consecutive session creations agree with no stale targets.
- Copy-bearing scene-75 case: 283 events, 283 encoded, 0 failed, 1 transfer;
  independently reproduced output preserves the historical divergence
  (1,792,005 / 1,930,176 pixels, max error 23). No unsupported state was
  reclassified as equivalent.
- Invalid trace/config are rejected; the core binary has no OpenGL linkage and
  needs no GL context.
- Existing M6 replay tests remain green.

## Authentic same-process launch (Stage C)

One original x86_64 EU4 process, **pid 91051**, session
`ce58136b-362d-4d72-9383-3846d71f2784`:

| Field | Value |
| --- | --- |
| capture id / boundary | `1`, scene 617 |
| ordered operations | 325 (315 draws, 9 clears, 1 resolve) |
| GL reference | `439da7ac…`, 7,720,704 bytes |
| trace manifest | `b80c1a7c…` |
| Metal replay | 325/325 encoded, 0 failed, readback ok, command status 4 |
| Metal frame | `7bf78ca0…` |
| replay duration | 410 ms |
| total callback time | 3.33 s (translation wait + replay) |
| one EU4 PID | yes; no second launch |
| termination | launcher `terminated=true`, settings restored |
| game stdout/stderr | 0 / 0 bytes |
| state coverage | **partial**: `gl_dither`, `multisample` unsupported |
| pixel verdict | **fail** |

Comparison: **1,160,637 / 1,930,176** pixels differ, max channel error **81**,
mean max error 0.87, alpha differences 0. The differences are spatially
uniform and low-amplitude over terrain and UI, and the images are visually
equivalent, but this is **not** proven to be exclusively dithering: 262,845
pixels differ by more than 1 and 112 by more than 40. No A/B run with dither or
multisample disabled exists. The canonical title fixture remains pixel-exact,
so this is a live-frame state/coverage gap, not a core regression. The
coordinator writes `state-coverage.json` with the exact checked/unsupported
flags.

The generic ops observer recorded `refused=0` during the blocked frame
boundary; the frame trace itself is complete and validated, stdout/stderr are
empty, and no GL error was recorded. The run is a one-shot diagnostic, not
continuous mirroring.

## Limits and next steps

- One active replay session per process; the C ABI is not designed for
  concurrent sessions.
- Shader translation is external and on demand; the engine blocks at the frame
  boundary for the bounded wait.
- Dither/multisample state is not implemented in the Metal host. A bounded
  correction (captured dither state and Metal-equivalent quantization, or a
  documented state-coverage exception) is the next correctness task.
- Renderer-generated resource history across successive frames is not
  maintained; this PR explicitly covers one sealed frame with captured
  initial conditions. Continuous dual rendering is out of scope.
- No continuous asynchronous mirroring, no performance optimization, no
  second process.
