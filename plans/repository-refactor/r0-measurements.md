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

The remaining native error is `ShaderCompileOptionsCacheTests.test_native_cache_uses_the_mode_passed_to_the_compiler`. The test host passes four arguments to `function_for`. The implementation has required five since `112b5847`. Clang reports the mismatch on all four calls. The same focused selection failed on preceding main `e701e43a` (`20261125T172950Z-6d1e8346`) and pinned main (`20261125T172951Z-c6179af8`). The runner comparison reported one pre-existing error and zero new failures. Keep this native test and repair its call signature before relying on the next full native gate. [Native cache test host](../../tests/gfx_shader_compile_options_host.mm) · [Replay implementation](../../benchmark/eu4_gfx_frame_replay.mm)

## Authentic offline replay

Three sequential pairs ran the same restored `47590918…` seal through event 20 with an offscreen Metal device. All six independently executed replays reported `FIRST_DIVERGENCE` at event 1, 23 RGB pixels different, and maximum channel difference 1. They had zero checker problems and verified prefix `[0, 0]`. The paired arms used the same host digest in each pair.

| Pair | Cold host build (s) | Warm host probe (s) | Cold trusted replay (s) | Warm trusted replay (s) |
| --- | ---: | ---: | ---: | ---: |
| 1 | 1.722 | 0.002 | 24.183 | 20.945 |
| 2 | 1.669 | 0.002 | 23.667 | 20.691 |
| 3 | 1.551 | 0.002 | 22.466 | 20.383 |
| **Median** | **1.669** | **0.002** | **23.667** | **20.691** |

The warm build timings include dependency verification and use the existing valid binary. The approximately 3-second median warm replay difference includes the effects of cache reuse across the end-to-end checker flow. GPU service time and a breakdown of hashing, file I/O, and native execution were unavailable in these receipts. [Trusted checker](../../automation/gfx_prefix_check.py) · [Replay host build](../../automation/m6_frame.py)

## Full native attribution

The original 614.868-second native result remains the performance baseline. Its runner log records aggregate duration and outcomes. To attribute full-suite costs, the audit ran an instrumented offline suite at the same Git pin, with a separate clean checkout and the same native permissions. The initial attribution attempt is excluded. Its temporary Python startup hook shadowed Homebrew's own hook, leaving NumPy and Pillow unavailable. The corrected probe chained the original startup hook and passed the affected 25-test smoke selection before the attributed run.

The scratch Python hook starts before the root unittest process, records monotonic start/end for test methods and class/module fixtures, and labels top-level subprocesses by executable family between successful spawn and observed exit. It also records parent sleep and copy intervals of at least 1 ms. A child marker prevents instrumentation of nested test runners. Class and module hooks include cleanup. Per-test wall time includes setup, teardown, failures and assertion work. Attribution gives time to a running test first, otherwise to the innermost fixture. Subprocess, compiler, waits and copies overlap those groups and each other. Child memory, child-internal I/O and GPU service time are unavailable from this probe.

To repeat attribution, use the pinned clean checkout and the same resolved Python and native prerequisites. Place the hook in a scratch directory, preserve the installed `sitecustomize` and retain the original monotonic function before tests can mock it. Activate the hook only for the top-level `-m unittest` worker. Collect the sanitized interval log and the runner summary, then union intervals before reporting group or subprocess time. The measured hook SHA-256 is `23303a0f189ff5287289821aed173161aaa7579387650bcaf3229827f6cc2149`.

```sh
export EU4_TEST_PROFILE=offline EU4_OFFLINE_ONLY=1
export PYTHONPATH="$audit_scratch"
export R0_ATTRIBUTION_LOG="$audit_scratch/timings.jsonl"
python3.14 -m automation.test_runner run --profile offline --label r0-attribution \
  --state-dir "$audit_scratch/runner" --min-tests 3000 --timeout 2400
```

Run `20261125T180755Z-bdd849a0` finished in 489.736 s, with 488.664 s inside unittest. It ran the same 4,516 tests, executed 4,447 and skipped 69. Runner `compare` against `20261125T171656Z-774615a9` returned `no_new_failures`, with the same one pre-existing compile error. The timed replicate was **125.132 s (20.35%) faster** than the original. This spread exceeds the entire first-wave savings estimate. Instrumentation, host load, thermal state and compiler/filesystem warmth differ or were uncontrolled. These runs establish cost pools and the observed spread. They cannot measure an optimization effect or estimate mean native gate time.

