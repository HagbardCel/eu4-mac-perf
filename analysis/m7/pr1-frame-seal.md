# M7 PR 1 — authoritative same-process frame seal

Status: implementation complete; authentic one-frame evidence is recorded below
after the run. This document is the review entry point for PR 1. PR 1 does not
execute Metal.

## Base and stack

The requested base was `main` with PR #198 merged. At implementation time PR
#198 (`fix/m7-diagnostic-correctness`) was still open, so PR 1 was based on its
head `271cd04` and documented as a stack. PR #198 has since merged to `main` as
`b4c3955`; `271cd04` is an ancestor of `main` and PR #199 is retargeted to
`main`. No stack remains at review time.

## Frame boundary (Ghidra + trace evidence)

Queried through `eu4_re_query` against the pinned executable
(`b3d38876…`, image base `0x100000000`):

| Symbol | RVA | Evidence |
| --- | --- | --- |
| `CGraphics::PresentScene` | `0x149daf0` | Decompile: if `this[0x4e8] != 0`, calls `_GfxPresent(*(this+0x80))` and stores its result; otherwise returns without a swap. `CFrontEnd::Render` calls it at `0xf05148` **after** `CGraphics::EndScene`. |
| `_GfxPresent` | `0x15ea6cb` | Decompile: `_SDL_GL_SwapWindow(*(param_1+0x178)); return 1;` — no drawing, only presentation. |
| `_GfxDraw` / `_GfxDrawIndexed` / `_GfxDrawIndexedInstanced` | `0x15ea789` / `0x15eaa77` / `0x15eac9c` | Existing observer entries; effective state is read in the draw body after the engine arguments are available. |
| `_GfxClear` | `0x15ea552` | Existing clear entry; state read before the engine clear. |
| `_GfxResolve` | `0x15ece13` | Decompile: binds read/draw framebuffers and calls `glBlitFramebufferEXT`; the interposer records the effective copy source/destination. |

Two boundaries are supported:

- **present**: `_GfxPresent` exit arms the next frame; the next `_GfxPresent`
  entry finalizes. The readback happens before `_SDL_GL_SwapWindow`.
- **scene** (title/menu): `PresentScene` is the last call in the rendered
  frame: `CFrontEnd::Render` runs `EndScene`, then `PresentScene` (Ghidra
  `0xf05148`). `PresentScene` exit arms the next frame; the next `PresentScene`
  entry finalizes it, after that frame's draws and before the body's
  conditional `_GfxPresent` swap. The menu skips `_GfxPresent` when its present
  flag is clear, but `PresentScene` still fires every rendered frame, so the
  boundary remains valid. Finalizing here (rather than at exit) is required
  whenever `_GfxPresent` actually swaps; the base M6 writer finalized at exit,
  which was only safe for the skip-present menu case. A missed entry is
  published as `scene_entry_missed` with no reference instead of reading a
  possibly swapped back buffer.

The event stream is ordered by the existing single writer mutex. Draws, clears
and resolve/copy events all go through the same `emit_event` path; no new
command recorder or graphics IR was introduced.

## Deliverables

### Descriptor and callback (`benchmark/eu4_gfx_frame_seal.h`)

A fixed-layout, ABI-guarded descriptor carries:

- `capture_frame_id` (monotonic per EU4 process), `session_id`, `pid`,
  `process_start_ns`;
- boundary type and present/scene indices;
- ordered operation count plus draw/clear/copy counts;
- render dimensions and `BGRA8`/lower-left pixel format;
- completeness (`complete`), trace completeness, event-stream integrity and
  readback status;
- reference path, byte count and SHA-256;
- manifest path and SHA-256;
- sealed directory;
- GPU/readback status, unsupported dependencies and failure reason;
- one-shot readback/finalize/seal timings.

`eu4_gfx_frame_sealed_subscribe()` registers exactly one consumer. The callback
receives a private descriptor copy valid only for the call. The callback runs
after the trace writer's mutex is released, after the event stream is closed and
validated, after the GL reference is read back and verified, and after
`frame-trace.json` and `frame-seal.json` are durably published.

The dispatcher rejects a same-thread re-entrant call and counts it; a concurrent
call from another thread waits and is serialized. A non-zero callback return is
recorded (`eu4_gfx_frame_sealed_last_callback_status`) but the frame is not
re-notified. A callback is never invoked for an incomplete frame.

### Artifacts

`frame-trace.json` remains the unchanged `gfx-frame-trace/v1` contract.
`frame-seal.json` is the new `gfx-frame-seal/v1` handoff and is written only
when `EU4_GFX_FRAME_SEAL=1`. It includes the immutable manifest digest, the
reference digest, ordered operation accounting and the explicit
`sealed`/`failure_reason` state. An incomplete capture publishes
`frame-seal.json` with `sealed=false`; no consumer is notified.

### Opt-in single-frame mode

Configuration (all disabled by default):

