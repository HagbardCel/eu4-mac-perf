# Post-PR-#206 gap analysis

Status: preparation baseline. This document records what already satisfies
Phases C/D, what was missing, and what remains. It is not a merge authorization.

## Baseline

| Item | Value |
| --- | --- |
| Repository | `HagbardCel/eu4-mac-perf` |
| Merged baseline | `f13602e4eede1418e92dee1042ac4b857a239926` (`Merge pull request #206`) |
| PR #206 merged | 2026-11-17T18:46:16Z |
| Preparation worktree | `wt_m7_prep`, branch `m7/prep-post206`, created from `f13602e4` |
| Checked interpreter | `/opt/homebrew/bin/python3` (Python 3.15.2, NumPy 2.5.5) |
| DSH / Node / npm | DSH `0.2.2-rc.1`; Node `v26.13.1`; npm `11.20.0` |
| Ghidra | `/opt/homebrew/opt/ghidra` (headless exports available through `eu4_re_query`) |
| EU4 | `/Applications/EuropaUniversalisIV`, executable SHA-256 `b3d38876…141d` |
| Display | BetterDisplay `EU4 Test`, display id 12, 1920×1200, non-main; primary `Built-in Display` 1728×1117 @2x |

The checkout used for the preparation is clean apart from the commits described in
this directory. `main` itself was stale in the local repository; `origin/main`
was fetched and `f13602e4` is the merged #206 baseline.

## Existing capabilities that already satisfy the phase

- One-process, ordered continuous capture with an immutable `gfx-frame-trace/v1`
  per frame, a `gfx-frame-seal/v1` handoff and a `gfx-frame-sequence/v1`
  campaign record.
- In-process Metal shadow replay through one persistent session, with
  cross-frame resource history, lifecycle records, carried renderer outputs
  and per-frame checkpoint requests.
- Truthful qualification classification (`qualified_match`,
  `qualified_difference`, `difference_with_partial_state_coverage`,
  `incomplete_capture`, `invalid_provenance`, `unsupported_state`,
  `gpu_execution_failure`, `incomplete_diagnostics`).
- First-divergence localization with operation/event/program/resource
  identities, unresolved interval, unsupported-state list and a recommended
  next checkpoint.
- Portable v2 cases with per-frame shaders, preserved receipts, digest
  inventories, relocation verification and bounded case budgets.
- Agent surface: `eu4_capture`, `eu4_replay`, `eu4_case_inspect`,
  `eu4_re_query`, the Python core and the DSH tools plugin.
- Tiered validation: `automation.test_runner`, focused modules, full-suite
  comparison and the rule that authentic qualification is separate from CI.

## Gaps found and addressed in this preparation

| Gap | Evidence of the gap | Change | Regression |
| --- | --- | --- | --- |
| Engine-frame identity was sealed but not propagated to sequence records, campaign rows, diagnoses, cases or agent results. | The old authentic case had `present_index` 616/617/618 only in `frame-seal.json`; sequence and diagnostic rows had no engine index. | Added `present_index`/`scene_index` to native sequence/campaign records; propagated through validation, supervisor rows, diagnosis frame/divergence, case manifests and agent envelopes. | `EngineIdentityTests`; real capture above shows 615/616/617 everywhere. |
| Gap-free monitoring could not fail closed. | A skipped present index was not detected. | `validate_sequence` rejects non-consecutive or partially missing identities; `--require-engine-identity` refuses a sequence without them. | `test_a_skipped_engine_frame_fails_closed`, `test_partial_identity_fails_closed`, `test_missing_identity_is_unavailable_but_required_fails`. |
| No controlled later-frame divergence was available for qualification. | The historical sampler fault applied to every frame and could not prove frame/location reporting. | Added frame/event-scoped sampler fault switches (`EU4_FRAME_REPLAY_FAULT_FRAME`, `EU4_FRAME_REPLAY_FAULT_EVENT`) and `eu4_replay sequence --fault-frame/--fault-event`. | Native frame-scope test; authentic frame-2 semantic divergence at event 1; synthetic later-frame image test. |
| No same-backend GL/GL control existed. | The authentic first divergence was a copy, but copy execution was not independently controlled. | Added `automation.gfx_gl_control` and `eu4_capture gl-control`: compare the captured GL default-framebuffer source with the captured GL destination texture under a declared channel normalization. | `GlTransferControlTests`; authentic frame 1 gives 0/1,930,176, while frames 2/3 are unavailable because the historical capture lacks copy-only lifecycle coverage. |
| Divergence reports had frame-level statistics but no pixel location. | A frame-level mismatch cannot prioritize a region or a shader. | Added `first_differing_pixel` and `differing_bounds` to exact comparison statistics and to the first-divergence report. | `PixelLocationTests`; authentic report reports `(0,0)` and full-frame bounds. |
| The recommended checkpoint was missing when the copy source itself differed. | The first divergent checkpoint became the copy source (event 284), and `next_checkpoint` was `null`. | Added a bisection recommendation for the unresolved interval (event 142, target 0, `requires_capture=true`) and explicit limitations. | Authentic diagnostic report and compact package. |
| A coding agent had to parse a multi-megabyte diagnostic report. | `diagnostic-report.json` is ~3 MB for one authentic frame. | Added `gfx-agent-diagnosis/v1`, a bounded 6 KB projection written beside the full report. | `CompactAgentDiagnosisTests`; authentic frame produces about 6.3 KB with the final projection. |

