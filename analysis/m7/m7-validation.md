# M7.0 validation against the base branch

This record reconciles the full-suite results for PR #195 on the current base.
It exists because an earlier comparison mixed the branch worktree (with a
populated `.local`) against a base checkout with a symlinked `.local`, and its
failure counts were not directly comparable.

## Environment

- macOS arm64, Python 3.14.10, `clang` toolchain available.
- **Base:** `origin/main` `5a0ac03` in a clean detached worktree
  (`/tmp/m7-base-full`), empty `.local`, gitignored `benchmark/.build` created
  during the run.
- **Candidate:** `m7/starter-canary` `1f0041b` in a clean detached worktree
  (`/tmp/m7-cand-full`), same empty `.local` and `benchmark/.build` handling.
- Command (identical): `python3.14 -m unittest discover -s tests`.

The validated code commit is `1f0041b`; the commits after it change only
documentation (this file and the canary report).

The base worktree's first run used a `.local` **symlink** to the main worktree;
the DSH bridge tests resolve campaign roots through that symlink and refused
them as "outside the trusted checkout", which produced ~50 spurious failures.
That run is discarded. A real empty `.local` reproduces the candidate layout.

## Focused validation (before the full suite)

```sh
python3.14 -m unittest tests.test_gfx_qualification tests.test_gfx_dual_run \
  tests.test_gfx_case tests.test_agent_tools tests.test_offline_campaign \
  tests.test_gfx_metrics tests.test_gfx_replay_case -q
```

Result: **138 tests, OK** (the qualification module contributes 28).

## Full-suite results

| Run | Commit | Tests | Failures | Errors | Skipped | Exit |
| --- | --- | --- | --- | --- | --- | --- |
| Base | `5a0ac03` | 2967 | 19 | 2 | 30 | 1 |
| Candidate (clean worktree) | `1f0041b` | 2989 | 18 | 2 | 30 | 1 |

Machine-readable comparison:
`.local/m7/validation-summary.json` (local). Raw logs:
`.local/m7/full-suite-base-5a0ac03.log`,
`.local/m7/full-suite-candidate-clean.log`.

- Common failing ids: **20**
- Candidate-only failing ids: **0**
- Base-only failing ids: **1**

## Classification

### Already present on base — 20 (identical on both)

`test_dsh_bridge` (9) and `test_trace_tr3` (11).

- `test_trace_tr3.*`: the QBDI x86-64 release is not installed
  (`/Users/fabian/.cache/eu4-mac-perf/qbdi/0.12.1`), so the native harness cannot
  build. Environmental, independent of this PR.
- `test_dsh_bridge.*`: the bridge tests run against the current checkout and
  refuse their temporary campaign/workspace layout with
  `integrity_refusal`/`execution_root` in this environment. Identical on base
  and candidate, in the same order and with the same exception types.

### Environment-dependent — resolved, 0 remaining

- The first clean candidate run also failed six `test_border_loop_*` tests with
  `CalledProcessError` from `clang`. Cause: those tests write into
  `benchmark/.build`, which is gitignored and not created by the tests. Proven
  deterministic on both commits:

  | Worktree | `benchmark/.build` absent | present |
  | --- | --- | --- |
  | base `5a0ac03` | 6 errors | OK |
  | candidate `1f0041b` | 6 errors | OK |

  With the directory present the rerun has no `test_border_loop_*` failures on
  either side.

### Flaky — 1 base-only

- `test_gfx_session.SessionTests.test_only_owned_process_is_terminated` failed on
  base with `identity_mismatch != exited` and passed on the candidate run. The
  test compares a freshly spawned process identity; the failure is timing
  dependent and unrelated to this PR (the session module is untouched).

### Not present in this environment

- `test_pre_ready_forensic.test_measured_193739Z_forensic_when_run_present`
  errored in the main worktree (which has recorded `.local` runs) and is skipped
  in the clean worktrees. Environment-dependent by design.


## Previous-implementation behaviour (regression justification)

The new regressions were replayed against the first revision (`24a2d14`) and the
pre-PR classifier (`b94b9e2`). Each row is a behaviour the corrected contract
refuses:

| Evidence | Previous result | Corrected result |
| --- | --- | --- |
| `identity_max_streak = 3`, no digest streaks (`24a2d14`) | `stability_summary(3) = verified` | `weak`; identity is reported as `identity_is_staticity: false` |
| Two identical bare digests without metadata (`24a2d14`) | `staticity_evidence.verified = true` | rejected: metadata/validity/session are required |
| Stable run at frames 10–12, compared frame 14 (`24a2d14`) | `verified = true` | rejected: the run must contain the compared frame |
| Campaign requires 3 stable frames, only 2 present (`24a2d14`) | offline case = `rendering_difference` (case defaulted to two) | `incomplete`; `stable_frames` is read from the scenario |
| Pair status `ambiguous`, shared identity, 14 % draw difference (`24a2d14`) | `_compare_pair` returned `pass` with `alignment = aligned` | `not_compared`; the aligner is authoritative |
| Static digests with `health = unknown` (`24a2d14`) | `rendering_difference` | `incomplete` |
| Recorded `live-a6` / `live-a10` (`b94b9e2`) | `rendering_difference` | `incomplete` |

## Conclusion

The candidate introduces **no new full-suite failure**. Every failure in the
candidate run is also present on the base commit with the same identifier; the
only base-only failure is an unrelated flaky process-identity assertion. No
module touched by this PR appears in either failure set.

The acceptance criteria for the review are covered by
`tests/test_gfx_qualification.py` (28 tests) plus the updated campaign and case
fixtures; the full evidence is in
[the canary report](starter-canary.md#8-correction-and-regression).
