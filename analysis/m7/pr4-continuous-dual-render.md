# M7 PR 4 — continuous same-process dual rendering with persistent resource history

Status: implementation in progress (stacked on PR 3, `m7/pr3-differential-diagnosis`).
Evidence is recorded below as each stage completes. This PR does not merge
automatically.

## Architecture

### Bounded continuous capture

`benchmark/eu4_gfx_frame_trace.c` gains an opt-in `continuous` mode. The
single-frame path is unchanged and keeps its one-shot gate.

```text
EU4_GFX_FRAME_TRACE_CONTINUOUS=1
EU4_GFX_FRAME_TRACE_MAX_FRAMES=<1..64>
EU4_GFX_FRAME_TRACE_MAX_PENDING=<1..64>
EU4_GFX_FRAME_TRACE_MAX_BYTES=<positive>
```

The capture root (`EU4_GFX_FRAME_TRACE_DIR`) becomes a campaign directory:

```text
<root>/sequence.json
<root>/frame-000001/{frame-trace.json,frame-events.jsonl,frame-seal.json,
                     frame-reference.bgra,blobs/,frame-sequence.json}
<root>/frame-000002/...
```

- One process-monotonic `capture_frame_id` and one `sequence_index` per frame.
- `sequence.json` is the campaign manifest (`gfx-frame-sequence-campaign/v1`):
  completed frames, capture ids, manifest/event/reference digests, bytes,
  bounds and stop reason.
- `frame-sequence.json` (`gfx-frame-sequence/v1`) is the per-frame identity:
  previous frame capture id and digests, render-target names, carried renderer
  outputs and lifecycle operations.
- Exactly one sealed record is written per completed frame. A frame that fails
  its event-stream/manifest/reference checks is written as incomplete and is not
  counted as completed.
- The frame limit, byte budget and capture timeout stop the campaign with an
  explicit reason. No frame is silently dropped or renumbered.
- Reentrant sealed callbacks remain rejected by the tracer's dispatch lock.
- The original OpenGL engine functions still run once; capture only observes
  and reads back.

`glDeleteTextures` is now observed by the existing GL interposer. A deleted GL
name is removed from the carried render-target set immediately and is published
in the next frame's `lifecycle` record. The call is always forwarded unchanged.

### Persistent Metal resource history

The replay C ABI is `EU4_METAL_REPLAY_ABI_VERSION == 3`. The config adds
`frame_sequence` and `persistent_history`; the result adds `history_status`,
`carried_bindings`, `missing_history` and `stale_history`.

The existing session already owns device, queue, pipelines, textures, samplers
and render targets for its lifetime. PR 4 makes that persistence explicit and
fail-closed:

- `gfx-frame-sequence/v1` is loaded before events. The sequence index must
  match the requested frame; `previous` must match the session's retained
  previous-frame identity; a restarted sequence in a live session is rejected.
- Lifecycle `delete`/`create`/`reset` records invalidate the named history and
  render targets before the frame executes.
- A texture without an in-frame producer is carried **only** when the capture
  declares it in `carried_renderer_outputs` and the session has a render target
  with matching size/format and `renderer_generated` history.
- A renderer-generated texture that is neither reproduced nor carried is an
  explicit failure (`no producer or carry record`); the captured OpenGL image
  is not imported to hide the gap.
- A carried texture with a missing Metal producer or stale size/format fails
  (`no Metal producer exists` / `carried history is stale`).
- `replay.json` publishes `gfx-metal-resource-history/v1`: every resource's
  kind, size, format, generation, last frame/event and the carried/missing/stale
  counters.
- A deliberate lifecycle reset allows a later CPU-originated upload to be
  imported as `external_capture` with its own content hash.

### In-process continuous bridge and coordinator

`EU4_GFX_SHADOW_MODE=continuous` keeps one `eu4_metal_replay_session` across the
bounded frame sequence. Per frame the bridge:

1. receives the sealed frame and increments the sequence;
2. waits for that frame's shader translation and verifies the receipt
   (including the checkpoint request digest and count);
3. executes the frame through the same session with `persistent_history=1`;
4. writes `shadow/frame-NNNNNN/shadow-report.json` with sequence/history
   counters; the session is destroyed only at the frame limit or on failure.

