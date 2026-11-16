# Current project status

Authoritative summary of what is established, qualified, and in flight. Operational commands live in [profiler and benchmark operations](reference/profiler-and-benchmark-operations.md).

## M7 diagnostic infrastructure (2026-11-15)

The M7.0 canary exposed where the offline diagnostic surface wastes an
autonomous agent's time. The infrastructure change keeps the Python core as the
single source of truth and the DSH plugin as a thin adapter:

- **One interpreter environment.** `run.sh`/`preflight.sh` resolve an absolute
  Python (a virtualenv entry point keeps its path), probe the selected
  interpreter by importing NumPy, and export it as `EU4_DSH_PYTHON`. Every tool
  envelope reports the executing environment and any configured mismatch.
- **Unambiguous outcomes.** `eu4-agent-tool/v2` separates execution, diagnostic
  completion, verdict, evidence validity and qualification; missing NumPy is
  `unavailable`, not success.
- **Focused inspection.** `eu4_session list` is active-only by default;
  `eu4_diff_inspect` accepts a campaign root and returns health provenance,
  correspondence, staticity, first discrepancy, missing evidence and
  category-specific next steps; `eu4_capture frames` honours exact frame/role
  filters with bounded pagination; health is reconciled across persisted and
  fresh evidence.
- **Temporal diagnostics.** `gfx_frame_compare.compare_temporal` compares
  reference N/N+1, candidate M/M+1 and reference N/candidate M with the existing
  decoder/metrics and reports mask overlap. Artifacts use exactly the pair and
  normalization behind their statistics.
- **Verification and discovery.** `automation.test_runner` runs focused modules,
  preserves exit codes, treats a running duplicate as pending, and only compares
  matching completed selections; `EU4_EVIDENCE_ROOT` indexes historical
  campaigns read-only across worktrees, including through the native bridge.
- `observation.capture_every_frame` fails closed rather than being ignored.

No EU4 launch was made and no Metal claim is involved. Reports:
[M7 diagnostic infrastructure efficiency](../analysis/m7/efficiency.md),
[verification](../analysis/m7/infrastructure-verification.md),
[native instrumentation follow-up](../analysis/m7/m7-instrumentation-followup.md).
Tool surface: [structured EU4 reconstruction tools](agent-tools.md).

## M7 PR 1 — authoritative same-process frame seal (2026-11-15)

The M6 tracer now publishes an opt-in, one-shot handoff for one authentic
frame. `EU4_GFX_FRAME_SEAL=1` writes `frame-seal.json`
(`gfx-frame-seal/v1`) after the GL reference readback, event-stream validation
and `gfx-frame-trace/v1` manifest publication. A single registered consumer is
invoked exactly once for a complete frame, outside the trace writer's mutex;
incomplete frames publish `sealed=false` and never notify. The descriptor carries
a process-monotonic capture id, session/pid identity, boundary, ordered
draw/clear/copy counts, dimensions, reference and manifest digests, readback
status and sealing cost. The existing title/menu `PresentScene` boundary is
finalized at entry (pre-swap) and works when `_GfxPresent` is skipped.

One authentic background launch (pid 16791) sealed capture id 1 at scene 617:
325 events (315 draws, 9 clears, 1 resolve), 1728×1117 reference
`cd0e7a16…`, one verified notification, empty stdout/stderr, clean launcher
termination and restored settings. Total sealing cost was 40.9 ms (readback
23.9 ms, finalize 17.0 ms including event-stream hashing); the consumer
callback took 18.6 ms. The seal records the event-stream SHA-256 (`31d03c73…`)
and byte count (6,392,559); the Python `load_seal`/`load_trace` consumer
re-verifies them plus every referenced blob. The historical
M6 `menu-occ300` frame still loads unchanged (326 events, `ff2f4e2b…`). PR 1
was developed on the PR #198 head `271cd04`; #198 is now merged as `b4c3955`
and PR #199 targets `main`. Report:
[PR 1 frame seal](../analysis/m7/pr1-frame-seal.md),
[evidence](../analysis/m7/pr1-live-evidence.json),
[implementation log](../analysis/m7/pr1-implementation-log.md). No Metal work
and no second EU4 process are involved.

## M7 PR 2 — in-process Metal shadow renderer (2026-11-15)

The Metal replay engine is now a reusable session-owned core
(`eu4_gfx_frame_replay.mm`) with a stable C ABI
(`eu4_gfx_metal_replay.h`), a thin standalone CLI
(`eu4_gfx_frame_replay_cli.mm`) and an opt-in in-process bridge
(`eu4_gfx_shadow_bridge.mm`). `EU4_GFX_SHADOW_MODE=single-frame` makes the
instrumentation dylib consume PR 1's sealed frame, wait for an external
on-demand shader translation, execute exactly that captured operation stream
through Metal offscreen, and write a shadow report. The original OpenGL frame
remains the displayed frame; no second EU4 process, GL/Metal interop or engine
draw re-execution is involved.

