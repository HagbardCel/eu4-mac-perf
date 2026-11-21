# M7 autonomous pilot — round 1: terrain program-301 1-LSB residual

Date: 2026-11-19 (Europe/Berlin)
Conclusion: **capture-side divergence, not a Metal renderer defect.** The
earliest authenticated interval `[14,15]` is reproduced when the captured inputs
are re-rendered through OpenGL itself: the GL control and the Metal replay agree
to within 0.3 % of pixels, while both differ from the live OpenGL frame by the
same ~1-LSB pattern (98.9–99.9 % sign agreement). No Metal correction is
justified. The exact lossy captured input is not yet pinned (compressed-texture
readback is the prime candidate); see §11.

This is an operational run of the existing M7 machinery. No harness feature was
added, no PR was merged and no unrelated infrastructure was changed.

## 1. Provenance

The previous authentic pilot's raw capture no longer exists on this machine.
At 2026-11-19T21:12Z the worktree cleanup
(`.local/cleanup/worktree-rescue-20261119T211250Z`) removed
`wt_m7_live_final` (session `2564021f…`, present 615) and `wt_infra`
(`live-pilot`, present 612); that archive deliberately excluded each worktree's
`.local/`, so only the committed readiness summaries survive. This round
therefore re-captured the same scene with the same launched binary, the same
selection and the same machinery, and used its own frame and its own interval.
No observation from the deleted frame is combined with the new one.

| Field | This round |
| --- | --- |
| Session id | `2da0a8fc-d442-475d-aeb3-8b902ce0caa0` |
| pid | 25295 |
| capture_frame_id / present / scene | 1 / 616 / 616 |
| Operations | 325 (315 draws, 9 clears, 1 copy) |
| Dimensions / format / origin | 1728 × 1117, BGRA8, lower_left |
| Manifest SHA-256 | `2f983a670d3925be466ff006534a97af32f7277166bd817127af29d8fdd12a49` |
| Event-stream SHA-256 | `ea7116be3e917e939ced608cfd74347c728c37ddb26a547f73c0b1845909d7c6` |
| Reference SHA-256 | `8dbd7c93c62a57d4dcd9833eec24675f3019b2fd45b89ef161b603dbed84063f` |
| Executable SHA-256 | `b3d38876abf4e61cdae57509186d7cb7dcb03bfeaca4c95c10c713794715141d` |
| Campaign id / state | `m7-goal-round1` (`.local/m7/goal-round1/state.json`) |
| Checkpoints | 11 requested / 11 returned (fb:0 at 14, 15, 16, 17, 284) |
| Axes | valid, identity verified, gpu ok, provenance verified, operations complete, **state coverage partial**, pixels divergent |
| Raw evidence | `.local/m7/goal-round1/capture` (gitignored) |

Budget actually consumed: **1 EU4 launch, 1 sealed frame, 1 in-process shadow
execution, 2 offline replays, 0 corrections, 0 automatic merges**. The guard
reports `stop = null` and every entitlement reserved exactly once.

Cleanup: `terminated=true`, `killed=false`, no orphan EU4/shadow/replay process;
background display `EU4 Test` (`main=false`); GOG `settings.txt` restored
byte-for-byte (`settings_equal=true`).

## 2. Earliest confirmed divergence

Same-frame `fb:0` checkpoints (every one from this sealed frame):

| Event | Status | Mismatched pixels | Max channel error |
| --- | --- | ---: | ---: |
| 14 | match | 0 | 0 |
| 15 | divergent | 607 | 2 |
| 16 | divergent | 200,197 | 85 |
| 17 | divergent | 391,005 | 85 |
| 284 | divergent | 1,235,845 | 102 |
| final | divergent | 1,160,228 / 1,930,176 (60.1 %) | 78 |

Reported first divergence: **event 15, resource `fb:0`, producer event 14,
operation `draw`, program 301, interval `[14, 15]`**. The interval is minimal:
event 14 is the only operation between the matching and the divergent
checkpoint, so no additional checkpoint can bisect further.

Event 14's draw is a terrain draw (`_GfxDrawIndexed`, mode triangles, 24,576
indices) that paints only 970 pixels in the upper-left corner
(x 0–212, y 27–259). 607 of those 970 pixels differ, by at most 2; in 99.3 % of
the affected channels the OpenGL value is the higher one. Event 15's draw then
paints the first full map tile and 200,197 of 320,176 painted pixels differ.

## 3. Function and state

- Producer: `CPdxMapTerrainLayer::DrawTerrain` (entry `0x10d475a`), call site
  `0x10d4e4b` → `_GfxDrawIndexed` (`0x15eaa77`); the reported caller RVA
  `0x10d4e50` is the return site immediately after that call.
