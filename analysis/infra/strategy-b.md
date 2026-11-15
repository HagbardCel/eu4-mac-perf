# Strategy B — independent Metal replay of a captured EU4 frame

Date: 2026-11-14. Worktree: `wt_infra`, branch `infrastructure`, based on the
reviewed integration head `485bb92955ad10231726ba16ba3466dd73836bf4`.
Follow-up commits: `eb814a6` (background display and capture hardening),
`fd4f175` (independent Metal intermediates), `beca74b` (agent tools),
`5d8bb6e` (portable case verification and evidence), `a6a1570` (PR #190
corrected M6 draw/capture semantics), and the final review corrections
described below.

This is the acceptance record for the capture-once/replay-independently
workflow. It is deliberately explicit about what is qualified and what is not.

## 1. What was built

| Layer | Implementation |
| --- | --- |
| Background display | `automation/gfx_display.py`, `benchmark/eu4_display_probe.m`, `benchmark/eu4_display_isolate.m` (opt-in guard); integrated with `gfx_launch`, `gfx_session` and `gfx_dual_run` |
| Capture integrity | strict payload/digest/extent validation before comparison; invalid evidence is preserved but never compared |
| Request capacity | Python refuses >256-frame requests; the native observer rejects an oversized request instead of truncating |
| Effective launch spec | `--continuelastsave` is part of the immutable effective argv and its digest |
| Startup health | GPU/API health is polled after readiness, not only in the comparison loop |
| Correspondence | image correlation is diagnostic only; identity and workload establish correspondence |
| Dependency contract | `automation/gfx_frame_graph.py` classifies external inputs, renderer-generated outputs and unproven content |
| Independent replay | `automation/gfx_independent_replay.py` drives the existing M6 Metal replay host, enforces provenance and compares checkpoints |
| Portable case | `automation/gfx_replay_case.py` packages capture, shaders, graph, checkpoints, replay, first divergence and a relative replay command |
| Agent surface | `eu4_capture graph`, `eu4_replay independent`, Strategy B `eu4_replay case` / `eu4_case_inspect`, DSH bridge/schema |

The replay host was corrected in three substantive ways:

1. **Numeric GL texture identities were dropped.** `string_value()` returned
   `nil` for JSON numbers, so `g_render_targets` was never populated. Every one
   of the 2,759 texture bindings used captured OpenGL contents. After the
   `texture_key()` fix, 274 bindings use Metal render targets and 2,485 use
   external inputs. This is the concrete mechanism by which the previous replay
   could match final pixels while Metal's intermediate rendering remained
   unvalidated.
2. **Render-target sampling lacked a V flip.** A Metal render target stores
   GL row 0 at the opposite end of the texture from a GL-uploaded asset.
   Sampling renderer-generated targets now goes through a cached
   `orient` compute pass (V flip; RGB targets also get alpha 1.0).
3. **Replay events are serialized.** A sampling transform owns its own command
   buffer, so prior events must be complete before it reads a target. One event
   per submission also makes checkpoint readback exact.

## 2. Selected capture

Canonical frame: `.local/m6/frames/menu-occ300/frame` (outside this worktree;
content copied into the portable case).

- 326 events: 317 draws, 9 clears, 0 captured resolve/copy events.
- Reference: 1728x1117 BGRA8, lower-left, SHA-256
  `ff2f4e2b9d2cfeef8509dc6777213c859a7ef6af7bc6ad37c4ca564ce57deb15`.
- 68 logical resources: 60 external inputs, 7 renderer-generated, 1 unproven.
- Five renderer-generated resources are sampled downstream: textures 431, 432,
  433, 443 and 447.
- The only frame seed is the first draw's pre-image on texture 433; an in-frame
  clear at event 0 supersedes it.

### Coverage report

```json
{
  "status": "partial",
  "counts": {
    "events": 326, "draws": 317, "clears": 9, "resolves": 0,
    "resources": 68, "external_inputs": 60,
    "renderer_generated": 7, "renderer_generated_sampled": 5,
    "unproven": 1
  },
  "unproven": ["tex:442"],
  "reasons": [
    "tex:442 is sampled but its content version is unproven: sampled full-extent texture has no captured producer; a resolve/copy or earlier pass is missing from the capture",
    "the capture records draws and clears only; a sampled full-extent texture without a producer indicates an uncaptured resolve/copy operation (for example _GfxResolve)"
  ]
}
```

Texture 442 is 1728x1117 and is sampled by the bloom pass (events 287 and 291)
but is never written by a captured draw, clear or copy. The engine has
`_GfxResolve`, and the frame trace has a resolve entry point, but the canonical
capture records no resolve/copy operations. The frame is therefore **partial**
by contract even though the replay's final pixels match. This is the correct
fail-closed result: complete-frame certification requires the missing producer
to be captured.

## 3. Independent replay result

Command (from the repository root):

```sh
python3.14 -m automation.gfx_independent_replay \
  --frame .local/infra/strategy-b/moved/case/capture \
  --shaders .local/infra/strategy-b/moved/case/shaders \
  --out .local/infra/strategy-b/cli-check
```

