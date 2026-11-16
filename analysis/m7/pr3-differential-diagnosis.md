# M7 PR 3 — same-process differential diagnosis and first-divergence evidence

Status: implementation and offline/authentic diagnosis complete. This branch is
rebased onto `main` at `35b8f504` (PRs #199, #200 and #201 merged). The native
early-exit diagnostic hardening from #201 is therefore in the base rather than a
cherry-pick. It does not reopen #199/#200 and does not implement continuous
rendering.

## What changed

### One reusable diagnosis path

`automation/gfx_independent_replay.py` now separates execution from analysis:

- `analyse_frame(frame_dir, metal_result_dir, policy, ...)` consumes an
  already-produced Metal result. It validates the frame seal and captured
  blobs, verifies capture/session/operation identity, builds the dependency
  graph, validates Metal resource provenance, reconciles checkpoints, audits
  shader bindings, calculates pixel metrics, localizes the earliest confirmed
  difference and applies the qualification contract.
- `diagnose_frame(frame_dir, metal_result_dir, output_dir, policy, ...)` writes
  `diagnostic-report.json` and optionally packages a portable case. The normal
  path never starts EU4, a host process or a second Metal replay.
- `replay()` keeps its standalone CLI/API, executes the host once, and then
  calls the same analysis function. There is one comparison engine and one
  diagnosis implementation.

CLI forms:

```sh
python3 -m automation.gfx_independent_replay \
  --frame <sealed-frame> --shaders <translated> --out <replay>

python3 -m automation.gfx_independent_replay diagnose \
  --frame <sealed-frame> --metal <replay> --out <diagnosis> [--package]

python3 -m automation.m6_frame diagnose \
  --frame <sealed-frame> --metal <shadow> --out <diagnosis>
```

### Checkpoints are ready before Metal executes

The in-process coordinator now performs the required sequence:

1. receive and validate the sealed frame;
2. run shader translation;
3. build `gfx-frame-graph/v1` and publish
   `checkpoint-request.json` atomically;
4. write `shader-receipt.json` binding the translated shaders **and** the
   checkpoint request (name, SHA-256, count) to the frame identity;
5. write `ready`;
6. the bridge verifies the receipt and request digest, then passes
   `checkpoint_path` to the existing C ABI;
7. the coordinator verifies the returned report's request digest and runs the
   diagnostic reconciliation.

A stale receipt that lacks the checkpoint fields, a mismatched request digest,
a count mismatch, a missing request, a path-traversal request name or a corrupt
shader artifact is rejected by the production bridge. A requested checkpoint
that never comes back is `incomplete_diagnostics`; duplicate, unexpected,
corrupt or identity-mismatched evidence is `invalid_provenance`. A later pixel
match cannot hide either.

The checkpoint plan remains first-consumer based: the captured OpenGL
intermediate is a **reference checkpoint only**. It is never seeded as a Metal
input. This is the same Strategy B rule as before.

### First-divergence localization

`first_divergence()` now reports:

- frame id, earliest confirmed event and resource;
- producer event, operation kind, entry/caller RVA, shader/program;
- inputs sampled by the producer, render target and downstream consumers;
- effective captured state at the producer;
- GL and Metal checkpoint digests, pixel statistics and prior producers;
- the unresolved interval between the previous matching checkpoint and the
  divergence;
- a `next_checkpoint` suggestion when the copy source or the final-only case
  needs more evidence;
- `confidence`, `authoritative_because`, explicit limitations and
  `root_cause: not_established`.

An earlier non-authoritative checkpoint difference is reported as
`diagnostic_checkpoints` / `earliest_observed_difference`, not promoted to the
confirmed divergence. If only the final image differs, the result is
`final_only` with a proposed next checkpoint. Missing evidence returns an
instrumentation/incomplete result and never a fabricated renderer difference.

### Explicit rendering-state coverage

`state_coverage()` distinguishes:

- `implemented` (captured and consumed by the Metal encoder);
- `unsupported` (`gl_dither`, `multisample`, and enabled stencil test,
  non-fill polygon mode, polygon offset or alpha test that the host does not
  implement);
- `unproven` (captured/referenced without a focused equivalence test);
- `proven_equivalent` (only from a `m7-state-equivalence/v1` evidence document
  or an explicit policy entry).

The report states that unsupported state prevents renderer-equivalence claims
and that it is not evidence that the state caused a particular difference. No
dither/MSAA A/B experiment is claimed.

### Qualification contract

Two disjoint layers are reported:

- the existing `classify_replay()`/`qualification_exit_code()` path remains for
  compatibility with the standalone replay CLI and its tests;
- the PR3 contract reports eight axes — capture validity, frame/shader
  identity, GPU execution, resource provenance, operation coverage,
  rendering-state coverage, pixel comparison and first-divergence confidence —
  and exactly one of:

  `qualified_match`, `qualified_difference`,
  `difference_with_partial_state_coverage`, `incomplete_capture`,
  `invalid_provenance`, `unsupported_state`, `gpu_execution_failure`,
  `incomplete_diagnostics`.

A completed pixel difference is a successful diagnostic, not a tool failure.
Missing evidence fails closed into `incomplete_*`. The native stage/operation
and the first error survive through `replay.json`, `shadow-report.json`,
`coordinator.json`, `capture-report.json` and the diagnostic report.

## Evidence

Tracked summary: [pr3-diagnosis-evidence.json](pr3-diagnosis-evidence.json).
Raw cases and full reports remain under `.local` (gitignored).

### Canonical M6 title fixture

326/326 operations executed, 0 failed, command status 4, readback ok. Final
pixels are exact (0 mismatches) and the diagnostic reports no divergence. The
historical capture is correctly classified `incomplete_capture` because
texture 442 has no captured producer; a pixel match is not promoted to a
capture claim. Checkpoints 431, 432, 447 and 443 match; 433 remains the known
diagnostic RGB8 checkpoint.

### Copy-bearing scene 75

283/283 operations, one transfer, checkpoints reconciled. Final pixels remain
1,792,005 / 1,930,176 different, max channel error 23. The earliest confirmed
authoritative divergence is the copy source checkpoint at event 271
(`fb:0`, producer 270, program 346); the later copy destination checkpoints do
not hide it. `gl_dither` and `multisample` are captured but unsupported, so the
classification is `difference_with_partial_state_coverage`, not a proven Metal
defect.

### Authentic live frame

Original PR2 in-process result (capture id 1, scene 603; no checkpoint
request): 1,162,852 / 1,930,176 final pixels differ (60.2%), max 66, mean max
0.86. Diagnosis of that already-produced result is `incomplete_diagnostics`:
the final difference is preserved, but no intermediate checkpoint exists, so no
root cause is invented.

One explicit offline replay of the same sealed capture, using the derived
checkpoint request (allowed by PR3, no EU4 launch), produced six checkpoints.
The earliest confirmed authoritative divergence is the copy destination
texture 442 at its first consumer (checkpoint event 286; producer copy event
284). The checkpoint differs in 1,237,771 pixels, max 95; final metrics remain
60.2%, max 66. Checkpoint 433 at event 270 is an earlier **diagnostic**
observation (non-authoritative RGB8 format), reported but not promoted.

After the review hardening, this standalone replay is classified
`incomplete_diagnostics`: it proves input digests and the checkpointed
difference, but it cannot prove the original in-process session/pid identity.
The PR 4 authentic campaign supplies that identity separately with its own
in-process shadow reports. The divergence is still reported; equivalence is
not claimed from an unverifiable identity.

The copy source `fb:0` at event 284 was not captured in that launch
(`--copy-source` was not enabled), so the report states the open limitation and
proposes a source checkpoint. It does not claim that the copy implementation is
the root cause, nor that dithering/MSAA caused the difference.

A portable case (`m7-pr3-live-603`) contains the seal, all referenced blobs,
shaders, replay, checkpoints, dependency graph, diagnostic report, difference
image and reproduction commands. `gfx_replay_case verify` rechecks all 1,243
payload SHA-256 entries. The case is 473 MiB under `.local`, so only its
manifest identity is tracked.

## Tests

Focused suites cover:

- the reusable analysis path with synthetic already-produced results
  (`tests/test_m7_diagnosis.py`): qualified match/difference, partial-state
  difference, final-only, intermediate localization, semantic wrong-unit fault,
  missing checkpoint, unbound request, corrupt checkpoint, corrupt capture
  blob, seal mismatch, missing result, state-equivalence evidence, exit codes
  and portable packaging;
- the production receipt verifier (`tests/test_m7_shadow_receipt.py`): valid
  binding, stale receipt, identity mismatch, request digest mismatch, missing
  request, count mismatch, path traversal and corrupt shader artifact;
- the canonical title and scene-75 fixtures through the real Metal host
  (`tests/test_m7_canonical_diagnosis.py`);
- the existing graph, checkpoint, case, seal, coordinator, ABI and replay
  suites (174 focused tests).

## Known limitations

- The live copy source is not checkpointed; the source-vs-copy attribution
  needs a launch (or a re-capture) with `EU4_GFX_FRAME_TRACE_CAPTURE_COPY_SOURCE=1`.
- `gl_dither` and `multisample` remain unimplemented. This PR does not expand
  into a wholesale state implementation and does not claim they explain the
  observed difference.
- The canonical historical capture's missing texture-442 producer is preserved
  as a capture limitation, not "fixed" in the evidence.
- The portable live case is large because every referenced authentic blob is
  retained; it is not committed.
- PR #201 is still open. This branch contains its diagnostic-hardening commit
  because the PR3 failure-preservation requirement depends on it; if #201
  merges first, this branch should be rebased and that commit dropped.

## Review

Requesting independent review. Do not merge automatically.

## Checkpoint-request provenance and packaging hardening (`fc9e3838`)

The offline diagnosis path now proves that a published
`checkpoint-request.json` belongs to the sealed frame before it can support an
equivalence claim:

- the request is compared with the checkpoint plan derived from the validated
  capture graph (exact event/target/resource identity, reference digest,
  dimensions and producer history);
- duplicate, extra, missing and substituted entries are `invalid_provenance`;
- the request SHA-256, file name and count are bound to the authoritative
  `shadow-report.json` and/or the coordinator `shader-receipt.json`; a present
  binding that disagrees is invalid, and no binding is `incomplete_diagnostics`;
- reconciliation additionally rejects a result whose target/resource identity
  differs from the requested `(event, target)`.

An offline fixture without authoritative request binding remains diagnosable
but cannot be `qualified_match`.

`diagnose_frame(package=True)` now defaults to a deterministic sibling case
directory (`<metal-dir>-case`) outside both the captured frame and the Metal
result directory. Re-running replaces only an owned case; a non-owned output
conflict is reported as an explicit error instead of deleting user data.

Regressions (final focused set: 168 passed, 11 skipped):

- stale empty request, same-count different-event request, wrong digest,
  missing binding, duplicate/extra/substituted entries, receipt from another
  frame;
- the real `python -m automation.m6_frame diagnose --package` CLI with no
  `--out`, repeated invocation and a non-owned conflict;
- canonical title and scene-75 Metal diagnoses are unchanged.

## Independent review remediation

An independent review of `b526d773` found two blocking defects plus hardening
gaps; all are fixed on the final head:

- **Packaged cases were not self-diagnosable.** The copied seal retained the
  original absolute `capture_dir`, so a relocated case failed seal validation.
  `gfx_replay_case` now rewrites only that location field to the portable
  `capture` marker (retaining the original for provenance), and `load_seal`
  accepts it only when the case manifest declares that capture directory.
  Content identity/digests are unchanged. Regression:
  `test_packaged_case_is_self_diagnosable_after_relocation` packages, relocates
  and diagnoses with `--require-seal`, asserting `qualified_match` and a
  verified seal. The existing real case now reports its true capture limits
  (missing request binding/session) instead of a false seal mismatch.
- **An unbound fixture with an empty checkpoint plan could qualify.**
  `request_source == "derived"` is now unconditionally
  `incomplete_diagnostics`, even when zero checkpoints are required.
  Regression: `test_unbound_fixture_with_empty_plan_does_not_qualify`.

Additional hardening: an absent seal plus a shadow report is
`unverified_session`; malformed checkpoint metadata (non-scalar event/count)
fails closed without a traceback; the plan comparison includes the output file
name and requires integer producer events; request/receipt names with path
separators are rejected; the diagnosis fixture's seal counts are derived from
its events.

Final focused set: **168 passed, 11 skipped** (environmental NumPy/evidence
gates only).
