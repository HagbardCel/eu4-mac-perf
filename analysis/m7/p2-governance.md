# M7-P2 — qualification and review gates

Status: branch protection is enabled on `main`. P2 is
[PR #212](https://github.com/HagbardCel/eu4-mac-perf/pull/212); it still requires
a fresh independent approval of its final head before merge.

## Enforced on `main`

Verified through the GitHub API on 2026-11-18 (receipt:
[p2-governance.json](p2-governance.json)):

| Protection | Value |
|---|---|
| Required status check | `portable` (Portable profiler correctness), strict/up to date |
| Pull request reviews | 1 approving review |
| Dismiss stale approvals | yes |
| Require approval of the last push | yes |
| Admin enforcement | yes |
| Conversation resolution | required |
| Force pushes / branch deletion | disabled |

Consequences:

- a mandatory offline failure blocks merge, including for administrators;
- a review-invalidating push dismisses the previous approval and requires a
  fresh one;
- a PR author cannot satisfy the independent-review requirement with their own
  approval (GitHub rejects self-approval);
- no merge with an outstanding failing qualification check.

The required check is the existing workflow job, not a new CI system. It runs
`automation.test_profiles verify --profile offline` and the complete offline
unittest discovery under `EU4_TEST_PROFILE=offline` / `EU4_OFFLINE_ONLY=1`.
Live EU4 qualification is deliberately not a required check: it needs a real
game process and is selected explicitly with `--profile live`.

## Not enforced automatically

- **Independent review.** Branch protection requires one approval, but cannot
  judge whether a reviewer is genuinely independent. The P2 PR must be reviewed
  by someone other than its author and the approval must name the final head.
- **No unattended interactive EU4 execution.** CI has no game install and the
  offline launch guard refuses a real launch. A live run remains an explicit
  operator action; it is not scheduled and is not part of CI.

## Merge decision

P2 is not complete until the final head has a fresh independent approval and
the required `portable` check is green. CI success alone is not approval.
