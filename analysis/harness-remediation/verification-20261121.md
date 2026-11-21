# Harness closure — verification report

Date: 21 November 2026. Host: Apple Silicon, macOS 27.0.1, Python 3.14.10.
Evidence is offline unless a row says otherwise. This report records actual
executed/skipped/failed counts; a green exit status alone is not used as
evidence.

## 1. Baseline and candidate full offline profiles

The full offline profile is one `unittest discover -s tests` run (no module
exclusion). Both worktrees were fresh, so `benchmark/.build` was cold.

| Run | Tree | Runner id | Ran | Executed | Skipped | Failures | Errors | Wall |
|---|---|---|---:|---:|---:|---:|---:|---:|
| Baseline | `25ee46db` (merged main, #262) | `20261120T224312Z-911ef529` | 4,221 | 4,156 | 65 | 0 | 6 | 1,244.7 s |
| Candidate | integrated `ba8b7ba4` (six fix heads) | `20261120T230503Z-40f002f7` | 4,241 | 4,176 | 65 | 0 | 6 | 621.4 s |

Both runs **completed**; neither timed out (`timed_out=false`, no
`[test_runner] timeout` marker). The 20-test difference is exactly the new
regressions (6 qualification + 12 claims + 2 flood); executed and skipped
counts differ by the same 20 and 0.

The six errors in both runs are the same clean-worktree defect, not a
regression: six native suites link into `benchmark/.build/` without creating it
(`ld: open() failed, errno=2`). They are fixed in #268; the clean-worktree
reproduction is 8 errors across six modules, and the fixed selection is
11 ran / 11 executed / 0 errors (runner `20261120T231738Z-d890def1`). The final
integrated gate below re-runs the full profile after that fix.

## 2. The reported full-profile hang

The audit record said `tests.test_reconstruction_differential` "hangs only in
full-suite order" and the profile was split around it. Reproduced controlled:

- The module executed **inside** both full profiles: baseline 4,221 ran /
  4,156 executed / 65 skipped minus the audit's split (4,166 / 4,103 / 63,
  runner `20261120T215930Z-f5b80bdf` at `4bf03285`, recorded in the historical
  matrix)
  equals the module's isolated result (55 ran / 53 executed / 2 skipped)
  exactly, so it was not skipped or excluded.
- Neither controlled run stalled. The historical records that were left
  `status: running` have no counts and no timeout marker; their logs stopped
  producing output about 9.5 minutes after start, while a fresh cold full
  profile on this host takes 1,244.7 s. They were externally stopped during a
  long no-output build/verification phase, not killed by the runner.
- The two historical full runs and the split that followed all ran in the same
  `full-main` worktree (the split started 8 minutes after the second), and
  `benchmark/.build` has no cross-process build lock. That configuration is not
  a controlled baseline.
- Independent oracle flood test: 5/5 in isolation on baseline, 7/7 on the
  hardened head; no flood failure in either full profile. The audit's single
  flood failure was a transient `ps` timeout, hardened in #265.

**Gate policy enforced.** #273 now holds one kernel `flock` per `--cwd`
worktree for the whole child execution: a different selection in the same
worktree is refused with a truthful pending/busy envelope and the running
locator, while separate worktrees still run in parallel. Identical-suite
deduplication is unchanged. Allow at least 30 minutes for a cold profile and
use `test_runner status` rather than log growth; never exclude a module from
the profile. The "hangs only in full-suite order" statement is withdrawn as
unsupported.

## 3. Regression verification and base comparison

