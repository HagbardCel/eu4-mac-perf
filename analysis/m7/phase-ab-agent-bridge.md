# M7 Phase A/B — v2 case dispatch and live portable packaging

Status: implementation and focused offline/native validation complete;
independent review and merge authorization pending. No automatic merge is
enabled.

Base: `origin/main` at `096c76ae` (PR #204). PR #205
(`m7/followup-portable-replay`, head `9c645d79`) is still open in the checked
out remote, so this branch fast-forwards `main` to that head and implements the
correction the task premise assumes was merged. The v1-only dispatch defect is
fixed here; the baseline is not a hypothetical hidden `main` commit.

Implementation commit: `53695757` (14 files). This report is a separate
documentation commit so the code identity remains stable.

## Objective

Complete the existing same-process OpenGL/Metal differential debugger rather
than add another renderer or orchestration layer:

1. Make the v2 ordered-prefix Strategy B case a first-class agent case.
2. Expose bounded M7 sequence execution, diagnosis, verification and replay
   through the existing tool core and the thin DSH plugin.
3. Make the production continuous shadow coordinator publish the same
   portable v2 reproduction that the offline supervisor already publishes.
4. Preserve transitive predecessor history, lifecycle records, per-frame
   translations, checkpoint requests/receipts and capture identity.
5. Fail closed on invalid, unsupported or incomplete evidence.

## Phase A — integration defects

### v2 Strategy B dispatch

`automation/agent_tools.py` previously compared `case.json.schema` only with
`gfx_replay_case.SCHEMA` (`gfx-strategy-b-case/v1`). A
`gfx-strategy-b-case/v2` ordered-prefix case therefore fell through to the
legacy image-comparison path.

The tool core now uses `gfx_replay_case.CASE_SCHEMAS` and treats both schemas
as Strategy B cases:

- `eu4_case_inspect` returns the v2 `sequence` summary, per-frame capture id,
  original sequence index, shader binding/digest, checkpoint count, dependency
  graph, diagnosis view, reproduction state and verification problems.
- `eu4_replay case` verifies before replay and dispatches to the existing
  `gfx_replay_case.replay_case` (which uses the existing
  `m7_continuous.run_campaign` / persistent Metal session path).
- `eu4_replay verify` verifies without executing Metal; the v1 behavior of
  omitting `--replay-out` remains.
- Containment mappings are explicit: `qualified_match` is the only mapping
  that can set `outcome.qualified`; `qualified_difference` is a complete
  diagnostic with verdict `different`; incomplete/invalid classifications
  produce `incomplete`/`invalid` and never a qualified result.
- A `CaseError` during replay becomes a structured failed envelope rather than
  an uncaught exception.

### Bounded M7 operations

`eu4_replay` now exposes three M7 operations in the same stable envelope:

- `sequence`: one bounded ordered sequence through
  `m7_continuous.run_campaign` (one persistent Metal session, per-frame
  translations, `--max-frames`, input `--max-bytes`, `--case-max-bytes`,
  `--max-seconds`, per-frame and overall timeouts).
- `diagnose`: `gfx_independent_replay.diagnose_frame` on already-produced
  Metal evidence, with `--require-seal`; no Metal execution and no EU4 launch.
- `verify`: `gfx_replay_case.verify` for v1/v2 cases.

`eu4_diff_run` remains the older two-process differential campaign and is not
used for M7. The Python docstring, CLI help, DSH tool description and
`docs/agent-tools.md` state that separation.

### Discovery

`automation/evidence_index.py` now records `case_schema` and `case_sequence`
for a discovered case, so a v2 ordered-prefix case is visible without opening
the payloads. Discovery remains bounded and read-only.

### Python → JavaScript → DSH bridge

`dsh/eu4-tools-plugin/lib/bridge.js` maps the new typed arguments
(`frame_dirs`, `metal`, `policy`, `require_seal`, budgets, package directory,
`continue_on_difference`) and `lib/index.js` exposes the new operation enum and
schema. No rendering or comparison logic was added to JavaScript.

## Phase B — live portable reproduction

### Per-frame translations

`m7_continuous.run_campaign` diagnosed every frame with the single top-level
`--shaders` directory even though the native sequence CLI prefers
`<frame>/shaders`. Diagnosis now resolves the same effective directory the
sequence host uses for that frame (`_effective_frame_shaders`) and records it
in the campaign row.

`gfx_replay_case.package_sequence_diagnosis` accepts an explicit
`frame_shaders` mapping for layouts where the translation lives beside the
frame's Metal output rather than under `<frame>/shaders`. Keys are normalized
after path resolution so `/var` and `/private/var` spellings cannot silently
miss a translation. Each selected frame still gets its own
`frames/NNNNNN/shaders` copy; the case-level `shaders/` remains the target's
translation and the sequence CLI fallback.

### Checkpoint preservation

`_checkpoint_setup` accepts the coordinator's published checkpoint sources. It
preserves a source request only when the request is byte-identical to the
deterministic request for the copied graph and the source receipt binds the
request digest, capture id and session. A stale or substituted request falls
back to the existing deterministic generation. A list of sources is remapped
through prefix selection, so a suffix prefix cannot bind a predecessor's
receipt to the wrong frame. v2 verification still rechecks the copied request
digest and receipt binding.

### Live continuous coordinator

`automation.m6_frame._package_continuous_case` packages the diagnosed prefix
from the live layout:

```text
run/frames/frame-NNNNNN              original captured frame
shadow/frame-NNNNNN/shaders          exact per-frame translation
shadow/frame-NNNNNN/checkpoint-request.json
shadow/frame-NNNNNN/shader-receipt.json
shadow/frame-NNNNNN/diagnostic-report.json
```

It calls the existing `package_sequence_diagnosis` with the per-frame shader
mapping, checkpoint sources and the target diagnosis. The coordinator invokes
it when it stops on the first nonqualified frame, at the frame limit, and for
the completed prefix when a frame preparation fails. The result appears in
`coordinator.json`, is copied into the capture summary by
`apply_shadow_report`, and carries `reproduction_complete` separately from the
renderer qualification.

The case budget is explicit (`--case-max-bytes`, default 512 MiB at the
coordinator and pass-through from `capture --continuous`). When the required
history does not fit, the existing v2 packager publishes a bounded
`reproduction.status=incomplete` summary with the required/max bytes and
missing dependency list, and no replay command. Required frames are never
dropped silently; incorrect or unavailable translations produce
`shader_artifact_unavailable` and are not claimed independently reproducible.

## Validation

Focused native/offline suite (the checked interpreter,
`/opt/homebrew/bin/python3`):

```sh
/opt/homebrew/bin/python3 -m automation.test_runner run \
  --module tests.test_agent_tools \
  --module tests.test_dsh_native_bridge \
  --module tests.test_evidence_index \
  --module tests.test_gfx_replay_case \
  --module tests.test_gfx_replay_case_sequence \
  --module tests.test_gfx_replay_checkpoints \
  --module tests.test_m6_frame \
  --module tests.test_m7_continuous_replay \
  --module tests.test_m7_continuous_supervisor \
  --module tests.test_m7_metal_replay \
  --module tests.test_dsh_profile
```

Result: **272 tests passed in 96.99 s** (run
`20261116T230654Z-beeb397d`). This includes real Metal replay on the available
macOS host.

JS plugin suite:

```sh
npm test --prefix dsh/eu4-tools-plugin
```

Result: **9 tests passed**, including argument mapping for `sequence`,
`diagnose` and `verify`.

End-to-end bridge regression
(`tests.test_dsh_native_bridge.NativeBridgeTests.test_native_v2_case_is_inspected_verified_and_replayed`):

- packages a three-frame heterogeneous-shader Metal case;
- relocates it and deletes the original campaign;
- calls the real DSH JavaScript bridge for `eu4_case_inspect`,
  `eu4_replay verify` and `eu4_replay case --replay-out`;
- all three reach the Python core and return `eu4-agent-tool/v2` envelopes;
- the replay preserves the diagnosis classification and target sequence.

Live-layout packaging regression
(`tests.test_gfx_replay_case_sequence.HeterogeneousShaderSequenceTests.test_live_layout_uses_per_frame_shaders_and_preserves_checkpoints`):

- removes all `<frame>/shaders` directories to emulate the live layout;
- packages with the per-frame shader mapping and checkpoint sources;
- checks each selected frame's program set (`P1`, `P3`, `P2`);
- checks each copied checkpoint receipt is byte-identical to its source;
- relocates, deletes the original campaign and output, verifies and replays
  to `qualified_match` with carried history.

Live coordinator regression
(`tests.test_m7_continuous_supervisor.LiveCoordinatorPackagingTests`):

- proves the packaging call receives all included frames, their own
  `shadow/frame-NNNNNN/shaders` directories and the corresponding checkpoint
  sources;
- proves incomplete reproduction is reported as incomplete, and a packager
  refusal is structured, not an exception;
- proves a stop on the first qualified difference publishes the case before
  the coordinator report is written.

### Authentic sealed three-frame capture

The existing follow-up A capture
(`.local/m7/followup-a-live-3`, session
`c66b3625-e06f-43f2-b957-61f2f14f66c2`, pid 27780, three sealed scene frames)
was cloned to a disposable directory and packaged without launching EU4. The
same transitive prefix closure selected all three frames because the target
carries six renderer-owned targets that the predecessors produce. Each frame's
own `shadow/frame-NNNNNN/shaders` translation and published checkpoint
request/receipt were preserved.

- 512 MiB budget: explicit `reproduction.status=incomplete`,
  `reason=artifact_budget`, required 1,310,375,043 bytes, max 536,870,912, no
  replay command.
- Complete case at 2 GiB budget: 1,317,958,286 bytes, 3,810 files,
  `case.json` SHA-256 `d7f648339e93593032e17d407fada4a047c824b1494b9fb3c7c9182cc3593929`,
  `reproduction.status=complete`, `independently_reproducible=true`.
- The case was relocated with hard links, the disposable original capture
  directory was deleted, and both the original and relocated directories
  verified with no problems.
- The relocated replay used one persistent Metal session for all three frames:
  15.2 s, campaign `complete`, `history.status=ok`, zero missing/stale history,
  274 Metal render-target bindings, 2,465 external-capture bindings and no
  seeded OpenGL intermediates.
- Live target and replay both classify as
  `difference_with_partial_state_coverage` with **identical statistics**:
  1,163,584 / 1,930,176 mismatched pixels, max channel error 72, mean absolute
  error `[0.400848, 0.520239, 0.656648, 0.0]`.
- The first authoritative divergence remains event 286 / texture 442
  (`glCopyTexSubImage2D`), with the unresolved interval 13–286 and the next
  checkpoint at the copy source (event 284).
- Packaging took 2.73 s; the case manifest is portable and contains no
  absolute capture path.

The agent surface was run against the relocated authentic case:

```sh
python3 -m automation.agent_tools eu4_case_inspect \
  --case .local/m7/phase-b-authentic-relocated
python3 -m automation.agent_tools eu4_replay verify \
  --case .local/m7/phase-b-authentic-relocated
```

Both returned `eu4-agent-tool/v2` envelopes with `ok=true`; the v2 schema,
three-frame sequence and target index were reported, verification was `ok` and
independently reproducible, and `outcome.qualified` remained `false` because
inspection/verification alone is not a rendering qualification.

Receipt: [phase-ab-authentic-receipt.json](phase-ab-authentic-receipt.json).
The complete relocated case remains under `.local/m7/phase-b-authentic-relocated`
(gitignored) for inspection and replay.

The full `unittest discover` suite was started but stopped when it reached
`tests.test_trace_tr3`, which prompts for an interactive live-EU4
authorization ("enter yes"). Phase A/B did not authorize or perform an EU4
launch. The focused suite above is the applicable native/offline gate; no
full-suite failure was observed before the interactive prompt.

## Reproducing a packaged case

```sh
python3 -m automation.agent_tools eu4_replay verify --case <case-dir>
python3 -m automation.agent_tools eu4_replay case --case <case-dir> \
  --out <replay-dir>
python3 -m automation.agent_tools eu4_case_inspect --case <case-dir>
```

A case produced by `capture --continuous --shadow --seal` is packaged under
`shadow/case` by default. Verification and replay never write into the
original capture directory.

## Limitations and unresolved cases

- No new EU4 process was launched in this phase. The authentic case above was
  built from an existing sealed follow-up A capture; the live coordinator
  wiring itself is covered by the synthetic live-layout and coordinator unit
  regressions, and the end-to-end bridge case uses deterministic Metal frames.
- The authentic target remains `difference_with_partial_state_coverage`
  because `gl_dither` and `multisample` are not implemented/proven. The
  acceptance here is an **unchanged differential verdict** after packaging,
  relocation and replay, not a qualified match. No threshold was changed and
  no A/B experiment attributes the difference to those states; Phase C owns
  that qualification.
- The older canonical prefix from the historical M6 capture still remains
  `incomplete_diagnostics` because that capture has no same-process frame seal.
  This phase does not synthesize a seal.
- The task premise said PR #205 was corrected and merged into `main`; in the
  checked-out remote it is open at `9c645d79`, and `origin/main` is
  `096c76ae`. This branch includes the PR head and the Phase A correction, so a
  future merge must preserve that ordering.
- No production replacement, Phase C qualification, Phase D diagnostic
  enhancement, Phase E autonomous correction or Phase F production PR is
  started. `gl_dither`, multisample and carried-resource coverage remain
  governed by the existing qualification policy; no threshold was changed.
- A genuine live capture should now be run at the Phase C gate:
  `capture --continuous --shadow --seal --max-frames N`, then inspect
  `shadow/case`, verify it after relocation, and compare the replay
  classification with the live coordinator's target classification.
