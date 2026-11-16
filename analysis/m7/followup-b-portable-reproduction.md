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

### Required contiguous prefix (conservative, not minimal)

`automation/gfx_replay_case.py` adds `package_sequence_diagnosis`.  When the
target frame declares no carried renderer output it delegates to the existing
single-frame API.  Otherwise it walks the ordered `gfx-frame-sequence/v1`
records and computes the contiguous prefix that is **transitively closed** over
carried resources: a predecessor that carries a resource produced even earlier
pulls that earlier producer into the prefix.  A missing producer, a delete
before the last carry without a later producer, or a CPU mutation of a carried
resource without a later producer stops packaging with an explicit
`missing_predecessor` / `missing_mutation_record` failure.

The prefix is conservative rather than provably minimal: once the earliest
transitive producer is selected, every intermediate frame up to the target is
copied because the predecessor digest chain and the frame-start carried
snapshots bind the ordered sequence.  The manifest field is still named
`minimal_prefix` for schema compatibility; it means "shorter than the full
campaign", not "graph-minimal".

A delete in an intermediate frame that last carries a resource is normal
end-of-life cleanup and is allowed; native capture snapshots the cumulative
carried set at frame start, so that frame may still list the resource.  A delete
in the **target** frame of a resource it carries is refused: replay removes the
resource from the final Metal history, so the target's carried-sample
provenance and the verifier's carried-resource check can no longer reconcile
even when the pixels match.  The first selected frame must declare no carried
output.

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

### Per-frame shader provenance

A live continuous capture can translate a program set per frame.  Packaging no
longer copies one shared shader directory for every frame: for each selected
frame it resolves the effective translation directory (`<frame>/shaders` first,
matching the sequence CLI's per-frame override; an explicitly supplied
directory, sibling `translated/` or the Metal result directory as fallbacks),
copies it to `frames/NNNNNN/shaders`, and records `shader_dir`, the report
digest and the captured program list in `sequence.json`.  No shader is
translated again and no union cache is introduced.  The case-level `shaders/`
directory remains the CLI fallback and is the target frame's own translation; it
is never a merged report.

Packaging validates every resolved frame report before claiming completeness:
the report must be parseable, every program referenced by that frame's draws
must be present with `status="ok"`, and each vertex/pixel stage must declare its
MSL payload and captured stage identity (a missing MSL file is rejected).  An
unusable report is routed to the incomplete path even when the directory and
`shaders.json` exist.  `verify` repeats the same checks on the relocated case and
requires the captured `source_sha256` identity to match the translation; a
missing declaration is a verification failure, not a silent pass.  If a selected
frame has no available or usable translation, packaging publishes an incomplete
summary with `reason=shader_artifact_unavailable` instead of claiming independent
reproducibility.

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

### Explicit artifact budget on every path

Both `package_sequence_diagnosis` and `package_diagnosis` now enforce
`max_bytes` against the **whole generated package**: captured frame payloads,
shader artifacts, replay/checkpoint readbacks, manifests and diagnostics.  The
single-frame path additionally budgets its generated difference image; the
sequence path never writes one.  The reviewed head instead fell back to an
unlimited (`max_bytes=0`) single-frame package when the prefix did not fit, so
the published case could exceed the configured budget by orders of magnitude.

Packaging is staged in a temporary sibling directory, measured after it is
complete and published only when the total size fits.  A pre-write estimate
(frame bytes excluding the shader subtree, each effective shader directory
counted once, plus replay/checkpoint bytes; the single-frame estimate also
charges its difference image) skips the work
entirely when the payload alone already exceeds the budget.  When the required
history does not fit, a bounded summary is published instead:

- `reproduction.status="incomplete"`, `independently_reproducible=false`,
  `reason="artifact_budget"`, `required_bytes`, `max_bytes` and the structured
  `missing` list (including `required_frames`);
- `reproduction-incomplete.json`, the scrubbed diagnostic report, the
  dependency graph, `graph.json` and the README, with no replay command;
