# Repository map and lifecycle labels (P1 audit)

Inventory of Git-tracked paths with lifecycle labels, so the Oracle migration (P2) can
move only what is safe to move. **This map records classification; it moves nothing,
and a lifecycle label is never archival authorization.**

The audit is a function of three separately identified inputs:

```
AuditResult = f( SourceSnapshot , Policy , ToolVersion )
```

- **Source** — a frozen Git tree-ish (`8033e35`), read from Git objects, not the worktree.
- **Policy** — `tooling/common/repo_lifecycle.json`, a curated, versioned classification layer.
- **Tools** — `tooling/common/repo_{snapshot,inventory,graph,lifecycle,audit}.py`.

All three identities are recorded in [`repo-audit-context.json`](repo-audit-context.json).
Re-running with the same triple reproduces byte-identical artifacts.

## Lifecycle legend

| Label | Meaning | Archival posture |
| --- | --- | --- |
| `ACTIVE` | Maintained; consumers depend on it now | Keep in place |
| `SHARED_DEPENDENCY` | Imported/included by active code or the test suite | Preserve interfaces |
| `FROZEN_VERIFIER` | Immutable evidence or the validators that check it | Never move; re-verify on change |
| `RETIRED_EXPERIMENT` | Historically retired; no *proof* it is unused | **Not** automatically safe to move |
| `GENERATED_DERIVED` | Regenerable output | Keep as evidence; regenerate, do not hand-edit |
| `UNKNOWN` | Not yet classified | Do not delete or move until classified |

Classification precedence is `explicit override → curated rule → consumer signal → UNKNOWN`.
A positively detected consumer reference (import / include / CI ref) always blocks archival
eligibility regardless of which rule produced the label — classification precedence cannot
conceal contradictory evidence.

## Curated area classification (human-authored)

| Area | Lifecycle (base) | Owner responsibility | Notes |
| --- | --- | --- | --- |
| `reverse_engineering/` | `ACTIVE` | Recover prototype implementation | Full-corpus pipeline + sealed snapshot validators are load-bearing. |
| `benchmark/` | `ACTIVE` (base) | Profiler + CLI entry points | Operational despite "legacy" label. Individual retired experiments are carved out by earlier rules. |
| `tests/` | `ACTIVE` | Portable test suite | Flat `unittest` discovery; the ceiling on regressions. |
| `analysis/` | `GENERATED_DERIVED` / `RETIRED_EXPERIMENT` | Investigations + generated evidence | `analysis/evidence/**` is `FROZEN_VERIFIER`. |
| `docs/` | `ACTIVE` (base) | Documentation | Superseded memo families are carved out by earlier rules. |
| `results/` | `FROZEN_VERIFIER` | Sealed/historical run evidence | Phase C capsule is CI-verified; leave at original paths. |
| `tooling/` | `ACTIVE` | Guards + shared tooling | `check_doc_links`, `check_forbidden_tracked_paths`, repo audit tools. |
| `experiments/`, `measurements/`, `patches/`, `fixtures/` | `ACTIVE` (placeholder) | Target homes during migration | Indexes only today. |
| `.github/`, `.githooks/` | `ACTIVE` | CI + hooks | CI compiles `benchmark analysis tooling reverse_engineering`. |

## Archival policy

Three separate decisions, intentionally not collapsed:

| Property | Meaning |
| --- | --- |
| `lifecycle` | Historical/operational classification |
| `archive_candidate` | Worth investigating for archival (retired/generated, no conflict) |
| `archive_eligible` | **Explicitly** approved for archival |

In this audit `archive_eligible` is `false` for **every** path: `approved_for_archive` in the
policy is empty. A later cleanup PR authorizes individual candidates after reviewing their
consumers and updating references. Static analysis can miss dynamic dependencies, external
scripts, and human operational procedures, so it never authorizes relocation by itself.

## Retired / preserved work (no deletion)

Preserved as evidence and provenance, not maintained. Archival requires the review above.

- Border multi-draw track (NO-GO) and the observer-bias calibration.
- State-cache and sampler-uniform experiments (failed engage / no signal).
- WP-series and other superseded historical memos under `docs/`.

## Hygiene findings

- `tests/re_fixtures/callsite_mini/callsite_mini` (built fixture binary) and
  `test_build_report_writes_candidates/` (test debris written into the working tree) are ignored.
- The test that writes `test_build_report_writes_candidates/` into the CWD, and the
  `manifest.json`-vs-directory guard in `tests/test_pre_ready_forensic.py`, are code-quality
  fixes deferred to a later phase (see [`oracle/BASELINE_P0.md`](oracle/BASELINE_P0.md)).

## Regenerating

```sh
PYTHONPATH=. python -m tooling.common.repo_audit --ref 8033e35 --write   # regenerate
PYTHONPATH=. python -m tooling.common.repo_audit --ref 8033e35 --check   # verify (CI)
```

## Not in scope here

No code moved, renamed, or deleted; no canonical strategy rewrite (coordinated with PR #44);
no new evidence system. Migration of the Oracle bundle and CI wiring is P2.

<!-- BEGIN GENERATED: repo-inventory -->

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
| markdown | 122 |
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
| cpp | 1 |
| gitignore | 1 |
| inc | 1 |
| m | 1 |
| recipe | 1 |
| s | 1 |
| sha256 | 1 |
| tsv | 1 |
| yaml | 1 |

Invariant: Σkinds = Σareas = N = 725 = 725 = 725.

| Lifecycle | Files |
|---|---|
| ACTIVE | 347 |
| FROZEN_VERIFIER | 257 |
| RETIRED_EXPERIMENT | 91 |
| GENERATED_DERIVED | 28 |
| UNKNOWN | 2 |

| Classification source | Files |
|---|---|
| rule | 725 |

| Analysis coverage | Files |
|---|---|
| analyzed | 705 |
| skipped_not_text | 19 |
| skipped_oversized | 1 |

| Reference edge | Count |
|---|---|
| doc_link | 420 |
| import | 330 |
| include | 61 |
| manifest_ref | 14 |
| ci_ref | 10 |

Audit invariants:

- `N_classified = N_tracked` → `725 = 725` (ok)
- `N_archive_eligible_but_unapproved = 0` (ok)
- dependency conflicts: 9; archive candidates: 110; archive eligible: 0

<!-- END GENERATED: repo-inventory -->
