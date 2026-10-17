# P0 baseline record

Frozen before any Oracle migration so later changes are measured against a fixed point.

## Revision and environment

| Field | Value |
| --- | --- |
| Frozen revision | `8033e35de6b6bb689c6affb9498eae0c166c5635` (`main`, merge of PR #55) |
| Date | 2026-10-16 |
| Interpreter | Python 3.12.13 (`.venv`); CI matrix is 3.10 / 3.14 |
| Test deps | `benchmark/requirements-test.txt` → `benchmark/requirements.txt` (Pillow 12.3.0) |

## Checks

Commands run from the repository root.

| Check | Command | Result |
| --- | --- | --- |
| Compile | `python -m compileall -q benchmark analysis tooling reverse_engineering` | **pass** |
| Portable tests | `PYTHONPATH=benchmark:. python -m unittest discover -s tests` | **709 run; 1 error (pre-existing, local-only)** |
| Sealed capsule | `PYTHONPATH=benchmark python benchmark/eu4_frame_model.py verify-evidence results/20261003T210202Z-intrusive-diagnostic --profile intrusive_complete_v1` | **pass** (`status: passed`) |
| Forbidden paths | `python tooling/common/check_forbidden_tracked_paths.py` | **pass** |
| Doc links (`--strict`) | `python tooling/common/check_doc_links.py --strict README.md docs results/README.md reverse_engineering/README.md experiments/README.md patches/README.md measurements/INDEX.md` | **pass** |

## Pre-existing failures (not introduced by this work)

`tests/test_pre_ready_forensic.py::test_measured_193739Z_forensic_when_run_present`
errors locally because the untracked local directory
`results/20261006T193739Z-submission-experiment/` exists with only gitignored
`powermetrics.*` files and no `manifest.json`. The test guards on `RUN_DIR.is_dir()`
rather than on `manifest.json` existing, so a partial local artifact set trips it.

- On CI (fresh checkout) the directory has **zero tracked files**, so the guard skips the
  test. This failure does not reproduce in CI.
- Not fixed here: P0/P1 is audit-only and moves no code. Recorded as a robustness finding
  for a later change (guard on the manifest, not the directory).

After installing the declared test dependencies, `709` tests run with `1` error and `0`
skips locally. The three Pillow-dependent errors and the live-preflight failure that
appeared before installing `requirements-test.txt` were environment-only and are not
part of the baseline.

## Interpretation

The baseline is green except for one local-artifact-dependent test. Any P2 change must
preserve: `compileall` clean, `709` tests with the same single local error and no new
failures, the sealed capsule verification, the forbidden-path guard, and `--strict`
documentation links.