## Remaining gaps (not fixed here)

1. **Full-frame GL/GL replay.** The GL/GL control proves the captured copy and
   readback path is exact; it does not re-execute all 325 operations through an
   OpenGL context. A full-frame GL/GL replay host would be a separate, larger
   component.
2. **`gl_dither` and `multisample` are still unsupported.** The authentic
   renderer is not pixel-equivalent. The pilot shows the residual is not a
   simple dither pattern and is not dominated by fast math, but this does not
   implement either state.
3. **Causal origin before event 284 is unresolved; the same-resource lower bound is unbounded.** The first
   authoritative divergence is the copy source (default framebuffer at event
   284). The next discriminating observation is a default-framebuffer
   checkpoint near event 142, which requires a new capture-side checkpoint.
4. **The live coordinator stops at the first difference by default.** The
   3-frame authentic sequence was replayed offline with
   `--continue-on-difference`; the live capture itself completed all three
   frames, but the coordinator only diagnosed frame 1. This is a policy choice,
   not a data loss.
5. **Full-suite status.** The focused suites pass. A full module run was
   attempted (`20261117T193147Z-12db1d13`) and deliberately stopped when it
   reached an interactive live-EU4 benchmark prompt; it is **not** a completed
   full-suite pass. `tests.test_trace_tr3` was already excluded because it
   contains a live authorization path. Treat the frame-counter/benchmark
   modules as live-capable and keep them outside the routine offline gate until
   their isolation is fixed. No EU4 process remained and the launch lease was
   free afterwards.
6. **Native DSH tool invocation in this session.** The plugin tests and the
   native bridge test pass, but this preparation session used the Python CLI
   through the DSH bash surface. The native tool list was not mounted in the
   session. This is a session-environment limitation, not a plugin failure.

## Authentic case budget

The 3-frame authentic case is **1,348,032,569 logical bytes in 3,811 files**.
The default 512 MiB case budget correctly publishes an explicit
`reproduction.status=incomplete`, `reason=artifact_budget`, no replay command.
The qualified configuration is:

```sh
--max-bytes 2147483648 --case-max-bytes 2147483648
```

No evidence is silently dropped and no global budget is disabled. The capture
root is outside CI (`.local/m7/phase-c-authentic-v2`, gitignored); compact
derived fixtures remain in `tests/`.

## Test baseline

- Baseline focused set (`test_agent_tools`, `test_m7_continuous_supervisor`,
  `test_gfx_replay_case_sequence`, `test_m7_diagnosis`): **232 passed, 1
  skipped, 0 failures** in 49.2 s (`20261117T184907Z-a54c1e2b`).
- PR #207 focused set: **419 passed, 1 skipped, 0 failures** (supervisor,
  continuous replay, agent tools, GL control, diagnosis, case sequence, native
  bridge, frame capture).
- PR #208 focused set: **328 passed, 1 skipped, 0 failures** (diagnosis, pixel
  diagnostics, agent tools, supervisor, continuous replay, case sequence).
- PR #209 final focused set: **492 passed, 1 skipped, 0 failures** across the
  capture, efficiency, agent, supervisor, replay, GL control, diagnosis, pixel,
  case and checkpoint modules.
- DSH tools plugin: **9 passed, 0 failed**.
- Known pre-existing/non-offline limit: `tests.test_trace_tr3` contains an
  interactive live-EU4 authorization path; it is excluded from the offline
  gate and is not reported as a new regression. The broader full run also
  reached an interactive frame-counter benchmark prompt and was stopped; no
  pass is claimed for that run.

The authentic capture and replay are recorded in
[`phase-c-qualification.md`](phase-c-qualification.md); the machine-readable
bundle is [`evidence.json`](evidence.json).

### Review remediation and cost controls

The review remediation is summarized in
[`review-remediation.md`](review-remediation.md). Cost controls applied here:
the capture default event budget is now 512 (the previous 256 default caused the
`draw_cap` failure), the compact diagnosis is bounded and selected causally,
the authentic case remains outside CI, and CI is checked with bounded
completion (`gh pr checks --watch`) rather than fixed sleeps. The full offline
run remains a separate gate with live modules excluded.
