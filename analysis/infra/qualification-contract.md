# Campaign qualification contract (Phase 1-4 corrections)

## Execution versus qualification

`result.json` now carries three separate facts:

- `result`: execution outcome (`completed`, `stopped`, `failed`, `incomplete`);
- `stop`: the low-level reason/trigger, unchanged;
- `classification` / `qualification`: whether the run *proves* rendering
  equivalence.

Classifications: `match`, `rendering_difference`, `gpu_execution_failure`,
`alignment_failure`, `incomplete`, `infrastructure_failure`, `process_failure`.

A `match` requires all of:

1. at least `requested_frames` valid pixel comparisons (`passed + failed`);
2. all required comparisons passed;
3. no unverified/not-compared/not-aligned required pair;
4. clean GPU health and no instrumentation/process failure;
5. the configured policy is exact or tolerant (never diagnostic);
6. **verified static-scene correspondence** (`settle.stable_frames`
   consecutive identical full-frame digests on both sides).

Frame-limit alone never qualifies: 100 paired observations with zero valid
comparisons classify as `incomplete` and report the exact coverage gap.

## Scenario controls

Unimplemented controls now fail closed with `unsupported_scenario_feature`
instead of being silently accepted: game pause, non-default speed, input
events, arbitrary game-config overrides, and save names other than
`continuelastsave`. The supported save scenario appends
`--continuelastsave` to both processes.

## Correspondence

Coarse image correlation is diagnostic only; it is no longer an authoritative
veto. A catastrophically wrong candidate frame is compared when scene/caller/
draw/extent correspondence is otherwise established. Conversely, a `match`
requires a verified static scene; animated or weakly identified scenes remain
`incomplete` with an explicit weak-correspondence reason.

An engine-level update identity is now emitted per observation. The observer reads the exported `CCurrentGameState::_pInstance` singleton at
RVA `0x2244620` with `vm_read_overwrite` (so a stale pointer cannot fault the
game) and reads the day counter at `+0x1d18` (written by
`CGameState::SetCurrentDate`), emitting `game_day` and an `update_identity`
derived only from that deterministic counter.  The rest of the date block varies
per process and is deliberately not hashed. When
both sides have a non-zero identity, equality is weighted as independent
alignment evidence and a mismatch makes the pair `not_aligned`; the target-phase
gate also refuses unequal identities. If the singleton is not live, identity is zero and correspondence is explicitly
weak. The identity was observed live in a `--continuelastsave` reference at frame
5 with `game_day=43808760`.  A later repeat showed that hashing the whole date
block produced different values between two processes; only the day counter is
deterministic.

Stability is also measured from consecutive full-frame digests. Loading screens
therefore cannot qualify.

Two earlier loaded-save OpenGL/OpenGL experiments (before update identity)
produced 373 vs 361 draws and different caller signatures, so all 20 pairs were
`ambiguous`. A later identity-gated run aligned 20 pairs with identical
signature/draw counts, but raw payloads were missing because the streaming path
issued a per-pair request after the phase-entry window and overwrote it. A
repeat then showed the full-block identity differed between processes. Streaming
now issues only one phase-entry window, and identity hashes only the
deterministic day counter.

Experiment A after those fixes produced 20 pairs but only 13 aligned. The
streams had equal `game_day`/`update_identity`; the pair dict simply omitted
both fields, so alignment could not use them. Pair records now carry identity, and a unit test asserts it. Replaying the
recorded Experiment A observation window offline through the fixed alignment
gives **20 aligned, 0 ambiguous** pairs; the real window is committed as a
regression fixture. Pixel comparison still needs a live re-run. The save
continues running, so the two processes are not at the same update. Experiment A
therefore failed before any GL/Metal comparison; this is recorded as an
`alignment_failure`, not as a renderer difference.

## Comparison policy

- `exact`: a scale-normalized comparison cannot be exactly qualified. If it
  differs, the pixel verdict remains `fail`; if it is equal, the result is
  `unverified`. `exact_qualified` is reported separately.
- `tolerant`: global thresholds plus declared region thresholds.
- `diagnostic`: reports differences, never qualifies.
- Region policies (`structural`, `tolerant`, `ignore`) now affect acceptance;
  a small text region can fail a run whose global fraction is low.
- Horizontal mirroring remains an independent failure signal.

## First-divergence integration (offline)

`gfx_case.build_case(..., semantic=...)` now packages a semantic divergence:
`operations/semantic.json` contains the first mismatched field, the analysis
names it, and the missing-evidence claim is suppressed. When no operation prefix
exists, `repro/targeted-capture-request.json` records the exact frame, caller
signature and required operation fields for one targeted capture before another
campaign. This is a capture request, not yet a packaged executable renderer
reproduction; the existing selected-event replay remains the executable path
when a trace exists.

## Cleanup

Each cleanup responsibility is best-effort and independent: release paused
frames, terminate owned process groups only, reap, release the lease, finalize
every ledger token, write terminal status/result, preserve evidence. A failure
in one action is recorded in `cleanup_errors` and does not skip the others.

Tests cover failures before reference launch, after reference launch/before
candidate launch, and during candidate launch, plus rejected draws, lockstep
timeouts, request acknowledgement, immutable dump metadata, region policies,
exact-vs-scaled differences, and qualification coverage.