| Property | Result |
| --- | --- |
| Events encoded | 326/326, 0 failed |
| Metal command status | completed (4) |
| Final readback | present, 1728x1117 |
| Final mismatch | **0 / 1,930,176** pixels, max error 0 |
| Final SHA-256 | `ff2f4e2b…` (equal to the OpenGL reference) |
| Metal render-target bindings | 274 |
| External captured-input bindings | 2,485 |
| Captured intermediate seeded into Metal | **0** |
| First sample before any write | **0** |
| Checkpoints 431, 432, 447, 443 | exact (0 differing pixels) |
| Checkpoint 433 | diagnostic only: GL RGB8 vs Metal BGRA8 readback, 573,377 pixels differ after channel normalization, max 114; not authoritative |
| Determinism | two consecutive replays produced identical final and checkpoint hashes; 4.12 s each |

The final image is produced after Metal independently renders textures 431,
432, 433, 443 and 447 from the captured external inputs. Texture 442 remains an
unproven external input for the bloom pass; that is the coverage boundary.

## 4. First-divergence experiment

Controlled fault: disable the render-target V flip.

| Field | Value |
| --- | --- |
| Earliest authoritative divergent checkpoint | event **291**, target **443** |
| Producer | event **289**, `_GfxDraw`, caller `0xed7c76`, program 349 |
| Divergence | 481,860 / 482,112 pixels, max 155 |
| Upstream checkpoint 447 | exact (proves the divergence is the consuming pass, not its input) |
| Final output | 53,449 / 1,930,176 pixels differ, max 17 |

This reproduces the earlier live "first divergence" class (render-target
orientation) offline and localizes it to one draw. The correct replay is
restored by removing the fault; all authoritative checkpoints and the final
output then match again.

## 5. Historical sampler-binding defect

Injection: for `texN` with `N >= 7`, select the engine texture captured at unit
`N-1`; when that unit has no payload, record the missing unit (the shader would
sample the default texture). The replay host and shader slots are unchanged.

| Property | Correct replay | Defective replay |
| --- | --- | --- |
| Binding audit | 0 mismatches | **842** mismatches |
| Earliest state divergence | none | event **14** |
| `tex7` | unit 7 -> texture 429 | unit 6 -> **missing** (default) |
| Other fields | — | `tex8` unit 7 -> texture 429 instead of 2; `tex9` -> 2 instead of 387; `tex10` -> 387 instead of 389; `tex11` -> 389 instead of 434; ... |

The title frame's terrain draws are overdrawn by the menu layer, so this
deliberate state defect does **not** change the final pixels. It is detected by
the explicit binding audit, which is the correct outcome for a defect that
global pixel similarity conceals. The mirrored UI-label regression remains in
the existing M6 case suite.

## 6. Portable case

`.local/infra/strategy-b/moved/case` contains `capture/`, `shaders/`,
`replay/`, `report.json`, `graph.json`, `case.json` and `README.md`. All paths
in `case.json` are relative and every file has a SHA-256. It was moved to a new
directory and verified; replay from the moved case reproduced the golden final
output. The replay command recorded in the manifest is:

```sh
python3.14 -m automation.gfx_replay_case replay --case . --out replay-rerun
```

## 7. Background display (Phase 0)

- BetterDisplay virtual screen `EU4 Test` exists once and is reused.
- Qualified geometry: 1920x1200, 60 Hz, HiDPI off, non-main, origin
  `(1728, 0)`; physical built-in display remains main at 1728x1117 points /
  3456x2234 pixels and its modes were not changed.
- One real original-OpenGL launch was executed directly into background mode:
  - observer installed, **820 frame observations** before termination;
  - the game's `settings.txt` was switched to windowed for the session and
    restored byte-for-byte afterwards;
  - process terminated cleanly by the owned-session path (SIGKILL after the
    harness stop); no unrelated process was signalled;
  - primary display configuration unchanged.
- **Acceptance not met.** The window was reported at `[0, 32, 1728, 1145]` on
  the physical display, not on the virtual screen, and EU4 was frontmost for 68
  of 71 verification samples. The first launch therefore demonstrates valid
  background captures, clean termination and correct restore, but **not**
  focus/placement isolation.
- The opt-in `eu4_display_isolate` AppKit guard was written to block
  activation and place the window before ordering it on screen. It is **not
  qualified**: in guarded launches the window was not visible to the window
  server and the game still became frontmost. It is therefore disabled by
  default (`focus_guard=False`) and background mode must not be claimed as
  default until a minimal AppKit qualification shows both placement and no
  activation.
- Five consecutive launches were not performed because the host lease was
  concurrently held by the parallel M6 worktree, and the isolation mechanism
  is not yet qualified. Foreground remains the safe fallback.

## 8. Tests

- Python 3.14: 208 relevant infrastructure tests pass, including 18 display,
  14 dependency/replay-contract and the extended Phase 1 regressions.
- Native macOS: frame observer host tests pass, including the new oversized
  request rejection. The replay host builds with `-Wall -Wextra`.
- DSH plugin: 6/6 Node tests pass; schemas and argument mapping cover the new
  graph/independent/fault operations.
