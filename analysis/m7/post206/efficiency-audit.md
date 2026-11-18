# Efficiency audit — DSH session

Source: the DSH session export for this preparation, analyzed by
`python3 -m automation.dsh_efficiency analyze`. The full bounded result is
[`dsh-efficiency.json`](dsh-efficiency.json). These numbers are measured from
the export, not estimated.

## What the parser reports, and what it means

| Metric | Value | Interpretation |
| --- | --- | --- |
| Elapsed / model calls | 38,667 s / 673 | Wall-clock spans a multi-hour unattended gap between remediation turns; it is not active work |
| Tool calls | 710 total (683 bash; 0 native tool-level failures) | The adapter reports no failed tool calls |
| Shell commands / shell failures | 683 / 21 | `calls.failed` only counts tool-adapter errors; `shell_failures` counts bash results with a nonzero exit marker. The 21 include intentional diagnostic/test failures and negative tests. |
| Tool wait | not meaningful across the gap | The raw call/result span sum is inflated by the unattended pause (about 1.5 Ms); do not use it as active wait |
| Tokens | 332.3 M total, 99.0% cache-read | Cache-heavy long session |
| Output volume | 1,048,347 chars; 0 oversized, 5 truncated | Bounded reads kept oversized output at zero |

## EU4 launches: reconciliation

The parser now counts **bash launch commands**, not only native launch tools. It
reports **3 capture command invocations**:

1. a command that failed at shell redirection before Python started;
2. the first real capture attempt, which ended `draw_cap` after launching EU4;
3. the successful three-frame capture.

Therefore **2 EU4 processes actually started**, matching the documented
captures. `eu4_launches.count` is explicitly a command-invocation count, not a
confirmed process-start count; `process_starts_estimate` is deliberately not
used as evidence. The Phase E0 pilot launched EU4 **zero** times.

## Preparation vs pilot

- **Preparation:** environment/baseline recon, the two capture attempts,
  Phase C/D implementation and focused tests, portable-case verification and
  replay.
- **Pilot:** selected the frozen case, reproduced it offline (21.56 s),
  inspected the event-284 source with the GL/GL control, ran Ghidra
  `_GfxResolve`, and tested copy/bias/Bayer/fast-math/edge hypotheses. No EU4
  launch; all pilot replays were offline.

## Test invocations

`tests.test_invocations=55`, `distinct_commands=25`,
`duplicate_invocations=30`, `full_suite_invocations=0`.

The 30 repeats are focused re-runs after edits and deliberate negative tests;
they are not duplicate full suites. The one broad module-list run was stopped
at a live benchmark prompt and is not counted as a full-suite pass. The
repetition is the main measurable cost in this session; batching the
repeated `test_m7_diagnosis`/`test_dsh_efficiency` loops would have saved
about 30 command invocations without losing coverage.

## Goal status

The export contains `create → complete → create → complete`: the first goal
and the PR-remediation goal both completed. The final snapshot records
`goal.terminal=true` and `goal.outcome=completed`; the earlier `active` state
was only an intermediate snapshot.

## Measured improvements

1. **Capture budget:** the default is now 512 events. The previous 256 default
   caused a real `draw_cap` failure; the successful capture used 325 events.
2. **Compact diagnosis:** the authentic `agent-diagnosis.json` is 6,075 bytes
   versus 3,007,849 bytes for the full report (about 99.8% smaller), and it
   contains a causally selected checkpoint subset, not the first 32 rows.
3. **Authentic case outside CI:** 1.348 GB is never part of routine CI; a
   512 MiB budget produces an explicit incomplete summary.
4. **No duplicate full suites:** one broad run was stopped at a live prompt;
   focused suites were used for the review gate.
5. **CI polling:** use bounded `gh pr checks --watch`/`gh run view` completion
   instead of fixed sleeps. The earlier fixed sleeps added minutes without
   adding information.

## Claims not made

- The 15 shell failures are not all defects; several are the negative tests
  that prove fail-closed behavior.
- The parser cannot prove an EU4 process started from text alone; the two
  documented capture records are the process evidence.
- No token or wall-clock reduction is claimed from a change that was not
  measured.