| Exclusive test or fixture group | Wall seconds | Share of unittest wall |
| --- | ---: | ---: |
| Function oracle, reverse engineering and byte-level qualification | 181.390 | 37.12% |
| Frame replay, graphics operations and native renderer | 159.318 | 32.60% |
| Campaign, DSH, launch safety and test tools | 65.642 | 13.43% |
| Profiler, frame model and bounded diagnostic fixtures | 52.435 | 10.73% |
| Other identified test modules | 20.573 | 4.21% |
| Repository inventory and documentation guards | 9.184 | 1.88% |
| **Attributed** | **488.542** | **99.98%** |
| Between tests and fixtures | 0.122 | 0.02% |

Take the first matching prefix in this order over the test module name after `test_`. Modules with no match account for the “Other” row. Groups follow ownership of the test, even where the test exercises a dependency.

| Group | Module prefixes used for aggregation |
| --- | --- |
| Frame replay and native renderer | `gfx, m[4567], metal, replay, shader, earliest_divergent, dense_mipmap` |
| Oracle and reverse engineering | `oracle, check_oracle, reconstruction, eu4_re2, re_, ghidra, renderer, trace, macho, callsite, static_` |
| Campaign, DSH and launch | `automation, test_runner, test_profiles, agent, dsh, campaign, governance, launch, offline, case_locator, evidence_index, reserve` |
| Profiler and bounded diagnostics | `frame_model, r0_, eu4_benchmark, thread_cpu, live_diagnostic, sampler, state_cache, submission, profiler, benchmark, measure, cpu_, display` |
| Repository guards | `repo_, check_doc, docs_, check_forbidden` |

The root worker observed **10,299 subprocesses across 391.124 s of interval union**. The compiler and SDK-tool family accounted for 821 launches across 121.069 s, including 364 `clang` invocations across 95.569 s. Git accounted for 2,837 launches across 32.618 s. The root process recorded 124.338 s in sleep intervals. These include subprocess polling and deliberate deadline cases. Fixtures outside the test-method intervals account for about 34.4 s, before counting per-test setup and teardown.

Largest modules were `test_oracle_slice_e2e` (46.923 s), `test_check_oracle_receipt` (25.317 s), `test_dsh_review` (18.170 s), `test_frame_model_wp5` (17.860 s), `test_reconstruction_differential` (17.454 s) and `test_gfx_live_capture` (16.818 s). Other substantial costs include `test_gfx_op_diff` (14.927 s), `test_test_runner` (14.434 s), and `test_m7_event20_prefix` (13.849 s).

| Slow individual case | Wall seconds | Dominant observed mechanism |
| --- | ---: | --- |
| `test_test_runner` / `test_timeout_stops_the_child_process_group` | 11.068 | Intentional timeout, group termination and post-termination assertion |
| `test_eu4_re2_accept` / `test_known_good_passes_and_signed_division_fails` | 10.855 | Known-good and deliberately different candidates across individual native rows |
| `test_oracle_slice_e2e` / `test_finalization_is_bounded_and_the_run_deadline_binds_tools` | 7.038 | Deliberate finalization deadline and observable child termination |
| `test_m7_continuous_supervisor` / `test_cancellation_interrupts_a_silent_worker` | 5.448 | Silent-child cancellation |
| `test_reconstruction_differential` / signed-division and selector-wrap near misses | 5.376 / 5.345 | 414 independent harness executions per negative |
| `test_m7_event20_prefix` / `test_two_rounds_use_new_execution_directories_and_pixels` | 4.997 | Two native executions with independent output and authority checks |

The copy hook observed one parent `shutil.copyfile` call above 1 ms. The sequence tests still include sealing, path resolution, reads, hashing, hard links and child-side materialization. Their 8.128 s includes 57 observed launches, 4.875 s of observed subprocess time and 0.401 s of class/module fixtures. The prefix selection's 13.849 s includes 122 launches, 6.627 s of compiler/SDK-tool time and 1.046 s of class/module fixtures. These provide bounds for focused R5 instrumentation. Python-level copy measurements cannot isolate the native reads or split hashing, tree scans and transfer. Preserve those checks and compare load/materialize/reconcile stages on identical evidence before crediting I/O savings. [Sequence tests](../../tests/test_gfx_replay_case_sequence.py) · [Prefix tests](../../tests/test_m7_event20_prefix.py) · [Replay case materialization](../../automation/gfx_replay_case.py)

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