| Variable | Meaning |
| --- | --- |
| `EU4_GFX_FRAME_SEAL=1` | publish the seal and enable one-shot notification |
| `EU4_GFX_FRAME_SEAL_LOG=1` | the injected dylib registers the built-in logging consumer used for evidence |
| `EU4_GFX_FRAME_TRACE_TIMEOUT_MS` | bound from arming to a sealed/incomplete result; `0` disables |
| existing selection variables | `EU4_GFX_FRAME_TRACE_DIR`, `CALLERS`, `MIN_DRAWS`, `OCCURRENCE`, `AT_FRAME`, `BOUNDARY`, `MAX_DRAWS`, `MAX_FRAME_DRAWS` |

The existing `g.captured` one-shot gate is unchanged: exactly one frame is
captured per process, and after it the normal observation counters continue but
no second capture or notification is issued. Unselected frames keep their
existing behavior. A timeout, draw cap, artifact cap or readback failure
produces a structured incomplete seal instead of a notification.

CLI: `python3 -m automation.m6_frame capture ... --seal --seal-log` maps these
variables. `automation.m6_frame.load_seal()` re-verifies the manifest and
reference digests, the event stream counts and the single notification.

## Verification

### Native/offline

`tests/gfx_frame_trace_scenarios.c` drives the production writer/selection paths
with `TEST_ONLY` readback (no GPU, no EU4):

- one callback for one complete frame; operation counts and order
  `draw, clear, draw, resolve`;
- callback lock probe proves the trace mutex is not held;
- incomplete draw cap, missing reference, malformed stream and count mismatch
  publish `sealed=false` and invoke no callback;
- scene boundary seals without any `_GfxPresent` call;
- re-entrant notification is rejected and the original notification completes;
- callback failure is recorded without re-notification;
- timeout produces `capture_timeout`.

`tests/test_gfx_frame_trace_writer.py` asserts the native scenarios and the seal
digests; `tests/test_m7_frame_seal.py` validates the Python contract, tamper
rejection and the capture CLI wiring. Existing M6 trace, graph, replay-case,
checkpoint, semantics and resolve suites remain green.

### Authentic one-frame launch

One original x86_64 EU4 process (pid `16791`, session
`9f036235-9cc0-4357-9e45-c35d12eb1619`) was launched with the existing host
lease/background-display tooling. The canonical menu selection (caller
`0x103b3c8`, occurrence 300, scene boundary) selected scene 617:

| Field | Value |
| --- | --- |
| capture frame id | `1` |
| boundary | `scene`, finalized at `PresentScene` entry (pre-swap) |
| ordered operations | 325 (315 draws, 9 clears, 1 resolve/copy) |
| render target | 1728×1117 `BGRA8`, lower-left |
| event stream | `31d03c73…`, 6,392,559 bytes, hashed only after validation |
| manifest | `b9de8e33…` (`frame-trace.json`, complete + stream validated) |
| GL reference | `cd0e7a16…`, 7,720,704 bytes |
| seal | `gfx-frame-seal/v1`, `sealed=true`, empty failure/unsupported fields |
| notifications | exactly 1, index 1, manifest/reference verified by the consumer |
| readback / finalize | 23.9 ms / 17.0 ms (total sealing cost 40.9 ms; includes stream hashing) |
| notification callback | 18.6 ms, outside the trace mutex |
| game stdout/stderr | 0 / 0 bytes |
| launcher termination | `terminated=true`, `killed=false`, settings restored |
| startup summary | complete, 0 overflow/unmatched/unavailable; the generic ops observer reported 4 refused at shutdown |

The earlier launches are retained as excluded instrumentation runs: run 1 used
the CLI default draw cap (256) below the frame's 325 events and produced an
explicit `draw_cap` incomplete seal with no notification; run 2 captured a
complete frame but the seal serializer omitted the redundant `complete` field
and the Python validator rejected it; run 3 was complete but finalized at
`PresentScene` exit before the entry-boundary correction (valid only because the
menu skips `_GfxPresent`). Run 4 uses the final entry boundary, terminates
normally through the launcher and returns `0`. The full package is
[pr1-live-evidence.json](pr1-live-evidence.json).

### Existing M6 compatibility

The historical canonical M6 frame (`.local/m6/frames/menu-occ300/frame` in the
main worktree) still loads through the unchanged `load_trace` path: 326 events,
317 draws, reference `ff2f4e2b…`, coverage `ok`, no seal. The M6 trace schema
and replay artifacts are untouched.

## Limitations

- The seal is a logical immutability boundary (atomic publication plus a
  one-shot writer). The directory is not chmod-ed read-only; evidence tooling
  must still treat it as read-only.
- The descriptor's `unsupported_dependencies` is populated from explicit
  capture failure reasons (for example an unsupported copy variant or default
  read buffer). It is not a per-draw state-coverage audit; PR 2 owns that audit.
- The built-in logging consumer verifies artifact digests but does not parse
  every event; Python does the full accounting validation.
- A timeout finalizes at the next observed engine hook, not with an independent
  timer thread. This bounds the overhead and keeps the detour cheap, but a
  completely stalled engine cannot be interrupted by this mechanism.
- PR 1 makes no Metal claim and starts no second process.