`m6_frame shadow-coordinator --continuous` loops over `run/frames/frame-*` in
order, translates/publishes that frame's request and receipt, waits for the
report, verifies identity, diagnoses it with PR 3, and stops on the first
non-qualified frame (or continues only under the explicit
`--continue-on-difference` policy). `coordinator.json` records every processed
frame and the stop reason.

### Bounded supervisor

`automation/m7_continuous.py` validates the ordered sequence (no duplicate or
missing indices, previous identity chain, digests), runs the production CLI's
`--sequence` mode through one session, diagnoses each result with PR 3, and
stops on:

1. the first qualified rendering difference;
2. capture/resource-provenance corruption;
3. GPU execution failure;
4. budget exhaustion;
5. loss of ordered continuity;
6. cancellation or the configured frame limit.

It preserves a portable case for the offending frame, reports per-frame and
cumulative provenance, and records replay/diagnosis average/median/p95/worst,
queue high-water, bytes written and wall time.

The supervisor now streams one JSON result per frame from the worker and writes
a per-frame `continue`/`stop` decision. The CLI waits at each frame boundary for
that decision, so a stop/cancel/time budget is enforced **during** execution,
not post-hoc. Hard failures (GPU execution failure or invalid resource history)
always stop, regardless of `stop_on_difference`; the policy flag applies only to
rendering-difference classifications. An early stop is also propagated to the
tracer (`EU4_GFX_FRAME_TRACE_STOP_FILE`) and the bridge (`shadow/stop`).

The current implementation is the synchronous stage: all captured frames are on
disk before the Metal worker starts. A live asynchronous capture/replay pipeline
is **not** claimed.

## Evidence

Tracked summary: [pr4-evidence.json](pr4-evidence.json).

### Offline persistent-history tests

Using the canonical title capture split into two ordered frames (blobs
hard-linked, mutable files copied):

- Frame 1 renders texture 443. Frame 2 removes its producers and declares it
  carried. The second frame executes with `carried_bindings=1`; the sample for
  443 reports `source=metal_render_target`, `carried=true`,
  `history_frame=1`, and the history resource stays `renderer_generated` from
  frame 1. No captured OpenGL intermediate was uploaded.
- If frame 2 declares 443 carried but frame 1 never produced it, the frame fails
  with `443 is carried but no Metal producer exists` (`history_status=2`,
  `missing_history=1`) instead of importing the GL image.
- If frame 2 neither reproduces nor declares 443 carried, the frame fails with
  `443 has renderer history but is not carried by this frame`.
- A lifecycle delete of 443 allows the captured OpenGL content to be imported
  as a deliberate CPU-origin update (`source=external_capture`, history kind
  `external`).
- A previous-frame manifest mismatch fails before any event is encoded
  (`stage=history_sequence`, `encoded=0`).
- The production CLI `--sequence` path and `m7_continuous.run_campaign` process
  the same two frames through one session, preserve frame order, and report
  metrics. The supervisor unit tests cover stop-at-K, GPU failure, cancellation,
  identity validation and frame/byte budgets.

### Authentic EU4 campaign (Stage 1)

One genuine x86_64 EU4 process, **pid 12009**, session
`4b1de013-bfdc-40d1-b3e6-8bbef4a52cbc`, executable
`b3d38876…`, instrumentation dylib `feddb2d5…`:

- `EU4_GFX_FRAME_TRACE_CONTINUOUS=1`, 3 frames, max pending 2, 2 GiB byte
  budget, scene boundary, occurrence 300;
- exactly one process; launcher termination `terminated=true`, `killed=false`;
  background display `EU4 Test` restored (`already_original`);
- three consecutive frame directories `frame-000001..3`, each 325 operations
  (315 draws, 9 clears, 1 copy), one seal, one reference and one
  `frame-sequence.json`;
- campaign completed with `status=complete`, `reason=frame_limit`, 3/3 frames;
- frame 1 captured the engine's startup texture-deletion burst as a lifecycle
  record; frames 2/3 chained `previous` to frame 1/2.

All three frames were replayed through the **same** Metal session and diagnosed:

