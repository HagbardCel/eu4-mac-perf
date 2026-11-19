# Implementation index

This file maps the review's steps to the pull requests that implement them.
It is a status pointer, not a second plan: the objective, acceptance criteria
and rollback notes remain in each step document.

## Delivered as first slices

| Step | Change | Pull request | Scope landed |
|---|---|---|---|
| 01 | Exact authoritative checkpoints for correction equivalence | #221 | Shared request/graph binding, authoritative checkpoint exactness, broken-checkpoint rejection, regression checkpoint exactness, new negatives. |
| 02 | Verified case locator across worktree disposal | #227 | `case_locator.publish/locate/inventory`, atomic verified copy, locator discovery through `evidence_index`, legacy/no-seal keying. |
| 03 | Campaign entry preflight | #226 | Workspace writability refusal before anchoring, compact startup block with identity, checker, limits and first command. |
| 04 | Correct pixel-region geometry | #222 | Integer-ceiling region spans, 9×1/1×9/1728×1117 regressions, unused accumulator removed. |
| 06 | Terminal goal phase | #223 | Goal outcome from the final goal record, separate turn reason, goal/session durations, summary dimensions. |
| 09 | Lossless tracked-image reduction | #228 | 17,870,276 → 12,009,448 bytes at the same path with identical decoded RGBA. |
| 10 | One shader-receipt owner | #225 | `m6_frame.shader_receipt_identity`, required/optional semantics, three duplicate definitions removed. |
| 11 | Retire the shadowed smoke source | #224 | Two unused files removed; both sidecars proven to resolve one canonical source. |

## Remaining recommended work

These are not dropped; they are the next slices. The review's priority order
still applies.

| Step | Remaining work |
|---|---|
| 01 | Receipt-schema migration for historical standalone results and checker-revision pinning in newly issued verdicts. |
| 02 | Real archive/restore dry run with the preserved 616 case; pre-disposal operational check. |
| 03 | Bind the startup block into the goal-plugin initial context and rehearse the misconfigured-smoke path end to end. |
| 04 | Effective sampler-descriptor cache key, signed scissor/viewport intersection, explicit unsupported cull/stencil states, native reference fixtures. |
| 05 | Producer-event detail, translated-shader identity, frozen-case A/B/A replay and a recorded negative result. |
| 06 | Campaign-guard/receipt join, per-test timing, and the three repeatable pilots; manifest deduplication remains deferred. |
| 07 | Four canonical knowledge pages and removal of superseded current text from `docs/status.md`. |
| 08 | Consumer-aware capture stop after a verified terminal shadow result with fake-clock lifecycle tests. |
| 09 | The local 36.99 GiB `ptr-02` archive, verified restore, and session-export deduplication. These are operator-local data operations, not tracked-tree changes. |
| 10 | Versioned one-graph replay-report storage and legacy reader compatibility. |
| 11 | Shared test input builders for recorder/texture fixtures and the legacy static-dump pair. |

## Verification contract

Every listed change was verified with the focused modules named in its plan,
then the neighbor suites, using `automation.test_runner` under the offline
profile. The full offline gate and GitHub Actions run on each PR. A local
archive, a GPU fixture or a live EU4 capture is never implied by a portable
pass.
