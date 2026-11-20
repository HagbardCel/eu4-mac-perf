# Harness review — implementation coverage matrix

Durable issue-to-PR coverage for the 20 November 2026 review
([assessment](assessment.md), [quickwins](quickwins.md), [roadmap](roadmap.md),
[steps](steps/01-exact-verdicts.md)). This file records implementation, the
exact reviewed head, technical verification and the remaining work.

**Nothing here is a merge or an issue closure.** At the 2026-11-20T16:54Z query
all twelve PRs #241–#252 were `OPEN` and issues #229–#234 were `OPEN`. Technical
state is deliberately separate from approval: a `verified` row has technical
evidence, not an independent approving review.

## State vocabulary

| State | Meaning in this matrix |
|---|---|
| `verified` | The named head contains the change; the recorded focused check is green on that head, and any automated-review finding was re-checked in the code. Not an approval or merge. |
| `partial` | Part of the issue is implemented/verified, or an automated-review finding is still open at the head. The remainder is named in the row. |
| `blocked` | The required authentic observation is unavailable from current evidence; the next discriminating test is named. |
| `accepted` | An independent reviewer/checker accepted the exact implementation head. No row has this state in this round; a checker's acceptance of a correction is not PR approval. |

`D2` re-ran only the focused modules and strict link check in the final
verification table. Other rows retain the verification recorded by the author's
head and are not presented as D2 reproductions.

## Baseline and query