- Full Python discovery: 2,814 tests, 11 failures. All 11 reproduce at the
  reviewed base `485bb92` in a detached worktree (Metal init capability span,
  shadow-worker texture-LOD state, live-capture native canonical bytes and
  process/worker host tests), so they are pre-existing environment/build-cache
  failures rather than Strategy B regressions. The 221 tests covering the
  touched areas pass.
- No test launches EU4.

## 9. Remaining limitations

1. The canonical capture is partial: `_GfxResolve`/copy capture is not yet part
   of the frame trace, so texture 442 has no producer. Implement resolve/copy
   capture and re-capture the frame before claiming a complete frame.
2. The live Metal-enabled EU4 qualification was not re-run in this session
   (the M6 worktree held the launch lease). Offline replay qualifies the
   renderer workload, not all live integration behavior.
3. Background display focus/placement isolation is unqualified; keep the
   foreground fallback.
4. Checkpoint 433 is diagnostic because GL RGB8 and Metal BGRA8 readbacks have
   different channel/alpha conventions and the checkpoint comparison does not
   yet declare a quantization policy.
5. The sampler-defect experiment demonstrates state divergence, but the title
   frame masks its pixels; a map/terrain frame is needed for pixel-level
   detection of that historical defect.

## 10. Efficiency measurements

See [`strategy-b-metrics.json`](strategy-b-metrics.json). Four authentic EU4
launches were attempted for display qualification (none qualified for focus
isolation); no new GPU capture was taken. One authentic frame was replayed
offline eight times; the qualified replay takes 4.12 s versus roughly 340 s to
reach the title scene live, and the five renderer/provenance changes were
validated without relaunching EU4.

## 11. Merge recommendation

The correctness fixes, dependency contract, independent replay, provenance
enforcement, first-divergence experiment and portable case are mergeable as
focused follow-up commits. The frame should be labeled **partial** until
resolve/copy capture is added. Background mode should remain opt-in and
unqualified; the existing foreground path and the dual-process runner are
unchanged in their qualification claims.

## 11. Final review corrections (2026-11-14)

- **Shared-settings lifecycle (critical).** Background preparation is split into
  `plan_background()` (validation only) and `activate_background()` (display
  preparation, durable backup, atomic settings write). The campaign acquires
  launch authorization and the host-wide lease before activation, terminates
  owned processes before restore, restores before releasing the lease, and
  rolls back on activation/attach failure. Restore is conditional: it accepts
  the already-original file, a game rewrite that preserves the windowed
  intent, or the exact modified file; an unrelated edit is preserved and
  reported as a conflict. Failure-injection tests cover planning, rollback,
  conflict, already-original and game-rewrite paths.
- **PR #190 integrated.** `fix/m6-post-merge-correctness@4d24754` was reviewed
  and merged as `a6a1570`. The replay host now uses signed additive base
  vertex, captured vertex origin, UInt16 indices, corrected topology, color
  write masks, unsupported stencil/raster rejection and attribute range
  checks. The canonical 326-event replay still produces 0 differing pixels.
- **Outcome dimensions separated.** A replay now reports independent
  `execution`, `coverage`, `pixels`, `semantic` and `qualified` fields. A
  partial capture with matching pixels is `coverage=partial, pixels=match,
  qualified=false`; a complete capture that differs is
  `confirmed_rendering_difference`; an overdrawn sampler defect is
  `semantic_divergence` even when pixels match.
- **First divergence includes semantics.** `binding_audit()` now feeds
  `first_divergence()`; the injected `tex7` defect is reported at event 14 as
  `kind=semantic`, not hidden by matching final pixels.
- **Capture request protocol.** `eu4_capture request` uses the existing
  `gfx_dual_run._request_dumps()` mechanism, including monotonic request IDs,
  the 256-frame capacity bound and refusal of oversized requests.
- **Portable case checkpoint files.** The packager copies
  `checkpoint["metal_file"]` (the comparison field) and verifies every
  packaged file. The final case was moved and replayed from packaged inputs
  only; all 1,242 hashes verify and the result is unchanged.
- **Resource classification.** External classification now records an explicit
  basis (no in-frame producer, captured content at every consumer, not an
  in-frame target). Full-extent resources without a producer are marked as a
  possible missing producer. Expected generation counts are checked so a stale
  nonzero generation cannot pass provenance.
- **Resolve/copy.** The replay host now **fails closed** on `resolve`/`copy`
  events instead of counting them as successful no-ops. It does not execute
  them yet, so a frame containing them is `invalid`. The canonical frame still
  has no resolve/copy events, and texture 442 remains unproven; the frame stays
  partial.
- **Background display remains unqualified.** The guard now installs all
  advertised hooks, places windows through `setFrame`/order paths, records a
  trace and yields focus back to the previously frontmost application.
  Observed launches still either left the main window on the physical display
  or produced a fullscreen 1920x1200 window on the virtual screen, changing the
  drawable extent; focus was still taken. Background mode stays opt-in and
  unqualified. No five-launch acceptance is claimed.
- **Session-long verification.** `WindowMonitor` samples window, display and
  frontmost identity for the whole campaign; activation/disconnection marks
  the campaign incomplete. It was not exercised live because background
  isolation did not qualify.