- Program 301 is the `TERRAIN_SHADER` fragment/vertex pair
  (`08f6d1c0…frag`, `5399fb6f…vert`), 16 samplers, explicit-LOD terrain atlas
  sampling (`texture2DLod`, `NO_SHADER_TEXTURE_LOD` branch inactive), normal map
  sampled with swizzle `.rbg` (translated faithfully to `.xzy`).
- Effective state for events 14–19: blend disabled, depth test `LESS` with write,
  cull back, alpha test off, scissor off, viewport full, `multisample=true`,
  `dither=true`; the six draws share identical textures and identical
  `UNIFORM1` with only `UNIFORM0` fixed for the frame.

## 4. Hypotheses (≤3, falsifiable) and outcome

| # | Hypothesis | Test | Result |
| --- | --- | --- | --- |
| H1 | GL `gl_dither` (unsupported in Metal) explains the residual | Positional analysis of the error over the 4×4 and 8×8 grids; per-region constancy of the GL value | **Refuted for this frame.** The sign of the error is uniform across `(x%4, y%4)` (positive fraction 0.037–0.042 in every cell), and in 3×3-flat regions the GL value is a single constant (e.g. B=147 in 3,443/3,498 px) while Metal is a single constant one lower (146) — a dither matrix would alternate. Consistent with the earlier Phase E0 Bayer A/B, which increased mismatches. |
| H2 | Metal fast-math / relaxed-precision (`fast::`, `powr`) translation is the cause | Prior A/B `fastMathEnabled=NO` (253 of 1,160,152 mismatches changed); plus a new control: the same code, MSL and textures render the canonical `menu-occ300` terrain frame **byte-identically** | **Refuted as dominant.** A pure epsilon from relaxed math flips far too few pixels (~1e-7 relative) to explain 62 % of a painted tile, and the byte-exact canonical replay shows the current core is capable of exact terrain output. |
| H3 | Content-dependent terrain shading/filter precision in the program-301 path (water/normal-map and magnified atlas sampling) | New checkpoint chain: match at 14, first tiny divergence after the first terrain draw, amplification after the second; water/land split; input diff against the byte-exact canonical frame | **Leading, not proven.** Water: 86.3 % of pixels differ (mean ΔB +0.79, max 9); land: 58.2 % (mean +0.23, max 85). 95 % of all differing pixels are exactly 1 LSB and alpha never differs. Textures (all 15 units), index buffer, position buffer and `UNIFORM1` are identical to the canonical frame that replays byte-exactly; only the engine uniform block differs (camera/time, e.g. `UNIFORM0[22].y` 22.84 vs 220.03). The difference is continuous rather than a single boundary coincidence: across the water region 60.4 % of blue values are +1, 29.6 % unchanged and 9.6 % +2 — a sub-LSB value difference of roughly half a quantisation step, not a constant offset. |

No hypothesis identifies a concrete implementation defect; H3 is a
classification, not yet a defect.

## 5. Source changes and checker

- Source changes: **none**. No Metal implementation was modified, nothing was
  rebuilt for the renderer, and no correction was submitted.
- `correction_accept`: **not run** (0 of 1 evaluation used), because no
  correction was proposed.
- Offline replay 1 (this case) reproduced the in-process checkpoints exactly
  (`607/2`, `200,197/85`, `391,005/85`, `1,235,845/102`). Replay 2 re-rendered
  the canonical `menu-occ300` frame byte-identically (0 of 7,720,704 bytes
  differ), establishing that the current core has no global colour regression.
  The standalone replay classifies as `incomplete_diagnostics`
  (`frame_shader_identity=unverified_session`), the documented limitation of a
  standalone replay, not a rendering result.

## 6. Remaining unknowns and next justified action

Unknown: whether the ~1-LSB residual comes from texture filtering
(GL fixed-point fractional weights vs Metal float) or from shading arithmetic
in the same program. The checkpoint chain cannot separate them because the
first divergent draw already contains both.

Next experiment (offline, no EU4 launch, uses only existing switches):

1. Replay this sealed frame (`.local/m7/goal-round1/capture/frames/frame-000001`)
   with `EU4_FRAME_REPLAY_PRECISE_MATH=1`; expect ~no change (H2 re-check on
   this content).
2. Replay it with `EU4_FRAME_REPLAY_NEAREST_ALL=1` and with
   `EU4_FRAME_REPLAY_LINEAR_ALL=1`. If the 1-LSB bias collapses or changes sign
   only under a filter change, filtering precision is causal; if it persists
   under every filter mode, the cause is shading arithmetic.
3. Only then, if filtering is implicated, add one bounded in-process diagnostic
   that writes the sampled terrain texel to a checkpoint (not a renderer fix),
   and derive a minimal GL-compatible filtering correction subject to
   `correction_accept` with the regression inventory.