Each fix was verified on its exact head; the closure PRs (#263–#268) also have
an independent review comment at that exact head. The new tests were run
against `25ee46db` in a separate worktree where the base module is unmodified.

| Fix / PR | Head | Focused result | Base comparison |
|---|---|---|---|
| #263 GL control attribution | `5ae0ccff` | 39/39; neighbour `test_agent_tools` 116 ran / 115 executed / 1 skipped | 3 new tests fail/error on base (`20261120T233031Z-95fdcebf`, `20261120T232302Z-426f7800`) |
| #264 claim ledger | `3cb4d39d` | 27/27; module+bridge 116/116 | 10 of 12 new tests fail/error on base (`20261120T232320Z-56213f9b`); the P2 first-claim delta was separately reviewed at `3cb4d39d` |
| #265 flood cleanup | `fbe23d51` | 7/7; order-sensitive selection 123 ran / 122 executed / 1 skipped; worker neighbour 19/19 | retry test fails on base (`20261120T232243Z-12c12cc9`) |
| #266 anchor/session-root | `dc219a6d` | 93/93; neighbours 241 ran / 241 executed | corrupt anchor was `infrastructure_error` + traceback and a refused prepare left the session root on base (`20261120T232243Z-5139baff`) |
| #267 goal-plugin unload | `f5301f69` | `node --test` 50/50 | late-fiber tests fail on `origin/main`; the race test fails on `a57ba95b` |
| #268 clean build dirs | `c3fbeb77` | 11/11 on a clean worktree | 8 errors on clean `25ee46db` (`20261120T231728Z-12176d5f`) |

Independent reviews of the exact heads are recorded on #263–#268 as review
comments (the only available GitHub identity is the author, so GitHub records
them as `COMMENTED`, not `APPROVE`). #251–#257 have **no** independent review
comment on GitHub; their evidence is the audit verdict, author verification and
CI, which are not approvals. #241–#250 were reviewed by Codex/audit at earlier
commits, not necessarily their final heads. Two review findings were fixed before
merge: the goal-plugin in-flight unload race and the remaining pre-check
session roots plus an empty-object anchor hole (which previously authorized a
context with an empty drift list).

## 4. Scientific measurements

### Authentic compressed-block vs decoded path (present 904)

Sealed 904, same host and shader set; the decoded path was forced by removing
the `compressed` members from a copy of the event stream (12,697 members; the
sealed capture was verified unchanged). Full report:
[`compressed-904.md`](compressed-904.md).

| Checkpoint | Compressed vs live GL | Decoded vs live GL | Compressed vs decoded |
|---|---:|---:|---:|
| 15 | 4 px / max 1 | 858 / 2 | 859 / 2 |
| 16 | 3,602 / 119 | 196,775 / 118 | 196,352 / 87 |
| 17 | 7,484 / 130 | 391,898 / 129 | 391,212 / 87 |
| final | 425,560 / 68 | 1,167,071 / 68 | 1,097,607 / 52 |

The compressed path reproduces the existing shadow byte-for-byte at all four
outputs, so the stored shadow is a compressed-path result. The decoded fallback
is strictly worse at every checkpoint; it is not sampling-equivalent to the BC
blocks. The four-pixel residual at checkpoint 15 is unchanged and still
unexplained by texture representation; no parity is claimed.

### Cache cost (authentic 904, medians of 3)

| Phase | Median | Range |
|---|---:|---:|
| Cold translation (empty cache root) | 3.211 s | 3.207–3.313 |
| Warm translation-cache lookup | 0.374 s | 0.364–0.376 |
| Cache-identity hashing (3.77 MB) | 26.8 ms | 19.2–29.1 |
| Full native replay (327 events) | 0.603 s | 0.599–0.758 |
| GPU service time | `unknown` | — |

The translation cache is real (8.59× on translation); the native Metal
function/pipeline cache is in-process only and recompiles every process. No
material regression was found and no caching redesign is proposed. Full report:
[`cache-profile.md`](cache-profile.md).

### Three paired efficiency trials

Fixed workload: sealed 904; ground truth first divergence event 15, 4 pixels,
max error 1. All six runs were correct.

| Arm | Median wall to classification | Payload | Replay invocations | Confirmed EU4 launches | GPU |
|---|---:|---:|---:|---:|---|
| A legacy full report | 7.525 s | 6,195,746 B | 1 | 0 | unknown |
| B canonical compact | 7.575 s | 7,768 B | 1 | 0 | unknown |

No wall-time improvement is claimed (0.05 s difference, inside the spread).
The measured interface difference is 798× less JSON for the same correct
classification. Full report: [`efficiency-20261121.md`](efficiency-20261121.md).

## 5. Intermediate integrated gate (post-#268)

Run on the merged closure tree (`d3a0ce6b`, merged to `main` as `fecff675`) in
a **fresh worktree with no `benchmark/.build`**, one full
`unittest discover -s tests` profile, no module exclusion:

| Field | Value |
|---|---|
| Runner | `20261120T234901Z-ea7c5f0d` |
| Ran / executed / skipped | **4,247 / 4,182 / 65** |
| Failures / errors | **0 / 0** |
| Status | `passed` (exit 0, not timed out) |
| Wall | 584.7 s |

The count decomposes exactly: 4,166 (audit split, runner `20261120T215930Z-f5b80bdf`) + 55 (`test_reconstruction_differential`) + 26 closure tests = 4,247; executed 4,103 + 53 + 26 = 4,182; skipped 63 + 2 = 65. The module that was previously excluded is included and fully executed (53) with its two declared Ghidra-dependent skips. The six clean-tree errors seen in both earlier full-profile runs are gone after #268.

## 6. Final integrated gate (post-#271, issue closure)

Run after #273 merged (`758e2f56`) in a fresh clean worktree with no other
suite using it, one full `unittest discover -s tests` profile, no module
exclusion:

| Field | Value |
|---|---|
| Runner | `20261121T080139Z-89089cf0` |
| Commit | `758e2f56` |
| Ran / executed / skipped | **4,254 / 4,189 / 65** |
| Failures / errors | **0 / 0** |
| Status | `passed` (exit 0, not timed out) |
| Wall | 627.7 s |

The seven-test difference from the intermediate gate is exactly the new
worktree-lock regressions (4,247 + 7 = 4,254; 4,182 + 7 = 4,189; skips
unchanged). `tests.test_reconstruction_differential` remains included and
executed (55 ran / 53 executed / 2 declared Ghidra-dependent skips).

Focused concurrency verification on merged main before the gate:
`tests.test_test_runner`, `tests.test_test_profiles`, `tests.test_dsh_efficiency`
and `tests.test_automation_runner` — 136 ran / 134 executed / 2 skipped / 0
failures (`20261121T080121Z-8c1390b9`). `tests.test_test_runner` alone is 22/22.

Issue #240 verification: `tests.test_gfx_compressed_textures` +
`tests.test_replay_blob_verifier` — 18 ran / 18 executed / 0 skipped / 0
failures (`20261121T074931Z-25ec3ffc`).

## 7. Limits

- No EU4 launch, no new capture and no renderer correction was performed.
- The historical 904 control remains unqualified: 5 different pixels at
  checkpoint 15 and 4,645 at checkpoint 16, with `GL_SAMPLES`,
  `GL_SAMPLE_BUFFERS`, bit depths and prior-frame contents absent from the
  seal. This report does not assign the residual to Metal.
- The paired efficiency trials measure machine-side harness time only; they do
  not measure model reasoning or session productivity.
- The cache numbers were taken under load (load average 6.8–7.6) with n=3; only
  the large cold/warm separation is claimed.
- The claim-ledger witness is local to the writable campaign directory; the
  documented boundaries (delete both files, pre-head ledger) remain.
