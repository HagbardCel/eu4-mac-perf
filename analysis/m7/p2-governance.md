# M7-P2 — qualification and review gates

Status: **merged implementation, outstanding independent review.** Branch
protection is enabled on `main`. [PR #212](https://github.com/HagbardCel/eu4-mac-perf/pull/212)
merged as `6e3b7913` on 2026-11-19T08:58:54Z **without a recorded approving
review**. The merge is a documented enforcement gap, not evidence that the
review requirement was satisfied. The closure work is a separate PR and must
receive a fresh independent review of its final head before merge.

## Enforced on `main`

Verified through the GitHub API on 2026-11-18 (receipt:
[p2-governance.json](p2-governance.json)) and re-checked on 2026-11-19 after
the merge:

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

## Observed enforcement gap on PR #212

Facts from the GitHub API, recorded 2026-11-19 (receipt:
[p2-governance-postmerge.json](p2-governance-postmerge.json)):

- The PR was merged by the repository owner at 08:58:54Z as merge commit
  `6e3b7913ec4f7dacf69e6520674bb43716aba6f9`; its head was `668b40ed`.
- The review history on #212 contains only `COMMENTED` reviews: one automated
  Codex review and four owner comments. There is **no `APPROVED` review** and no
  approval on the final head.
- After the merge, GitHub still reports `reviewDecision: REVIEW_REQUIRED` for
  the PR. This is direct evidence that the configured one-approval gate was
  never satisfied.
- The `portable` check on the merge commit completed successfully at
  09:03:09Z. CI was green; CI is not review.
- Branch protection remains enabled with the table above (`enforce_admins:
  true`, `required_approving_review_count: 1`, `require_last_push_approval:
  true`).

The repository API does not expose whether the merge used an administrative
bypass path or a transient protection change. Either way, the gate that the
repository documents was not effective for #212. The same pattern applies to
#210 and #211: both merged with only `COMMENTED` reviews. The gap is therefore
systemic in the M7 series, not a single accidental click.

**Effective permissions (2026-11-19).** The repository is user-owned and has
exactly one collaborator, the owner with `admin` permissions; there are no
repository rulesets; auto-merge is disabled and merge/squash/rebase merges are
enabled. With one required approval, `enforce_admins=true`, GitHub's rejection
of self-approval, and no second human, the configured gate is **unsatisfiable
by any legitimate actor**. A merge therefore requires an administrative bypass
or a transient protection change, neither of which the API exposes. #212
remains an unresolved governance incident. To prevent recurrence, add an
independent reviewer/collaborator or move to an organisation ruleset with
required reviewers and an empty bypass list; at minimum, record an explicit
pre-merge override decision. Do not weaken protections or fabricate approvals.

Do **not** disable or weaken protections to make future merges easier. The
correct remediation is procedural and reviewable:

1. every future PR (including this closure) requires an explicit `APPROVED`
   review on its final head from someone other than the author;
2. a merge performed under an administrative override must be recorded in the
   PR and in the governance report **before** the merge, with the exact reason
   and the compensating review plan; a post-hoc note is not approval;
3. if no independent reviewer is available, the PR stays unmerged and the work
   is reported as awaiting review. A green CI run is never a substitute.

## Not enforced automatically

- **Independent review.** Branch protection requires one approval, but cannot
  judge whether a reviewer is genuinely independent or whether an approval
  names the final head. A repository with one human writer cannot satisfy
  GitHub's self-approval rule; an external reviewer or an explicit,
  pre-declared override decision is required.
- **No unattended interactive EU4 execution.** CI has no game install and the
  offline launch guard refuses a real launch. A live run remains an explicit
  operator action; it is not scheduled and is not part of CI.

## Merge decision

M7-P2 is merged as implementation. M7 operational acceptance is **not**
complete: the independent final-head review of #212 is missing, and the pilot
acceptance evidence is being closed in a separate PR. The closure PR must not
be merged by the author, by admin bypass, or on the strength of CI alone.