Offline, the refactored core replays the canonical title frame exactly
(326/326, `ff2f4e2b…`) through both the CLI and the C ABI, repeated sessions
agree, and the copy-bearing scene-75 case preserves its known divergence
(1,792,005 / 1,930,176, max 23). One authentic same-process launch (pid 92781,
capture id 1, scene 603) produced a GL reference `a7991870…` and an independent
Metal frame `0dad7bc6…`: 325/325 operations encoded, 0 failed, command status 4,
readback ok, one in-frame transfer, 274 Metal target / 2,465 external
bindings. The live frame is **not** pixel-exact: 1,162,852 / 1,930,176 pixels
differ, max 66, with `state_coverage=partial` for `gl_dither` and
`multisample`; the difference is spatially uniform and low-amplitude,
but is not proven to be exclusively dithering; no A/B with dither/MSAA
disabled exists. Report:
[in-process Metal](../analysis/m7/pr2-inprocess-metal.md),
[evidence](../analysis/m7/pr2-live-evidence.json),
[implementation log](../analysis/m7/pr2-implementation-log.md). PR 2 is rebased onto `main` after PR 1 (#199) merged as `67bf7a1`.

## M7 follow-up — native replay failure diagnostics (2026-11-16)

Every early `run_replay()` failure now publishes one structured result instead
of a generic `replay failed (code N)`. The C ABI remains v2; `error` starts with
a stable stage token (`manifest_load`, `event_stream_load`, `input_validation`,
`shader_load`, `attachment_initialization`, `event_boundary`,
`pre_image_upload`, `event_encode`, `checkpoint_readback`,
`frame_completion`, `frame_readback`, `artifact_write`, ...) and includes the
operation index, underlying error, real timeout classification and command
status. `replay.json` carries the same fields under `failure`/`failures`.

Malformed JSON and type-confused nested objects fail as `input_validation` (or
`internal_exception` in the defensive catch) rather than raising. Missing-error
paths in sealed verification now populate an `NSError`, and no diagnostic passes
a possibly null error string to `%s`. The shadow bridge reads the structured
failure and writes the native stage to `shadow-report.json`; the coordinator and
`capture-report.json` preserve the same stage/error instead of substituting a
generic message. Every report path (normal replay, receipt/session failure and
Objective-C exception) shares the sealed capture identity; a successful report
has `status=ok`, `stage=complete`, an empty error and no failure object, while a
caught exception keeps its original description under `internal_exception`.
The shader-ready wait is reported as `shader_wait`/`timed_out=true` at both the
report top level and in the structured failure; shader-receipt,
session-creation and exception reports are explicitly non-timeouts. The
earliest meaningful replay failure remains primary when a later boundary wait
or artifact write fails, and a failed replay does not advertise artifacts
left by an earlier run in the same output directory. Requested checkpoint
readback failures and `metal-frame.bgra` write failures are now failures rather
than silent success.

Successful replay output is unchanged: canonical title frame 326/326, 0 failed,
command status 4, `ff2f4e2b…`. Focused native tests cover missing/malformed
inputs and stage injection; no EU4 launch or in-process callback behaviour
change is involved. Report:
[native replay diagnostics](../analysis/m7/pr3-replay-diagnostics.md).

## M7 PR 3 — same-frame differential diagnosis (2026-11-16)

The replay engine and the diagnostic analysis are now separate. The reusable
`analyse_frame()` / `diagnose_frame()` path consumes an already-produced Metal
result and performs seal/capture validation, capture/session/operation identity
checks, dependency-graph construction, provenance evaluation, checkpoint
reconciliation, shader-binding audit, state-coverage classification, pixel
metrics, first-divergence localization and the qualification contract. The
standalone `replay()` API remains and now calls the same analysis after it
executes the host once; the normal diagnosis path does not launch EU4 or a
second replay.

The in-process coordinator publishes the checkpoint request before the shadow
bridge is released. `shader-receipt.json` binds the request name, SHA-256 and
count to the sealed frame; the bridge verifies them, passes `checkpoint_path`
to the existing C ABI, and the coordinator verifies the returned request
digest before diagnosis. Missing checkpoints are `incomplete_diagnostics`;
duplicate, unexpected, corrupt or identity-mismatched evidence is
`invalid_provenance`. Captured OpenGL intermediates remain reference
checkpoints only and are never seeded as Metal inputs.

State coverage now distinguishes implemented, captured-but-unsupported,
unproven and focused-test-proven equivalent states. `gl_dither` and
`multisample` remain unsupported; the report explicitly does not attribute the
live difference to them without an A/B experiment. The qualification contract
reports eight evidence axes and the disjoint classifications
`qualified_match`, `qualified_difference`,
`difference_with_partial_state_coverage`, `incomplete_capture`,
`invalid_provenance`, `unsupported_state`, `gpu_execution_failure` and
`incomplete_diagnostics`.

Focused verification: 150 tests in the graph/checkpoint/case/seal/coordinator/
ABI/diagnosis set, plus 8 production receipt tests and the canonical title and
scene-75 fixture tests through the real Metal host. The canonical title frame
remains pixel-exact (0 mismatches) with no reported divergence; its historical
texture-442 producer gap stays `incomplete_capture`. Scene 75 keeps
1,792,005 / 1,930,176 differing pixels (max 23) and localizes the earliest
authoritative divergence to the copy source checkpoint at event 271. The
original live in-process result (no checkpoint request) is
`incomplete_diagnostics`, preserving the 60.2% / max 66 final difference; one
explicit offline checkpointed replay localizes the earliest authoritative
divergence to texture 442 at checkpoint event 286 (copy producer 284,
1,237,771 checkpoint pixels, max 95) while stating that the copy source was not
captured. The portable `m7-pr3-live-603` case verifies 1,243 payload digests.
Report: [PR 3 diagnosis](../analysis/m7/pr3-differential-diagnosis.md),
[evidence](../analysis/m7/pr3-diagnosis-evidence.json).

PR #201 remains open; this branch includes its native early-exit diagnostic
hardening as a cherry-pick because PR 3 must preserve the first native stage
through the shadow report and the diagnostic package.

## Strategy B — independent replay (2026-11-14)

Capture once, replay independently. A canonical authentic OpenGL title frame
(326 events, 317 draws) is replayed through the existing M6 Metal host. The
replay now distinguishes external captured inputs from renderer-generated
resources: 274 bindings use Metal render targets and 2,485 use captured
assets/inputs; no captured OpenGL intermediate is seeded. The final output is
pixel-identical (0 / 1,930,176, SHA-256 `ff2f4e2b…`) and two consecutive replays
are deterministic (4.12 s each). Checkpoints for textures 431, 432, 443 and 447
match exactly; the GL-RGB8/Metal-BGRA8 checkpoint 433 is diagnostic.

The historical canonical capture (326 events, 317 draws) is **partial** in one
respect: texture 442 has no captured producer because that frame predates copy
recording. Current trace writers record resolve/copy events and replay executes
them rather than counting them as successful no-ops; the newer copy-bearing
scene-75 frame resolves texture 442 in-frame (see the final closure below). A
controlled V-flip fault localizes the earliest divergence in the canonical frame
to event 291 (producer 289, program 349). The historical `tex7` defect is
injected offline and the binding audit identifies the exact wrong engine unit at
event 14 (842 state mismatches) even though the title frame's terrain is
overdrawn and its pixels do not change. Replay outcome is reported as separate
`execution`, `coverage`, `pixels`, `semantic` and `qualified` fields; PR #190's
corrected draw semantics are merged into `main` through PR #191.

BetterDisplay `EU4 Test` (1920x1200, 1x, non-main) is created once and reused.
The shared-settings lifecycle is lease-ordered, with durable backup, rollback,
monitor coverage and field-level restore. Deterministic SDL call-site placement
(clearing the engine's requested fullscreen flags) fixed the window geometry:
**five consecutive OpenGL background launches passed** placement, focus,
frame progress and settings restoration. Metal background is likewise **5/5 qualified**: background
flags are propagated after clearing fullscreen and `ensure_visible_on_main()`
no longer activates or raises the window. Background remains opt-in.

Evidence: [Strategy B report](../analysis/infra/strategy-b.md). Tools:
`eu4_capture graph`, `eu4_replay independent`, `eu4_case_inspect` for Strategy B
cases, and `python3.14 -m automation.gfx_replay_case`.

### Final correctness closure (2026-11-15)

PR #191 merged the infrastructure closure to `main` as merge `7456a7c`; PR #192
then merged the P4 submission/draw-overhead work as merge `66f4178`, which is
the current `origin/main` base for this document. The performance changes
preserve the M6 acceptance and the replay contracts. The closure fixes the
startup launcher interface, makes the trace writer fail closed, applies one
scene-occurrence semantic to both boundaries, bounds the Metal copy kernel to
the requested rectangle, validates every clear flag combination against actual
pixels, and rejects missing, duplicate or zero-index-shifted consumer
provenance.

The closure also makes the final reference readback passive and deterministic
(one `read_drawable_bgra` path, explicit default framebuffer/`GL_BACK`, restored
bindings/read buffer/pack state/PBO, no error drain), makes interposer
resolution failure terminal instead of silently dropping the copy, shares one
copy contract between graph and replay, recognizes transfers in coverage, and
requires the Python JSON/blob/hash loader before a capture is accepted.

Texture 442 is now resolved **in-frame**, not left as an unexplained input. A
bounded copy trace and source capture produced a complete authentic menu frame
(scene 75, 283 events / 273 draws / 1 resolve). `_GfxResolve` copies the default
framebuffer (`GL_BACK`, 1728x1117) into texture 442 at event 271; events 273 and
277 sample it. The graph is complete with 0 unproven resources.

The replay executes the copy faithfully: GL source BGRA→RGBA equals the
captured GL 442 exactly, and Metal 442 equals the channel-swapped Metal default
source exactly. The first divergence is the **copy source** (target `0`, event
271, producer draw 270, program 346). The captured draws record `dither=true`
and `multisample=true`, which the Metal host does not implement; the source
difference is dominated by 1–2 LSB but includes 169,367 pixels differing by
more than 2 (max 105), so dithering is a hypothesis, not an established
exclusive cause. The copy-bearing
frame therefore reports `coverage=complete`, `semantic=match` (binding-audit
scope), `state_coverage=partial` (`gl_dither`, `multisample` unsupported),
`pixels=divergent`, `qualified=false` (1,792,005 / 1,930,176 final pixels
differ, max channel error 23). The canonical no-copy frame remains pixel-exact
(0 differences); scene 79 remains the four-alpha-LSB case.

One longer background OpenGL capture now reaches the title scene and completes
283 events with the window placed on `EU4 Test`, physical display unchanged,
clean termination and restored settings. A single sampled frontmost process was
`eu4`, so unfocused rendering was not independently re-proven in that long run;
the existing 5/5 OpenGL and 5/5 Metal short-run placement/focus qualification
remains the supported evidence. `strategy-b-metrics.json` records these
categories separately and is reconstructed from the session records.

Checkpoint evidence is now reconciled against the request: missing, duplicate,
unexpected, failed or identity-mismatched checkpoints are instrumentation
failures (CLI exit 1), never a qualified match. Copy-only resources are
validated from ordered transfer records rather than invented sampler bindings.
The semantic verdict is explicitly `semantic_scope=binding_audit`; unsupported
captured state (`gl_dither`, `multisample`) makes `state_coverage=partial` and
prevents full qualification. Non-default framebuffer source metadata is read
from the selected attachment under saved/restored binding state, with
unsupported attachment types failing closed.

Invalid execution, coverage, pixel, state or checkpoint evidence is now
classified `invalid` with exit 1; valid deliberate fault experiments remain exit
3, and `status=qualified` is emitted only with `qualified=true`. Binding
diagnostics preserve engine unit 0 instead of converting it to -1.

Transfer provenance now enforces the captured source category for every copy
(`metal_default` for the in-frame default framebuffer, `metal_render_target`
for generated textures, declared boundary categories otherwise). Captured
checkpoint formats are authoritative, so a reported RGB8 format cannot downgrade
an RGBA8 checkpoint to diagnostic. Replay/case output directories are
ownership-checked and overlap-checked before replacement, and evaluation
severity keeps `invalid` from being overwritten by `partial`.

The portable Linux workflow's unit-test dependencies were made deterministic
(mocked placement libraries, pinned identity fixtures, test-only `/proc`
identity on Linux) without changing production platform restrictions. The
exact GitHub command was run in an amd64 Ubuntu 24.04 VM with Python 3.14 and
Node 26; the previously failing modules pass. Scene-75's difference is
characterized quantitatively in `scene75-difference.json`: the first observed
difference is the default framebuffer before the copy, with dithering a
hypothesis rather than an exclusive established cause.

## Differential debugging infrastructure (2026-11-14, stacked PRs 1-5)

Five stacked branches implement the autonomous OpenGL-vs-Metal differential
harness on top of PR #189:

| PR | Branch | Delivered |
| --- | --- | --- |
| 1 | `infra/pr1-diagnostics` | capture validity (`valid`/`invalid`/`unverified`), aggregated GPU health gate, event-driven waits/profiles, owned-session isolation, atomic dual ledger claims, bounded content-addressed artifacts, GL pack-state/PBO restoration, window-capture qualification |
| 2 | `infra/pr2-dual-runner` | shared native per-frame observation module, bounded ring/dump protocol, scenario schema, concurrent dual runner with streaming and strict-lockstep modes, owned termination and evidence preservation |
| 3 | `infra/pr3-differential-compare` | semantic alignment and content veto, settle draw threshold, normalized pixel/region metrics, exact/tolerant/diagnostic policies, mirror detection, same-backend consistency experiments |
| 4 | `infra/pr4-first-divergence` | cross-API semantic/local resource comparison, ten synthetic historical regressions, bounded flight recorder, self-contained offline case package with analysis and replay |
| 5 | `infra/pr5-agent-tools` | `eu4_re_query` over existing extracts, one JSON tool core for the seven agent tools, native DSH plugin, efficiency metrics, operational defaults |

Evidence: [dual-process feasibility](../analysis/infra/dual-process-feasibility.md),
[dual-process campaigns](../analysis/infra/dual-process-campaigns.md),
[alignment and comparison](../analysis/infra/frame-alignment-and-comparison.md),
[first divergence and offline cases](../analysis/infra/first-divergence-and-offline-cases.md),
[agent integration and efficiency](../analysis/infra/agent-integration-and-efficiency.md),
[diagnostic integrity audit](../analysis/infra/diagnostic-integrity-audit.md).

**Established.** Two genuine pinned EU4 processes (authentic OpenGL and
reconstructed Metal) ran concurrently twice for 20 s with on-screen windows and
clean termination. A later campaign ran 60 and 80 paired lockstep frames with
bounded evidence and no false rendering verdicts after the loading-stage gate
was added. A recorded loading-stage comparison exposed a real correspondence
bug (identical caller composition at different loading progress); it is now a
regression and comparison is gated on `settle.min_draws` and target-phase
pairing. An **offline 100-frame campaign with a controlled mirrored-text defect
at frame 57** detects exactly that frame, builds a replayable case, and
reproduces it without an EU4 launch (`tests/test_offline_campaign.py`).

The real 100-frame **title-scene** demonstration is still open. Two live
attempts failed for different, now-understood reasons:

1. The host lease was held by the M6 agent's real EU4 process; the harness
   refused instead of interfering (intended coexistence).
2. With the lease free, the passive OpenGL observer sealed at its 1024-event
   quota after ~12 s and stopped emitting frame observations. A **frame-only
   observer mode** (`EU4_GFX_FRAME_ONLY=1`) now installs only the present/draw/
   clear hooks and bypasses the event-record quota; it streamed 20,000
   metadata frames at ~112 fps. A later long run streamed **35,234 frames over
   302 s** (draws 17-81, still loading) before the reference process was killed
   externally with SIGKILL; the historical M6 title evidence took about 5m40s
   from launch, so the 302 s run had not yet reached the title. The live title
   campaign is therefore not claimed; the remaining unknowns are the long
   reference loading time and the external kill. The frame cap is raised to
   120,000 observations, and phase readiness now also requires similar draw
   counts.

Metadata-only observation, phase-gated pairing, staggered launch and pre-requested
full-resolution windows are implemented and tested. A later real run did reach
110 paired observations, but every pair was correctly reported `ambiguous`
because the reference had 46 draws while the candidate had 273; no rendering
verdict was issued. Phase readiness now also requires the two draw counts to be
within `settle.draw_tolerance` (default 25%), and the frame limit is enforced
while pairing rather than after a whole batch.

**Qualification corrections (P0/P1).** Campaign execution and rendering
qualification are now separate. A `match` requires the requested number of
valid, passing pixel comparisons, clean GPU health and verified static-scene
correspondence; frame-limit completion alone is `incomplete`. Image correlation
is diagnostic only. Scenario controls that are not implemented fail closed.
Incremental health polling stops a campaign on the first rejected draw.
Lockstep timeouts invalidate synchronization. GL readback no longer consumes
pending engine errors. Ring dumps retain the selected frame's immutable
metadata, and capture requests use request IDs with explicit acknowledgements.
Region-specific policies (including text) now affect acceptance. See
[qualification contract](../analysis/infra/qualification-contract.md). The first GL/GL
consistency experiment (same Venice save, two processes) produced 20 pairs but
zero aligned comparisons: draw counts were 373 vs 361 and the caller signatures
differed. The save keeps running, so the processes are not at the same update;
this fails Experiment A and blocks GL/Metal qualification until an
engine-level update identity or a paused/static scenario is available. The observer now emits that identity from `CCurrentGameState::_pInstance` (date
block at `+0x1d18`, read with `vm_read_overwrite`), and alignment rejects unequal
identities; the day counter was observed live in a `--continuelastsave`
reference (`game_day=43808760` at frame 5). A repeat showed that hashing the full
date block is not deterministic across processes, so identity now derives only
from the day counter. An identity-gated GL/GL run aligned 20 pairs but produced
zero comparisons because per-pair requests overwrote the phase-entry capture
window; streaming now issues a single pre-requested window. A later Experiment A
run had equal day/update identities but only 13/20 pairs aligned because the
pair records omitted both identity fields; they are now carried and unit-tested.
Neither fix has been re-run live. Replaying the recorded 20-frame
observation window offline through the fixed alignment yields 20/20 aligned
pairs; the real window is a committed regression fixture. A new Experiment A run
then paired 20 frames but the same-day processes carried 241 vs 370 draws (34.9%
apart), so comparison was correctly refused. The day identity is too coarse for
a running save; a paused save or a finer tick identity is required. Replay tool
envelopes now distinguish successful replay execution from the reproduced
comparison outcome, and metrics count executable renderer reproductions
separately from image-comparison cases.

**Lease preflight.** The agent surface now exposes `eu4_session lease`; on the
current host it reports `busy`, holder worktree
`/Users/fabian/Projects/MiniProjects/eu4-mac-perf/eu4-mac-perf`, and the running
game PID, so a general agent does not attempt a futile launch while the M6
worktree is active.

**DSH tools mount.** A temporary dedicated DSH home
(`EU4_DSH_HOME=/tmp/eu4-dsh-tools-test`) provisioned both `@eu4/dsh-goal` and
`@eu4/dsh-tools`; `dsh plugin add link:` succeeded for both and the profile row
is present. `preflight.sh` reaches the plugin/runtime checks. The Node pin was
reconciled to the installed `v26.13.1` patch, so the earlier `v26.13.0`
mismatch is no longer an environmental failure. The effective `--dump-config`
contains the `eu4-tools` row and `name: '@eu4/dsh-tools'`. The live profile was
deliberately not mutated while the Web session and the M6 agent were active.

**Safety properties.** A rejected draw is detected on its first diagnostic
execution in about 4 s; command-buffer completion is never reported as pixel
correctness; owned cleanup signals only re-verified session identities; an
unrelated EU4 worktree cannot be terminated by a harness session; inconclusive
alignment is counted separately from a rendering difference.

**Open limits.** A per-frame update-state/scene fingerprint is still missing;
without it loading progress cannot be aligned generally. The live campaign
records frame payloads and health, not a per-draw operation prefix. The real
GL/GL and Metal/Metal consistency campaigns remain to be run. Headless DSH
tool discovery in the live Web GUI was not re-provisioned in this session; the
plugin and profile row are tested independently. The differential stacks are
merged PRs #189-#192, not local branches; the limits above still apply.

## Artifact ownership

One stage owns each artifact directory. Later stages read it; they do not
rewrite it, and a derived artifact never upgrades a capture's coverage claim.

| Stage | Producer / entry point | Owns | Never does |
| --- | --- | --- | --- |
| Capture | `eu4_capture` probe/request; the frame-trace writer | the run/session directory and the immutable frame trace, blobs and reference | rewrite a captured frame, synthesize a missing producer, or count a failed event as captured |
| Seal | the frame-trace writer with `EU4_GFX_FRAME_SEAL=1` | `frame-seal.json` and the single completion notification | notify for an incomplete frame, notify twice, or rewrite a sealed artifact |
| Graph | `eu4_capture graph`; `automation.gfx_frame_graph` | derived `graph.json` and its coverage/provenance report | resolve a resource the capture did not record |
| Replay | `eu4_replay independent` / `case`; `automation.gfx_independent_replay`, `automation.gfx_replay_case` | the replay output directory and its readbacks | overwrite the capture or an existing case, or report command-buffer completion as pixel correctness |
| Live comparison | `eu4_diff_run` and `eu4_diff_inspect` | the campaign session/run directory, paired observations, health, first divergence | terminate an unrelated process, or turn inconclusive alignment into a rendering verdict |
| Offline case | `eu4_diff_inspect --build-case`; `gfx_replay_case package` | one self-contained, relative-path, digest-verified case directory | copy an unverified or partial frame as a complete case |

The tool-level commands and envelopes are in [structured EU4 reconstruction
tools](agent-tools.md).

## Local evidence prerequisites

The canonical frame (`.local/m6/frames/menu-occ300/frame`), the portable replay
case, and live session/run directories live under `.local/`, which is
gitignored. A clean clone and CI do not contain them. A committed report is the
historical record of a run; it is not a substitute for the bytes.

Check a prerequisite before citing or replaying it:

```sh
test -f .local/m6/frames/menu-occ300/frame/frame-trace.json
python3.14 -m automation.gfx_replay_case verify --case <case-dir>
```

When a path is absent, report it as absent. Do not reconstruct it from a report,
and do not treat missing local evidence as a passing result.

### M7.0 correspondence canary (2026-11-15)

One bounded offline investigation of the GL/GL Experiment A evidence established
why metadata-aligned paused frames are not trustworthy: the preserved frame-74
pair matched on day, `update_identity`, caller signature, draw count and extent,
yet differed on 1.35 % of pixels, while the same process's consecutive frames
differed by 3.7 % and 79.7 %; 92 % of the cross-process differing pixels also
change between two consecutive reference frames, and the candidate pause
released ~2 s after the request. Both recorded `rendering_difference` verdicts
were emitted with `stability=weak` and `render_stage_proven=false`.

The correction requires verified static-scene evidence before a pixel difference
is attributed to a renderer (otherwise `incomplete`), and the same evidence for
a `match` on **every** requested comparison: `settle.stable_frames` consecutive
identical full-resolution digests whose run contains the compared frame, from
valid dumps that belong to the campaign session. A passing local comparison in
a packaged case is reported as local equality; the case can report `match` only
when its source campaign is a qualified `match`. Engine day, update
identity, caller composition and draw count are correspondence evidence and
never establish staticity. The same predicate
(`automation/gfx_qualification.py`) gates campaign and offline case
classification, requires healthy (not `unknown`) GPU execution, and the pixel
path may not promote a pair the sequence aligner rejected. Stop-time neighbour
frames make staticity observable without another campaign. No EU4 launch was
made and no Metal claim is involved. Report and validation:
[M7.0 starter canary](../analysis/m7/starter-canary.md),
[validation](../analysis/m7/m7-validation.md).

`live-a6` and `live-a10` are therefore reclassified `incomplete`; the measured
differences are unchanged. The remaining unknown is the mechanism of the
residual border/HUD difference, which needs a per-frame full-resolution digest
(or an implemented `capture_every_frame`) and a per-draw operation prefix.

## M6 — authentic title scene through Metal (accepted 2026-11-14)

The authentic title scene renders through Metal and passes the three-start
qualification, including the new Credits/Options text-orientation check.
Evidence: [qualification report](../analysis/m6/qualification-report.md),
[frame evidence](../analysis/m6/menu-frame-evidence.json),
[text orientation](../analysis/m6/texture-orientation.json),
[live acceptance](../analysis/m6/live-acceptance.json),
[live starts](../analysis/m6/live-title-starts.json),
[campaign record](../analysis/m6/campaign-record.md).

- **Canonical frame.** The corrected menu-caller occurrence-300 capture
  (`.local/m6/frames/menu-occ300/frame`) has 326 events, reference SHA-256
  `ff2f4e2b…`. `menu-15` (observer unit-0 corruption) and `menu-audit`
  (undirected) are superseded and are not pixel references.
- **Offline captured-state replay.** 326/326 events encoded, 0 failed, GPU
  completion, 0 differing pixels of 1,930,176 including alpha. This is a
  captured-state replay; it does not independently reconstruct the upstream
  terrain pass. A synthetic capture-format/replay regression now covers a
  sparse 16-bit index list with a nonzero signed base vertex and a nonzero
  captured `vertex_origin` (`tests.test_m6_frame.SparseIndexReplayTests`). It
  builds a frame-trace document in the captured format and does not exercise
  the OpenGL capture writer; the historical canonical frame's origins are zero,
  so it does not exercise that path either. A negative case omits the origin and
  proves the replay cannot fetch the captured triangle while still reporting a
  successful command.
- **Text root cause (fixed).** The Credits/Options labels are directly sampled
  render targets. GL stores render-target row 0 at the bottom; Metal stores it
  at the top, so the engine's GL bottom-up UVs reflected the label. Live vertex
  data matches GL exactly, the 168x63 Metal textures match the GL contents in
  the same orientation, and the normal-upload controls (1537x192, 111x111)
  sample correctly. `eu4_gfx_prepare_sampled_textures` now converts each bound
  render target into a cached GL-row shadow, encoded into the draw's own
  command buffer; ordinary textures, resolves and sprite UVs are untouched.
  The native regression
  `tests.test_gfx_live_draw::test_sampled_render_target_orientation` fails with
  the raw-render-target path and passes after the correction.
- **Live acceptance policy.** The checker now requires a tight-glyph
  orientation check for both labels (upright correlation high, reflected
  correlation low). The map structure check is calibrated against the authentic
  reference spread instead of a fixed floor that rejected the reference itself
  (reference spread 18.1; the old 20.0 floor was miscalibrated). The UI,
  palette-affinity (0.85) and map coherence requirements are unchanged.
- **Earlier live contracts (retained).** Offscreen targets use their own
  extent; `_GfxResolve` flips into the GL lower-left row convention; the engine
  base/mip filters are decoded separately; the shader-texture-LOD extension is
  advertised so the authentic 78-source variant is used; samplers named `texN`
  bind to unit N; the settle gate requires 252 terrain calls.
- **Live result.** Three fresh independent settled starts on the finalized
  implementation: zero refused draws, no failed resolves, GPU completion,
  presentation, UI correlations 0.882/0.946/0.836, map mean 72.2, palette
  affinity 0.968, and Credits/Options upright with the reflected correlation
  below the upright correlation. The earlier beige-map, magenta-palette,
  partial-composition and inverted-text blockers are closed.

## M4 — original-GL reference and Metal presentation (2026-11-12)

Qualified by the M4 checker. The authentic Simple loading draw was captured
from the original OpenGL renderer, the corresponding reconstructed Metal
output is pixel-identical under a fixed exact policy, and the reconstructed
frame is independently observable in the real EU4 window.
[Qualification report](../analysis/m4/qualification-report.md),
[checker](../campaigns/eu4-m4/accept.py), [policy](../analysis/m4/comparison-policy.json).

- **Original-GL reference.** `_GfxDraw` at caller `0xedfcd1`, mode 5, 4 vertices,
  1728×1117 BGRA8 from `GL_BACK`, `glFinish`+`glReadPixels` clean, raw SHA-256
  `69d41a88…`; an independent launch produced the same hash. The live slot-0
  vertex payload is captured and bound to the draw.
- **Reconstructed Metal output.** The live route renders the same draw at the
  engine's logical extent with the same encoder and the same live vertex
  payload (`f13dea22…`), GPU-completed (raw SHA-256 `c668b054…`).
- **Comparison.** `automation.gfx_compare` reports `pass` with 0 differing
  pixels out of 1,930,176; the only normalization is the declared lower-left to
  top-left row decode.
- **Presentation.** `presentDrawable` completed, the destination drawable
  contains the image (`6921c242…`), and the main-runloop evidence hold lets
  Core Animation commit. An independent window-server capture of the EU4 window
  (`window-21765.png`) shows the loading frame with mean |Δ| 1.311/255 against
  the drawable.
- **Checker.** The M4 checker fails closed on missing reference, GPU,
  presentation, provenance or regression evidence and pins the evidence manifest
  digest. It recomputes screenshot/drawable similarity and validates the
  after-present checkpoint; its verdict on the recorded evidence is `pass`.
- **Readback safety.** The original-GL observer saves/restores pixel-pack state
  and rejects unsupported framebuffer configurations; the final live capture
  records `restored=true`.

## M5 — live sprite draw and composition (2026-11-12)

The live Metal-backed EU4 process now reaches the next authentic original draw
after the M4 Simple text: the loading-screen sprite at `_GfxDraw` caller
`0x14f6c1b` (`CSpriteType::RenderInternal + 2271`). The probe records the
authentic engine invocation, admits `sprite-map/asset-maincode-v0` through the
real shader path, encodes it through the shared dispatcher/sprite encoder, and
completes on the GPU against the engine-owned 3456×2234 backbuffer
(`command_status=4`). The live inputs match the original: 80-byte stride-20
vertex buffer, 96-byte uniform prefix, 1235×175 texture, linear/clamp sampler
and alpha blend. The live prefix composes the frame's Simple draw and the
sprite and produces the same 157,254 changed pixels as the GL pre/post pair.

Three original entries were reconstructed to reach the draw: `_GfxBeginScene`
(preserve the current-shader clear, default blend application and return 1,
skip the GLEW attrib-disable branch), `_GfxClearTexture` (release the backend
texture and preserve header mutations), and the sprite blend descriptor
(admit the measured alpha blend). The exact seeded offline replay remains the
pixel qualification. The live prefix pre-draw still differs from the original-GL
pre-draw because the loading-screen background producer is unreconstructed, so
live composed pixel equivalence is reported separately and is not claimed. See
[the M5 qualification report](../analysis/m5/qualification-report.md),
[target](../analysis/m5/target.md) and [checker](../campaigns/eu4-m5/accept.py).

## Latest milestone — authentic Simple draw on Metal (2026-11-12)

Merged in [PR #185](https://github.com/HagbardCel/eu4-mac-perf/pull/185). Evidence: [live Simple draw](../analysis/reconstruction/simple-loading-draw/live-evidence.md); reusable workflow: [live graphics experiments](live-gfx-experiments.md).

- **Authentic engine invocation.** The pinned GOG EU4 1.37.5 x86-64 executable reached `_GfxDraw` at caller RVA `0xedfcd1` (`CEU3Graphics::ShowBasicLoadText`). The recorded status is `draw_reached=true`.
- **Metal draw encoding and GPU execution.** The shared Simple encoder accepted the admitted shader, vertex, constant, texture and viewport state; the command buffer reached `MTLCommandBufferStatusCompleted`. The recorded status is `draw_encoded=true` and `gpu_completed=true`.
- **Pixels captured from the engine-owned backbuffer.** The checkpoint read back the 3456×2234 `BGRA8Unorm` target (30,882,816 bytes). The same raw SHA-256 was produced by independent launches; `pixels_captured=true`.
- **Presentation not demonstrated by this checkpoint run.** The probe stops at the draw, before later font/UI drawing and before any present. `presented=false`.
- **No original OpenGL reference equivalence yet.** No authentic original-OpenGL image exists for this checkpoint, so `reference_matched=null`. An independent CPU bilinear reference from `load_0.dds` matches the captured pixels on 23,808 samples, but that is not an OpenGL capture.

This milestone does **not** claim a fully rendered or presented game frame, and it does not establish production submission or resource-lifetime behavior.

The sections below record earlier evidence and remain as history; they do not override this milestone.

## Closed

- **Phase C intrusive profiling and forensic investigation** — complete. Do not repeat live intrusive capture (`run --diagnostic-only`) merely to collect more diagnostic data.
- Sealed record: [Phase C capsule report](../results/20261003T210202Z-intrusive-diagnostic/report.md). Offline verification: [`analysis/frame-model-verification.md`](../analysis/frame-model-verification.md).

## Still usable (within qualification limits)

- **R-mode** segments and structural evidence from the Phase C capsule support baseline and contract interpretation.
- **C-mode** absolute timings are **not** quantitatively qualified for production claims (same tier as post-WP11 / Tier-1 admission failure).
- **Tier-1 / WP11** (`33339073`, failed v4 admission): profiler absolute timings are not production-qualified. See [live diagnostic measurement contract](live-diagnostic-measurement-contract.md) and [post-WP11 diagnostic roadmap](eu4-live-diagnostic-roadmap.md).

Future **targeted** measurements (including further R0-M work) remain possible when separately justified; this does not reopen the Phase C investigation.

## R0-M integrated session (`20261012T210806Z`) — partial only

[Results index](../results/INDEX.md), [validation](../results/20261012T210806Z-r0-measurement/validation.md), [R0-M protocol](r0-measurement.md):

- CPU reconciliation obtained in epoch A.
- Drawable census **not evaluated** (`unsupported_drawable` readback policy).
- Automated stack attribution failed on an unsupported `sample` formatting variant.
- Acquisition status **`incomplete_gates`**; frame staticity and lossless-skip candidates were **not** evaluated.

## Current technical direction

- **L3.2b-II fail-closed completion infrastructure (2026-11-09).** The completed L3.2b-I checker is frozen by its manifest; the new immutable revision is `automation/gfx_draw_accept_v2.py`. It classifies every required draw input as `validated`, `provisional`, `missing` or `invalid`, so an established field without a trusted validator can never pass. Structurally valid evidence for a provisional field produces a durable checkpoint bundle under the workflow root; a partial checkpoint is a qualification work order, not L3.2b acceptance. Contract versions are proposed in a separate campaign, executed in a separate process, and reviewed by an independent reviewer with field-specific adversarial cases. The coordinator `automation/gfx_l32b_complete.py` chains checkpoint validation, snapshot, qualification, review, integration and continuation; the completion campaign uses two identities (`candidate_base_commit` and `trusted_contract_commit`) and one global launch ledger. The bootstrap campaign is [campaigns/eu4-l32b-bootstrap](../campaigns/eu4-l32b-bootstrap/) with the frozen acceptance suite `tests/test_gfx_infra_frozen.py`; the completion template is [campaigns/eu4-l32b-complete](../campaigns/eu4-l32b-complete/). No EU4 launch, Metal workload or pixel claim is made by the infrastructure. See [decision 0004](decisions/0004-l32b-autonomous-completion.md).
- **Live operation capture and Metal replay (2026-11-08).** `python3 -m automation.live_gfx capture` produced one authentic original-EU4 `_GfxSetViewPort` capture (session `f975f721-ec09-4a57-87b3-4e7a53fb3243`, pid 43379, seven invocations, no overlap, complete integrity). The first invocation is the `CGraphics::InitGfx` call at `0x149b21a` (return RVA `0x149b21f`); the other six are the `CGraphics::Setup2dGraphics` callsite at `0x149d046`; all run on the init thread. The unchanged `gfx-startup/v0` context is paired with the new operation sidecar at [analysis/gfx-ops/v0/capture](../analysis/gfx-ops/v0/capture) ([note](../analysis/gfx-ops/v0/note.md), [contract](../analysis/gfx-ops/v0/comparison-contract.md)). `python3 -m automation.live_gfx replay` executes the captured initialization and all seven operations through the existing Metal shadow worker; all six viewport words match for every call. A deliberate swap in `eu4_gfx_set_viewport` produced a precise `viewport[0]`/`viewport[1]` mismatch (original 1117/1728, Metal 1728/1117), and the restored implementation passed again. A pass establishes only `function_cpu_state` for the captured invocations, not GPU state, pixels, or complete engine-path equivalence. Worker failure, missing evidence, unsupported ordering, and declined qualification are distinct verdicts and never become a false pass.
- **Authentic Simple draw probe (2026-11-08).** One operator-authorized passive original launch used `python3 -m automation.live_gfx capture --target _GfxDraw --target _GfxSetPrimitiveType --until-target-draw --caller-rva 0xedfcd1 --timeout 120 --authorize-live` (session `d5af7baf-cc1e-4def-a5f4-96e1d5325641`, pid 34926). The first retained `_GfxDraw` is `call_id` 4 at caller `0xedfcd1`, with mode 5, incoming `esi` 0, first 0, effective count 4 from `master+0x64`, a non-null shader, and a preceding `_GfxSetPrimitiveType(4)` at `call_id` 3. This confirms Decision 0003's predicate on the passive original path; the earlier observed zero is consistent with the Metal draw shim, not the original loading screen. The capture is [analysis/gfx-draw/v0/probe/capture](../analysis/gfx-draw/v0/probe/capture), the packet is [analysis/gfx-draw/v0/probe/draw-packet.json](../analysis/gfx-draw/v0/probe/draw-packet.json), and the note is [analysis/gfx-draw/v0/probe/note.md](../analysis/gfx-draw/v0/probe/note.md). The packet is `incomplete`/`packet_ready=false`: it establishes the draw invocation, primitive semantics, viewport, default render target and structural vertex bindings, and explicitly reports shader identity/sources, vertex contents/layout, texture/sampler, constants, render state and scissor as missing. No Metal workload ran and no pixels are claimed.
- **Agent-driven L3.2b-I effective state (2026-11-08).** One OpenCode campaign (`eu4-l32b-effective-state`, model `opencode/big-pickle`) ran from the L3.2a merge `1b5a82a`, passed the new trusted draw checker on its first round, and used one authorized capture of three. The fresh capture (session `9a8a50c4-c42c-40da-b3db-9472820ce477`, pid 44927, observer `0f53b00e…`) has the selected `_GfxDraw` at caller `0xedfcd1`, `call_id` 11, mode 5, effective count 4. It establishes `vertex_buffer_bindings`, `vertex_buffer_contents` (`vb_3`, 96 bytes, stride 24) and `matrix_constant` (`cb_1`, slot 0, 64 bytes) from same-session evidence; shader/texture/sampler/attribute-layout/render-state/scissor inputs remain explicitly missing and `packet_ready=false`. The candidate is [analysis/gfx-draw/v0/l32b/effective-state](../analysis/gfx-draw/v0/l32b/effective-state/note.md); the campaign/checker are [campaigns/eu4-l32b-effective-state](../campaigns/eu4-l32b-effective-state/) and [automation/gfx_draw_accept.py](../automation/gfx_draw_accept.py); the manual review sequence is documented there. The initial review (`r1`) was superseded after the checker was corrected to bind matrix/vertex/evidence bytes to the original capture records; the existing capture was re-validated in the original pinned workspace without relaunch (`requalification.json`, final manifest `f3c87a4a…`, packet unchanged), and a fresh independent review (`r2`, `opencode/iceman-free`) returned `qualified`. No Metal workload ran.
- **First live Metal draw (2026-11-02).** [Decision 0002](decisions/0002-first-live-metal-draw.md) keeps 0001's Metal-only execution order and changes the next runtime target from a presented frame to the first eligible game-owned `_GfxDraw*`. [Decision 0003](decisions/0003-d1-draw-selection.md) selects the first `_GfxDraw` at caller `0xedfcd1` with a complete master, a non-null shader, mode 5, and an effective count of at least 3. `results/20261103T062113Z-metal-init` is pid 93416: init and `_GfxCreateVertexBuffer` succeeded, and the format-5 `_GfxInitializeTexture` stop at `0x15ec663` did not recur. The crash file `eu4_20261103_072119` records `SIGSEGV` at address `0xbc6a0ce4bf4c2646` with the EU4 frame at the return from `call _GfxCreateIndexBuffer` (RVA `0x14bfa60` in `CreateIndexBuffer`), reached from `CPdxMeshType::InitForDevice`. The first indirect call in `_GfxCreateIndexBuffer` is `callq *(%rax)` at RVA `0x15ec1a9` through `___glewGenBuffersARB`. The launcher deadline then killed the process. There is no draw receipt. `d1` is `not_started`. It is not a Decision 0003 candidate, and it is not `packet_ready`. `results/20261102T223946Z-metal-init` is pid 72943: init and `_GfxCreateVertexBuffer` succeeded, `_GfxSetDepthStencilState` returned at `0x149d061`, and the rasterizer tail jump at `0x149d07d` did not stop the process. The same thread then stopped on `_GfxInitializeTexture` at return RVA `0x15ec663` in `_GfxCreateTexture`. `blocked_at.json` records branch `compressed`, format 5, kind 0. Exit 2, launcher did not signal. There is no draw receipt and no `depth_state.json`. `d1` is `blocked_at`. It is not a Decision 0003 candidate, and it is not `packet_ready`. `results/20261102T221329Z-metal-init` is pid 69436: init and `_GfxCreateVertexBuffer` succeeded, the draw shim returned, and the same thread stopped on `_GfxSetDepthStencilState` at return RVA `0x149d061` in `CGraphics::Setup2dGraphics`. Exit 2, launcher did not signal. There is no draw receipt. `d1` is `not_started`. It is not a candidate. `results/20261102T213704Z-metal-init` is pid 62930: the same caller `0xedfcd1` reached `_GfxDraw` with a captured shader pointer `0x7f77e3c3a5d0` and `effective_count` 0. The master read is complete. `rsi` is 0 and the dword at `+0x64` is 0, so the tail would call `_glDrawArrays` with mode 5, first 0, and count 0. `d1` remains `draw_reached`. Invocation 1 is `draw_nonvisible` because the count is 0. This session does not show a later positive count at that callsite. D1b is not `packet_ready`. `results/20261102T210315Z-metal-init` is pid 59605: init and `_GfxCreateVertexBuffer` succeeded, the texture stub returned, and the same thread stopped on `_GfxDraw` at caller RVA `0xedfcd1` in `CEU3Graphics::ShowBasicLoadText`. Exit 2, launcher did not signal. That is `draw_reached`. The receipt records the original call. The shader failure returns, the clear stub, and the texture stub did not issue a GPU draw. `results/20261102T202215Z-metal-init` is pid 56537: init and `_GfxCreateVertexBuffer` succeeded, the `_GfxClear` stub returned, and the crash file records `SIGSEGV` at address `0xc` with the EU4 frame at the return from `call InternalSetTextureAndSamplerState` (RVA `0x15ecc72` in `_GfxSetTextures`). The launcher deadline then killed the process. That is not a draw. `results/20261102T200328Z-metal-init` is pid 53881: init and `_GfxCreateVertexBuffer` succeeded, the pixel-shader failure return continued, and the same thread stopped on `glScissor` at RVA `0x15ea5ec` in original `_GfxClear`. Exit 2, launcher did not signal. That is `blocked_at`, not a draw. `results/20261102T194105Z-metal-init` is pid 51420: init and `_GfxCreateVertexBuffer` succeeded, the vertex-shader failure return continued, and the crash file records `SIGSEGV` at the return from `call _GfxAddPixelShader` (RVA `0x15eb844`). The launcher deadline then killed the process. That is not a draw. `results/20261102T171241Z-metal-init` is pid 47765: init and `_GfxCreateVertexBuffer` succeeded, `BuildGLSL` returned, and the crash file records callee address 0 at the return from `call _GfxAddVertexShader` (RVA `0x15eb3e5`). The launcher deadline then killed the process. That is not a draw. `results/20261102T163915Z-metal-init` is pid 44393: init and `_GfxCreateVertexBuffer` succeeded, `_GfxCreateShader` returned an object with program name 0, and the same thread stopped on `glGetString` (`0x1F00`, `GL_VENDOR`) at RVA `0x15eff24` in `BuildGLSL`. Exit 2, launcher did not signal. That is `blocked_at`, not a draw. `results/20261102T161213Z-metal-init` is pid 41936: init and the vertex-buffer receipt succeeded, then a 3-second `sample` found the main thread blocked in `AbortHandler` waiting on the crash reporter. The sample shows callee address 0 and the crash-reporter wait, with `CreateEffectFromFileNew` at RVA `0x1511e8d` above the signal. The binary identifies that as the return from `_GfxCreateShader`; `callq *(%rax)` at RVA `0x15eaeb9` loads the qword at `___glewCreateProgramObjectARB`. The launcher deadline then killed the process. That is not a draw. A draw receipt counts only when the caller is the pinned executable and the launcher stop matches it.
- **Semantic reading, then Metal (2026-11-01).** The reading of the frozen 92 `_Gfx*` functions is recorded ([decision 0001](decisions/0001-semantic-gfx-then-metal.md)). The Metal `_GfxInit` backend for the six entries is implemented and the offline host tests pass ([note](../analysis/metal-init/pr3/note.md)). Live `_GfxInit` integration is recorded in `results/20261101T220023Z-metal-init`: init succeeded, then `glGetIntegerv` at RVA `0x15e9e20` on the init thread, and the process exited 2 without a launcher signal. PR4 replaces `_GfxReset` and `_GfxOnResetMasterContext` ([note](../analysis/metal-init/pr4/note.md)). The offline host tests pass. `results/20261101T224903Z-metal-init` `stop.json` is not the game. `results/20261101T233006Z-metal-init` is pid 76944 under LLDB: init succeeded, no `stop.json`, and the fault stop is `EXC_BAD_ACCESS` at pc 0 with return address RVA `0x15ebd02` in `GfxCreateVertexBuffer`, called from `CPdxParticleObject::OnResetDevice`. `AbortHandler` was resolved and not hit. `metadata.json` there is reconstructed evidence (`status` `evidence_only`), not a launcher result. That is verified continuation and a later fault, not a GL diagnostic and not a successful completion. The run is one operator-authorized `metal_init` session, not a CI job. PR5 replaces `_GfxCreateVertexBuffer`, `_GfxUpdateVertexBuffer`, and `_GfxDestroyVertexBuffer` ([note](../analysis/metal-init/pr5/note.md)). The offline host tests pass. `results/20261102T072350Z-metal-init` is pid 5979: init succeeded, then `glViewport` at RVA `0x149b21f` on the init thread, and the process exited 2 without a launcher signal. That callsite is `_GfxSetViewPort` from `CGraphics::InitGfx` after `OnResetDevice` returned. The create return is indirect, and the pc-0 fault at `0x15ebd02` did not recur. `results/20261102T100321Z-metal-init` is pid 14511: `create.json` records success 1 for `_GfxCreateVertexBuffer` at RVA `0x14d44e4` on the init thread, and the same process then stopped on `glViewport` at RVA `0x149b21f` and exited 2 without a launcher signal. That pair shows the particle creation path was reached and execution continued. PR6 replaces `_GfxSetViewPort` ([note](../analysis/metal-init/pr6/note.md)). The offline host tests pass. `results/20261102T120220Z-metal-init` is pid 20282: init succeeded, `create.json` again records `_GfxCreateVertexBuffer` at RVA `0x14d44e4` on the init thread, and the same thread then stopped on `glGenTextures` at RVA `0x15ec4ed` in `_GfxInitializeTexture` and exited 2 without a launcher signal. The previous stop at `0x149b21f` did not recur. From the create return at `0x14d44e4` through `InitGfx`'s return at `0x149b25c`, the image call tree is settings, the logger, strings, the stream, and the mutex, and the logger virtual at `0x149b17d` is `ctype<char>::do_widen`. That path does not reach `_GfxInitializeTexture`, so the later `glGenTextures` stop is after the viewport replacement and after `InitGfx` returned. B2 remains unfixtured and is not a prerequisite. B0 stays the last artificial startup checkpoint.
- **Metal-only backend (2026-10-31).** OpenGL is a separate reference run. Hybrid GL/Metal frames, including M1/R2b and the shared map seam, are superseded in the [rendering strategy](eu4_recommended_strategy.md). The primary graphics window is the `SDL_CreateWindow` call at RVA `0x13cc1d7` in `CApplication::CreateApplicationWindow`. The `PdxDetectWindow` call at `0x15ed55a` is a temporary settings probe and is not patched. A1 verified: the primary SDL/Cocoa window hosted a Metal layer; command-buffer completion and 4×4 clear readback succeeded in `results/20261031T200822Z-metal-boot/` (exit 0). A2 verified: offline GL–Metal pixel equivalence for synthetic Color fixtures (shader pair, vertex layout, CW and CCW culling, and the clip-z remap), which is not the historical live EU4 draw and not a live frame. Startup was observed through the `_GfxInit` GLEW checkpoint. No CI job launches EU4 for this probe. The opt-in B0 checkpoint reached `_glewInit` inside `_GfxInit` in `results/20261031T223735Z-metal-boot-bootstrap/` (exit 0). It is not a Metal backend. B1 is the default depth-stencil description from `GetDefaultDepthStencil`, checked offline as a Metal depth-stencil state. B2's selected candidate is the observed Color draw; B2 is not complete ([contract](../analysis/metal-draw/b2/color-contract.md)). The 2026-11-01 session inspected 22 indexed programs and recorded no qualifying Color identity ([note](../analysis/metal-draw/b2/color-note.md)); it does not accept B2. An offline audit of the retained traces found no draw with complete same-draw replay inputs ([audit](../analysis/metal-draw/offline-candidate.md)). That search is closed. An offline `_GfxDrawIndexed` adapter copies caller-supplied vertex records on the CPU into the existing `drawPrimitives` reference; it is not `drawIndexedPrimitives`, not an engine replacement, and not EU4 pixel equivalence. One Venice observation of the original `_GfxDrawIndexed` at `0x15eaa77` retained 64 post-readiness samples, all mode 4 with `esi` 0 and a pending uniform byte ([note](../analysis/metal-draw/gfx-draw-indexed-live.md)). That session is not a Metal draw. The replacement boundary stays `_Gfx*` ([handoff](../analysis/metal-census/v1/gfx-init-handoff.md)).
- Renderer **reconstruction**, **differential validation**, and **elimination of unnecessary rendering work** per the canonical [rendering strategy](eu4_recommended_strategy.md) ([navigation](roadmap.md)).
- **Function-first reconstruction (2026-10-18; amended 2026-10-23/24).** The acquisition gate is split: **P5a** (first real-function acquisition) and **P5b** (frame-level). The first target is `_GfxSetConstantBuffer` (21 instructions, 0 selected direct callees); RE0 (contract) and a bounded offline original-code execution pilot precede the function-oriented P4.10, and frame-level P4.11 no longer blocks P5a. The workstream is now **two-tier** — Tier 1 offline replay against qualified fixtures, Tier 2 live shadow replay in an isolated worker with the original authoritative — and **RE3 is deferred** until both tiers succeed. **PR A1**–**PR D** are implemented (PR D merged at `09223f8`). **D-live (2026-10-25):** one bounded P5a session completed — `gfx-live-20261025T155406Z`, reporter `accepted`, 64/64 qualified; manifest SHA-256 `e8914fb1…ea437`; publication [summary](../results/20261025T155406Z-gfx-live-publication/publication-summary.md). Raw `.capture` transcripts remain operator-local. First E1 replay on that corpus: **64/64 match**, promotion **`scoped`** (missing slots 3–5, `input:zero`, diversity 10/12). See [Oracle plan of record](oracle/PLAN_OF_RECORD.md) and [PR D contract](oracle/P4_INTEGRATION.md).
- **Exact-dedup recorder (2026-10-26).** Merged in PR #94 (`7b17c83`). Runtime `--max-unique` is 1..4096. Offline native and reporter tests passed. Live diversity, overhead, and any further EU IV session remain separately gated. See [exact-dedup contract](oracle/EXACT_DEDUP_V1.md).
- **Parallel E–G implementation (2026-10-24).** **E1** (Tier 1 certifier), **F1** (offline shadow worker), **G1** (second-function RE0) and **G3a** (agent adapter seam on `synth:apply`) may proceed offline from the amendment merge; **E2** prepares adversarial fixtures once the E contracts freeze. **F2**, **G2**, **G3b**, **G4** and **H1** remain gated. G3b's first real-function pilot is an offline reconstruction pilot, not an E live-grounded certificate. No package authorizes a further EU IV launch without separate operator authorization.
- **Trace and fixture stage (2026-10-26).** Offline **TR0**, **TR1**, and **INV** may start from the contract amendment; **TR2** composition follows TR1, and its QBDI-to-fixture integration follows a supported TR0 result. The schema is [`execution-observation/v1`](oracle/TRACE_V1.md). **Historical TR3 preflight gate:** live EU IV instrumentation was not authorized by that gate. Its preflight passed on 2026-10-29 at `0a24c90` (zero-skip macOS run of the two TR2 modules). The preflight-only work order is [work-orders/TR3.md](oracle/work-orders/TR3.md). The work order records prerequisites and a proposed later experiment. That pass authorizes no implementation, EU4 launch, injection, live trace, or replacement. The implementation scope is [work-orders/TR3-IMPL.md](oracle/work-orders/TR3-IMPL.md). Writing its named paths waits for that work order to merge. That gate left an EU4 run unauthorized. See [Oracle plan of record](oracle/PLAN_OF_RECORD.md).
- **Function-agnostic tracing (GEN0, 2026-10-30).** The next trace milestone is one preselected function, described by [`trace-target/v1`](oracle/GENERIC_TRACE_V1.md). The [plan of record](oracle/PLAN_OF_RECORD.md) records an operator report of the first EU4 interception: 15 instructions, eight memory operations, natural return boundary, no sealed digest, no qualified fixture. **GEN1** is the native tracer ([work-orders/GEN1.md](oracle/work-orders/GEN1.md)). **GEN2** may start offline from [work-orders/GEN2.md](oracle/work-orders/GEN2.md). **GEN3-A** may be written from [work-orders/GEN3.md](oracle/work-orders/GEN3.md); after it merges, **GEN3-B** may repair the launcher. One live EU4 launch stays separately authorized.
- **Next phase (primary capability):** ground-truth acquisition + agent-driven C++ reconstruction of the graphics stack, per the [Oracle plan of record](oracle/PLAN_OF_RECORD.md) ([program index](oracle/README.md)). The Oracle **P3** slice is complete and released (`oracle-v2-p3b-20261017`), and so is **P4/P4b** — the offline recorder plus synthetic target, then the synthetic graphics replay — released as annotated tag `oracle-v2-p4b-20261018`. **P4r-A**, **P4r-B** and **P4r-C** (the record schema and immutable evidence store; exact capture accounting, a declared capture policy, offline finalization, concurrency and bounded capture; the resource registry/closure verifier and offline R0-M protocol compatibility) and **P4b** (the independent synthetic graphics replay) are all merged. No EU IV launch is authorized until P4/P4b pass **and the applicable offline half-gate passes**; passing P4/P4b does not satisfy that gate. That gate is sliced **P4.9-P4.11** ([P4_INTEGRATION.md](oracle/P4_INTEGRATION.md)): **P4.9** — bounded resource-content capture, whole-version content semantics, content provenance, fail-closed malformed declarations and the versioned observation export — is implemented. The remaining gate is split by acquisition scope (see above): the function-oriented **P4.10** authorizes **P5a**, and **P4.11** authorizes **P5b**. **PR D** P5a acceptance evidence exists for the session above; no CI job launches EU IV.
- Consolidated rendering knowledge: [macOS rendering findings](eu4_macos_rendering_findings.md), [alternatives ranking](eu4_rendering_alternatives_ranked.md).

## Reverse engineering (PR D tooling)

- **Ghidra Phase 1 automation** is in [`reverse_engineering/`](../reverse_engineering/README.md): headless import/inventory/decompile orchestration, SQLite search CLI, and census reports.
- **Local full export** requires `GHIDRA_INSTALL` and a maintainer run of D1–D5; committed aggregate reports are integration-fixture samples until `measurement_source` is `local_ghidra_export`.

## Pinned environment

GOG EU IV v1.37.5.0 x86-64 at `/Applications/EuropaUniversalisIV`, Venice paused fixture, documented in the [operations manual](reference/profiler-and-benchmark-operations.md).
