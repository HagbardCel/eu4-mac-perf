# Implementation index

This file records where the reviewed work is and its observed review/merge
state as of **2026-11-20 09:15 Europe/Berlin**.  It is not an approval and not a claim that
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
| #220 | index | this commit | Assessment, plans and this implementation index. | Corrected after the final gate; final review requested. | Not merged |
| #221 | 01 | `a776830c` | Authoritative checkpoint exactness, request/binding validation, producer checkpoint-file binding, strict returned-entry declarations, complete-graph gating for every coverage branch, distinct primary/authenticated verdicts. | Reviewed at this head: no blockers; P3 residuals (non-list request/replay containers, docs/status predicate). | Not merged |
| #227 | 02 | `e8da7907` | Verified case locator: host-locked atomic publication, full-identity race handling, sequence target seals, metadata-only inventory, bounded verification, symlink handling, broken-locator reporting, size/free-space preflight. | Reviewed at this head: no blockers; P3 residuals (copy-window symlink injection, discovery count scoping). | Not merged |
| #226 | 03 | `80a32e00` | Campaign entry preflight with a host-recorded exclusive probe registry, reaping and freeze filtering, compact startup block. | Reviewed at this head: no blockers; P3 (startup block not yet consumed by launchers). | Not merged |
| #222 | 04 | `178b2e06` | Integer-ceiling pixel-region spans and edge/tiny-image regressions. | Reviewed at this head: no blockers; P3 test-hardening suggestions only. | Not merged |
| #223 | 06 | `53197ca7` | Goal identity, clear tombstones, substantive outcomes, terminal/ongoing/unknown-start/unknown-end durations, separate turn/action fields. | Reviewed at this head: no blockers; P3 (edit+clear window, schema documentation). | Not merged |
| #228 | 09 | `fcfc449e` | Lossless re-encode of one illustrative tracked PNG with identical decoded RGBA. | Reviewed at this head: no blockers. | Not merged |
| #225 | 10 | `022f347c` | Single shader-receipt identity owner with required/optional semantics. | Reviewed at this head: no blockers; P3 call-site test gap only. | Not merged |
| #224 | 11 | `f0ab3b76` | Retire the shadowed smoke campaign source and verify both profiles resolve the canonical policy and limits. | Reviewed at this head: no blockers; P3 reader-default pinning only. | Not merged |

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
combined candidate is the disposable local branch `remediation/final2` at
`263418dd5ce92be01f983f5f7bc72e01faadba06` (not merged into `main`).  Its full
offline gate run is `20261120T080353Z-62f5ac38`: **3,996 ran, 3,961 executed,
35 skipped, 0 failed, 721.4 s**.  Every listed PR head is an ancestor of that
candidate.  GitHub Actions runs on each pushed head.

A portable pass does not claim a native Metal correctness result, a local
archive, or a live EU4 capture.  Independent review remains the merge gate.
