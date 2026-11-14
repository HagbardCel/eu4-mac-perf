# Dual-process campaign infrastructure (PR 2)

**Status: vertical slice demonstrated with two genuine EU4 processes.** The
reference (authentic OpenGL) and candidate (reconstructed Metal) ran
concurrently, emitted logical-frame observations to two streams, were aligned
post-readiness, and were stopped by the coordinator with preserved evidence.
Pixel comparison is the PR 3 layer and is reported as `not_run` here.

Machine evidence: [`dual-process-campaigns.json`](dual-process-campaigns.json).
Raw runs are local-only under `.local/infra/dual-1/session-*`.

## Architecture

```
scenario.json ──> gfx_dual_run (coordinator)
                     │  one host-wide launch lease
                     │  two atomic ledger claims
                     ├── reference: eu4 + passive OpenGL observer dylib
                     └── candidate: eu4 + Metal boot dylib
                              │                    │
                     observe/frame-observe.jsonl  observe/frame-observe.jsonl
                              └──── FrameStream ───┘
                                      │
                    post-readiness anchor -> paired frames
                                      │
                 bounded ring dumps <- capture-request.json
                                      │
                    health gate / frame limit / process exit / deadline
                                      │
                 owned-process termination, artifact store, result.json
```

The coordinator reuses the existing launch lease, ledger, OpenGL observer
(`eu4_gfx_frame_trace.c` + observe site patch) and Metal boot path
(`eu4_gfx_init.mm`). No second renderer, emulator or agent loop exists.

## Scenario description

`automation/gfx_scenario.py` validates a compact JSON scenario and computes a
semantic digest. It pins:

- executable SHA-256, launch args, backend pair (`opengl`/`metal`);
- logical extent, start kind (`frontend` or `save`), pause/speed;
- input event schedule (frame-indexed), game config overrides;
- settle requirements, observation limits (frames, runtime, ring, dumps);
- comparison policy (`exact`, `tolerant`, `diagnostic`), regions, alignment
  requirement;
- synchronization mode (`streaming` or `lockstep`) and wait profile.

Unknown fields and out-of-range regions fail closed. The digest excludes the
local shader-cache/authorization paths, which are deployment inputs rather than
scenario semantics.

## Per-frame observation

A shared C module (`benchmark/eu4_gfx_frame_observe.c`) is linked into both
observer dylibs. At the logical boundary it emits one line to
`observe/frame-observe.jsonl`:

- backend, session, pid, `frame_index`;
- width/height/origin/pixel format/row stride;
- full-frame SHA-256;
- a content-addressed 64×41 preview (bilateral box sample) and its digest;
- draw count, caller composition signature, distinct callers;
- `skipped_before` gap count, `gpu_completed`, `capture_valid`, error.

The full framebuffer is copied into a bounded in-process ring (default 8
frames, max 32). The coordinator requests specific frame indices via
`capture-request.json`; the observer writes `captured/frame-N.bgra` and
`.json` from the ring. Requests are serviced while the process is paused in
lockstep mode, so a paired frame can be preserved before the process advances.
Every payload is hashed and stored content-addressed; the coordinator keeps a
bounded preserved set.

### GL integration

`eu4_gfx_frame_trace_boundary_exit` (PresentScene exit) reads the drawable with
`glFinish` + `glReadPixels`, restoring draw/read framebuffer, read buffer, pack
alignment/row/skip state and a bound pixel-pack buffer. It emits `lower_left`
BGRA8 at the logical extent (1728×1117).

### Metal integration

`eu4_gfx_swap_window` already owns the game color target and a shared buffer
path; when streaming is on, it encodes one extra blit into the existing
command buffer, waits once (the presentation already waits), and emits
`top_left` BGRA8 at the physical drawable extent (3456×2234, 2× logical).
`eu4_gfx_frame_note_draw_caller` feeds the per-frame draw composition
signature.

The extent relation is recorded per pair (`scale`, `normalized`). A candidate
that is not an integer multiple of the reference is **ambiguous**, not aligned.

## Modes

### Streaming

Both processes run freely. Observations are buffered and paired by a
post-readiness relative frame ordinal: once both streams have at least
`settle.min_frames` records, the next frame from each side is the same logical
interval. This avoids comparing a reference loading frame with a candidate
title frame. A gap, invalid capture, or unrelated extent is reported as an
alignment outcome, never as a pixel difference.

A streaming pair's raw frames may already have left the ring by the time the
coordinator stops; the run reports `missing` rather than pretending otherwise.
The last paired frames are requested at stop, plus any pair that triggered a
failure.

### Strict lockstep

Both observers pause after emitting the frame (`ready/frame-N.json`) and wait
for `release/frame-N`. The coordinator pairs the two ready frames, requests
their ring dumps, then releases both. The wait is bounded (default 8 s), so a
crashed coordinator cannot suspend the game indefinitely. In the demonstrated
run both sides stayed at the same ordinal and all five paired frames had both
raws available.

