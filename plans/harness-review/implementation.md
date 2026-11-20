# Implementation index

This file records where the reviewed work is and its observed review/merge
state as of **2026-11-20 11:10 Europe/Berlin**.  It is not an approval and not a claim that
anything has landed on `main`.  The roadmap, step documents and assessment
remain authoritative for scope and acceptance criteria.

## Status vocabulary

- **Local independent review** — a read-only reviewer (separate context) tested
  the named SHA and reported no blocking finding.  It is not a GitHub review.
- **GitHub code-review findings** — automated review comments on the PR.  A
  finding is “addressed” only when a newer head has a regression test and the
  thread/response records the evidence; it is not an approval.
- **Formal approval** — a GitHub approving review by a maintainer.  **None of
  these PRs has one.**
- **Merge state** — whether the branch is on `main`.  **None is merged.**
- **Remaining** — recommended step work not yet implemented in these PRs.

## Open pull requests and observed state

| PR | Step | Head SHA | Scope in the branch | Local independent review | GitHub code-review findings | Formal approval | Merge |
|---|---|---|---|---|---|---|---|
| #220 | index | this commit | Assessment, plans and this implementation index. | No blockers at `fc213857`; this counts/status refresh follows. | Codex summary at `409bc8b8`, no findings; no open thread. | None | Not merged |
| #221 | 01 | `51d883e5` | Authoritative checkpoint exactness, request/binding validation, producer checkpoint-file binding, complete-graph gating, exact-control policy. | No blockers at `51d883e5` (exact-control policy re-reviewed). | No open Codex thread; prior summary reviewed. | None | Not merged |
| #227 | 02 | `fc2962b2` | Verified case locator: durable-root containment, honest verification state, immutable provenance, atomic publication, sequence seals, bounded lookup. | No blockers at `fc2962b2` (containment/verification/provenance/edges re-reviewed). | Three Codex P2/P3 threads resolved with evidence at newer heads. | None | Not merged |
| #226 | 03 | `80a32e00` | Campaign entry preflight with a host-recorded exclusive probe registry. | No blockers at `80a32e00`. | One Codex P2 thread addressed (root-privilege test). | None | Not merged |
| #222 | 04 | `178b2e06` | Integer-ceiling pixel-region spans and edge/tiny-image regressions. | No blockers at `178b2e06`. | Codex summary, no findings. | None | Not merged |
| #223 | 06 | `53197ca7` | Goal identity, clear tombstones, substantive outcomes, duration states. | No blockers at `53197ca7`. | Two Codex P2 threads addressed (active end time, goal identity). | None | Not merged |
| #228 | 09 | `fcfc449e` | Lossless re-encode of one illustrative tracked PNG. | No blockers at `fcfc449e`. | Codex summary, no findings. | None | Not merged |
| #225 | 10 | `022f347c` | Single shader-receipt identity owner. | No blockers at `022f347c`. | Codex summary, no findings. | None | Not merged |
| #224 | 11 | `f0ab3b76` | Retire the shadowed smoke source; verify both profiles. | No blockers at `f0ab3b76`. | Codex summary at older head; no open thread. | None | Not merged |

A head SHA that postdates its review is explicitly “re-review requested”; the
table records the exact SHA tested.  The consolidated review report is attached
to PR #220.

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
combined candidate is the disposable local branch `remediation/final6` at
`81caeeceedb0140b38f3633718482e7d91b1ed28` (not merged into `main`).  Its full
offline gate run is `20261120T095939Z-bbae743d`: **4,003 ran, 3,968 executed,
35 skipped, 0 failed, 723.2 s**.  Every listed implementation head is an
ancestor of that candidate.  This index commit adds the gate counts and is
docs-only after the gate.

A portable pass does not claim a native Metal correctness result, a local
archive, or a live EU4 capture.  Independent review remains the merge gate; no
entry here is a formal approval.