- `verify` fails with `reproduction is explicitly incomplete`, and
  `replay_case` refuses the case before Metal starts.

If even that minimum summary exceeds the budget, packaging raises an explicit
`case_budget` error and publishes nothing.  Source input directories are never
deleted or overwritten: the destination is validated for ownership/overlap
before staging, and a failed or over-budget build removes only its staging
directory.

### Complete manifest verification

`verify` now requires the v2 inventory to be complete, not merely internally
consistent:

- every structurally required payload (sequence manifest, per-frame shader
  reports and their MSL/GLSL/SPV files, diagnostics, dependency graph, each
  frame's sequence/trace/events/reference, each checkpoint request and receipt)
  must be listed in `case.json.files`;
- each frame's declared programs must match the programs its draw events
  reference, including the captured stage digests;
- within the authoritative payload directories (`frames/`, `shaders/`,
  `checkpoints/`) any file absent from the inventory is rejected, so a
  substituted shader, checkpoint or resource blob cannot hide outside the
  inventory.  Scratch output written by the documented replay command
  (`replay-rerun/`) is not reproduction evidence and does not invalidate the
  package;
- `case.json.sequence` must resolve to the in-case `sequence.json`; verifying an
  external/heuristic sequence path that replay never reads is refused;
- duplicate manifest paths are rejected;
- absolute paths, `..` traversal and symlink escapes are rejected by resolving
  every declared path and requiring it to stay inside the case directory;
- each declared file's size and SHA-256 are rechecked, and every resource blob
  referenced by an event stream is re-hashed by `m6_frame.load_trace`.

These checks run in `verify` before `replay_case` can start Metal.  Valid
historical v1 cases keep the original required-file rules; the stricter
inventory additionally applies only to complete v2 sequence packages, while
path-safety and duplicate-entry checks apply to every schema.

## Evidence

### Deterministic cross-frame fixture

Four synthetic Metal frames: frame 1 is unrelated and successful; frame 2
renders texture 500; frame 3 leaves it unchanged; frame 4 samples it.  The
required contiguous prefix excludes frame 1 and packages frames 2–4 (target
renumbered 3).

- Prefix source: 10,763 bytes; complete four-frame source: 13,684 bytes;
  excluded: 2,921 bytes (21.4%).
- Relocated case on disk: 72,568 bytes (70 files) including the per-frame
  copies of the shared translation, checkpoints and replay artifacts.  Immutable payloads are hard-linked before relocation.
  The case digest is independent of the case/capture directory path.
- The original campaign directory is removed before verification and replay.
- Unfaulted relocated replay: final classification `qualified_match`.
- Controlled fault (`EU4_FRAME_REPLAY_DISABLE_RT_FLIP=1`, first observable when
  frame 4 samples the carried target): final classification is divergent,
  mismatched pixels > 0, earliest affected frame is the renumbered target
  (sequence 3).  The captured first-divergence record does not attribute an
  event or operation to this fault, so `earliest_affected_operation` is `null`
  rather than an invented `draw`.
- A five-frame transitive fixture (frame 2 produces 600; frame 3 carries 600
  while producing 500; frame 4 carries 500/600 and deletes 600; frame 5 samples
  500) packages frames 2–5, not 3–5, verifies, and replays to
  `qualified_match` with carried history.  The delete frame keeps the
  cumulative carried declaration that native capture writes.
- Negative tests cover missing predecessor, reordered predecessor, corrupt
  payload, wrong session, missing lifecycle/mutation record, delete of a carried
  resource, wrong generation, stale checkpoint request, packaging-time missing
  producer/mutation refusal, an undeclared required shader, an undeclared
  checkpoint request, duplicate manifest entries, `..` traversal, a symlink
  outside the case, an external `case.json.sequence` reference, a missing
  predecessor resource blob and a target-frame cleanup delete of a carried
  resource.  A further test runs the documented replay command into the case
  and re-verifies it, so scratch output cannot invalidate the package.
- Budget tests cover `max_bytes=1` (explicit `case_budget`, nothing published),
  a complete package measured at N=72,560 bytes and repackaged at N-1 (a
  30,196-byte incomplete summary, no replay command, `verify` fails) and at N
  (complete and verifiable), a single-frame budget below its summary, and an
  over-budget run that leaves every source frame byte-identical.

### Prefix efficiency

The selected prefix is the contiguous run from the earliest transitive producer
to the target frame.  It is conservative, not provably minimal:

| fixture | selected original frames | carrying resources | producing frames | prefix / full bytes | excluded |
| --- | --- | --- | --- | --- | --- |
| four-frame | 2, 3, 4 (target renumbered 3) | 500 | 2 | 10,763 / 13,684 (78.7%) | 1 frame, 2,921 bytes (21.4%); 70 files, 72,568 host bytes |
| transitive five-frame | 2, 3, 4, 5 (target renumbered 4) | 500, 600 | 2, 3 | 15,025 / 17,946 (83.7%) | 1 frame, 2,921 bytes (16.3%); 88 files, 84,704 host bytes |

In the transitive fixture only frames 2 and 3 actually produce the carried
resources; frames 4 and 5 are the cleanup frame and the target.  Frame 4 is
retained because the replay contract binds each frame to the immediately
preceding capture identity and to the cumulative carried set at frame start.
Removing an interior frame would require rewriting the predecessor digest chain
and re-deriving every later carried snapshot; the resource graph alone does not
prove that unnecessary, so selection stays conservative rather than claiming
minimality.  Exact file counts and case sizes are recorded in the receipt.

### Canonical captured-frame prefix

A three-frame ordered prefix built from the canonical `menu-occ300` capture
(frame 1 produces 443; frames 2–3 carry and sample it) was packaged, relocated,
verified and replayed with the original campaign removed.

| item | value |
| --- | --- |
| Case schema | `gfx-strategy-b-case/v2` |
| `sequence.json` SHA-256 | `c3dcbb7d…` |
| Case manifest SHA-256 | `791c20bc…` |
| Case size | 1,322,029,913 bytes, 3,814 files |
| Shader layout | per-frame `frames/NNNNNN/shaders`, target fallback |
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
  graph and diagnostic report, include the incomplete receipt in the manifest,
  and state explicitly that they are not independently reproducible.
- The replay reuses the existing Metal core and diagnosis contract; there is no
  second comparison engine or renderer implementation.
- Copied shader translation metadata (`shaders/translation.json`,
  `shaders/shaders.json`) is verbatim digest-addressed source evidence and may
  retain the translation tool's own provenance paths.  It is not rewritten;
  case digests remain deterministic because the same captured shader source is
  packaged.
- A target-frame cleanup delete of a carried resource is refused at packaging
  instead of being advertised as reproducible: replay would remove the resource
  from the final Metal history before the carried-sample provenance check.
- Per-frame shader provenance assumes the frame's declared translation covers
  its captured programs; it does not merge arbitrary program sets into a
  synthetic union.  Frames without a usable translation are explicitly
  incomplete rather than silently reusing another frame's shader report.
- Portable Linux CI skips the macOS-only native/Metal tests.  A green portable
  check is not macOS runtime evidence.
- The three-frame canonical prefix is larger than the 512 MiB default budget.
  The default path now publishes an incomplete budget summary; the relocation
  test opts into an explicit unlimited budget (`max_bytes=0`) because it
  measures relocation, not budget policy.

## Files

- `automation/gfx_replay_case.py`
- `automation/m7_continuous.py`
- `automation/m6_frame.py`
- `automation/gfx_frame_graph.py`
- `tests/test_gfx_replay_case_sequence.py`
- `tests/test_gfx_replay_case.py`, `tests/test_gfx_output_safety.py`
- `tests/test_m7_continuous_supervisor.py`
- `analysis/m7/followup-b-relocation-receipt.json`
