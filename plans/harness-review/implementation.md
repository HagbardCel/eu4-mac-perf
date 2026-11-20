# Implementation index

This file maps the review's steps to the open pull requests that implement
them.  It records **where the work is**, not whether it has been merged.  The
roadmap, step documents and assessment remain authoritative for scope and
acceptance criteria.

## Status vocabulary

- **Implemented (open PR)** — the change exists on the named branch and its
  focused tests pass.  It is not on `main`.
- **Reviewed** — a review finding was addressed with a regression test and the
  new head was rechecked.  It is still not an approval to merge.
- **Merged** — the change is on `main`.  No entry in this file is merged.
- **Remaining** — recommended work not yet implemented in these PRs.

## Implemented in open, unmerged pull requests

| PR | Step | Scope in the branch | Review state | Merge state |
|---|---|---|---|---|
| #221 | 01 | Authoritative checkpoint exactness, request/binding validation, producer checkpoint-file binding, distinct observed/pixel-perfect/authenticated verdicts, exact regression binding. | Review findings addressed; independent re-review requested. | Not merged |
| #227 | 02 | Verified case locator with atomic unique publication, full-identity conflict detection, sequence target seals, metadata-only inventory and bounded lookup. | Review findings addressed; independent re-review requested. | Not merged |
| #226 | 03 | Campaign entry preflight with an exclusive probe and compact startup block. | Review findings addressed; independent re-review requested. | Not merged |
| #222 | 04 | Integer-ceiling pixel-region spans and edge/tiny-image regressions. | Focused verification; no review finding. | Not merged |
| #223 | 06 | Goal identity selection, terminal/ongoing duration semantics and separate turn/action fields. | Review findings addressed; independent re-review requested. | Not merged |
| #228 | 09 | Lossless re-encode of one illustrative tracked PNG with identical decoded RGBA. | Asset verification; no review finding. | Not merged |
| #225 | 10 | Single shader-receipt identity owner with required/optional semantics. | Focused verification; no review finding. | Not merged |
| #224 | 11 | Retire the shadowed smoke campaign source and verify both profiles resolve the canonical policy. | Focused verification; no review finding. | Not merged |

## Remaining recommended work

These are not dropped; they are the next slices.  The review's priority order
still applies.

| Step | Remaining work |
|---|---|
| 01 | Checker-revision pinning in newly issued verdicts and any historical receipt-schema migration beyond the documented legacy limitation. |
| 02 | Real archive/restore dry run with the preserved 616 case and the pre-disposal operational check. |
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

Each change is verified with the focused modules named in its plan, then its
neighbor suites, using `automation.test_runner` under the offline profile.  A
disposable local integration branch (not merged into `main`) is gated once with
the full offline suite; its exact commits and counts are recorded in the
review report attached to the PRs.  GitHub Actions runs on every pushed head.

A portable pass does not claim a native Metal correctness result, a local
archive, or a live EU4 capture.  Independent review remains the merge gate;
this index is not an approval.
