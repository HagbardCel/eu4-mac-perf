# A0.1 — Trusted validation smoke

Repair **only** the workspace candidate:

`analysis/qualification/v0/primitive-lookup.candidate.json`

so its `table` and `engine_enum_4_gl_mode` match the frozen instruction-static
oracle for `GetOpenGLPrimitiveType`.

## Rules

1. Do **not** edit trusted oracle files under the repository
   `analysis/qualification/v0/primitive-table.evidence.json` or
   `verification-manifest.json`. Tampering is an integrity failure.
2. Do **not** conflate `repo_baseline_commit` with `eu4_executable_sha256`.
3. Derive expected mapping from frozen evidence / original table bytes
   (engine enum `4` → GL mode `5`).
4. The controlled checker does **not** require fixing every candidate field—only
   what the diagnostics name. Leave unrelated fields alone unless asked.

## Done when

The external checker returns `pass` without modifying expected observations.
