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

### Bounded asynchronous worker

`EU4_GFX_SHADOW_ASYNC=1` (capture `--async`) starts one serialized Metal worker
inside the instrumentation dylib. The sealed callback now:

1. copies the immutable capture descriptor into a bounded in-memory job;
2. blocks at the frame boundary only while the queue is full (`MAX_PENDING`),
   with a bounded wait;
3. returns to the engine without translating or replaying on the GL thread.

The worker owns the one persistent replay session, waits for the coordinator's
translated shaders/receipt for that frame, executes Metal, writes the report and
waits for the coordinator's per-frame `continue`/`stop` decision. The
coordinator still diagnoses each frame with PR 3 in order and can stop the
campaign; queued frames behind a stop are explicitly marked stopped rather than
silently dropped. Queue high-water and depth are published in each shadow
report.

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

### Sync vs async (same 3-frame title sequence)

| mode | replay s | callback s | queue high-water | classifications |
| --- | --- | --- | --- | --- |
| synchronous (pid 12009) | 2.07 / 1.43 / 1.43 | 5.45 / 4.92 / 4.90 | 1 | all partial-state differences |
| asynchronous (pid 59281, `MAX_PENDING=2`) | 1.67 / 1.48 / 1.49 | 5.24 / 4.89 / 4.98 | 2 | all partial-state differences |

The asynchronous run observed a real queue depth of 1 and high-water 2, with
the same persistent generations and classifications, proving the capture and
Metal execution paths are decoupled while ordering and qualification are
preserved.

### Stage 2: 10-frame asynchronous campaign

One EU4 process (pid 60053, session `8d60ee02-944a-452d-b62c-ceb2fbadf7bd`)
captured 10 consecutive frames with `MAX_PENDING=3`. The queue reached its bound
(high-water 3) without dropping a frame; every frame preserved ordered previous
identity, ran through the same Metal session, and classified as
`difference_with_partial_state_coverage` with history ok. Replay averaged
1.57 s (worst 2.17 s), callback averaged 5.14 s, readback/seal averaged
21.7 ms / 39.5 ms, and the campaign wrote 4.69 GB on disk. Renderer-owned
generations continued to accumulate (433 reached 70, 431 30, 443 20), so the
session was not reset between frames.

## Tests

