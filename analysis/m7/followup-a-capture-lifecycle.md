# M7 follow-up A — capture and lifecycle correctness

Status: implementation and native/authentic validation complete; independent
review and merge authorization pending. No automatic merge is enabled.

Base: `main` at `e2853b1f` (PR #203). This follow-up does not reopen or rewrite
PR #202/#203.

## Problem

The per-frame texture cache was keyed by the formatted `target-name` string and
was never invalidated inside a frame.  `glTexSubImage2D`/`glTexImage2D` between
two draws therefore made the second draw reuse the first draw's payload.  The
delete record deduplicated by numeric GL name for the whole frame, so a
`delete → recreate/render → delete` sequence lost its second transition.  All
bounded collections silently dropped entries at 64 elements.

## Changes

### Targeted texture-content invalidation

`benchmark/eu4_gfx_frame_trace.c` now stores the real texture identity
(`GLenum target`, numeric name) in each cache entry.  The mutation callback
normalises a cube face to `GL_TEXTURE_CUBE_MAP`, invalidates exactly
`(target, name)`, and continues to cache unchanged textures.  `glDeleteTextures`
invalidates every cached target for that name.  The GL interposer passes the
actual target instead of a boolean.

The captured event stream already carries the authoritative payload per draw;
the replay's external-texture cache key already included the payload digest, so
no replay change was needed for content versions.  No extra GPU readbacks were
added: a changed version costs the one readback that produces its evidence, and
an unchanged version is still a cache hit.

### Repeated lifecycle transitions

Delete records are appended per observed transition.  Only duplicate names
inside one `glDeleteTextures` call are collapsed.  A `delete → recreate/render
→ delete` sequence now survives with both event indices.

The Metal session already applied lifecycle records in event order.  It now also
drops the framebuffer-keyed Metal allocation when a named render target is
deleted, so a recreated numeric name gets a fresh generation instead of
continuing the deleted target's generation.

### Fail closed at the capacity boundary

The 64-entry current targets, carried targets, delete records, mutation records
and campaign records now call `resource_limit` instead of silently dropping an
entry.  A frame that overflows is published `complete=false`,
`reason=resource_limit`, with `capacity_exceeded` and `capacity_limit`; the
continuous campaign stops as `incomplete`/`resource_limit`.  Carried-set
overflow is detected when the next frame merges the preceding frame's writes,
before that frame's manifest is produced, and is propagated as a failed frame.
Lifecycle serialization is dynamically sized, so 64 surviving records are not
truncated by a fixed JSON buffer.

`frame-sequence.json` and `frame-trace.json` now publish cache
reads/hits/invalidations and the capacity classification, which makes the
overhead and fail-closed path observable rather than inferred.

## Regressions

Native (macOS, real legacy GL context, no Metal or EU4):

- `tests/gfx_texture_mutation_host.m` + `tests/test_gfx_texture_versions.py`:
  subimage mutation, reallocation, numeric-name reuse, update before the first
  sample, and an unchanged texture.  Assertions cover both draw payload digests,
  cache hits/invalidations, frame completeness and the seal.
- Metal replay of two payloads of the same texture name in one frame: distinct
  checkpoint readbacks prove each draw consumed its own version.
- `tests/gfx_frame_trace_scenarios.c` + `tests/test_gfx_frame_trace_writer.py`:
  repeated delete records at events 2 and 4, exact-capacity/one-beyond for
  targets, deletes and mutations, carried-set overflow, 64-frame campaign record
  capacity, and an incomplete overflow frame that the graph classifies
  `invalid`.
- `tests/test_m7_continuous_replay.py`: delete-before-recreate resets the
  Metal generation; two distinct deletes leave no stale recreated resource;
  renderer-owned mutation still fails closed.
- `tests/test_gfx_frame_trace_writer.py`: a renderer-owned mutation is recorded
  with its target so the Metal lifecycle can fail closed; 64-frame campaign
  records are preserved exactly.

Full suite on Python 3.14: 11 failures, all in `tests.test_trace_tr3` because
the QBDI 0.12.1 release is absent from this host.  The same module on the exact
base `e2853b1f` produces the same 11 failures; `test_runner compare` reports
`no_new_failures` with all 11 classified as pre-existing.  No other full-suite
failure was observed.

## Authentic validation

One bounded EU4 campaign using the final instrumentation build, three
consecutive title frames, one process (pid 27780, session
`c66b3625-e06f-43f2-b957-61f2f14f66c2`), executable
`b3d38876…`, instrumentation dylib
`e8f683c934b726e24188a68c6f78a6b79cc06cc8cda4b3c6215e6e2ec5283b3f`.

| frame | ops | seal | carried | lifecycle | cache reads/hits/invalidations | readback / seal |
| --- | --- | --- | --- | --- | --- | --- |
| 1 | 325 | valid | 0 | 1 delete burst | 2467 / 2406 / 0 | 23.8 / 40.7 ms |
| 2 | 325 | valid | 6 | 0 | 2467 / 2406 / 0 | 23.8 / 41.4 ms |
| 3 | 325 | valid | 6 | 0 | 2467 / 2406 / 0 | 21.0 / 37.6 ms |

- Campaign `complete`/`frame_limit`, 487,868,876 bytes written and 775,259,422
  deduplicated.
- All three frames replayed in one persistent Metal session: `history_status=1`,
  `missing_history=0`, `stale_history=0`; coordinator `status=ok`.
- One live EU4 process, `terminated=true`, `killed=false`; background display
  restored (`already_original`); no `interpose-error.jsonl`.
- All three classifications remain
  `difference_with_partial_state_coverage` with the known event-286 copy
  difference; no new Metal defect is claimed.
- Overhead is within PR #203's range: readback 21–24 ms, sealing 38–41 ms per
  frame.  The cache served 2,406 of 2,467 samples; the title frame observed no
  CPU texture mutation, so it recorded zero invalidations.

Evidence: [followup-a-evidence.json](followup-a-evidence.json).

## Scope and limitations

- The authentic title frame did not exercise `glTexSubImage2D` between draws,
  so A1 versioning is proven by the native GL capture regression, not by the
  live campaign.
- No carried renderer output was sampled in the authentic campaign
  (`carried_bindings=0`); carry, mutation and delete/recreate fail-closed
  branches are synthetic native/Metal evidence.
- Portable Linux CI skips the macOS-only native tests; a green portable check
  is not macOS runtime evidence.
- The event-286 copy difference and partial state coverage
  (`gl_dither`, `multisample`) are unchanged from PR #203; this follow-up does
  not qualify renderer equivalence.

## Files

- `benchmark/eu4_gfx_frame_trace.c`, `benchmark/eu4_gfx_frame_trace.h`
- `benchmark/eu4_gfx_gl_interpose.c`
- `benchmark/eu4_gfx_frame_replay.mm`
- `tests/gfx_frame_trace_scenarios.c`
- `tests/gfx_texture_mutation_host.m`, `tests/test_gfx_texture_versions.py`
- `tests/test_gfx_frame_trace_writer.py`
- `tests/test_m7_continuous_replay.py`
- `tests/gfx_interpose_host.c`, `tests/gfx_interpose_source_host.m`
