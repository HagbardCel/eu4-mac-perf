# Repository map and lifecycle labels (P1 audit)

Inventory of Git-tracked paths at revision `8033e35` with lifecycle labels, so the
Oracle migration (P2) can move only what is safe to move. **This document records
classification; it moves nothing.**

Regenerate the mechanical summary below with:

```sh
PYTHONPATH=. python -m tooling.common.repo_inventory summary
# full per-path manifest:
PYTHONPATH=. python -m tooling.common.repo_inventory list
```

## Lifecycle legend

| Label | Meaning | Disposition |
| --- | --- | --- |
| `ACTIVE` | Maintained; consumers depend on it now | Keep in place; migrate incrementally |
| `SHARED_DEPENDENCY` | Used by active code/tests/CI across areas | Preserve interfaces until consumers migrate |
| `FROZEN_VERIFIER` | Immutable evidence or the validators that check it | Never move; re-verify on any change |
| `RETIRED_EXPERIMENT` | Completed/negative experiment, no active consumer | Preserve provenance; archive only after consumer check |
| `GENERATED_DERIVED` | Regenerable output | Keep as evidence; regenerate, do not hand-edit |
| `UNKNOWN` | Not yet classified | Do not delete or move until classified |

## Area classification

| Area | Files | Lifecycle | Owner responsibility | Notes |
| --- | --- | --- | --- | --- |
| `reverse_engineering/` | 29 | `ACTIVE` / `SHARED_DEPENDENCY` | Recover prototype implementation | Full-corpus pipeline + sealed snapshot validators are load-bearing (REUSE_MAP). |
| `benchmark/` | 110 | `ACTIVE` (legacy home) | Profiler + CLI entry points | Operational despite "legacy" label; **do not rename**. Migrates to `tooling/*` incrementally. |
| `tests/` | 186 | `ACTIVE` | Portable test suite | Flat `unittest` discovery; the single ceiling on regressions. |
| `analysis/` | 134 | `GENERATED_DERIVED` / `RETIRED_EXPERIMENT` | Investigations + generated evidence | Evidence under `analysis/evidence/` is provenance; do not hand-edit. |
| `docs/` | 45 | `ACTIVE` | Documentation | Canonical strategy, status, findings, reference; add `oracle/` here. |
| `results/` | 207 | `FROZEN_VERIFIER` | Sealed/historical run evidence | Phase C capsule is CI-verified; leave at original paths. |
| `tooling/` | 4 | `ACTIVE` / `SHARED_DEPENDENCY` | Guards + shared tooling | `check_doc_links`, `check_forbidden_tracked_paths`, new `repo_inventory`. |
| `experiments/`, `measurements/`, `patches/`, `fixtures/` | 1 each | `ACTIVE` (placeholder) | Target homes during migration | Indexes only today. |
| `.github/`, `.githooks/` | 1 each | `ACTIVE` | CI + hooks | CI compiles `benchmark analysis tooling reverse_engineering`. |

## Retired / preserved work (no deletion)

These are preserved as evidence and provenance, not maintained. Archival (a move under
`docs/archive/` or a tag) happens only in a later phase, after confirming no active CLI,
test, verifier, or planned Oracle adapter depends on them.

- Border multi-draw experiments (NO-GO) and the observer harness — negative results.
- State-cache and sampler-uniform experiments (failed engage / no signal).
- WP-series memos (`docs/wp*.md`) superseded by `docs/status.md` and the canonical strategy.
- Legacy phase/tier diagnostic memos in `docs/`.

## Generated summary (revision `8033e35`)

Tracked paths: **725**

| Area | Role | Files |
|---|---|---|
| `results` | sealed/historical run evidence | 207 |
| `tests` | portable test suite | 186 |
| `analysis` | legacy analysis + generated evidence | 134 |
| `benchmark` | legacy profiler / CLI entry points | 110 |
| `docs` | documentation | 45 |
| `reverse_engineering` | static RE tooling | 29 |
| `(root)` | — | 4 |
| `tooling` | shared guards / measurement tooling | 4 |
| `.githooks` | Git hooks | 1 |
| `.github` | CI workflow | 1 |
| `experiments` | isolated hypotheses (placeholder) | 1 |
| `fixtures` | local fixtures / references | 1 |
| `measurements` | measurement registry (placeholder) | 1 |
| `patches` | version-pinned modifications (placeholder) | 1 |

| Kind | Files |
|---|---|
| python | 174 |
| json | 148 |
| markdown | 121 |
| c | 66 |
| csv | 44 |
| jsonl | 35 |
| stderr | 28 |
| c-header | 25 |
| stdout | 21 |
| binary | 11 |
| text | 11 |
| asm | 10 |
| java | 6 |
| no-extension | 4 |
| gzip | 3 |
| image | 3 |
| shell | 3 |
| log | 2 |

## Hygiene findings (this PR)

- `tests/re_fixtures/callsite_mini/callsite_mini` is a locally built fixture binary that
  was untracked but not ignored; added to `.gitignore` so `git status` stays clean.
- `tests/test_frame_evolution_analyze.py::test_build_report_writes_candidates` writes a
  `test_build_report_writes_candidates/` directory into the working tree (via
  `Path(self._testMethodName)`) instead of a temp directory. Ignored here as test debris;
  the underlying fix (write to `tempfile.TemporaryDirectory`) is a code change for a later
  phase, as is the P0 baseline finding in [`oracle/BASELINE_P0.md`](oracle/BASELINE_P0.md)
  (guard on `manifest.json`, not the result directory).
- Several `.pyc` cache sets (3.12 / 3.14 / 3.15) exist locally for the same modules; they
  are already ignored and indicate multiple local interpreters, not CI drift.
- `results/` is large on disk but almost entirely ignored; tracked `results/` files are
  sealed evidence and are left untouched.

## Not in scope here

No code moved, renamed, or deleted; no canonical strategy rewrite (coordinated with
PR #44); no new evidence system. Migration of the Oracle bundle and CI wiring is P2.