| Field | Value |
|---|---|
| Starting `main` | `e71c15d6` (merge of review PR #239); renderer candidate #238 at `34158764` |
| Review baseline in the assessment | `1838bc9f`; current HEAD already contained #238/#239 |
| PR inventory at 2026-11-20T16:54Z | #241–#252 all `OPEN`, none merged; issues #229–#234 all `OPEN` |
| CI at the same query | success: #241 #242 #243 #244 #246 #247 #248 #249 #250 #251 #252; #245 has no check (stacked branch, merge state `DIRTY`) |
| Automated review | Codex `COMMENTED` on #241–#250; #251/#252 had no review yet. A `COMMENTED` review is not an approval. |
| D2 pre-change focused gate | `20261120T164733Z-7819fdda`: 43 ran / 42 executed / 1 skipped / 0 failures / 3.543 s |
| Known limitation | No authentic EU4 launch, present-616/904 replay or same-seal three-way image comparison was executed in this worktree. Pixel-level claims are contract/fixture evidence unless a row says otherwise. |

## Findings and quickwins

Head SHAs are the PR heads at 2026-11-20T16:54Z. “Finding re-checked” means the
line was inspected at the head, not that a reviewer approved the fix.

| ID | Severity | Source | Defect / recommendation | PR / branch | Head SHA | State | Independent approval | Verification / open remainder |
|---|---|---|---|---|---|---|---|---|
| 1 | high | assessment §1, step 01, Q1/Q2 | A policy pass is advertised as exact; a completed divergent replay becomes a tool failure; loose classifier can qualify without identity | #241 `fix/harness-a1-exact-verdicts` | `818e8360` | `verified` | pending — Codex COMMENTED at `4ddd15ed`; P2 size-cap finding re-checked fixed at head; no approving review | Recorded `test_m7_diagnosis`, `test_agent_tools`, `test_gfx_replay_checkpoints`, `test_m7_checkpoint_provenance`, `test_correction_accept`, `test_m7_pixel_diagnostics` (291 ran / 290 executed / 1 skip). Portable CI success. |
| 2 | high | assessment §2, step 02, Q5 | Hand-written matrix misattributes writers and event entries; default checkpoints carry empty producer history | #242 `fix/harness-a3-attribution` | `6a52e005` | `verified` | pending — Codex COMMENTED at `8c95fc10` and `6a52e005`; both P2 findings re-checked fixed at head; no approving review | Recorded `test_gfx_frame_graph`, `test_m7_checkpoint_provenance`, `test_gfx_replay_checkpoints` (236 passed). Portable CI success. |
| 3 | high | assessment §3, step 02, B1 | Compressed chain silently decodes; raw DXT1 enum collapsed; no size/orientation/device validation | #244 `fix/harness-b1-compressed-textures` | `b274eeb6` | `verified` | pending — Codex P1 import finding was against `d337540f` and is re-checked fixed at head; no approving review | Recorded native `test_gfx_compressed_textures` (34 ran / 33 executed / 1 skip). Rendered BC1 transparent-index and block-row fixtures were inspected on this branch only (unmerged, not run by D2). Portable CI success. Authentic same-seal comparison is blocker 2 below. |
| 3b | high, unresolved | assessment §3, step 02, B2/B3 | Live GL vs offline GL vs Metal three-way equality is not established; depth history and drawable remain missing | **Not implemented** | — | `blocked` | not requested | Stored present-904 control still differs from live GL; see blocker 1. Required: an authentic selected-draw control with valid color+depth prestate and all four BGRA channels compared pairwise. |
| 4a | high | assessment §4, step 03, B4 | Shader stage/program cache keys omit effective linkage and tool identity; publication can overwrite a concurrent builder | #248 `fix/harness-b4-identity-recipes` | `aeb4e97d` | `partial` | pending — three Codex findings open at head; no approving review | Recorded `test_m7_translation_cache`, `test_m6_frame`, correction/shadow receipts (116 passed); CI success. Open: diagnostic overrides do not set `diagnostic`; `consumed_env` is read before the host environment is resolved; an invalid cache entry is discarded instead of repaired. Fix and rerun before `verified`. |
| 4b | high | assessment §4, step 03, B5 | Replay receipts omit consumed overrides/fault/shader/policy recipe | #248 | `aeb4e97d` | `partial` | pending as 4a | The recipe exists, but the `consumed_env` finding means it can record settings the host did not consume. Treat the recipe as `partial` until that finding is fixed and re-tested. |
| 4c | high | assessment §4, step 04, B6 | Sampler key omits R/compare/LOD; cull face and signed scissor are wrong | #245 `fix/harness-b6-render-state` | `843a05cc` | `verified` | pending — Codex P2 effective-cull finding re-checked fixed at head; branch is stacked on #244 and a native-build commit; not merged | Recorded native `test_gfx_render_state` (83 ran / 82 executed / 1 skip) plus `test_m7_metal_replay`, `test_m6_frame` (76 ran / 75 executed / 1 skip). Rendered front/back winding fixture inspected on this branch only (unmerged, not run by D2). No CI result: base is #244 and merge state is `DIRTY`. |
| 5 | high | assessment “Architecture/recovery”, step 05, Q3 | Startup/resume lacks verified case/worktree/budget context | #247 `fix/harness-a4-startup-context` | `b3655fb8` | `partial` | pending — Codex P2 anchor-drift finding open at head; no approving review | Recorded `test_dsh_bridge` (79 passed); CI success. Open: `context` reports `anchored: true` from identity-file presence without comparing the current sidecar/policy to the stored anchor. Next: refuse or mark drift, then rerun `test_dsh_bridge`. |
| 6 | high | assessment “Verification/CI”, step 07, Q4 | Native compiler errors become skips | #243 `fix/harness-a5-native-builds` | `0982ece0` | `partial` | pending — Codex P2 Intel-eligibility finding open at head; no approving review | Recorded `test_native_build` and 7 native suites (138 ran / 137 executed / 1 declared skip); CI success. Open: OpenGL-only frame-trace and texture-version hosts pass the default `require_arm64=True`, which removes eligible Intel coverage. Next: `require_arm64=False` for those hosts and rerun. |
| 7 | high | assessment “Autonomy”, step 06, C1 | Owned launch waits for an ops pair that never exists after a valid shadow result | #249 `fix/harness-c1-terminal-capture` | `5270fc1c` | `partial` | pending — Codex P2 cleanup-propagation finding open at head; no approving review | Recorded `test_gfx_startup_trace`, `test_m6_live_capture`, `test_m7_continuous_supervisor` (90 passed); CI success. Open: `cleanup_errors` is recorded but not propagated, so a capture with a failure can still return success. Next: propagate or reject, then rerun. |
| 8 | high | assessment resource limits, step 05, C3/C4 | Nested entry points double-charge; direct `frame` replay is unreserved; identical failures repeat | #250 `fix/harness-c3-reservations-no-progress` | `44e547cb` | `partial` | pending — three Codex P2 findings open at head; no approving review | Recorded `test_campaign_guard`, `test_automation_runner`, `test_gfx_replay_case_sequence`, `test_agent_tools`, `test_m6_frame` (199 ran / 198 executed / 1 skip); CI success. Open: candidate digest omits untracked/non-Git contents; a nonzero replay return settles as success; a sequence reserves `max_frames` instead of the supplied length. Next: fix all three and rerun. |
| 9 | medium | assessment knowledge, step 08, D2 | Status leads with stale 615/next-15 narrative; four canonical docs absent; evidence discovery needs an index | #252 (this PR) `docs/harness-d2-knowledge-retention` | `57d10e5b` before this commit; this commit advances it | `verified` | not requested — no review yet; portable CI success at `57d10e5b` | D2 ran the focused gate and strict links (results below). Adds byte-identical restore with digest+size, plus missing-member, missing-digest and size-drift negatives, to `test_case_locator`; synthetic fixture only. |
| Q6 / #232 | medium | quickwins, step 07, D1 | Launch/runner/build/replay counts and service timing are conflated; edit+clear window ends at the tombstone | #251 `fix/harness-d1-physical-work` | `7c95d804` | `verified` | not requested — no review yet; portable CI success | Recorded `test_dsh_efficiency` (42 passed). Paired efficiency pilot remains blocker 5. |
| #229 / #230 | P3 | issue | Copy-window symlink/byte injection in `case_locator.publish`; discovery mixes pre/post-filter counts and ignores locator offset; truncated verified lookup reports success | #246 `fix/harness-c2-locator-safety` | `ea204014` | `partial` | pending — Codex P2 `verified_all` finding open at head; no approving review | Recorded `test_case_locator`, `test_evidence_index`, `test_agent_tools` (145 ran / 144 executed / 1 skip); CI success. Open: a metadata-only lookup (`verify=False`) can report `verified_all`/`safe_to_reuse` without verifying bytes. Next: gate both on verification actually running; rerun. Issues #229/#230 remain `OPEN`. |
| #231 | P3 | issue | Startup block not consumed by the launcher/goal context | #247 | `b3655fb8` | `partial` | pending as 5 | Launcher text and read-only reconnect `context` exist. The goal plugin still does not inject a systemPrompt section, and the anchor-drift finding remains. Issue #231 remains `OPEN`. |
| #233 | P3 | issue | Non-list checkpoint containers traceback | #241 | `818e8360` | `verified` | pending as 1 | Non-list/non-object regressions recorded in `test_m7_checkpoint_provenance`. Issue #233 remains `OPEN` until a separate closure is recorded. |
| #234 | P3 | issue | Status predicate, shader-less translation test, sidecar defaults, pixel-edge tests | #252 / #248 | heads above | `partial` | pending | Status/predicate refreshed. Coordinator shader-less fail-closed and pixel-edge hardening remain in #248. Issue #234 remains `OPEN`. |

## Roadmap packages

| Package | Step | PR(s) | State |
|---|---|---|---|
| A — Harness truth and startup | 01, 05 first context, 07 eligibility | #241, #243, #247 | `partial` — #241 verified; #243 and #247 have open P2 findings |
| B — Rendering correctness/reproducibility | 02, 03, 04 | #244, #248, #245 | `partial` — chain/sampler mechanisms verified at their heads; #248 findings open; authentic three-way control `blocked` |
| C — Autonomous lifecycle | 05 recovery, 06 | #246, #249, #250 | `partial` — all three heads have open P2 findings; per-session write roots not implemented |
| D — Verification, metrics, docs | 07 measurement, 08 | #251, #252 | `verified` technical evidence; paired efficiency pilot `blocked`; no independent approval |

## Dependency order

The only stacked implementation dependency at this query is #245:

- #245 base is `fix/harness-b1-compressed-textures` (#244), and its test module
  imports `automation.native_build`, delivered by #243 (the branch carries a
  content-equivalent commit `11a8c6be`). Integrate #243 and #244 before #245;
  #245 has no standalone CI because its merge base is not `main`.
