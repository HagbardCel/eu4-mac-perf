# M7 follow-up B — portable multi-frame reproduction

Status: implementation and synthetic/authentic-fixture validation complete;
independent review and merge authorization pending. No automatic merge is
enabled.

Base: `main` at `e2853b1f` (PR #203). Follow-up A (`m7/followup-capture-lifecycle`)
is a separate PR and is not a base dependency of this branch.

## Problem

A continuous campaign packaged only the offending frame.  That cannot reproduce
a frame K that samples a renderer-owned texture last produced in frame K-2: the
relocated case has no producer for the carried resource and cannot reconstruct
its Metal history.  A single-frame case could also retain an absolute
`previous.dir` pointing at the original campaign.

## Changes

### Minimal ordered prefix

`automation/gfx_replay_case.py` adds `package_sequence_diagnosis`.  When the
target frame declares no carried renderer output it delegates to the existing
single-frame API.  Otherwise it walks the ordered `gfx-frame-sequence/v1`
records and computes the smallest contiguous prefix that is **transitively
closed** over carried resources: a predecessor that carries a resource produced
even earlier pulls that earlier producer into the prefix.  A missing producer, a
delete without a later producer before the last carry, or a CPU mutation of a
carried resource stops packaging with an explicit `missing_predecessor` /
`missing_mutation_record` failure.  The first selected frame must therefore
declare no carried output.

Only that prefix is copied.  Unrelated successful frames are excluded.
Immutable payloads (blobs, event streams, manifests, references) are hard-linked
when the filesystem allows; mutable sequence/seal metadata is copied.  One
shader directory is copied, so unchanged shaders are never retranslated.

### Relocatable ordering metadata

Each copied frame keeps its authoritative capture identity (session, pid,
capture id, manifest/event/reference digests, lifecycle, renderer targets).
Only the sequence container is rewritten:

- `sequence_index` becomes the position in the included prefix;
- `previous.dir` becomes a sibling-relative `../NNNNNN` link;
- the original index/campaign length and the original predecessor are retained
  as `portable_original_*`.

The case-level `sequence.json` (`gfx-strategy-b-case-sequence/v1`) binds every
included frame to the original session and ordered sequence, records the
checkpoint request digest per frame, and states the prefix size and excluded
frame count.  `m6_frame.load_seal` accepts a relative seal path only when it
matches the frame's location inside a v2 case, and
`gfx_frame_graph.load_frame_history` resolves a relative predecessor against
the owning frame.

### Verification and replay

`verify` for a v2 case detects:

- missing or reordered predecessor, including a broken capture-id/digest chain;
- corrupt resource payload (file size/digest drift and blob digest mismatch);
- wrong session identity, including checkpoint receipts from another session;
- a carried resource with no included producer (incorrect generation);
- a missing lifecycle array, a mutation of a carried resource, or a delete of a
  carried resource without a later Metal producer;
- a stale or substituted checkpoint request (request digest and receipt
  binding);
- nonportable absolute frame paths.

`replay_case` executes the relocated prefix through the existing
`m7_continuous.run_campaign` path: one persistent Metal session, one translated
shader directory, same diagnosis engine.  It returns the final frame diagnosis
plus `campaign` and `reproduction` summaries.  Existing fault switches
(`EU4_FRAME_REPLAY_DISABLE_RT_FLIP`, sampler shift) are scoped to the replay
process.

### Explicit incomplete packaging

If the minimal prefix exceeds the configured artifact budget, the case falls
back to the target's single-frame diagnostic evidence when possible, writes
`reproduction-incomplete.json`, sets
`reproduction.status="incomplete"`/`independently_reproducible=false`, and
records the exact missing dependency or required byte count.  `verify` then
fails, so the case cannot be advertised as independently reproducible.  If the
target itself carries history, only a diagnostic-only case is written; the raw
diagnostic report and replay artifacts are preserved.

## Evidence

### Deterministic cross-frame fixture

Four synthetic Metal frames: frame 1 is unrelated and successful; frame 2
renders texture 500; frame 3 leaves it unchanged; frame 4 samples it.  The
minimal prefix excludes frame 1 and packages frames 2–4 (target renumbered 3).

- Prefix source: 10,817 bytes; complete four-frame source: 13,713 bytes;
  excluded: 2,896 bytes (21.1%).
- Relocated case on disk: 60,540 bytes including shaders, checkpoints and replay
  artifacts.  Immutable payloads are hard-linked before relocation.
- The original campaign directory is removed before verification and replay.
- Unfaulted relocated replay: final classification `qualified_match`.
- Controlled fault (`EU4_FRAME_REPLAY_DISABLE_RT_FLIP=1`, first observable when
  frame 4 samples the carried target): final classification is divergent,
  mismatched pixels > 0, earliest affected frame is the renumbered target
  (sequence 3), operation `draw`.
- A five-frame transitive fixture (frame 2 produces 600; frame 3 carries 600
  while producing 500; frame 4 deletes 600; frame 5 samples 500) packages
  frames 2–5, not 3–5, and verifies successfully.
- Negative tests cover missing predecessor, reordered predecessor, corrupt
  payload, wrong session, missing lifecycle/mutation record, delete of a carried
  resource, wrong generation, stale checkpoint request, packaging-time missing
  producer/mutation refusal, and the artifact-budget incomplete result.

### Canonical captured-frame prefix

A three-frame ordered prefix built from the canonical `menu-occ300` capture
(frame 1 produces 443; frames 2–3 carry and sample it) was packaged, relocated,
verified and replayed with the original campaign removed.

| item | value |
| --- | --- |
| Case schema | `gfx-strategy-b-case/v2` |
| `sequence.json` SHA-256 | `6221c9fe…` |
| Case manifest SHA-256 | `ad796119…` |
| Verification | ok, no problems |
| Replay target | sequence 3 |
| Campaign | complete |
| History | `ok`, carried bindings 1 |
| Missing/stale history | 0 / 0 |
| Metal render-target bindings | 273 |
| Final pixels | 0 / 1,930,176 difference |

The final classification is `incomplete_diagnostics` only because this
historical capture has no same-process seal; the relocation and carried-resource
replay are verified, and no renderer-equivalence claim is made.  The frame
content is authentic captured evidence, but the three-frame ordering is a
synthetic reconstruction of a continuous sequence.  It is not an authentic
same-process campaign.

Receipt: [followup-b-relocation-receipt.json](followup-b-relocation-receipt.json).

## Scope and limitations

- The injected cross-frame discrepancy is synthetic; no new EU4 launch is made
  by this follow-up.  The canonical fixture proves relocation of authentic
  captured frame content, not an authentic cross-frame defect.
- The synthetic frames are unsealed; the canonical capture is unsealed.  The
  classification labels this limitation instead of treating it as a renderer
  match.
- Diagnostic-only budget cases keep absolute paths out of their dependency
  graph and state explicitly that they are not independently reproducible.
- The replay reuses the existing Metal core and diagnosis contract; there is no
  second comparison engine or renderer implementation.
- Portable Linux CI skips the macOS-only native/Metal tests.  A green portable
  check is not macOS runtime evidence.
- The three-frame canonical prefix is larger than the 512 MiB default budget;
  the budget-limited path correctly produces an incomplete case, so the
  relocation test uses an explicit unlimited budget.

## Files

- `automation/gfx_replay_case.py`
- `automation/m7_continuous.py`
- `automation/m6_frame.py`
- `automation/gfx_frame_graph.py`
- `tests/test_gfx_replay_case_sequence.py`
- `tests/test_m7_continuous_supervisor.py`
- `analysis/m7/followup-b-relocation-receipt.json`