The first lockstep attempt produced **0 paired frames**: the Metal observer did
not call the pause and the settle poll discarded records instead of indexing
them. Both are fixed and the failed attempt is retained as a regression case.

## Stop and cleanup

The campaign stops on:

- a health-gate failure (rejected draw, pipeline failure, validation error,
  failed resolve/readback, failed command buffer);
- an alignment failure (invalid capture, unrelated extent);
- a process exit;
- the configured frame limit;
- the stage deadline.

On stop: release any paused frames, preserve the selected ring dumps, write
`result.json` and the artifact manifest, terminate only the owned process
groups (identity re-verified), release the lease, and finish both ledger
tokens. A structured terminal status is written even on an exception.

## Demonstrated runs (2026-11-14)

| Session | Mode | Result | Paired | Ref/Cand observed | Preserved raws |
| --- | --- | --- | --- | --- | --- |
| `20261114T133000Z-61c7376d` | streaming | frame limit | 12 | 37 / 12 | candidate 11,12 |
| `20261114T133201Z-67a62071` | streaming (anchored) | frame limit | 8 | 39 / 13 | candidate 12,13 |
| `20261114T133407Z-27afd1a0` | lockstep (broken) | deadline | 0 | 7 / 184 | none |
| `20261114T133608Z-122c4985` | lockstep (fixed) | frame limit | 5 | 5 / 5 | both 4,5 |

All successful runs terminated both owned processes with SIGTERM and exit 0.
No health diagnostic was raised: the candidate was the current M6
implementation with its shader cache. The observed preview differences are
expected because no pixel normalization or comparison has run yet; full-frame
hashes cannot match across lower-left 1728×1117 and top-left 3456×2234.

## Target-phase pairing and metadata-first observation

Pairing does not start at process start.  The coordinator waits until both
sides have reached the configured target draw threshold (`settle.min_draws`),
then anchors on the next frame from each side.  While one side is still
loading, only that side is advanced in lockstep; the side already in the target
scene stays paused.  This removes the loading-progress mismatch that produced a
false 13.9% difference.

For streaming campaigns the reference observer can run in metadata-only mode
(`EU4_GFX_FRAME_METADATA=1`): it emits draw count, caller composition,
dimensions and health every frame without a full readback.  The coordinator
pre-requests a small full-resolution window at the target phase, so selected
frames carry raw payloads for comparison while loading is not slowed by
8 MB-per-frame GPU syncs.

A **staggered** mode starts the reference first and launches the candidate only
after the reference reaches the target scene, so the candidate does not idle
while the GL reference loads.  Both processes overlap for the differential
interval.  The first staggered attempt was refused by the host lease because
the M6 worktree held a real EU4 process; no interference occurred.  This is the
coexistence contract working as designed, and the staggered live run can be
retried when the lease is free.

Real limitation observed: the passive OpenGL observer originally sealed at its
1024-event quota after ~12 s, which silently stopped the frame stream.  A
frame-only observer mode now bypasses that quota; it streamed 20,000 metadata
frames at ~112 fps with the lease free.  The reference loading draw count still
fluctuated between 17 and 81 and did not reach the configured target of 100
before the phase deadline (the staggered candidate was intentionally not
started yet).  The reference loading path/long-run behaviour needs a
windowed/foreground or user-dir isolation experiment before the live title
campaign is claimed. A long frame-only run streamed 35,234 frames over 302 s
(draws 17-81, still loading) and was then killed externally with SIGKILL; the
historical M6 title capture took about 5m40s, so the run had not reached the
scene. The observation cap is now 120,000 frames.  Loading the Venice save with
`--continuelastsave` made the reference reach the map (506 draws), but the Metal
candidate stopped emitting after 72 frames (~52 s, 4,089 draws peak), hung, and
exited 11 on termination.  A campaign-level stall detector (default 45 s with no
new observation while both owned processes are alive) now reports
`observation_stall` instead of consuming the full deadline.

A further staggered run with a 40-draw phase threshold paired 110 observations,
but every pair was `ambiguous`: the reference had 46 draws while the candidate
had 273, i.e. different phases. No pixel verdict was issued. Phase readiness now
requires both the minimum and a relative draw-count tolerance
(`settle.draw_tolerance`, default 0.25), and pairing enforces the frame limit
instead of consuming a whole buffered batch.

## Limits carried into PR 3

1. The correspondence anchor is relative frame ordinal, not semantic scene /
   update identity. It is adequate for a settled scene but must be replaced or
   corroborated by scene/phase and draw-composition evidence.
2. The candidate is 2× physical while the reference is 1× logical. The
   comparator must normalize explicitly and record the resampling provenance.
3. Streaming mode cannot guarantee paired raw availability for early frames;
   lockstep is the reliable preservation path.
4. Native physical readback cost (8 MB reference, 31 MB candidate per frame)
   is measured only indirectly by the run duration. A bounded overhead metric
   belongs in PR 5.
5. No pixel verdict is fabricated from command-buffer completion; the health
   gate keeps `pixel_correctness = null` until an aligned comparison runs.