The isolated focused times include different compiler and filesystem conditions from the attributed full run. Prioritize comparisons with the same selection, toolchain, platform, and input roots. [Proposed test dispositions](r0-disposition.md#source-overlap-and-retirement)

## Optimization budget

R5 owns the first suite changes. Within a single execution, reuse proven immutable inputs or share setup where it serves the same invariant. Keep the runner's existing behavior, independent trusted builds, fresh authority checks, exact receipt validation and real intentional kills. The savings below are per full run on the attributed workload, with costs assigned to separate module or case pools. Estimates require paired checks of both elapsed time and retained coverage before entering the PR budget.

| Priority and concrete change | Current measured cost | Estimated saved | Retained coverage | Complexity |
| --- | --- | ---: | --- | --- |
| 1. Separate receipt-only mutations from repeated oracle controller runs. Produce one sealed synthetic publication for immutable receipt cases and preserve standalone controller/deadline cases. | Oracle E2E plus receipt modules took 72.240 s. Their root sleep unions were 39.245 and 18.374 s and overlap child work. | 15–28 s | Positive controller path, finalization deadline and process-tree exit, corrupt seals/corpus, pinned revisions and fail-closed certificates. [E2E](../../tests/test_oracle_slice_e2e.py) · [Receipt](../../tests/test_check_oracle_receipt.py) | Medium |
| 2. Share byte-identical native shadow-worker builds across equivalent operation-diff fixtures. `_default_worker_factory` calls the builder even when the same source and flags were just exercised. | `test_gfx_op_diff` spent 11.865 of 14.927 s in nine build intervals. | 5–9 s | Cold compile/failure test, independent workers, differential state and out-of-envelope cases. [Builder call](../../benchmark/eu4_gfx_op_diff.py) · [Tests](../../tests/test_gfx_op_diff.py) | Low |
| 3. Share proven native graph/frame fixture builds, starting with frame-model reference, alias, and native capture graph fixtures. Exercise separate executions for state or ABI transitions. | WP5, draw API, live capture and M6 frame took 52.236 s together. Compiler-tool unions in WP5, draw API and M6 alone totalled 17.364 s. | 10–22 s | Build invalidation, alias ABI, capture/readback lifetime, trace and shader integrity. [WP5](../../tests/test_frame_model_wp5.py) · [Draw API](../../tests/test_frame_model_draw_api.py) · [Live capture](../../tests/test_gfx_live_capture.py) · [M6](../../tests/test_m6_frame.py) | Medium |
| 4. Batch row checks through the existing byte-qualification harnesses if each row can prove fresh state. Retain standalone executions where process isolation is required, especially expected aborts. | Reconstruction differential, RE2 and adversarial acceptance took 38.064 s. The first two observed 1,250 and 912 child launches. | 9–18 s | Every corpus row, signed-division and selector near misses, original/candidate separation, byte/canary comparisons and explicit unqualified results. [Differential](../../tests/test_reconstruction_differential.py) · [RE2](../../tests/test_eu4_re2_accept.py) · [Adversarial](../../tests/test_oracle_certify_adversarial_acceptance.py) | Medium–high |
| 5. Minimize repeated construction of equal immutable sequence/receipt inputs. Group equivalent assertions around a single scoped result where their source, seal, recipe and outputs stay identical. | Replay sequence and event-20 prefix took 21.977 s. Isolated per-stage data is still needed. | 3–9 s conditional | Retain exact blob/manifest validation, immutable inputs, distinct actual executions, observed/candidate separation and mutation tests. [Sequence](../../tests/test_gfx_replay_case_sequence.py) · [Prefix](../../tests/test_m7_event20_prefix.py) | Medium |
| 6. Separate DSH review snapshot/bridge mutation cases from repeated Git initialization. Share immutable committed fixtures and copy only the state under test. | DSH review and bridge took 26.862 s. They observed 520 and 364 root subprocesses. | 4–10 s | Reviewer isolation, write-set verification, swapped and stale snapshots, authority refusal and real Git publication. [Review](../../tests/test_dsh_review.py) · [Bridge](../../tests/test_dsh_bridge.py) | Medium |
| 7. Shorten independent timeout fixtures where the same real process-group signal and escalation assertions still run. Leave production policy durations unchanged. | Runner timeout at 11.068 s and silent supervisor at 5.448 s. Oracle deadline tests were budgeted in row 1. | 0–6 s conditional | Timeout receipt, termination of descendants, escalation and supervisor cancellation. [Runner](../../tests/test_test_runner.py) · [Supervisor](../../tests/test_m7_continuous_supervisor.py) | Medium |

The identified first-wave opportunities total **46–102 s**, roughly 9–21% of the attributed native run. This estimate does **not establish feasibility of a 70% reduction**. For scale, the original 614.868 s native run would require savings of about 430 s for 70%. The 262.183 s portable run would require about 184 s for 70% and about 142 s to reach its separate two-minute aspiration. The native estimates do not transfer to portable CI. R0 provides no two-minute native target and no demonstrated optimized timing. Report actual paired gains and seek approval for any revised aspiration under the approved plan.

## Section 8.3 latency status

| Approved aspiration | Measured status or next comparable sample |
| --- | --- |
| Common read-only diagnostic <1 s | Earlier evidence discovery and temporal diagnostic took 0.15 s and 0.4 s on named cases. Measure the same bounded queries with the R7 corpus and roots before claiming typical latency. [Historical samples](../../analysis/m7/efficiency.md#improved-offline-workflow-measured) |
| Typical focused portable <10 s | Earlier 11-module offline selection ran in 6.1 s. Ten of 13 current isolated native modules fell below 10 s. Neither sample establishes the current portable distribution. Time a fixed common portable selection on CI. |
| Small native experiment with existing build <30 s | The retained warm host probe took median 0.002 s and the matching warm event-20 replay 20.691 s. Repeat an agreed small native workload at identical proof scope to establish broader latency. |
| Common authentic offline replay <60 s | All six 475 samples met the time bound at their retained scope. The checker still reports event-1 divergence. Sample a second protected workload before generalizing. |
| Full portable offline gate <2 min | **262.183 s** at the pin. Native wall times of 614.868 s and 489.736 s are separate comparisons. |
| Comparable full offline validation ≥70% lower | Unproven. Establish separate portable and native before/after pairs with equivalent invariants. |
| Eliminate unnecessary equivalent native builds | Nine operation-diff fixture builds took 11.865 s in the attributed run. Confirm matching dependency and flag identity, then count launches and verify retained fresh-build/failure coverage. |
| Zero routine autonomous original EU4 launches | R0 ran offline with zero launches. R6 must verify actual routine sessions and authorization receipts. |

## Agent traces and repeated work

Reanalyzed the retained DSH export ending 15 November with the pinned `automation.dsh_efficiency` analyzer. Across seven turns it identified 56 test invocations, 20 distinct normalized commands, and 36 repeats beyond the first invocation of a selection. Nine were direct full-discovery commands. The most repeated focused signature appeared 10 times and combined agent tools, case, qualification, dual-run, metrics, replay-case, and offline campaign tests. The standalone graphics qualification selection appeared eight times.

The export spans 19,657.739 seconds of client wall time, with a 6,171.399-second combined service interval union and 5,396.741-second tool-wait union. GPU service time is unknown. Those intervals overlap and include work beyond tests. The counts are historical command observations across edits, retries, and intentional negatives. They identify repetition to inspect, with savings contingent on comparable source and workload. [Historical trace review](../../analysis/m7/efficiency.md) · [Analyzer](../../automation/dsh_efficiency.py)

### Recent campaign audit

Reviewed eight retained DSH exports between 20 and 24 November. Match tool calls to results by call ID and background registrations/settlements by job ID. Read the adjacent source edits, checkout and diff checks, commands, candidate or producer identities, and receipts before classifying an invocation. The analyzer's direct invocation counts can miss shell pipelines and indirect jobs, so the sampled full-gate attempt below comes from the raw job and runner records.

| Export and source records | Classification and measured cost |
| --- | --- |
| 20 Nov `3dc697da-68ac-45e3-8579-bdf3814a7ec3`, hash `0e5fa9e9…`, seq 709–913 and 1989–2289 | Needed focused validation after an agent-tools edit and after adding compressed-blob coverage. Fixing the intentionally corrupt payload fixture required another run. The late agent-tools check took 0.994 s after the commit and artifact documentation. The trace shows no relevant source edit in between, making it a candidate for omission if build/input identity is reconfirmed. The compressed-texture full gate followed an actual retained change and was cancelled. The five empty status replies on its job totalled 0.047 s. Keep process time and status-query overhead separate. |
| 22 Nov `4a030e35-7238-4e92-8f90-f0264fbe836c`, `ad10708c…`, seq 250–554 | Isolation canary and production checker verification address distinct trust boundaries. The initial checker attempt used a disallowed evidence-report path, a retry failed with exit 127, and the corrected production replay completed. These are negative or failed invocations, not proof of repeated equivalent validation. Four output calls totalled 19.532 s, of which 19.472 s awaited the corrected job. |
| 22 Nov run-60m-01, `10579f10-9858-4df0-b674-a211d88b9231`, `8447a04f…`, seq 74–105, 281–341, 757–854 | The two focused/same-command selections changed cwd and source context. Their equivalence is unproven. Baseline and intentionally modified decoder qualification took 41.644 and 41.893 s of tool call wall time. A **654-second** full gate (`20261122T214211Z-a5528971`) followed removal of the temporary decoding experiment and only a research record was retained for that round. No need for the full selection was documented. Class it as a removal candidate under the current exact-rollback policy, contingent on verification of the restored source, toolchain and build identity. Three waits on that job totalled 642.708 s and are already inside its elapsed run. |
| 22 Nov run-60m-03, `e8b6f3a9-e54d-4e7f-835b-5c20586e335d`, `448cded0…`, seq 71, 424, 471, 735, 861, 918 | The initial qualification established baseline authority. Three requalifications followed only research documentation. One followed the exact depth-probe rollback. The [campaign audit](../../analysis/m7/run-60m03-evidence-audit.md#trusted-validation-audit) attributes **158.203 s of validation execution** to those four, out of 236.998 s total. Their measured tool-call wall time was 163.311 s. A separate diagnostic-only validation would bring the possible saving to 196.625 s of execution if its scope did not require acceptance. Whole-worktree digests changed. The equivalent renderer/input scope and unchanged exact failure establish the redundancy. |
| 22–24 Nov run-60m-02 `0f120ab8-8834-4858-b438-951589e9ef57` (`4719f8ff…`), diagnostic `9aa1105b-a4bd-437d-9d0a-0e4252fa41eb` (`b3f38648…`), run-60m-05 `863207fc-cef4-4c1f-ab4c-913dc1b2dcf3` (`707d2edb…`), and qualification `78977c9f-8392-479a-ae6c-ec494e53561b` (`5142dd19…`) | The first and last retain explicit qualification receipts. The last took 42.224 s and is the authority anchor named for the 20-pixel historical candidate. In run-60m-05, two output waits totalled 78.766 s for distinct diagnostic jobs. Their results include unsupported variants and changed UV coordinates. Treat these as separate service/wait observations. Where source or recipe identity cannot be established, no avoidable runtime is budgeted. |

The confirmed avoidable execution in this sample is 158.203 s. The 654-second post-rollback full gate is a separate policy opportunity with the identity qualification above. These are historical session costs, independent of the R5 single-suite optimization budget. Prefer a result receipt for already-measured diagnostic inputs and the existing runner's run-ID status over repeated empty polling. Keep fresh authority for finalization, current source changes, and deliberate negatives. This is a validation trigger policy, with no test cache.

To reproduce the trace audit, locate the named `session-<UUID>/session.v4.jsonl.zstd` under the EU4 DSH home, verify its SHA-256 prefix, run `python3.14 -m automation.dsh_efficiency analyze <export>`, and join the cited tool calls and jobs to their recorded results. Protect raw transcript contents when publishing aggregate costs.

## Measurement contract

- For R5 pair base and candidate on the same host or CI image, OS/toolchain/interpreter, test profile and selection, native prerequisites, evidence and cold/warm setup. Keep background load and thermal conditions comparable and record the order, runner IDs, wall times, failures and skips. Repeat targeted pairs with reversed order where effects overlap observed run-to-run spread. If a test selection changes, map every removed case to the retained invariant and report the composition and coverage change alongside the result. Use the existing runner and keep portable and native comparisons separate. The final full gate follows the normal merge workflow.
- The replay experiment uses seal `47590918…` through event 20 and the current trusted checker. Each pair starts with no replay host or translation cache at its isolated output root. The warm arm verifies the existing host and translation identity and writes a fresh execution directory. Host-build timing includes dependency identity validation. Replay timing includes trusted-root verification, materialization, translation, host execution, and reconciliation. It does not estimate GPU service time.
- Report single-invocation wall time separately from session validation count, execution intervals, elapsed session time and avoidable waits. Join calls and jobs by identity and avoid double-counting overlapping intervals. Confirm equivalent validation scope and relevant source/input identity before assigning avoidable cost. The historical 158.203 s saving is a cumulative session finding.
- Keep raw runner results, timing trials and DSH analyzer output under the private audit root. Exact profiling scripts and the retention location are recorded with the [reproduction archive](r0-audit.md#reproducibility).
