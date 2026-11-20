# Implementation index

This file records the observed state after the harness-review merge on
**2026-11-20**.  The roadmap, step documents and assessment remain authoritative
for scope and acceptance criteria.

## Status vocabulary

- **Local independent review** — a read-only reviewer in a separate context
  tested the named SHA and reported no blocking finding.  It is not a GitHub
  review.
- **GitHub code-review findings** — automated review comments.  Findings are
  addressed only with a regression test and a recorded reply/thread resolution.
- **Formal approval** — a GitHub approving review by a maintainer.  Branch
  protection requires zero approving reviews; none was manufactured.
- **Merge state** — whether the change is on `main`.  All nine PRs below are
  merged.

## Merged pull requests

| PR | Step | Merged head | Merge commit | Local review | GitHub findings | Formal approval | Merge |
|---|---|---|---|---|---|---|---|
| #220 | index | `5c4a90c6` | `6eab2cb9` | No blockers at `5c4a90c6` | Codex summary, no findings | None required (0 approvals policy) | Merged |
| #225 | 10 | `022f347c` | `2b62c854` | No blockers at `022f347c` | Codex summary, no findings | None required | Merged |
| #221 | 01 | `51d883e5` | `ed5d4a3f` | No blockers at `51d883e5` | No open thread | None required | Merged |
| #227 | 02 | `d7454ed2` | `cd28bd1c` | No blockers at `d7454ed2` | All Codex threads resolved with evidence | None required | Merged |
| #223 | 06 | `53197ca7` | `aed6361a` | No blockers at `53197ca7` | Codex threads resolved with evidence | None required | Merged |
| #226 | 03 | `80a32e00` | `8549deee` | No blockers at `80a32e00` | Codex thread resolved with evidence | None required | Merged |
| #222 | 04 | `178b2e06` | `da5ceef1` | No blockers at `178b2e06` | Codex summary, no findings | None required | Merged |
| #224 | 11 | `f0ab3b76` | `94ea83e4` | No blockers at `f0ab3b76` | Codex summary at older head | None required | Merged |
| #228 | 09 | `fcfc449e` | `4beefd76` | No blockers at `fcfc449e` | Codex summary, no findings | None required | Merged |

The branches were brought up to date before each merge; those update merges only
added already-merged `main` content.  No conflict resolution changed
implementation code.  All conversations were resolved before merging.

## Post-merge verification

`main` at `4beefd76605b5bb27fd6cfbc425cc8998d1890bc` passed the full offline
gate (`20261120T121352Z-f6f1ca44`): **4,005 ran, 3,970 executed, 35 skipped,
0 failed, 902.1 s**.  This is portable offline evidence, not a native Metal
correctness claim.  The pre-existing incomplete local
`results/20261006T193739Z-submission-experiment` directory was quarantined for
the gate and restored unchanged.

All nine intended changes are present on `main`: the review/plans index, the
single shader-receipt owner, authoritative checkpoint/exact-control
qualification, the verified case locator, goal-identity/duration handling,
campaign-entry preflight, integer-ceiling pixel regions, smoke-campaign
retirement and the lossless PNG re-encode.

## Remaining non-blocking work

| Issue | Follow-up |
|---|---|
| #229 | case_locator copy-window symlink injection |
| #230 | discovery locator count scoping and truncated verified lookup |
| #231 | consume the campaign startup block in the goal context |
| #232 | dsh_efficiency edit+clear window outcome |
| #233 | correction_accept non-list checkpoint containers |
| #234 | docs/status predicate refresh and P3 test hardening |

These are P3 hardening items; none blocks the merged safety corrections.
