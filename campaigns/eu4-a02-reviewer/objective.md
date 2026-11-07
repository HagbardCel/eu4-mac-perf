# A0.2 Independent reviewer

Inspect the **pinned candidate worktree** (HEAD is the frozen qualification
commit). Compare `analysis/qualification/v0/primitive-lookup.candidate.json`
to frozen evidence `analysis/qualification/v0/primitive-table.evidence.json`.

Independently compare the candidate's supported behavior with the frozen
original evidence, including edge cases and untested observations the
controlled checker may not cover. Do not assume the controlled suite is
complete.

Write a validated `eu4-review/v1` JSON report to the path given by the
environment variable `EU4_REVIEW_REPORT_PATH` (outside this worktree).

Do **not** modify oracle files or any path outside the allowlisted candidate
area. Leave the worktree clean except for `progress.md` updates if needed;
prefer writing only the external report.