- **Tests.** 196 tests across the touched modules pass, including PR #190's
  native draw tests, the settings lifecycle regressions, dependency/outcome
  tests, agent request tests and portable-case verification.

## 12. Final closure experiments (2026-11-14, post-fe0e151)

- **PR #190 final delta.** `61f3b9a` (missing render-target rejection, stale
  shadow conversion validation, no destructive GL error drain) merged without
  undoing the earlier draw/topology/colour-mask/attribute corrections. Native
  `test_gfx_live_draw` regressions pass.
- **Deterministic SDL placement.** The engine's SDL_CreateWindow call requested
  `x=y=0x30000002` (SDL undefined/centred) and `flags=7`
  (`FULLSCREEN|OPENGL|SHOWN`). A reference-only dylib patches the call site
  (`RVA 0x13cc1d7`) and clears fullscreen flags while forcing the virtual-screen
  origin. The Metal boot wrapper now applies the same rule. This explains both
  previous failures: fullscreen mode adopted the 1920x1200 display mode, and
  SDL's centred/undefined coordinates ignored `SDL_VIDEO_WINDOW_POS`.
- **Five-launch background acceptance (OpenGL).** Five consecutive launches on
  `EU4 Test` (1920x1200, 60 Hz, 1x, non-main) passed:
  - window `1728x1145` frame / `1728x1117` drawable, entirely on the virtual display;
  - physical display unchanged;
  - 179-200 frame observations in the 20 s interval;
  - zero activations, zero placement issues, zero disconnections;
  - clean owned termination and settings restore on every launch.
  The focus guard suppresses SDL's activation calls and yields any residual
  activation to the prior application.
- **Metal background.** Placement succeeds (1694x1123 visible window inside the
  virtual screen, correct 1728x1117 drawable), the process initializes and
  produces observations, but the candidate still becomes frontmost (36 monitor
  samples). Metal background focus is **not qualified**.
- **Fail-closed outcomes.** `qualification_exit_code()` is shared by the replay
  CLI and portable case: 0 qualified, 2 executed-but-unqualified, 3 deliberate
  fault injection, 1 invalid. The agent `eu4_replay` envelope reports the same
  dimensions and no longer calls a partial/semantic-divergent replay `ok`.
- **Per-consumer generations.** Each consumer carries
  `required_generation`; provenance must match exactly. A second sample using
  the earlier generation is rejected (regression added).
- **Settings restore.** Restoration now reverts only harness-controlled fields
  when the game rewrote unrelated settings; controlled-field equality is
  accepted as already restored; unrelated edits are preserved and reported.
- **Monitoring.** `WindowMonitor` starts immediately after spawn, ignores
  zero-size startup helpers, and records late movement, wrong display, changed
  dimensions, absence after first sight, activation and disconnection. Any of
  these marks the campaign incomplete.
- **Remaining blocker.** Texture 442 is still unproven. Resolve/copy events are
  rejected by the replay host; no fresh capture with ordered resolve events was
  obtained. The canonical frame remains `coverage=partial, pixels=match,
  qualified=false`.

Targeted tests: 201 pass across display, campaign, graph, replay-case, agent,
M6 frame and PR #190 draw suites; 6/6 DSH plugin tests pass.

## 13. Final closure (2026-11-14, post-19fb671)

- **Metal background qualified.** `eu4_metal_boot_on_create()` now propagates the
  background-modified flags (`passed = flags & ~OPENGL`) instead of rebuilding
  from the original `incoming` value, and `ensure_visible_on_main()` attaches
  the child layer without `activateIgnoringOtherApps`, `makeKeyAndOrderFront` or
  `orderFrontRegardless` in background mode. Five consecutive Metal launches
  passed: correct 1728x1117 drawable, window entirely on `EU4 Test`, zero
  activations, 78-80 observations each, clean termination, settings restored.
  OpenGL remains 5/5 from the previous qualification.
- **Clear semantics corrected.** Clear bits are now treated as the engine's
  `color=1, depth=2, stencil=4`, not GLbitfield. Color-only preserves depth,
  depth-only preserves color and does not advance the color generation, and
  stencil clears use a `Depth32Float_Stencil8` attachment. Native regression
  `tests/test_gfx_replay_semantics.py` covers the flag mapping.
- **Every expected consumer validated.** The evaluator reconstructs
  `(event, sampler, texture, required_generation)` from the graph and rejects
  missing, ambiguous, wrong-resource, captured-intermediate and stale-generation
  bindings. A missing second sample after a rewrite is a regression test.
- **Texture 442 lifecycle evidence.** A new GL interposer records ordered
  `glCopyTexSubImage2D` events. In a live run it observed **5,618 copies into
  texture 442**, all 1728x1117, all with `read_fbo=0` (the default framebuffer).
  This establishes that 442 is a per-frame backbuffer copy, not a CPU upload or
  an unrelated alias.
- **Resolve replay implemented.** The Metal host now allocates resolve
  destinations, executes the copy through a compute pass with GL-row-preserving
  orientation and correct destination generation, and fails closed on unknown
  variants. A synthetic native test (`tests/test_resolve_replay.py`) copies a
  red default target into texture 10 and samples it back exactly.