- #246, #247, #248, #249, #250 and #251 are based on `main` and do not depend on
  one another for their focused checks.
- #252 is documentation-only. Its claims name the PR heads above; if one of
  those heads changes, refresh the affected row before merging #252.

## Exact code locations

Paths are relative to the named PR head; this documentation-only branch does
not contain the implementation files.

| Change | Location |
|---|---|
| Exact four-channel predicate | `automation/gfx_independent_replay.py: exact_comparison_statistics`, `final_comparison_report` |
| Canonical classification projection | `automation/gfx_independent_replay.py: classify_diagnosis`, `classification_outcome` |
| Agent compact/`--full` | `automation/agent_tools.py: eu4_replay`, `_replay_compact_view`, `_bound_replay_compact` |
| Graph writers/matrix | `automation/gfx_frame_graph.py: _producer_events`, `checkpoint_plan`, `provenance_matrix` |
| Native prerequisite/build split | `automation/native_build.py` (#243) |
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

| PR | Command | Result | Source |
|---|---|---|---|
| #241 | `test_runner run --profile offline --label a1 --module tests.test_m7_diagnosis ...` | 291 ran / 290 executed / 1 skip / 0 failures | recorded on `818e8360` |
| #242 | `--label a3 --module tests.test_gfx_frame_graph ...` | 236 passed | recorded on `6a52e005` |
| #243 | `--label a5-native --module tests.test_native_build ...` | 138 ran / 137 executed / 1 declared skip / 0 failures | recorded on `0982ece0` |
| #244 | `--label b1 --module tests.test_gfx_compressed_textures ...` | 34 ran / 33 executed / 1 skip / 0 failures | recorded on `b274eeb6` |
| #245 | `--label b6 --module tests.test_gfx_render_state ...` | 83 ran / 82 executed / 1 skip / 0 failures | recorded on `843a05cc` |
| #246 | `--label c2 --module tests.test_case_locator ...` | 145 ran / 144 executed / 1 skip / 0 failures | recorded on `ea204014` |
| #247 | `--label a4 --module tests.test_dsh_bridge ...` | 79 passed | recorded on `b3655fb8` |
| #248 | `--label b45 --module tests.test_m7_translation_cache ...` | 116 passed | recorded on `aeb4e97d` |
| #249 | `--label c1 --module tests.test_gfx_startup_trace ...` | 90 passed | recorded on `5270fc1c` |
| #250 | `--label c34 --module tests.test_campaign_guard ...` | 199 ran / 198 executed / 1 skip / 0 failures | recorded on `44e547cb` |
| #251 | `--label d1 --module tests.test_dsh_efficiency` | 42 passed | recorded on `7c95d804` |
| #252 | `test_runner run --profile offline --label d2-fix --module tests.test_dsh_docs --module tests.test_case_locator --module tests.test_pre_ready_forensic` | D2 final gate: see below | D2 |
| docs | `python3 tooling/common/check_doc_links.py --strict docs plans/harness-review` | D2 strict link check: see below | D2 |

D2 final gate: `20261120T165901Z-0b2ff070`: 45 ran / 44 executed / 1 skipped
/ 0 failures / 4.968 s child.
D2 strict links: `python3 tooling/common/check_doc_links.py --strict docs plans/harness-review` exited 0.

## Unresolved scientific blockers

1. **Live-GL control equality (finding 3).** `blocked`. The review's three-way
   table is not reproduced. A local, unmerged recomputation on
   `analysis/harness-904-three-way` (`9949f083`, based on `e71c15d6`) confirms
   the stored offline GL control still differs from live GL at checkpoint 15
   (5 px / max 1) and 16 (4,645 / max 86); all three images are the same seal
   and size, all four BGRA channels are compared. This is local analysis, not a
   merged record and not parity. Required next observation: the authentic
   present-904 sealed case with its original compressed blocks, a selected-draw
   original-API control with valid color and depth prestate, and all four BGRA
   channels compared pairwise. Do not substitute DDS assets, a decoded texture,
   a cleared depth buffer or a window drawable and call it a control.
2. **BC1 transparent-index rendering.** `partial`. #244 head `b274eeb6` adds a
   synthetic rendered BC1 alpha fixture (RGBA DXT1 samples alpha 0; RGB DXT1
   stays opaque and uses the decoded fallback) and a block-row flip fixture.
   The branch was inspected; the fixture was not run in D2 and is not merged.
   Authentic same-seal decoded-vs-block comparison on present 904, including
   alpha, is still missing. Next: that comparison on the 904 seal; do not infer
   it from the synthetic fixture.
3. **Winding.** `partial`. #245 head `843a05cc` adds a rendered front/back
   culling fixture. The branch was inspected; the fixture was not run in D2 and
   is not merged. The direct GL→Metal front-face mapping remains the validated
   historical choice. Next: run the rendered fixture on an eligible native host
   and compare it with an authentic front/back draw.
4. **Per-session write roots / plugin binding isolation.** `blocked`. Not
   implemented; concurrent sessions still share campaign-level binding and
   validation logs. #229/#230 cover publication/discovery only. Next: a
   two-session concurrent campaign test proving separate write roots and
   validation logs while shared case locators remain read-only.
5. **Native `function_for`/pipeline cache.** `blocked`. B4 covers the
   stage/translation/cache publication layer, not the native reader still keyed
   by file name plus entry. Next: a two-entry trace fixture with distinct
   function identities, then a cache audit showing no aliasing.
6. **Paired efficiency benchmark.** `blocked`. D1 makes the metrics honest but
   no three paired trials of the new workflow were run. Next: three paired
   cold/warm pilot trials with host, driver, power/display and cache state
   recorded; report unknown GPU time as unknown.
7. **Plugin systemPrompt reconnect context.** `partial`. A4 provides the
   launcher text and a read-only `context` operation; the goal plugin does not
   yet inject a systemPrompt section, and the anchor-drift finding is open.
   Next: a plugin test that resumes and asserts the verified context is present
   and a drifted sidecar is refused.

## Merged-state note

No implementation PR above is merged. No issue was closed by this matrix.
Issues #229–#234 remain `OPEN`; a later merge or closure needs its own record.
The protected tests, frozen evidence, reference authority and campaign safety
constraints are preserved; no historical evidence was deleted or relabeled.
