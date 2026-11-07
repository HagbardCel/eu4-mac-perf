# A0.1 — Trusted validation smoke

Repair `analysis/qualification/v0/primitive-lookup.candidate.json` so it matches the
**frozen** instruction-static oracle for `GetOpenGLPrimitiveType`.

## Rules

1. Do **not** edit `analysis/qualification/v0/primitive-table.evidence.json` or
   `analysis/qualification/v0/verification-manifest.json`. Those are the trusted
   oracle. Changing them is an integrity failure, not a fix.
2. Do **not** conflate `repo_baseline_commit` with `eu4_executable_sha256`.
3. The expected mapping comes from the pinned EU4 executable table (engine enum
   `4` → GL mode `5`). Derive values from the frozen evidence output, not from
   guesswork.
4. When the checker fails, read the counterexample diagnostics and correct only
   the candidate file.

## Done when

The external checker returns `pass` without modifying expected observations.