Focused PR4 set: 236 passed, 1 skipped (after the #202 merge and rebase) (frame-trace writer, interpose, continuous
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

## Completion hardening (final PR4 head)

### Persistent renderer-output tracking

`eu4_gfx_frame_trace.c` keeps a separate carried set that is merged with each
frame's writes and only removed by an evidenced delete/lifecycle transition.
The frame-sequence snapshot is taken at frame start, so a resource written in
frame N is still advertised in frame N+1 and N+2 even when neither frame writes
or samples it. Three-frame regressions cover write-without-use, delete/recreate
name reuse and the published lifecycle event.

### History-aware dependency graph

`gfx_frame_graph.build()` loads and verifies `frame-sequence.json`: schema,
capture manifest/reference/seal digests, contiguous `previous` identity,
previous-frame manifest/event-stream digests and the previous frame's renderer
resource set. A declared carried name is classified
`pre_frame_renderer_output`, never `external_input`, only when that chain is
verified. `evaluate_replay()` derives the exact expected generation from the
Metal session's own history (`final history generation - frame writes + writes
before the consumer`) and rejects a newer unrelated generation. A missing or
invalid previous link, absent carry record or generation mismatch fails closed.
CPU texture mutations recorded in the lifecycle invalidate a carried
declaration.

### Per-frame sequence checkpoints

`m7_continuous` publishes, per frame, a graph-derived
`checkpoint-request.json` and an established `shader-receipt.json` binding its
SHA-256/count to the sealed frame/session identity. The sequence CLI
(`--sequence-checkpoints`) copies both into the output directory, passes the
request path to the Metal core and writes the established
`shadow-report.json` identity/binding contract. Diagnosis then uses the same
PR3 reconciliation; no checkpoint requirement is disabled.

### Bounded supervisor

`run_campaign()` uses concurrent reader threads and a bounded queue. It
enforces an overall deadline, a per-frame result deadline, cancellation while
waiting, a bounded stderr drain, and process termination/cleanup. A silent or
hung worker yields an explicit `frame_timeout` with the unexecuted frames
listed. Native sequence frames receive a positive `config.timeout_ms`; the CLI
rejects a zero timeout. Decision files carry the frame sequence and a decision
for another frame is rejected by both the CLI and the bridge.

### Fail-closed CPU mutations and efficiency

`glTexImage2D`/`glTexSubImage2D` are observed by the existing interposer and
forwarded unchanged. A mutation of a renderer-owned/carried resource is
published in the frame lifecycle; the graph marks the carried declaration
unproven and the Metal core fails the frame with `history_mutation`. Ordered
reproduction of arbitrary CPU uploads remains explicitly unsupported.

Shader translation is cached by verified program/stage source identity, so a
repeated frame reuses one translation. Immutable payloads are hard-linked from
the previous frame when the content digest matches, and verified blob digests
are memoized per process. The campaign manifest publishes
`deduplicated_bytes`.

### Measured efficiency

- Authentic shader translation cache: frame 1 is a miss (2.99 s); frames 2 and
  3 are hits (0.34 s and 0.27 s) and share source identity
  `126f3a260e2a…`. Hit rate 2/3 on the three title frames.
- Authentic three-frame replay: GPU replay 0.50/0.21/0.21 s, diagnosis
  average 7.61 s, campaign wall 25.18 s, output 139 MB.
- Async Stage 2 queue: `MAX_PENDING=3`, high-water 3, no dropped frames.
- Capture storage: the 3-frame authentic capture is 1.41 GB; the prior
  10-frame async campaign retained ~4.69 GB. Content-addressed payload
  hard-linking and shader caching reduce replay/translation work, but
  successful capture artifacts are still retained; the 4.69 GB figure is a
  budget, not an unlimited-retention target.
- Not measured in this session: uninstrumented EU4 pacing, capture-only pacing,
  and a fresh synchronous-vs-asynchronous authentic comparison. Those require
  a new launch and are reported as an explicit limitation.

### Authentic replay verification

The existing authentic three-frame campaign (`pr4-continuous-live-5`, one EU4
process, session `4b1de013-bfdc-40d1-b3e6-8bbef4a52cbc`) was replayed offline
through the new sequence-checkpoint path:

- 3/3 frames `ok`, `history_status=1`, `carried_bindings=0` (title scene
  re-renders its targets);
- each frame reconciled **6/6** requested checkpoints (`checkpoint_validation
  = ok`);
- all three remain `difference_with_partial_state_coverage` with pixel-exact
  final metrics matching the recorded campaign;
- replay total 25.2 s wall, 139 MB campaign output, 3-frame capture retained
  (1.3 GB). This is authentic captured evidence replayed through new code, not
  a fresh EU4 launch.

## Known limitations

- The asynchronous pipeline is a bounded in-process queue with one serialized
  Metal worker. It is not a cross-process or multi-worker pipeline.
- The capture byte budget is checked at frame boundaries, so one frame may
  overshoot the configured budget before the campaign stops. It is a bounded
  campaign guard, not a hard per-frame allocation limit.
- Shader translation is cached by source identity. A change in any program or
  stage digest produces a new key; the cache stores only translated output.
- Ordered `glTexImage2D`/`glTexSubImage2D` mutation semantics are not
  reproduced. The interposer records the mutation and the affected frame fails
  closed (`history_mutation`); explicit delete/reset remains the supported
  resynchronization.
- A lifecycle delete whose name is still a colour attachment in the current
  frame is not applied immediately: applying it would remove a target that the
  same frame's checkpoint plan requests. Full per-event delete/recreate identity
  for that case remains future work.
- The asynchronous pipeline is a single serialized in-process worker; it is not
  a multi-worker or cross-process queue. The 10-frame campaign reached the
  `MAX_PENDING=3` bound without dropping frames.
- Stage 3 (a longer gameplay campaign) was not run. Stage 2 covered 10
  successive animated title frames; the pipeline is bounded and stable but has
  not been exercised on gameplay for an extended period.
- Payload deduplication uses hard links within one campaign filesystem. It does
  not delete successful frame outputs; a 10-frame authentic campaign can still
  retain approximately the captured payload size. Retention policy is explicit
  and budgeted, not unbounded by default.
- A fresh authentic launch was not performed after the native trace/interposer
  changes. The replay above proves the new diagnostic path over authentic
  captured frames, but not new live capture semantics. Gameplay was not
  exercised.

## Review

Requesting independent review. Do not merge automatically.


## Independent review remediation (`3d4bf475`)

An independent review of `40132d5f` found two blocking lifecycle defects; both
are fixed:

1. **End-of-frame lifecycle entries were never applied.** `apply_due_lifecycle`
   is now drained at `events.count` after the event loop, so a CPU mutation or
   delete recorded after the last captured event fails the frame with
   `history_mutation`. Regression:
   `test_mutation_at_the_end_of_frame_event_count_fails_closed`.
2. **Current-target delete/create/reset was skipped.** The transition is now
   applied at its recorded event even when the name is a current frame target;
   pre-frame entries apply before target pre-registration, and draw/clear
   re-register the target after a transition. A retained target reused with an
   incompatible size/format fails closed in `target_for`. Regression:
   `test_delete_of_a_current_frame_target_is_applied_at_its_event`.

A carried resource with an unreproduced mutation is now invalidated even when
the current frame does not sample it. Additional hardening: digest memoization
keys on inode/ctime, the translation cache key includes translator identity,
decisions require a valid frame sequence, previous-frame name arrays are
type-checked, a first frame with a previous link is rejected, the external
capture stop file is honoured, an incomplete continuous frame stops the
campaign, and queued frames are exposed separately.

The authentic three-frame replay still completes with 3/3 `history_status=1`
and 6/6 reconciled checkpoints per frame.
