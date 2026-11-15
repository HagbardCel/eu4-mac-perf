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

Result: **152 tests, OK** (the qualification module contributes 37).

## Full-suite results

Final qualification-review run (NumPy installed, same command; logs in
`.local/m7/final-base-full-suite.log` and
`.local/m7/final-full-suite.log`):

| Run | Commit | Tests | Failures | Errors | Skipped | Exit |
| --- | --- | --- | --- | --- | --- | --- |
| Base | `5a0ac03` | 2967 | 21 | 4 | 30 | 1 |
| Candidate (validated code head) | `5a78c95` | 3001 | 11 | 0 | 26 | 1 |

Machine-readable comparison:
`.local/m7/validation-summary.json` (local). Raw logs:
`.local/m7/full-suite-base-5a0ac03.log`,
`.local/m7/full-suite-candidate-clean.log`.

- Common failing ids: **11** (all `test_trace_tr3`; QBDI release not installed)
- Candidate-only failing ids: **0**
- Base-only failing ids: **14** (environment-only: clean checkout/build layout,
  oracle-pinned evidence and signed-division cases that pass in the populated
  worktree)

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
| Passing pairs + `stability=verified`, no pair-local evidence (`b11d877`) | `match` | `incomplete`; per-pair evidence is required |
| Passing pair from an `incomplete` campaign (`b11d877`) | case `match` | `incomplete_evidence` (campaign floor) |
| 120 vs 200 draws, equal identity (`b11d877`) | aligner `aligned`, pixel path `ambiguous` | `ambiguous` in both |
| Repeated digests with `raw_bytes` absent (`b11d877`) | staticity accepted in the qualification re-check | rejected: payload declaration is required |

## Final correctness review

The last review found three remaining qualification inconsistencies. They are
now closed:

1. **No `match` from an unvalidated digest streak.** `classify_campaign()` no
   longer reads `result.stability.status` for the `match` decision. Every
   requested comparison must now have its own pair record with authoritative
   correspondence and a pair-local static run. The runner persists those records
   in `frames.pairs`; `gfx_qualification.match_evidence()` returns every
   blocking reason, and missing per-pair evidence is `incomplete`, never a
   match.
2. **No stronger offline verdict than the campaign.** `gfx_case._classify()`
   applies the campaign floor to passing comparisons too. A PASS pair is
   preserved in the statistics and analysis, but the case reports `match` only
   when its source campaign is a qualified `match` with authoritative pair
   correspondence. Missing campaign qualification is `incomplete_evidence`.
3. **One correspondence decision.** `gfx_align.align_sequences()` still uses
   the monotonic DP to propose pairings, but each pair's final status now comes
   from `pair_correspondence()`. Equal identity with 120 vs 200 draws at 0.30
   tolerance is `ambiguous` in both paths; 120 vs 150 remains aligned.

The small metadata correction now requires the complete payload declaration in
`_static_frame()` (`raw_bytes` absent is rejected). Origin, pixel format and
row-stride remain enforced by the authoritative `_verify_dump_payload()` /
`_collect_dumps()` layer; a collector integration test proves a valid record
supports staticity and malformed stride/origin/format records are rejected.
Per-pair evidence carries the collector's validity flag rather than synthesizing
`valid=True`.

CI now installs `numpy==2.5.4` from `benchmark/requirements-test.txt`, so the
offline qualification tests execute in the portable workflow. The
NumPy-dependent test class is skipped explicitly when the dependency is absent;
the helper function no longer carries a misleading skip decorator.

### New regression additions

- campaign with two passing pairs, complete counts, healthy GPU and
  `stability.status=verified` but no pair-local evidence → `incomplete`;
- campaign where the only stable digest run is at frames 10–12 while the
  compared pairs are 4/5 → `incomplete`;
- campaign with collector-validated per-pair dumps → `match`;
- passing case from `incomplete`, `alignment_failure`, `match`, and missing
  campaign evidence;
- 120/200-draw same-identity pair agrees as `ambiguous` in
  `align_sequences()` and `pair_correspondence()`, while 120/150 aligns;
- real `_collect_dumps()` validity drives staticity; missing `raw_bytes`,
  missing row stride, `RGBA8` format and an invalid origin are rejected.

## Conclusion

The candidate introduces **no new full-suite failure**. Every failure in the
candidate run is also present on the base commit with the same identifier; the
only base-only failure is an unrelated flaky process-identity assertion. No
module touched by this PR appears in either failure set.

The acceptance criteria for the review are covered by
`tests/test_gfx_qualification.py` (28 tests) plus the updated campaign and case
fixtures; the full evidence is in
[the canary report](starter-canary.md#8-correction-and-regression).
