# A0.2 Independent reviewer

Inspect the **pinned candidate worktree** (HEAD is the frozen qualification
commit). Compare `analysis/qualification/v0/primitive-lookup.candidate.json`
to frozen evidence `analysis/qualification/v0/primitive-table.evidence.json`.

Look for defects the controlled suite may not cover (notably
`out_of_range_default`).

Write a validated `eu4-review/v1` JSON report to the path given by the
environment variable `EU4_REVIEW_REPORT_PATH` (outside this worktree).

Do **not** modify oracle files or any path outside the allowlisted candidate
area. Leave the worktree clean except for `progress.md` updates if needed;
prefer writing only the external report.
