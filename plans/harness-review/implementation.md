# Implementation index

This file records where the reviewed work is and its observed review/merge
state as of **2026-11-20**.  It is not an approval and not a claim that
anything has landed on `main`.  The roadmap, step documents and assessment
remain authoritative for scope and acceptance criteria.

## Status vocabulary

- **Implemented (open PR)** — the change exists on the named branch and its
  focused tests pass.  It is not on `main`.
- **Review finding addressed** — a review finding was fixed with a regression
  test; the new head still needs an independent re-review.
- **Reviewed** — an independent review of the named SHA reported no blocking
  finding.  This is not an approval to merge.
- **Merged** — the change is on `main`.  No entry below is merged.
- **Remaining** — recommended step work not yet implemented in these PRs.

## Open pull requests and observed state

| PR | Step | Head SHA | Scope in the branch | Independent review | Merge |
|---|---|---|---|---|---|
| #220 | index | `a032bc57` | Assessment, plans and this implementation index. | Review found stale gate/status claims; index corrected after the final gate (re-review requested). | Not merged |
| #221 | 01 | `e7caa47c` | Authoritative checkpoint exactness, request/binding validation, producer checkpoint-file binding, strict returned-entry declarations, complete-graph gating for final-only, distinct primary/authenticated verdicts. | First review found two P1 gaps (partial graph, fileless entries) and two P2s; fixed; re-review requested. | Not merged |
| #227 | 02 | `c16422e4` | Verified case locator: unique atomic publication, full-identity race handling, sequence target seals, metadata-only inventory, bounded verification, symlink refusal, broken-locator reporting, size/free-space preflight. | First review found two P2 gaps (reuse metadata, symlink accounting) and P3s; fixed; re-review requested. | Not merged |
| #226 | 03 | `c14c54f8` | Campaign entry preflight with an exclusive probe, fail-closed residue handling and compact startup block. | First review found probe-residue and startup-consumption gaps; residue fixed, consumption remains listed below; re-review requested. | Not merged |
| #222 | 04 | `178b2e06` | Integer-ceiling pixel-region spans and edge/tiny-image regressions. | Reviewed, no blockers (P3 test-hardening suggestions only). | Not merged |
| #223 | 06 | `101cf6a9` | Goal identity selection, clear tombstones, terminal/ongoing/unknown-start durations and separate turn/action fields. | First review found a resumed-goal P1 and clear/window P2s; fixed; re-review requested. | Not merged |
| #228 | 09 | `fcfc449e` | Lossless re-encode of one illustrative tracked PNG with identical decoded RGBA. | Reviewed, no blockers. | Not merged |
| #225 | 10 | `022f347c` | Single shader-receipt identity owner with required/optional semantics. | Reviewed, no blockers; duplicate gate/alias removed after review. | Not merged |
| #224 | 11 | `f0ab3b76` | Retire the shadowed smoke campaign source and verify both profiles resolve the canonical policy and limits. | Reviewed, no blockers; parity assertions extended after review. | Not merged |

A head SHA that postdates its review is explicitly “re-review requested”; the
review report attached to the PRs records the exact SHA tested.

## Remaining recommended work

This table is illustrative of the next slices, not a complete rewrite of the
step acceptance criteria; the step documents stay authoritative.

| Step | Remaining work |
|---|---|
| 01 | Checker-revision pinning in newly issued verdicts; optional validation of `result.checkpoint_files` inside the portable `verify_producer_receipt`. |
| 02 | Real archive/restore dry run with the preserved 616 case and the pre-disposal operational check. |
| 03 | Serialize the startup block into the goal-plugin initial context; implement the exhausted-run diagnostic conclusion and blocked/stop path in `decisions.js`; rehearse the misconfigured-smoke path end to end. |
| 04 | Effective sampler-descriptor cache key, signed scissor/viewport intersection, explicit unsupported cull/stencil states; step item 5 state-coverage/SRGB/sample-count/dither diagnostics; native reference fixtures. |
| 05 | Producer-event detail, translated-shader identity, frozen-case A/B/A replay and a recorded negative result. |
| 06 | Campaign-guard/receipt join, per-test timing, and the three repeatable pilots; manifest deduplication remains deferred. |
| 07 | Four canonical knowledge pages and removal of superseded current text from `docs/status.md`. |
| 08 | Consumer-aware capture stop after a verified terminal shadow result with fake-clock lifecycle tests. |
| 09 | The local 36.99 GiB `ptr-02` archive and verified restore, session-export deduplication, plus the tracked producer budget/one-payload-per-digest change in step item 6. |
| 10 | Versioned one-graph replay-report storage and legacy reader compatibility. |
| 11 | Shared test input builders for recorder/texture fixtures and the legacy static-dump pair. |

## Verification contract

Each change is verified with the focused modules named in its plan, then its
neighbor suites, using `automation.test_runner` under the offline profile.  The
combined candidate is a disposable local integration branch, not `main`; the
review report records its exact commits, full-gate counts and duration.  GitHub
Actions runs on every pushed head.

A portable pass does not claim a native Metal correctness result, a local
archive, or a live EU4 capture.  Independent review remains the merge gate.