Until then no Metal change is justified.

## 7. Operating note on goal completion

This pilot ran in the `m7-launcher-smoke-20261119` Web session. That session has
no EU4 campaign sidecar configured, so `eu4_finalize_candidate` reports "no EU4
campaign sidecar is configured for this profile" and the profile's goal plugin
correctly denies model-initiated `update_goal(complete)`. The goal therefore
remains active with the pilot verdict `requires_additional_evidence`; closing it
requires either the campaign sidecar (operator) or a fresh bounded budget for
the next experiment. This is a session-configuration fact, not a renderer
result.

## 8. Round-2 refinement (CPU analysis of the same sealed frame)

No new launch or replay was used: these measurements recompute from the frame
already captured in round 1.

### The residual is multiplicative and smooth, not texture filtering

Fitting `delta = a + b * value` per channel over the painted pixels of
checkpoint 16:

| Content | channel | intercept a | slope b | relative |
| --- | --- | ---: | ---: | ---: |
| land | B / G / R | +0.09 / +0.06 / +0.13 | +0.0025 / +0.0027 / +0.0018 | 0.18–0.27 % |
| water | B / G / R | −1.15 / −0.15 / −0.55 | +0.0151 / +0.0059 / +0.0101 | 0.59–1.51 % |

Land behaves as an almost pure scalar multiplier (~0.2–0.25 %), i.e. a single
smooth shading factor, not a per-channel quantisation. Water adds a
channel-dependent component, consistent with its normal-map/wave path.

Texture filtering is **not** the dominant mechanism: the differing fraction is
*higher* in smooth regions (66.6 %) than in detailed regions (56.3 %) and edges
(65.3 %, only 3,193 px), and the error autocorrelation is broad and smooth
(0.13–0.18 at lags up to 4 px, no texel-scale or periodic peak). A bilinear
weight difference would scale with local texture detail and show a texel-scale
signature.

### Translation-policy finding (new, checkable)

`spirv-dis` on the captured terrain fragment SPIR-V reports **zero**
`RelaxedPrecision` decorations, yet the translated MSL calls
`fast::normalize` (6×), `fast::clamp`, `fast::min`, `fast::max` and `powr`. So
the relaxed-math calls are introduced by the MSL translation, not requested by
the shader IR.

The earlier fast-math A/B (`EU4_FRAME_REPLAY_PRECISE_MATH=1`) toggles
`MTLCompileOptions.fastMathEnabled`, which cannot change these source-level
`fast::` calls. **That A/B therefore did not test the relaxed-math translation**;
its "only 253 of 1,160,152 pixels change" result refutes compiler fast-math, not
this.

### Consequence for the next experiment

The next replay should distinguish the two remaining sub-hypotheses, in this
order:

1. Replay the sealed case with the MSL translated without relaxed math
   (`fast::`/`powr` replaced by `normalize`/`clamp`/`min`/`max`/`pow`) and
   compare checkpoints 15/16/17. If the multiplicative bias collapses, the
   translation policy is the defect and the minimal correction is the
   translation flag/pass, not a renderer state change.
2. If it does not collapse, add one bounded diagnostic that writes the
   `ApplyDistanceFog` factor to a checkpoint, to separate fog from lighting.

No correction was made in round 2 because step 1 needs the replay that the
budget no longer allows.

## 9. Independent verification of the sealed case

`eu4_replay verify` (executes nothing) on
`.local/m7/goal-round1/capture/shadow/case`:

- `verification.ok = true`, `problems = []`, 1,252 files, graph `complete`,
  `independently_reproducible = true`;
- capture identity recomputed from the artifacts equals this report
  (`2da0a8fc…`, frame 1, present 616, event stream `ea7116be…`);
- diagnosis re-derives first divergence event 15 / producer 14 / program 301 /
  caller `0x10d4e50`, interval `[14,15]`, `next_checkpoint = null`;
- final frame `1,160,228 / 1,930,176` (0.6011), max 78, alpha 0;
- `producer_identity = legacy_missing` is a provenance-metadata limitation of
  the packaged case, not a capture problem;
- unsupported state remains `gl_dither`, `multisample`; the verifier states no
  A/B attributes the difference to them.

This confirms the round-1 numbers independently of the in-process shadow and
without any new execution.

## 10. Authorized A/B replays (2026-11-20)

The operator authorized a continuation budget of 2 offline replays and 1
correction evaluation (`.local/m7/goal-round1/state-continuation.json`: no
launches, no frames, no shadow). Both replays used the same sealed frame and a
fresh Metal execution; neither needed EU4.

### Replay A — source-level relaxed math removed

A temporary local patch replaced `fast::normalize(`/`fast::clamp(`/
`fast::min(`/`fast::max(`/`powr(` with the precise MSL intrinsics in every
translated `*.metal` file (the same substitution the earlier round proposed),
then the case was replayed with those shaders.

