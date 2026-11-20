# Harness review — implementation coverage matrix

Durable issue-to-PR coverage for the 20 November 2026 review
([assessment](assessment.md), [quickwins](quickwins.md), [roadmap](roadmap.md),
[steps](steps/01-exact-verdicts.md)). This file records what was implemented,
where, how it was verified, and what remains unresolved. A PR being listed here
means **implemented and locally verified**, not independently reviewed or
merged. The review policy requires an independent review of the exact final SHA
before merge.

## Baseline

| Field | Value |
|---|---|
| Starting `main` | `e71c15d6` (merge of review PR #239); renderer candidate #238 at `34158764` |
| Review baseline in the assessment | `1838bc9f`; current HEAD already contained #238/#239 |
| Open PRs at start | none (`gh pr list --state open` returned `[]`) |
| Reviewer-visible overlap | #239 is documentation-only (`10f5cbab`); #238 is the renderer/test candidate (`34158764`) and is its parent in `main`. There is no content overlap to split; the #238 follow-up is B1 below. |
| Baseline focused gate | `20261120T142241Z-4a12353e`: 276 ran, 275 executed, 1 skipped, 0 failures, 3.161 s |
| Known limitation | No authentic EU4 launch, present-616/904 replay or same-seal three-way image comparison was executed in this environment. All pixel-level claims below are contract/fixture evidence unless stated. |

## Findings and quickwins

| ID | Severity | Source | Defect / recommendation | Implementation | State | Verification |
|---|---|---|---|---|---|---|
| 1 | high | assessment §1, step 01, Q1/Q2 | A policy pass is advertised as exact; a completed divergent replay becomes a tool failure; loose classifier can qualify without identity | PR **#241** `fix/harness-a1-exact-verdicts` | implemented, awaiting review | `test_m7_diagnosis`, `test_agent_tools`, `test_gfx_replay_checkpoints`, `test_m7_checkpoint_provenance`, `test_correction_accept`, `test_m7_pixel_diagnostics` (291 ran / 290 executed / 1 skip); plugin tests |
| 2 | high | assessment §2, step 02, Q5 | Hand-written matrix misattributes writers and event entries; default checkpoints carry empty producer history | PR **#242** `fix/harness-a3-attribution` | implemented, awaiting review | `test_gfx_frame_graph`, `test_m7_checkpoint_provenance`, `test_gfx_replay_checkpoints` (236 passed) |
| 3 | high | assessment §3, step 02, B1 | Compressed chain silently decodes; raw DXT1 enum collapsed; no size/orientation/device validation | PR **#244** `fix/harness-b1-compressed-textures` | implemented, awaiting review | new native `test_gfx_compressed_textures` (BC1/BC2/BC3, DXT1 RGB/alpha, partial/mixed/invalid/unsupported) + blob verifier + Metal replay |
| 3 (control) | high, unresolved | assessment §3, step 02, B2/B3 | Live GL vs offline GL vs Metal three-way equality is not established; depth history and drawable remain missing | **Not implemented** | open scientific blocker | Authentic 904/916 replay and depth/sample/drawable capture are required; no offline substitute was fabricated |
| 4a | high | assessment §4, step 03, B4 | Shader stage/program cache keys omit effective linkage and tool identity; publication can overwrite a concurrent builder | PR **#248** `fix/harness-b4-identity-recipes` | implemented, awaiting review | `test_m7_translation_cache`, `test_m6_frame`, correction/shadow receipts (116 passed) |
| 4b | high | assessment §4, step 03, B5 | Replay receipts omit consumed overrides/fault/shader/policy recipe | PR **#248** | implemented, awaiting review | recipe unit regression; correction rejects diagnostic recipes |
| 4c | high | assessment §4, step 04, B6 | Sampler key omits R/compare/LOD; cull face and signed scissor are wrong | PR **#245** `fix/harness-b6-render-state` | implemented, awaiting review | new native `test_gfx_render_state`; `test_m7_metal_replay`, `test_m6_frame` (76 ran / 75 executed / 1 skip) |
| 5 | high | assessment “Architecture/recovery”, step 05, Q3 | Startup/resume lacks verified case/worktree/budget context | PR **#247** `fix/harness-a4-startup-context` | implemented, awaiting review | `test_dsh_bridge` (65 passed): startup text, budget, read-only context |
| 6 | high | assessment “Verification/CI”, step 07, Q4 | Native compiler errors become skips | PR **#243** `fix/harness-a5-native-builds` | implemented, awaiting review | new `test_native_build`; 7 native suites on eligible arm64 (137 executed, 1 declared skip) |
| 7 | high | assessment “Autonomy”, step 06, C1 | Owned launch waits for an ops pair that never exists after a valid shadow result | PR **#249** `fix/harness-c1-terminal-capture` | implemented, awaiting review | `test_gfx_startup_trace`, `test_m6_live_capture`, `test_m7_continuous_supervisor` (90 passed) |
| 8 | high | assessment resource limits, step 05, C3/C4 | Nested entry points double-charge; direct `frame` replay is unreserved; identical failures repeat | PR **#250** `fix/harness-c3-reservations-no-progress` | implemented, awaiting review | `test_campaign_guard`, `test_automation_runner`, `test_gfx_replay_case_sequence`, `test_agent_tools`, `test_m6_frame` (199 ran / 198 executed / 1 skip) |
| 9 | medium | assessment knowledge, step 08, D2 | Status leads with stale 615/next-15 narrative; four canonical docs absent; evidence discovery needs an index | PR **#252** (this PR) | implemented, awaiting review | strict doc links, `test_dsh_docs`, `test_case_locator`; restore/inventory instructions |
| Q6 / #232 | medium | quickwins, step 07, D1 | Launch/runner/build/replay counts and service timing are conflated; edit+clear window ends at the tombstone | PR **#251** `fix/harness-d1-physical-work` | implemented, awaiting review | `test_dsh_efficiency` (42 passed) |
| #229 | P3 | issue | Copy-window symlink/byte injection in `case_locator.publish` | PR **#246** `fix/harness-c2-locator-safety` | implemented, awaiting review | `test_case_locator` copy-window/republish regressions |
| #230 | P3 | issue | Discovery mixes pre/post-filter counts and ignores locator offset; truncated verified lookup reports success | PR **#246** | implemented, awaiting review | `test_evidence_index`, `test_case_locator`, `test_agent_tools` (145 ran / 144 executed / 1 skip) |
| #231 | P3 | issue | Startup block not consumed by the launcher/goal context | PR **#247** | implemented for launcher + read-only reconnect context; plugin systemPrompt injection remains | `test_dsh_bridge` |
| #233 | P3 | issue | Non-list checkpoint containers traceback | PR **#241** | implemented, awaiting review | `test_m7_checkpoint_provenance` non-list/non-object regressions |
| #234 | P3 | issue | Status predicate, shader-less translation test, sidecar defaults, pixel-edge tests | partially in PR #252 / #248 | open | Status/predicate refreshed; coordinator shader-less fail-closed and pixel-edge hardening remain |

## Roadmap packages

| Package | Step | PR(s) | State |
|---|---|---|---|
| A — Harness truth and startup | 01, 05 first context, 07 eligibility | #241, #243, #247 | implemented, awaiting review |
| B — Rendering correctness/reproducibility | 02, 03, 04 | #244, #248, #245 | correctness mechanisms implemented; authentic three-way control still blocked |
| C — Autonomous lifecycle | 05 recovery, 06 | #246, #249, #250 | implemented, awaiting review; per-session write roots not implemented |
| D — Verification, metrics, docs | 07 measurement, 08 | #251, #252 | implemented, awaiting review; paired efficiency pilot not run |

## Exact code locations

| Change | Location |
|---|---|
| Exact four-channel predicate | `automation/gfx_independent_replay.py: exact_comparison_statistics`, `final_comparison_report` |
| Canonical classification projection | `automation/gfx_independent_replay.py: classify_diagnosis`, `classification_outcome` |
| Agent compact/`--full` | `automation/agent_tools.py: eu4_replay`, `_replay_compact_view`, `_bound_replay_compact` |
| Graph writers/matrix | `automation/gfx_frame_graph.py: _producer_events`, `checkpoint_plan`, `provenance_matrix` |
| Native prerequisite/build split | `automation/native_build.py` |
| Compressed chain classification | `benchmark/eu4_gfx_frame_replay.mm: compressed_chain_status`, `build_texture`; `benchmark/eu4_gfx_frame_trace.c: format_name`, compressed record |
| Sampler/raster | `benchmark/eu4_gfx_frame_replay.mm: sampler_key_for_state`, `cull_mode_for`, `scissor_rect_for` |
| Translation identity | `automation/gfx_shader_translate.py: translate_stage`; `automation/m6_frame.py: _translator_identity`, `_shader_source_identity`, `_translation_cache_valid` |
| Replay recipe | `automation/gfx_independent_replay.py: replay_recipe`, `write_producer_receipt` |
| Terminal capture | `benchmark/eu4_gfx_startup_trace.py: _wait_pair`, `run_ops`; `automation/m6_frame.py: _shadow_terminal` |
| Locator safety/paging | `automation/case_locator.py: _tree_inventory`, `publish`, `locate`, `inventory` |
| Exactly-once reservations | `automation/campaign_guard.py: attempt`; `automation/gfx_replay_case.py: replay_case`; `automation/m6_frame.py: replay_command` |
| No-progress | `automation/runner.py: _candidate_digest`, `_no_progress` |
| Metrics | `automation/dsh_efficiency.py: analyze`, `filter_turn` |
| Startup context | `automation/dsh_bridge.py: _startup_text`, `op_context`; `dsh/scripts/run.sh` |

## Verification evidence

| PR | Command | Result |
|---|---|---|
| #241 | `test_runner run --profile offline --label a1 --module tests.test_m7_diagnosis ...` | 291 ran / 290 executed / 1 skip / 0 failures |
| #242 | `--label a3 --module tests.test_gfx_frame_graph ...` | 236 passed |
| #243 | `--label a5-native --module tests.test_native_build ...` | 138 ran / 137 executed / 1 declared skip / 0 failures |
| #244 | `--label b1 --module tests.test_gfx_compressed_textures ...` | 34 ran / 33 executed / 1 skip / 0 failures |
| #245 | `--label b6 --module tests.test_gfx_render_state ...` | 83 ran / 82 executed / 1 skip / 0 failures |
| #246 | `--label c2 --module tests.test_case_locator ...` | 145 ran / 144 executed / 1 skip / 0 failures |
| #247 | `--label a4 --module tests.test_dsh_bridge ...` | 79 passed |
| #248 | `--label b45 --module tests.test_m7_translation_cache ...` | 116 passed |
| #249 | `--label c1 --module tests.test_gfx_startup_trace ...` | 90 passed |
| #250 | `--label c34 --module tests.test_campaign_guard ...` | 199 ran / 198 executed / 1 skip / 0 failures |
| #251 | `--label d1 --module tests.test_dsh_efficiency` | 42 passed |

## Unresolved scientific blockers

1. **Live-GL control equality (finding 3).** The review's three-way table is not reproduced. Required next observation: the authentic present 904 sealed case with its original compressed blocks, a selected-draw original-API control with valid color and depth prestate, and all four BGRA channels compared pairwise. Do not substitute DDS assets, a decoded texture, a cleared depth buffer or a window drawable and call it a control.
2. **BC1 transparent-index rendering.** Chain classification and format choice are tested; sampled alpha from a transparent-index BC1 block is not pixel-compared.
3. **Winding.** The direct GL→Metal front-face mapping remains the validated historical choice; B6 does not add a rendered front/back fixture.
4. **Per-session write roots / plugin binding isolation.** Not implemented; concurrent sessions still share campaign-level binding and validation logs. #229/#230 closed the publication/discovery portion.
5. **Native `function_for`/pipeline cache.** Still keyed by file name plus entry; B4 covers the stage/translation/cache publication layer, not the native reader.
6. **Paired efficiency benchmark.** D1 makes the metrics honest but no three paired trials of the new workflow were run.
7. **Plugin systemPrompt reconnect context.** A4 provides the launcher text and a read-only `context` operation; the goal plugin does not yet inject a systemPrompt section.

## Merged-state note

All implementation PRs above are open for independent review. No autonomous
merge was performed. The protected tests, frozen evidence, reference authority
and campaign safety constraints are preserved; no historical evidence was
deleted or relabeled.
