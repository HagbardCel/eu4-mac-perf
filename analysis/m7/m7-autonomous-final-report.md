# M7 autonomous final report

Date: 21 November 2026. PR #280 review baseline: `b1b9aeb6`; remediation head
covered here: `9a6be32c` (plus a documentation-only follow-up if noted).

## Final PR revision and review status

- PR: https://github.com/HagbardCel/eu4-mac-perf/pull/280
- Head at the time of this report: `9a6be32c8b10b812c6a3c91c4ac257a40f14a8eb`
- Independent review of the exact final head: **not obtained**.
- Portable CI: **pass** on `b1b9aeb6`; the new head requires the same check
  before merge.
- Merge recommendation: **do not merge**.

## Phase A preflight

- Remote PR head verified: `b1b9aeb6c481276e5549eef8d0aee0c95eb919d0` before
  the Phase B changes in this round.
- Active DSH sidecar and source campaign both pin `6619f13a`.
- DSH preflight initially failed on the installed DSH package pin: installed
  `0.2.2-rc.5`, runtime pinned `0.2.2-rc.1`. The runtime pins were updated to
  the installed rc.5 package and shipped plugin identities; preflight then
  passed:
  `{"ok":true,"profile":"eu4-web","dsh_version":"0.2.2-rc.5",...}`.
- Focused suite: **passed**.
- Copied trusted reference verified intact and outside the candidate:
  `.local/m7/event20/trusted-copy/47590918`, 1,192 files, root
  `b3e3d7b5d1e4cefe814f460c99a19c32011242d100a8b0c5bdd97aa9e485756c`.

## Phase B trusted reference selection

The reference root now comes from trusted campaign configuration
(`trusted_reference_root` in the sidecar/source campaign), not from the
candidate or a newest-directory heuristic. `resolve_trusted_reference` rejects
a missing, ambiguous, nonexistent or candidate-internal root. A regression
creates two valid trusted seals, selects the explicitly assigned one, and
refuses the ambiguous parent; switching to the other seal is not possible
without changing trusted configuration.

Canonical `accept.py` on the final candidate (`6619f13a`) and the explicitly
selected copied reference returned `fail`: `FIRST_DIVERGENCE` at event 1.

## Phase C native DSH multi-round

**Not completed.** No genuine native Web-profile multi-round goal was executed
for this round. The human-driven A/B test below proves the trusted candidate
execution path, but the task and the definition of `M7_AUTONOMOUS_READY`
explicitly do not accept a human-driven A/B test as the multi-round gate.

Therefore:

- No native goal transcript is available for this round.
- No independent checker acceptance of an agent-authored correction was
  produced.
- `M7_AUTONOMOUS_READY` is **NO**.

The prior controlled two-round DSH evidence predates the candidate-execution
path and is not substituted.

## Trusted candidate execution proof

The checker verifies the candidate worktree/revision/policy, snapshots the
replay-host dependency closure, builds it with the trusted toolchain, and
executes against the immutable copied reference. The receipt binds candidate
revision, changed paths, source digest, per-file hashes, compiler/flags, host
SHA-256, request/shader digests, event count and output digests. Host, source,
outputs, receipt and replay result are rehashed after execution.

| Candidate | Source digest | Host SHA-256 | Checker |
| --- | --- | --- | --- |
| Baseline | `255a5b7e…` | `190fde92…` | event 1, 23 px / max 1 |
| Controlled defect (red clear +0.01) | `492e6654…` | `533f79c6…` | event 0, 1,930,176 px / max 3 |
| Defect reverted | `255a5b7e…` | `190fde92…` | event 1, 23 px / max 1 |
| On immutable copy | `255a5b7e…` | `190fde92…` | event 1, 23 px / max 1 |

Record: `analysis/m7/m7-event20-candidate-execution.json`.
Replay accounting tests cover one-charge-per-execution, failure charging,
nested joins and pre-build refusal on exhaustion.

## Phase D independent review

Not obtained. Required review focus remains: candidate source/build identity,
dependency closure completeness, reference isolation/selection, candidate-to-
trusted escapes, output/receipt authenticity, budget charging, RGB8/RGBA8/R32F
exactness, first-divergence classification and native multi-round evidence.

## Phase E live pilot

**Not executed.** Phase E is gated on `M7_AUTONOMOUS_READY`, approval of the
exact head, green CI and preflight. `M7_AUTONOMOUS_READY` is not established, so
no EU4 launch was made. The one-launch recipe remains in
`analysis/m7/m7-event20-live-plan.md`; it includes the R32F event-11 observation
requirement.

## Tests

- Focused Phase A/B suite: **passed**.
- `test_profiles verify --profile offline`: **passed**.
- Full offline gate `20261121T180219Z-e07595fc` at `9a6be32c`:
  **4,446 ran / 4,410 executed / 36 skipped / 0 failures / 0 errors**,
  1,387.2 s. `test_profiles verify --profile offline` passed before the gate.
- EU4 launches in this round: **0**. Total event-20 launch count remains **3**
  from the earlier pilot stages.

## Existing event-1 result

From the old seal `47590918-d63a-4798-9737-df00285ad51b`:

| Event(s) | Scope | Result |
| --- | --- | --- |
| 0 | RGB | exact |
| 1 | RGB | 23 differing pixels, max byte-channel difference 1 |
| 10, 12–17 | RGBA | exact |
| 11 | R32F | unobserved in this seal |
| 18–20 | RGBA | divergent (359/12, 1,186/12, 4,781/22) |

First verified event: **0**. Last verified event: **0**. First divergence:
**1**. `EVENT20_EXACT` is **NO**.

## Remaining unsupported operations and gaps

- Event 11: R32F observation absent from the old seal; support exists for a new
  capture.
- Event 1: unresolved 1-LSB RGB difference; decoded-texture fallback is worse.
- Events 18–20: later differences, preserved but not diagnosed because the
  campaign stops at event 1.
- Native DSH multi-round integration: **missing**.
- Independent exact-head review: **missing**.

## Decisions

- **M7_AUTONOMOUS_READY: NO**
- **EVENT20_EXACT: NO**
- **Merge: NO**

Infrastructure correctness and pixel equivalence are separate gates. Neither is
claimed beyond the evidence above.
