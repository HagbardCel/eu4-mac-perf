# M7 instrumentation follow-up plan (deferred)

**Status: plan only.** This infrastructure PR deliberately does not implement a
new native per-frame instrumentation system, a finer simulation identity or
generalized per-draw tracing. The M7.0 canary established that those are the
minimum observations needed to resolve the residual question; this document
records the smallest implementation and a prioritized, evidence-driven path.

## What is already in place

- `capture_every_frame` now **fails closed**: `gfx_scenario` rejects a scenario
  that asks for it instead of silently ignoring it.
- The observer already emits a per-frame 64x41 `preview_sha256`, full extent,
  `game_day`, `update_identity`, `caller_signature`, draw count,
  `capture_valid` and `gpu_completed`.
- The campaign stop path preserves the failing frame and its immediate
  neighbours from the bounded ring, and `gfx_align.staticity_evidence()` can
  verify `stable_frames` consecutive identical full-frame digests when they are
  present.
- `eu4_diff_inspect --temporal` can compare preserved consecutive frames and the
  cross-process pair offline.

The missing piece is that normal observation streams carry only a downscaled
preview digest. A preview can be bit-identical while the full-resolution frame
differs (covered by a regression test), so preview hashes cannot establish
staticity. Pause state is also not recorded per frame, and `game_day` is too
coarse for intra-day correspondence.

## Smallest necessary observation changes

These are additive fields/controls on the existing observer; no new agent loop,
graphics IR or per-draw tracing system is required.

### 1. Full-resolution digest series (P0)

Add to each `gfx-frame-observe/v1` record:

| Field | Type | Meaning |
|---|---|---|
| `full_sha256` | 64 hex | SHA-256 of the full-resolution frame as observed (before downscale) |
| `full_bytes` | integer | bytes hashed; must equal `width * height * 4` |
| `digest_scope` | `"full"` | distinguishes it from `preview_sha256` |

The observer computes this once per observed frame while the payload is already
in memory. It does **not** need to dump raw frames: resume/pause verification
and staticity need equality, not pixels. Keep the existing raw dump-on-stop
behaviour for the failing pair.

`capture_every_frame` (or a renamed `observe.every_frame_digest`) then becomes a
bounded *digest* mode: emit a digest for every observed frame, cap the ring,
never retain full raw frames for every frame.

Acceptance: a synthetic observer fixture and one recorded session where
`full_sha256` is stable for N consecutive frames while `preview_sha256` is also
stable; and a case where preview is stable but `full_sha256` is not. The runner
must be able to wait for `stable_frames` identical `full_sha256` values on both
roles and stop with `scene_not_static` instead of comparing.

### 2. Per-frame pause state (P0)

Add a nested block to the observation record:

```json
"pause": {
  "requested": true,
  "confirmed": true,
  "state": "paused",          // paused | running | unknown
  "confirmed_at_draws": 506,
  "since_frame": 72
}
```

The native pause path already knows each of these at the draw threshold. Record
them, and re-assert the pause over the comparison window rather than checking
the day counter once after two seconds. A `running` state anywhere in the
window makes the pair `incomplete` with reason `scene_not_static` (or
`pause_released`), never `rendering_difference`.

Acceptance: replay an offline session whose candidate resumed 1,994 ms after the
request; the runner must report `pause_released`, not a renderer verdict.

### 3. Finer simulation identity (P1)

`update_identity` is the coarse update singleton; `game_day` is daily. Add one
field read from the same engine singleton already used for `update_identity`:

| Field | Type | Meaning |
|---|---|---|
| `simulation_tick` | unsigned 64-bit | monotonic in-session update/tick serial |

If no monotonic serial exists, keep the field `null` rather than inventing one;
the correspondence predicate must stay three-valued (`equal`, `different`,
`unknown`). Alignment may use it only when both sides provide it.

Acceptance: a recorded intra-day pair that shares `game_day` but differs on
`simulation_tick` is `not_aligned`; a pair that shares both is eligible for
pixel comparison only after staticity and pause verification.

### 4. Bounded per-draw operation prefix (P2)

The canary's residual ~1.3 % border/HUD difference needs the first differing
pass attributed to a draw. The smallest useful extension is a bounded ordered
prefix for **one** frame, not generalized tracing:

- reuse the existing selected-event capture path (`automation.m6_frame`) with a
  frame window around the first differing frame;
- record `caller_rva`, shader/pipeline identity, the effective
  sampler-to-texture-unit map, and render-target/viewport state for at most the
  first N draws after the correspondence point;
- hash and bound the artifact; store provenance (frame identity, digests,
  capture time, observer version).

Acceptance: a replay of the preserved failing frame yields a prefix whose first
divergence is attributable to one operation; byte/length hashes are preserved
and the capture is bounded.

## Prioritized work plan

1. **P0 — staticity is observable, not inferred.** Full-resolution digest per
   observed frame + pause state + runner wait for stable digests/pause over the
   comparison window. Stop with `scene_not_static`/`pause_released` instead of
   comparing. This is the only change required to make the M7.0 question
   answerable without preserving every raw frame.
2. **P1 — intra-day correspondence.** Add `simulation_tick` (nullable) and use
   it in alignment; reclassify the recorded `live-a6`/`live-a10` evidence
   offline. Do not weaken the existing day/identity predicates.
3. **P2 — first-difference attribution.** Bounded per-draw prefix for the first
   differing frame, reusing the selected-event capture. Only after P0/P1 show a
   genuinely static, same-tick pair.
4. **P3 — one live qualification.** Run one `opengl-save-consistency`
   experiment with the new observation fields. Expected outcomes: a verified
   static pair (then the residual difference is a real same-backend variability
   measurement) or an explicit `scene_not_static`/`pause_released` result. A
   renderer-blame verdict is not reachable without staticity and pause evidence.

## Non-goals

- No generalized per-draw tracing, frame graph, or second comparison engine.
- No new agent loop, MCP server or graphics intermediate representation.
- No threshold changes to make a comparison pass.
- No raw full-resolution frame stream: digests are the bounded observation;
  pixels remain dump-on-stop.
- No Metal correctness claim from this infrastructure work.

## Schema and provenance discipline

- Additive fields keep `gfx-frame-observe/v1`; bump the observer schema only if
  a field changes meaning. Consumers must treat missing fields as `unknown`,
  never as equal.
- Every digest/pause record keeps its source file and capture time; the
  campaign result preserves the observation window so an offline replay can
  re-derive the same staticity/pause predicate.
- The offline case and the campaign verdict must keep using one predicate
  (`gfx_align.staticity_evidence` plus the pause check), as the canary correction
  already does for staticity.
