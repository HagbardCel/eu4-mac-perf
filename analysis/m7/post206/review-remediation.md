# Review remediation — PRs #207, #208, #209

Status: fixes implemented in the owning PR; focused tests pass; fresh independent
review still required. No PR is merged.

## PR #207 — Phase C

### C1. Fault experiments are always disqualified

- Added the disjoint `fault_experiment` classification. A selected fault is
  never a `qualified_match`, even when the fault is pixel-neutral or did not
  fire. `gpu_execution_failure` and incomplete-evidence classifications still
  take precedence.
- The fault request is recorded separately from `fault_evidence`, which is
  derived from replay provenance (`fault_sampler_shift` and
  `fault_missing_engine_unit`). A request that names a nonexistent event or a
  draw with no eligible sampler reports `fired=false`.
- The campaign predicate now includes `not fault`; the agent `_campaign_outcome`
  has a dedicated fault branch and returns `qualified=false` in every case.
- Regressions: fault changes pixels, nonexistent event, effective but
  pixel-neutral fault, fault-free qualified campaign, and the three
  classification cases.

### C2. GL control is resource-version aware

- The control scans the destination from the selected copy to its first sample
  and rejects a later copy, draw write, clear, lifecycle mutation/delete or
  missing lifecycle record as `unavailable`/`incomplete`, never `different`.
- It records the selected copy event, first consumer event and provenance
  status. Corrupt digests and malformed payloads are `invalid`; a real
  mismatch in the correct version is `different`.
- Regressions: one copy + sample, two copies before sample, intervening write,
  incorrect bytes in the correct version, missing lifecycle, unsupported
  channels, missing source, missing sample, digest corruption, lifecycle
  mutation and clear.
- The authentic frame-1 transfer control still matches 0/1,930,176; frames 2/3 are `unavailable` because the historical capture lacks copy-only lifecycle coverage. This is an explicit limitation, not a difference.
- The internal-format table no longer mislabels enum 33327 as `RGB10_A2`; it is `GL_RG16F`, whose captured RGBA8 readback has a zero blue channel and opaque alpha. It now fails closed as `unsupported_internal_format` instead of fabricating a difference. Regression: `test_rg16f_destination_is_not_mistaken_for_rgb10_a2`.

## PR #208 — Phase D

### D1. Copy-source inference

- Added `_copy_source_evidence()`: only a checkpoint at the copy producer with
  target `0` can establish a divergent, matching, invalid or unobserved source.
  Destination `captured_versions` are no longer used as a source proxy.
- Divergent source → upstream midpoint bisection. Matching source → transfer
  investigation, no upstream bisection. Unobserved/invalid source → capture or
  repair the source boundary.
- Regressions cover all four states, including a destination with
  `captured_versions` but no source checkpoint.

### D2. Consistent recommendations

- Every divergence return carries `recommended_checkpoint` with `event`,
  `target`, `resource`, `requires_capture`, `reason`, `source` and `interval`,
  normalized from `next_checkpoint` or `suggested_checkpoint`.
- `recommended_action` covers transfer investigation and evidence repair.
- Regressions cover intermediate, final-only, missing-evidence and
  unsupported-state cases.

### D3. Compact evidence selection

- `agent-diagnosis.json` no longer takes the first 32 checkpoints. It selects
  the first divergent checkpoint, last matching checkpoint, copy-source/
  predecessor events, the recommended next event, and first/last observations,
  deterministically, with `selected_because` reasons.
- The authentic compact artifact is 6,274 bytes, has no absolute paths and
  retains the actionable event-148 recommendation.

### D4. Compact copy-source provenance

- `_compact_source_evidence()` now publishes the authoritative
  `reference_sha256` from `reconcile_checkpoints()` as the compact
  `gl_sha256`; it previously projected a non-existent `gl_sha256` and
  published `null`.
- The Metal digest, source event/target/resource/status and authority stay
  attached to the same source checkpoint. Missing source evidence remains
  explicit (`checkpoint=null`, both digests `null`) rather than fabricated.
- Regression: `CompactSourceEvidenceDigestTests` asserts both digests are
  preserved and agree with the full report, that they name the source
  checkpoint, and that missing evidence stays missing. The authentic frame-1
  artifact confirms the projected GL digest `abd4ad6b…` equals the source
  checkpoint's `reference_sha256`, and `c1c35604…` equals its Metal digest.

## PR #209 — evidence, integration and readiness

### E1. Efficiency audit

`automation.dsh_efficiency` now separates native tool failures from shell
command failures, counts bash-launched EU4 commands as command invocations,
strips heredoc bodies so edited test text is not counted as a launch, reports
duplicate test invocations and represents an active goal as `active` rather
than inheriting a stale turn-end reason. The final JSON is
[`dsh-efficiency.json`](dsh-efficiency.json); the reconciliation is in
[`efficiency-audit.md`](efficiency-audit.md).

### E2. DSH integration

- Mounted model session: **blocked before the first tool call** by
  `MISSING_CREDENTIAL` in the headless profile. The plugin itself mounted seven
  tools; the error is in the session credentials service.
- Plugin bridge smoke (same `runTool` path): passed `eu4_capture gl-control`
  against the authentic frame (`match`, 0 pixels) and `eu4_re_query resolve
  _GfxResolve` (`0x15ece13`). Recorded in
  [`dsh-native-smoke.json`](dsh-native-smoke.json).
- Readiness is downgraded for unattended migration; the plugin implementation
  itself is not implicated.

### E3. Evidence reproducibility

- Re-verified all frame digests and all 3,811 case file digests: zero drift.
- Re-verified the case: `gap_free` 615/616/617, 1.93 s.
- Replayed the case again: complete, target 3, history `ok`, 1,162,904 /
  1,930,176 mismatched, max 73, 21.56 s.
- The 1.35 GB case remains local-only; no external artifact store is
  configured. Manifest hashes and the reproduction procedure are committed.

### E4. Readiness and next task

[`readiness.md`](readiness.md) now separates **offline diagnostic readiness**
(READY WITH LIMITATIONS) from **fully unattended migration readiness** (NOT
READY). The proposed next task is **M7-P0**, a separate default-framebuffer
checkpoint near event 148, not a `gl_dither` migration. The precise-math switch
remains opt-in and is not a correction.

### E5. Cost controls

- Capture default `--max-draws` is 512; the former 256 default caused the
  `draw_cap` failure. `--max-draws` must be positive.
- Compact diagnosis replaces multi-megabyte parsing for agents.
- Live/interactive benchmark modules are explicitly outside the routine
  offline gate; the full run that reached a live prompt was stopped and is not
  reported as a pass.
- CI is checked with bounded completion rather than fixed-sleep polling.

## Validation summary

| PR | Focused result |
| --- | --- |
| #207 | 417 passed, 1 skipped, 0 failures |
| #208 | 321 passed, 1 skipped, 0 failures |
| #209 | 483 passed, 1 skipped, 0 failures |
| plugin | 9 passed, 0 failed |

The portable CI workflow must be re-run on each new head. The authentic case is
not part of routine CI.

## Remaining limitations

- Full-frame GL/GL replay is absent; the control is transfer-scoped.
- `gl_dither` and `multisample` remain unsupported.
- The causal interval 13–284 is unresolved; M7-P0 owns the next bisection.
- The mounted DSH headless session cannot authenticate in this environment.
- One authentic capture and one stopped full-suite attempt; no broader live
  qualification is claimed.

## Review boundary

Each PR requires a fresh independent review against its latest head. No PR has
been merged, no auto-merge is enabled, and passing CI is not approval.