- **Capture attempts.** Three authenticated background capture attempts exited
  before occurrence 300 and produced no frame. The 442 copy evidence is real,
  but the exact copy position relative to the canonical frame boundary is still
  unproven, so 442 is not declared a boundary input and the frame remains
  `coverage=partial`.

## 12. Final correctness closure (2026-11-15)

### Baseline and ancestry

The reviewed integration head was `b8fdd0b95567567aa4cf63203fe5cd3da278fe57`.
It was synchronized with the current integration baseline `origin/main`
(`61e7795`, the PR #190 merge) as merge commit `cb35385`, preserving the five
historical infrastructure branches and all follow-up corrections. The merge-closure corrections are commit `dabca9f`; no original branch was
rewritten. The integration PR is https://github.com/HagbardCel/eu4-mac-perf/pull/191.

### Executable defects fixed

1. **Startup launcher (`eu4_gfx_startup_trace.py`).** `run()` referenced
   `env_extra` and `game_args` without declaring them. Both are now optional
   parameters mirroring `run_ops`; the CLI exposes `--env` and `--game-arg`.
   A new offline test mocks `preflight()`, the lease and `Popen`, and asserts the
   exact argv and environment.
2. **Fail-closed trace writer (`eu4_gfx_frame_trace.c`).** A copy at capacity now
   sets `truncated`; `emit_event()` returns success and marks
   `capture_failed` on write, flush or close failure; the event stream is closed,
   re-read, structurally checked and counted before any manifest may publish
   `complete=true`. Regressions cover copy capacity, forced flush failure,
   malformed records, count mismatch, and a valid draw+copy frame.
3. **Scene selector.** The scene path now applies the same caller, minimum-draw,
   maximum-frame-draw and occurrence semantics as the present path, finishes the
   selected scene at its corresponding next boundary, and explicitly rejects the
   unsupported scene+absolute-frame combination. Synthetic scene tests assert
   occurrence 2 selects scene 5, capture spans scenes 6→6 through both
   boundaries, and only scene-6 draws appear.
4. **Copy interposer self-recursion.** `dlsym(RTLD_NEXT)` returned the interposed
   function itself, so `GfxResolve` recursed on the main thread. The original is
   now resolved explicitly from the OpenGL framework handle, with a self-pointer
   guard. This also invalidated the old 5,618-copy lifecycle record.
5. **Partial copy kernel.** The compute kernel was bounded only by texture
   extent, so a 2×2 request overwrote an 8×8-clipped region. The kernel now
   receives the requested size and returns outside it.
6. **Consumer validation.** Event index 0 is preserved, and two provenance rows
   for the same `(event, sampler)` are rejected as `duplicate_consumer` instead
   of counting as one valid binding.

### Authentic capture and independent replay

A new original-OpenGL menu scene was captured after the interposer fix:

- scene 79, 282 events, 273 draws, 9 clears, no recorded resolve/copy event;
- reference 1728×1117 BGRA8, SHA-256 `3b832401…`;
- capture manifest `complete=true`, `stream_validated=true`.

Independent Metal replay:

| Property | Result |
| --- | --- |
| Draws executed | 273/273, 0 failed |
| Command status | completed |
| Bindings audited | 2,573 checked, 0 mismatched |
| Checkpoints 431, 432, 443, 447 | exact |
| Checkpoint 433 | diagnostic (GL RGB8 vs Metal BGRA8) |
| Final pixels | 4 / 1,930,176 differ, all alpha LSB, max channel error 1 |
| Determinism | two replays produced identical candidate SHA-256 |
| Qualification | **not qualified** (coverage partial, 4-pixel difference) |

The four differing pixels are at the top and bottom screen edges
(`(2,1)`, `(1725,1)`, `(1727,1)`, `(0,1116)`); colour is identical and alpha is
229 in the GL reference versus 228 in Metal. The first-divergence report is
therefore `final_only`, not an intermediate mismatch. The historical canonical
frame still replays with 0 differing pixels.

### Texture 442 blocker

**Superseded by §13.** The in-frame copy was captured on 2026-11-15; the text
below records the state before that capture.

Texture 442 is 1728×1117, sampled by two in-frame passes, and has no in-frame
producer. The earlier metrics claimed 5,618 `glCopyTexSubImage2D` events into
texture 442. That record is **not authentic**: before the interposer fix,
`dlsym(RTLD_NEXT)` returned the replacement itself, so each `GfxResolve` call
recursed until termination and every recursion appended a log line. With the
original OpenGL function resolved correctly, the captured menu workload contains
no authentic copy event. The precise temporal relationship between the default
framebuffer, the copy and the frame boundary therefore remains **unproven**.

The correct classification is still `unproven`, not "external input": treating
442 as a boundary input without temporal evidence would violate the capture
contract. The frame remains partial and replay qualification remains false even
though final pixels almost match.

### Controlled regressions

- Render-target V-flip disabled on the canonical frame: earliest authoritative
  divergence event **291**, target **443**, producer event 289
  (`_GfxDraw`, caller `0xed7c76`, program 349), 481,860/482,112 pixels differ.
- Shifted-sampler fault (`--sampler-shift 1 --sampler-shift-min 7`) on the new
  frame: 806 binding mismatches, earliest semantic divergence **event 17**
  (`tex10` shader unit 0 selected engine unit 11 instead of texture 389).
  Later overdraw hides most of the pixel effect; the semantic audit still
  detects it.
- Both faults are offline and do not relaunch EU4.

### Portable case

`case-menu-clean-moved` contains capture, shaders, graph, checkpoint request,
replay outputs and relative commands (1,084 files). It was moved to a new
directory, verified (`problems: []`) and replayed from the packaged inputs; the
rerun reproduced the same four-pixel alpha difference.

### Background integration

Placement/focus qualification remains 5/5 OpenGL and 5/5 Metal. Three background
title-capture attempts stalled at the loading scene (6 draws, 70 scenes, no
authentic copies), but all three predated the interposer self-recursion fix.
They are therefore **inconclusive** about background rendering itself, not
evidence that background title rendering is impossible. Background title
rendering remains unverified; the preserved foreground fallback produced the
authentic capture. Background mode remains opt-in and is not qualified for long
OpenGL title capture.

### Remaining limitations

1. Texture 442 has no authentic captured producer; the frame is partial.
2. The new frame has a deterministic 4-pixel, 1-LSB alpha difference at screen
   edges; the canonical frame has none. This is reported as a real result, not
   normalized away.
3. Background OpenGL title rendering remains unverified. The pre-fix attempts
   stalled at loading and are inconclusive; the foreground fallback produced
   the authentic capture.
4. The copy/clear semantics are fully validated synthetically and by native
   pixel tests, but no authentic copy-bearing menu frame has been captured.
5. Checkpoint 433 remains diagnostic because RGB8/BGRA8 conventions differ.

### Merge recommendation

Merge the integration branch as a coherent infrastructure PR. It contains the
baseline synchronization, the executable fixes, fail-closed evidence, native
pixel semantics, the authentic replay case and honest partial coverage. Do not
claim texture 442 provenance or complete Strategy B qualification.


## 13. Merge closure: in-frame copy and final validation (2026-11-15)

### Copy correctness

The final readback now uses the single `read_drawable_bgra()` path. It selects
the default draw/read framebuffer and `GL_BACK`, verifies the bindings, restores
draw/read bindings, read buffer, pixel-pack state and PBO, and does not call
`glGetError` or `glFinish`. The reference is written only after dimensions,
byte count and SHA-256 validate. `tests/gfx_trace_state_host.m` deliberately
selects a non-default FBO, sets a pending GL error, and verifies restoration,
error survival and exact reference pixels.

The interposer now resolves the original binding from the `__DATA,__interpose`
replacee, with `RTLD_FIRST` framework fallback and explicit self-rejection. A
failed resolution writes `interpose-error.jsonl`, prints a diagnostic and aborts
instead of silently dropping the copy. `tests/gfx_interpose_host.c` covers
successful resolution, self-rejection, missing original, fatal visibility and
argument-exact forwarding.

One copy contract is shared by `automation/gfx_frame_graph.py` and the Metal
replay host: operation identity, source framebuffer/read buffer/attachment,
levels, dimensions, formats, rectangles, offsets and resolved identities.
Coverage counts supported transfers and marks malformed ones invalid. The final
capture path loads the frame through the Python JSON/blob/hash loader; a
balanced but malformed JSON line is covered as an authoritative rejection.

### Texture 442 is an in-frame producer

`eu4_re_query` decompiles `_GfxResolve` as a full-extent
`glCopyTexSubImage2D` of the bound texture from the default framebuffer, called
only by `CEU3GraphicalMap::RenderPostEffects`. The bounded copy trace found the
copy in the selected menu frame:

- scene 75 capture: 283 events, 273 draws, 9 clears, **1 resolve**;
- resolve event **271**: source framebuffer 0, read buffer GL_BACK, source rect
  `[0,0,1728,1117]`, destination texture **442**, offset `[0,0]`, RGBA8;
- consumers at events 273 and 277 sample texture 442 after the copy;
- graph status **complete**, 0 unproven, 8 renderer-generated resources, 6 sampled.

This is Case A. The copy is in the ordered event stream, sourced from the
Metal-rendered default framebuffer and consumed from the Metal destination.

### Why the copy-bearing replay is not exact

The capture includes the default-framebuffer source pixels for the copy. The
replay now checkpoints that source at event 271 (target `0`):

| Checkpoint | Result |
| --- | --- |
| 431, 432 | exact |
| 433 | diagnostic RGB8/BGRA8 |
| 271 default source | 1,205,235 / 1,930,176 differ, max 106, mean max ~1.04 |
| 273 texture 442 | 1,205,235 differ (same count) |
| 275 / 277 downstream | 291,686 / 307,421 differ |
| Final | 1,792,005 / 1,930,176 differ, max 23 |

The first authoritative divergence is the **copy source**, event 271, producer
event 270 (`_GfxDraw`, caller `0x10d25c3`, program 346). Two facts separate the
copy from the source:

- GL source BGRA→RGBA equals the captured GL 442 texture exactly (0 different
  pixels), so GL's copy is a pure channel conversion.
- Metal 442 equals the channel-swapped Metal default source exactly (0
  different pixels), so the compute copy is faithful.

The captured draw state records `dither=true` and `multisample=true`, which the
Metal host does not implement. The observed distribution is **not uniformly a
one-LSB dithering artifact**:

| Surface | Mismatched | abs=1 | abs=2 | >2 | Max | Alpha | RGB |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| Copy source (event 271) | 1,204,600 | 910,637 | 124,596 | 169,367 | 105 | 7 | 1,204,600 |
| Texture 442 (event 273) | 1,204,600 | 910,637 | 124,596 | 169,367 | 105 | 7 | 1,204,600 |
| Final frame | 1,792,005 | 1,757,148 | 31,364 | 3,493 | 23 | 1,785,844 | 976,504 |

Source channel maxima are `[105, 66, 71, 4]`; large differences occur across
the whole frame (x 0–1727, y 0–1116), with only 4,075 of 169,367 larger
differences within 8 pixels of an edge. The dominant 1–2 LSB population is
consistent with default-framebuffer dithering/conversion, but the >2 outliers
are not explained by dithering alone. The established fact is narrower: **the
first observed difference is the default-framebuffer output before the copy**;
the exact conversion/blending cause remains unresolved. Full evidence is in
`analysis/infra/scene75-difference.json`.

Implementing GL dithering or exact default-framebuffer conversion is outside
this infrastructure milestone. The result is reported as `coverage=complete`,
`semantic=match` (binding-audit scope), `state_coverage=partial` (unsupported
`gl_dither`, `multisample`), `pixels=divergent`, `qualified=false`, with exact
source coordinates preserved.

### Requalification and portable cases

- Canonical `menu-occ300`: 0 / 1,930,176 final differences, checkpoints 431,
  432, 443, 447 exact; coverage partial (historical frame has no copy).
- Scene 79: deterministic 4-pixel alpha-LSB difference, coverage partial.
- Copy-bearing scene 75: deterministic source-localized difference, coverage
  complete.
- Controlled V-flip fault on the canonical frame: first divergence event 291 /
  texture 443, producer 289.
- Shifted-sampler fault: 806 binding mismatches, first semantic divergence
  event 17.
- Portable `case-copy-442-moved` (1,088 files) verifies with no problems and
  replays from the moved inputs with the same candidate SHA-256.

### Background title acquisition

One longer background OpenGL capture reached the title scene and completed
283 events; the window was placed at `(1755, 27)` on the `EU4 Test` display, the
physical primary display remained unchanged, the observer did not stall, the
process ended cleanly, and shared settings restored. A single sampled
frontmost application was `eu4`, so unfocused rendering was not independently
re-proven in this long run; the existing five-launch OpenGL/Metal isolation
qualification remains the supported evidence for placement/focus.

### Merge recommendation

The infrastructure is merge-ready with complete, fail-closed diagnostic
capability. Texture 442 is no longer an unresolved dependency: its in-frame
producer is captured, replayed, checkpointed and localized. The remaining
copy-bearing pixel difference is a documented GL dithering/conversion
limitation, not a hidden seed. Do not describe the copy-bearing frame as
bit-identical; exact pixel equivalence is established only for the canonical
no-copy frame.

## 14. PR #191 final review corrections (2026-11-15)

### Checkpoint qualification is reconciled end to end

`automation/gfx_independent_replay.py` now reconciles the checkpoint request
with the replay response. Every requested `(event, target)` must have exactly
one result. Missing, duplicate and unexpected results, `ok=false`, absent output
files, absent/corrupted references, dimension disagreement, byte-count errors
and reference-SHA mismatches are instrumentation/qualification failures. A
failed checkpoint cannot be hidden by a matching final frame: the outcome gains
`checkpoints=invalid`, `qualified=false` and CLI exit code **1**. Valid
`divergent_diagnostic` checkpoints remain non-authoritative; authoritative
divergences remain rendering differences (exit 2).

Regressions cover all requested checkpoints matching, a missing result,
`ok=false`, a missing output file, a missing/corrupted reference, duplicate and
unexpected results, a valid diagnostic RGB8/BGRA8 difference, an authoritative
divergence, a matching final with one invalid checkpoint, the CLI exit contract
and the DSH envelope.

### Copy-only provenance uses transfer execution, not sampler bindings

The replay host now emits an ordered `transfers` record for each successful
resolve/copy: event, kind, operation, source, source kind, destination, source
generation, destination generation before/after and status. `evaluate_replay()`
validates copy/resolve consumers against those records:

- event and source/destination identity;
- expected source generation for texture sources;
- destination generation advancement;
- successful GPU execution;
- no captured-OpenGL source substitution for a renderer-generated source.

The sampler-binding requirement now applies only to resources with draw/sampler
consumers. A copy-only source qualifies without an invented texture-unit
binding, while stale, wrong-source, missing-transfer and captured-substitution
cases fail closed. Seven synthetic graph/replay regressions cover draw→copy→
sample, copy-only, stale generation, wrong source, missing transfer, default-FB
copy and captured-source substitution.

### Portable Linux CI

The portable workflow's local dependency assumptions were removed from unit
tests without changing production platform behaviour:

- placement/isolation libraries are mocked in settings-lifecycle tests;
- scenario/ledger tests use a deterministic pinned `gog_identity` fixture and a
  mocked user-data preflight;
- ownership tests use a test-only `/proc` identity reader on Linux, so foreign
  process refusal, dead-identity rejection, parent/child validation, terminal
  status persistence and cleanup after exceptions remain exercised;
- the agent error-envelope test reaches the missing-ledger condition with a
  controlled identity fixture.

The exact GitHub command (`PYTHONPATH=benchmark:. python -m unittest discover -s
tests`) was run in an amd64 Ubuntu 24.04 VM with Python 3.14 and Node 26; the
previously failing modules pass. The full portable workflow, including DSH,
evidence, documentation and final-diff steps, passed on PR #191:
https://github.com/HagbardCel/eu4-mac-perf/actions/runs/43708678925.

### Non-default framebuffer source metadata

The interposer now queries the selected attachment's own texture on the active
unit, saving and restoring the binding, and rejects cube/layered or
non-texture attachments instead of reading the currently bound `GL_TEXTURE_2D`.
It does not change framebuffer bindings or consume GL errors. The native fixture
uses an 8×6 RGBA8 attachment while a 4×4 RGB8 texture is bound, and verifies the
recorded extent/format/attachment, state restoration, sentinel-error survival
and the unsupported renderbuffer path.

### Qualification terminology and scene-75 evidence

- `semantic` is now explicitly scoped: `semantic_scope=binding_audit`.
- `state_coverage` reports captured state the Metal host does not implement.
  The current menu frames record `gl_dither` and `multisample`, so state
  coverage is `partial`; `qualified` requires complete state coverage.
- Scene 75 remains an explicit rendering divergence. The source checkpoint
  differs in 1,204,600 pixels: 910,637 at 1 LSB, 124,596 at 2 LSB and 169,367
  above 2 (max 105, alpha only 7). The final frame differs in 1,792,005 pixels:
  1,757,148 at 1 LSB, 31,364 at 2, above 2 3,493 (max 23, alpha 1,785,844, RGB
  976,504). The first observed difference is the default-framebuffer output
  before the copy; dithering/conversion is consistent with the dominant 1–2 LSB
  population but is not established as the exclusive cause of the >2 outliers.
  Full evidence: `analysis/infra/scene75-difference.json`.
- Texture 442's producer/consumers and the copy's fidelity relative to the Metal
  source remain established. Exact scene-75 equivalence is not claimed.

### Merge status

The two PR review threads are addressed by the code and regressions above. The
historical five branches remain unchanged; this remains one consolidated
integration PR.

## 15. Final provenance, checkpoint-authority and output-safety pass

### Copy-source category is enforced for every transfer

The evaluator now derives the required replay source category from the captured
resource, rather than treating source name `"0"` as proof:

| Captured source | Required transfer category |
| --- | --- |
| In-frame default framebuffer (`fb:0`, generated) | `metal_default` |
| In-frame renderer-generated texture | `metal_render_target` |
| Explicitly supported frame-boundary input | its declared boundary category |
| Anything else | invalid |

A transfer that reports an external capture, the wrong Metal category, or no
category is `invalid`. The existing source/destination identity, generation and
ordering checks remain. Seven provenance regressions plus a combined
invalid-transfer/stale-resource regression cover the categories.

### Captured checkpoint format is authoritative

`reconcile_checkpoints()` no longer lets a replay-reported format override the
captured resource format. When the captured format is known, the reported
format must match it; only the documented RGB8/BGRA8 (or SRGB8) diagnostic
convention is accepted. An authoritative RGBA8 checkpoint reported as RGB8 is an
invalid checkpoint, not a diagnostic downgrade. A returned `reference_file` that
disagrees with the request is also invalid. This closes the path where a real
difference could be reclassified as non-authoritative.

### Output directories are ownership-checked

`gfx_independent_replay.replay()` and `gfx_replay_case.package()` no longer
delete arbitrary existing directories. They:

- reject protected workspace/repository/home roots;
- reject output paths overlapping capture, translated shaders or replay inputs;
- refuse nonempty directories that lack a prior replay marker or validated
  replay/case manifest;
- permit safe replacement of a prior tool-owned output;
- leave all rejection targets untouched.

The replay marker is `.gfx-replay-output.json`; a validated `replay.json`,
`replay-report.json` or portable `case.json` is also accepted for compatibility.

### Evaluation severity is monotonic

`evaluate_replay()` now keeps `invalid` once a consumer or transfer check has
failed. A subsequent seeded/stale-resource condition can no longer downgrade it
to `partial`. A combined invalid-transfer plus external/zero-generation resource
regression asserts this.

### Verification

- 83 focused provenance/checkpoint/output/case/native tests pass.
- Authentic cases: canonical `menu-occ300` remains pixel-exact with partial
  dependency coverage; scene 79 remains four alpha LSB with partial coverage;
  copy-bearing scene 75 remains complete coverage with a known rendering
  divergence and `qualified=false`.
- Portable cases verify and replay; safe reruns over owned output directories
  work.
