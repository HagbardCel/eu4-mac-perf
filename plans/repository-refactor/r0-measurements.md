# R0 measurements

Baseline commit `6710a341d864466d7b0ab2d297ab513922d582b6`. All R0 execution used the offline profile. The authentic workload used retained evidence and local offscreen replay. [R0 audit](r0-audit.md) · [Disposition map](r0-disposition.md)

## Offline suites

| Environment | Run | Wall seconds | Tests ran | Executed | Skipped | Failures | Errors |
| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Linux portable CI | `20261124T220336Z-d0372459` | 262.183 | 4,416 | 3,935 | 481 | 0 | 0 |
| macOS native | `20261125T171656Z-774615a9` | 614.868 | 4,516 | 4,447 | 69 | 0 | 1 |
| macOS managed sandbox, diagnostic attempt | `20261125T170625Z-bc9acae5` | 481.789 | 4,512 | 4,438 | 74 | 184 | 68 |

Portable CI ran the pinned head, using full offline discovery and a 3,000-test minimum. The portable suite excludes native Apple and machine-local prerequisites. Compare later portable results with the same workflow and selection. [Exact-head portable job](https://github.com/HagbardCel/eu4-mac-perf/actions/runs/45497164694/job/138810599539) · [Workflow](../../.github/workflows/profiler.yml)

The managed macOS attempt hit local socket bind denial, unavailable Metal device, and unavailable nested Seatbelt. The retry used a clean checkout of the same source with the same full selection and native access to those facilities. Use the 614.868-second native run as the measured timing baseline and retain its failed verdict. Offline exclusions for live tests, platform dependencies, and optional machine-local capture or RE fixtures account for distinct skip categories. The stock summary records aggregate skips, so exact per-reason counts are unavailable from these runs. [Offline test profiles](../../automation/test_profiles.py) · [Test runner](../../automation/test_runner.py)

The remaining native error is `ShaderCompileOptionsCacheTests.test_native_cache_uses_the_mode_passed_to_the_compiler`. The test host passes four arguments to `function_for`; the implementation has required five since `112b5847`. Clang reports the mismatch on all four calls. The same focused selection failed on preceding main `e701e43a` (`20261125T172950Z-6d1e8346`) and pinned main (`20261125T172951Z-c6179af8`). The runner comparison reported one pre-existing error and zero new failures. Keep this native test and repair its call signature before relying on the next full native gate. [Native cache test host](../../tests/gfx_shader_compile_options_host.mm) · [Replay implementation](../../benchmark/eu4_gfx_frame_replay.mm)

## Authentic offline replay

Three sequential pairs ran the same restored `47590918…` seal through event 20 with an offscreen Metal device. All six independently executed replays reported `FIRST_DIVERGENCE` at event 1, 23 RGB pixels different, and maximum channel difference 1. They had zero checker problems and verified prefix `[0, 0]`. The paired arms used the same host digest in each pair.

| Pair | Cold host build (s) | Warm host probe (s) | Cold trusted replay (s) | Warm trusted replay (s) |
| --- | ---: | ---: | ---: | ---: |
| 1 | 1.722 | 0.002 | 24.183 | 20.945 |
| 2 | 1.669 | 0.002 | 23.667 | 20.691 |
| 3 | 1.551 | 0.002 | 22.466 | 20.383 |
| **Median** | **1.669** | **0.002** | **23.667** | **20.691** |

The warm build timings include dependency verification and use the existing valid binary. The approximately 3-second median warm replay difference includes the effects of cache reuse across the end-to-end checker flow. GPU service time and a breakdown of hashing, file I/O, and native execution were unavailable in these receipts. [Trusted checker](../../automation/gfx_prefix_check.py) · [Replay host build](../../automation/m6_frame.py)

## Focused native shortlist

Ranked by measured wall time. All 13 single-module runs passed, with 689 executions and five skips across the selections. The top three consumed 62.894 of 77.031 measured seconds. Focus further repeat-cost work on independently executed checkpoint verification, DSH bridge snapshot and subprocess work, and native frame build and replay fixtures.

| Test module | Wall seconds | Executed / skipped | Runner ID |
| --- | ---: | ---: | --- |
| `test_m7_event20_prefix` | 30.120 | 50 / 0 | `20261125T173016Z-dea79fd8` |
| `test_dsh_bridge` | 19.489 | 89 / 0 | `20261125T172942Z-9cc1185a` |
| `test_m6_frame` | 13.285 | 60 / 0 | `20261125T172929Z-1f0cf390` |
| `test_case_locator` | 3.552 | 54 / 0 | `20261125T173013Z-5bcb802a` |
| `test_agent_tools` | 3.394 | 123 / 1 | `20261125T173008Z-8bef0b46` |
| `test_gfx_gl_control_qualification` | 2.859 | 103 / 0 | `20261125T173002Z-b4ba0a25` |
| `test_gfx_dual_run` | 2.015 | 84 / 0 | `20261125T173005Z-e6fda023` |
| `test_test_profiles` | 0.924 | 26 / 2 | `20261125T173012Z-d1228d3b` |
| `test_gfx_qualification` | 0.430 | 52 / 0 | `20261125T173007Z-0c3d5837` |
| `test_gfx_case` | 0.340 | 11 / 0 | `20261125T173007Z-bd9a8b18` |
| `test_gfx_replay_case` | 0.243 | 10 / 0 | `20261125T173011Z-7270a01c` |
| `test_oracle_function_fixture` | 0.213 | 23 / 0 | `20261125T173047Z-237645c5` |
| `test_reconstruction_contract` | 0.167 | 4 / 2 | `20261125T173047Z-b7fe9cf4` |

Unmeasured modules and native-build, subprocess, and hashing counts inside the full suite remain open for R5. The observed full run times exceed the two-minute portable target and provide a separate native comparison point. Test coverage and skip scope still govern the performance target. [Proposed test dispositions](r0-disposition.md#tests-to-review-first)

## Agent traces and repeated work

Reanalyzed the retained DSH export ending 15 November with the pinned `automation.dsh_efficiency` analyzer. Across seven turns it identified 56 test invocations, 20 distinct normalized commands, and 36 repeats beyond the first invocation of a selection. Nine were direct full-discovery commands. The most repeated focused signature appeared 10 times and combined agent tools, case, qualification, dual-run, metrics, replay-case, and offline campaign tests. The standalone graphics qualification selection appeared eight times.

The export spans 19,657.739 seconds of client wall time, with a 6,171.399-second combined service interval union and 5,396.741-second tool-wait union. GPU service time is unknown. Those intervals overlap and include work beyond tests. The counts are historical command observations across edits, retries, and intentional negatives. They identify repetition to inspect, with savings contingent on comparable source and workload. [Historical trace review](../../analysis/m7/efficiency.md) · [Analyzer](../../automation/dsh_efficiency.py)

## Measurement contract

- The full suite uses the existing runner. Focused costs use single-module `--module` selections with the same checkout and environment. Compare the measured shortlist before a consolidation and preserve each unique invariant. A focused sum is not the full-suite cost.
- The replay experiment uses seal `47590918…` through event 20 and the current trusted checker. Each pair starts with no replay host or translation cache at its isolated output root. The warm arm verifies the existing host and translation identity and writes a fresh execution directory. Host-build timing includes dependency identity validation. Replay timing includes trusted-root verification, materialization, translation, host execution, and reconciliation. It does not estimate GPU service time.
- Store raw runner results, timing trials, and DSH analyzer output under the local audit root described in the [audit](r0-audit.md). Reuse the pinned case and match platform, toolchain, test selection, and artifacts when measuring R5 and R7.