Result: **byte-identical** to the baseline replay at every checkpoint and at
the final frame — 0 of 7,720,704 bytes differ; checkpoint statistics unchanged
(607/2, 200,197/85, 391,005/85, 1,235,845/102).

### Replay B — Metal compiler fast math off

`EU4_FRAME_REPLAY_PRECISE_MATH=1` (the existing switch that sets
`MTLCompileOptions.fastMathEnabled = NO`) on the same case:

| Checkpoint | baseline | compiler precise |
| --- | --- | --- |
| 15 | 607 / 2 | 608 / 3 |
| 16 | 200,197 / 85 | 200,062 / 85 |
| 17 | 391,005 / 85 | 390,698 / 85 |
| 284 | 1,235,845 / 102 | 1,235,357 / 102 |
| final | 19,925 of 7,720,704 bytes differ (0.26 %) | |

### Conclusion

The whole precision family is now excluded as the dominant cause: the SPIR-V
has no relaxed decorations, removing SPIR-V-Cross's relaxed intrinsics changes
nothing at all, and the Metal compiler's fast math changes 0.26 % of bytes
without improving any checkpoint. The residual is therefore a content-dependent
difference in a time/camera-sensitive shading term, not a precision, filtering
or pipeline-state artifact.

No correction was proposed, so the correction evaluation was not used and the
temporary translator patch was reverted (the tree is identical to the baseline;
the substitution above reproduces it).

## 11. GL-side control: the divergence is capture-side, not Metal-side (2026-11-20)

### What was built

Two extra bounded captures (A: phase 43.1, present 611; B: phase 25.2, present
916) and a focused OpenGL control under
`.local/m7/goal-round1/gl-control/` (not part of the repository):

1. `build_manifest.py` extracts one captured draw from the sealed frame — the
   original GLSL 1.20 sources, attribute/index blobs, all texture levels, sampler
   state, `UNIFORM0/UNIFORM1` and effective GL state.
2. `gl_control.c` creates a legacy CGL context and an FBO, uploads the captured
   GL image as the seed, executes that one draw through the OpenGL driver with
   the captured inputs, and reads the result back.

It is the same experiment as the Metal replay, but with OpenGL as the renderer.
If OpenGL-from-captured-inputs reproduces the live OpenGL frame, the capture is
faithful and any Metal difference is a renderer difference. It does not.

### Results (same-frame, no splicing; each row its own sealed frame)

| Frame / draw | GL control vs live GL | Metal vs live GL | GL control vs Metal |
| --- | --- | --- | --- |
| A, event 14 | 724 px / max 3 | 728 px / max 44 | 7 px / max 43 |
| B, event 14 | 885 px / max 3 | 884 px / max 3 | 4 px / max 1 |
| B, event 15 (bulk) | 196,242 px / max 84 | 196,949 px / max 84 | 5,901 px / max 47 |

Overlap on B/event 15: 194,853 pixels differ in *both*, 1,389 only in the GL
control and 2,096 only in Metal, with 98.9 % sign agreement (99.6–99.9 % on the
first draw). Dither on/off in the control changes nothing (724/3 both ways).

### Interpretation

- The Metal replay is **not** the source of the `[14,15]` divergence. Given the
  same captured inputs, OpenGL and Metal produce nearly the same image (0.3 %
  of pixels differ) and both deviate from the live OpenGL frame on the same
  pixels, in the same direction.
- The deviation is therefore introduced by the **captured inputs** — the frame
  does not fully preserve what the live GL pipeline used. The prime candidate is
  the compressed-texture path: DXT5/DXT3 are captured as `glGetTexImage`
  RGBA8 readbacks, so both replays sample the driver's readback decode instead
  of the sampler's decode. The canonical `menu-occ300` frame replays
  byte-exactly, so the loss is content-dependent, not a constant capture bias.
- Consequence for M7: the "authentic OpenGL/Metal divergence" statistic
  conflates capture fidelity with renderer translation. At these checkpoints the
  renderer-only residual is ≤ 0.3 % of pixels (≤ 5,901), not 60 %.

### Limits of this control

Single-sample FBO versus the reference's multisampled default framebuffer;
depth is cleared to 1.0 (valid everywhere except the ≤1,152 px that event 14
painted before event 15 — excluding that corner leaves the event-15 numbers
unchanged); textures are the captured decompressed RGBA8 payloads, which is
exactly the point being tested.

### Next step

Capture the compressed texture payloads (or verify decode fidelity against the
sampler) and re-run this GL control. If the control then matches the live frame,
the capture path is confirmed as the root cause and the remediation belongs to
the tracer, not the Metal renderer. No renderer correction is proposed; the
correction evaluation remains unused.