| frame | classification | history | carried | missing/stale | replay | callback |
| --- | --- | --- | --- | --- | --- | --- |
| 1 | difference_with_partial_state_coverage | ok | 0 | 0/0 | 2.07 s | 5.45 s |
| 2 | difference_with_partial_state_coverage | ok | 0 | 0/0 | 1.43 s | 4.92 s |
| 3 | difference_with_partial_state_coverage | ok | 0 | 0/0 | 1.43 s | 4.90 s |

Persistent renderer-owned state is visible in the retained generations:
texture 433 went `7 → 14 → 21`, 431 `3 → 6 → 9`, 442 `1 → 2 → 3`, 443
`2 → 4 → 6`. The session was not reset or reseeded between frames.

**Scope of this evidence:** the authentic campaign proves persistent session
state and ordered multi-frame execution. It does **not** exercise a carried
renderer output: the title scene re-renders every target before sampling it, so
`carried_bindings` is 0 in all three frames. The carried-output, missing
producer, stale history and delete→CPU-reimport branches are proven by the
canonical two-frame synthetic fixture, not by this authentic run. The report
does not upgrade those fixture proofs to authentic claims.

Each frame's earliest authoritative divergence is the same copy/composite
region as PR 3 (event 286, texture 442; copy producer 284). All three are
`difference_with_partial_state_coverage` because `gl_dither` and `multisample`
remain unsupported; they are not labelled proven Metal defects.

Capture cost was 23.5/22.1/20.8 ms readback and 40.7/38.9/37.7 ms sealing per
frame. The campaign accounts 1,263,128,298 bytes of frame payload; the three
sealed capture directories occupy 1,406,082,344 bytes on disk (manifest, seal,
sequence and shadow artifacts add the difference). The byte budget is checked
at frame boundaries, so the third frame completes and the campaign stops at the
frame limit. Queue high-water is 1 with `max_pending=2` in the synchronous
bridge.

Report package: [pr4-evidence.json](pr4-evidence.json). Raw captures remain
under `.local` (gitignored).

## Tests

Focused PR4 set: 96 passed, 1 skipped (frame-trace writer, interpose, continuous
replay, supervisor, coordinator, PR3 diagnosis, Metal replay and canonical
diagnosis).

- `tests/test_gfx_frame_trace_writer.py`: ordered continuous frames, per-frame
  seals, previous links, campaign limit/byte budget, lifecycle delete.
- `tests/test_m7_continuous_replay.py` (6): carried renderer output, missing
  producer, unrecorded renderer output, lifecycle reset/CPU upload, previous
  identity mismatch, one-session campaign integration.
- `tests/test_m7_continuous_supervisor.py`: stop-at-K, GPU failure,
  cancellation, ordering, budgets, package preservation.
- `tests/test_m7_continuous_coordinator.py`: synchronous per-frame receipts and
  stop policy, tool failure vs difference.
- `tests/test_gfx_interpose.py`: the delete interposer still forwards.

## Known limitations

- The live asynchronous pipeline (engine callback enqueues and returns while a
  worker replays) is not implemented. The synchronous bridge blocks the engine
  at each frame boundary; that is the Stage 1 design. A bounded file-queue
  worker is the next architectural step.
- The capture byte budget is checked at frame boundaries, so one frame may
  overshoot the configured budget before the campaign stops. It is a bounded
  campaign guard, not a hard per-frame allocation limit.
- Shader translation is still done per frame by the coordinator with `--force`.
  Cross-frame translation caching by authoritative source identity is not yet
  implemented.
- Lifecycle capture covers GL texture deletion. It does not yet capture every
  `glTexImage2D`/`glTexSubImage2D` update as an ordered CPU-write record; a
  deliberate `delete`/`reset` lifecycle record is the supported resynchronization
  mechanism.
- The authentic campaign is bounded to 3 frames for Stage 1. Stages 2/3
  (async, 10–20+ frames, gameplay) are not run unless Stage 1 passes.
- Package retention is bounded by the per-frame case already produced by PR 3;
  successful intermediate replay directories are retained for now rather than
  deduplicated.

## Review

Requesting independent review. Do not merge automatically.
