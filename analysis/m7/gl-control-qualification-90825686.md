# Original-OpenGL control qualification — new seal `90825686` (present 72)

Date: 21 November 2026 (Europe/Berlin). Issue: #270. Status:
**Case 2 — the authenticated original-API GL control differs from live GL.
Unqualified. Metal blame disabled.** No Metal shader or comparison rule was
changed after the result. The original-OpenGL control authority was re-pinned
after review fixes to the executor, sampler/polygon handling and verifier; that
changes the reviewed execution path, not the measured residual.

This report records one newly sealed EU4 observation, the reviewed
original-OpenGL control executed against that seal's own restricted replay
inputs, the exact four-channel comparison, and the precise remaining
observable. The historical present-904 seal and its measurements are untouched
and are not combined with this capture.

## 1. Capture provenance and attempt accounting

| Attempt | Session | pid | present | Seal | Result |
| --- | --- | --- | ---: | --- | --- |
| `capture-20261121` | `2f84fbb6-568f-471c-8b08-cb5b99e9bbb7` | 11815 | 72 | no | failed closed `default_checkpoint_not_default`; selected 14/15/16 were checked at the pre-entry binding, before the draw body bound frame 0 |
| `capture-20261121b` | `c4f3821c-1856-43d0-a567-2a0ab43f6dfb` | 13742 | 72 | no | failed closed `default_checkpoint_not_default`; this session inserted offscreen passes, so 14/15/16 were offscreen and the recorded-target guard refused them |
| `capture-20261121c` | `90825686-5154-4173-99b6-d2704893839e` | 13847 | 72 | **yes** | one newly sealed observation; checkpoints 17,18,19,20 |

The third attempt is the one bounded new-seal observation. The first two
launches are capture-configuration hypotheses that failed closed (no seal, no
pixels claimed); they are not combined with the new seal. Each launch
terminated the game, released the lease and restored `settings.txt`
byte-for-byte (`sha256 62b4902a…dd496`).

The harness `capture-report.json` for the third attempt says `failed` only
because no `frame-seal-notification.json` consumer was registered. The
authoritative frame descriptor is valid and was re-verified through
`m6_frame.load_seal`: `sealed=true`, `complete=true`, `trace_complete=true`,
`trace_integrity=true`, `readback_ok=true`, `event_stream_verified=true`,
`operation_count=283`, `draw_count=273`, `clear_count=9`, `copy_count=1`.
All event-stream blobs were re-hashed by the verifier.

| Field | Value |
| --- | --- |
| Capture root | `.local/m7/pr4/capture-20261121c` (gitignored) |
| Frame directory | `capture-20261121c/frame` |
| Extent / format / origin | 1728 × 1117, BGRA8, lower-left |
| Manifest SHA-256 | `25a413fc08a280af7db28f3765b924fa9a9fb43c65663960b11863a6db996260` |
| Event-stream SHA-256 | `95697a8e1b217f6f824a4e7cf00c7789bf55810855052dd0c207a69d22081ad3` |
| Reference SHA-256 | `c89481747f63af40face7aa4a3cb8f7b2b970d375de8a6af7c73d1948353a0d5` |
| Seal SHA-256 | `07f54bcee877cc017872cc58e1c15cc641ec5c98e50ed5283d5c081e5da0c8ce` |
| Renderer / Metal | Apple M5 Max, `2.1 Metal - 91.7` |

### First effective selected draw

Event 17 is the first framebuffer-0 terrain draw (`_GfxDrawIndexed`,
`caller_rva=0x10d4e50`, program 301), but it leaves the framebuffer unchanged
(checkpoint 17 == checkpoint 18, 0 differing pixels). Event 18 is the first
terrain draw with an observable effect:

| Pair | Mismatched pixels | Max channel | Alpha | First pixel |
| --- | ---: | ---: | ---: | --- |
| 17 → 18 | 0 | 0 | 0 | — |
| **18 → 19** | **29,144 / 1,930,176** | **184** | **0** | **(326, 0)** |
| 19 → 20 | 54,637 | 183 | 0 | (603, 0) |

These are the live draw's own effects (pre-draw image → later checkpoint), not
a renderer differential. The primary pair is **draw 18 → checkpoint 19**, with
checkpoint 18 (`137637b6…`) as the exact pre-draw colour seed and checkpoint 19
(`ab379ee4…`) as the post-draw live reference. This is the first checkpoint
changed by a selected draw in the new seal and the same class of first terrain
draw as historical 904. The 29,144-pixel change is not the GL-control residual;
that residual is the separate 347-pixel comparison in section 4.

## 2. Input provenance and coverage

Derived by `automation.gfx_gl_replay_input.derive_evidence` from the seal's
verified event graph (never caller JSON). Under the gate revision that executed
the control, evidence status was `proven`. Under the corrected gate the
drawable's missing internal-format identity is an explicit missing observation:
`status=missing`, `proven=false`, `drawable_equivalence=unproven`. The
correction does not change the measured pixels; it withdraws the claim that the
offscreen control target is proven equivalent to the live drawable, so the
current gate refuses to authorize a control from this capture before it runs
the renderer.

| Input | Observation |
| --- | --- |
| Drawable | `samples=0`, `sample_buffers=0`, depth 32, stencil 8, RGBA 8/8/8/8, double-buffered, `drawn_framebuffer_binding=0`, extent 1728×1117. **Captured-state completeness only:** the live default drawable's internal colour/depth/stencil component formats and render-target path were not captured, so live-drawable equivalence is unproven |
| sRGB / dither / multisample | `GL_FRAMEBUFFER_SRGB=false`, `GL_DITHER=true`, `GL_MULTISAMPLE=true` |
| Draw/read buffer | `GL_BACK` (`0x405`) both |
| Colour seed | pre-draw checkpoint 18, BGRA8 lower-left, `137637b6…24ba` (the same bytes as checkpoint 17; draw 17 had no effect) |
| Reference | post-draw checkpoint 19, BGRA8 lower-left, live output `ab379ee4…f494c` |
| Depth seed | pre-draw checkpoint 18, f32, 1728×1117, `d3cee941…d257`; depth test `LESS` with write |
| Stencil | test disabled; checkpoint records `status=absent` (bits 8); no stencil dependence |
| Blend | disabled (src/dst/equations recorded) |
| Scissor / viewport | disabled / full 1728×1117 |
| Cull / front | back / CCW |
| Polygon | fill, no offset, factor/units 0 |
| Alpha test | disabled |
| Colour mask | 1,1,1,1 |
| Logic op | disabled (`mode=0x1503`, not applied) |
| Depth range | [0,1] |
| Program | 301, vertex `5399fb6f…`, pixel `08f6d1c0…`; 15 samplers + `UNIFORM0[23]`/`UNIFORM1[8]` float4 arrays |
| Textures | 15 units, 72 levels, original blocks: DXT1_ALPHA, DXT3, DXT5; uncompressed RGB8/RGBA8 levels with their captured sampler state |
| Geometry | indexed, `GL_TRIANGLES`, 24,576 indices, 2-byte indices, first/base/vertex-origin 0; two typed attributes (position 4×float, texcoord 2×float) |
| Clear/prior history | framebuffer-0 clear at event 10, full extent, masks full; no unknown/target-less writer before draw 18; no intervening writer between 18 and 19 |

The package is content-addressed, excludes the target checkpoint blob, and
contains only the authorised pre-draw inputs. The renderer received no frame
directory, live path or reference digest.

## 3. Renderer execution and authentication

| Field | Value |
| --- | --- |
| Authority | `automation/gfx_gl_control_authorities.AUTHORITIES` |
| Renderer | `m7-original-gl-control`, driver `automation/gfx_gl_control_renderer.py` |
| Executed driver SHA-256 (pre-fix) | `77d152c5d9e5822a852be746621ed930f04af02aa538ecaa9fd1fbcb2faf1af6` |
| Executed native executor SHA-256 (pre-fix) | `d03228553f3f31f30e92a5cde588f879220a71b912826a1a12c8a9292d74164b` |
| Executed replay-input verifier (pre-fix) | `831050f051a3b8097431d71fafe96775878422b6c268483360a3eaa17d179526` |
| Executed receipt signer (pre-fix) | `a72d7fccc5430be1192ea010bc5e432266341dafc82bc4c33941445ea54da8ce` |
| Public key | `b129e1bcd834600e8a3986159b7d9a540d1951f09f30ce3bfd4881844247a727` |
| Challenge nonce | recorded in the qualification document |
| Receipt SHA-256 | `c3d6017066d80bfb66b690ba70bf6af3e17116dca1e9fed4d6c5687c2701fea0` |
| Execution | `completed`, one draw, `gl.error=0`, target samples 0 / depth 32 / stencil 8 / RGBA8 / 1728×1117 |

The Ed25519 receipt binds the nonce, renderer and component digests, frame/
stream/draw/checkpoint identity, package digest, output digest/dimensions/
format/origin and the reported target properties. `attribution()` re-hashes the
renderer, request, package, receipt and components and recomputes equality.
The authority entry was independently reviewed before the live run
(`review-pr3` at `ec034fbd…` and at the capacity head `92f84b41…`).

Those digests are historical: they identify the execution that produced the
recorded image. Post-review fixes changed the driver, native executor,
replay-input verifier and receipt signer, so the current authority registry pins
different digests. The historical receipt is not presented as a signature over
the current execution path, and a signature alone is not proof that a renderer
made OpenGL calls; that property follows from the reviewed implementation and
the digest-pinned path. Under the corrected gate this capture stops earlier,
at `drawable_equivalence=unproven`, so no new authorization is derived from the
old receipt.

## 4. Exact four-channel comparison

| Field | Value |
| --- | ---: |
| Pixels | 1,930,176 |
| Mismatched pixels | **347** (0.01798 %) |
| Alpha-different pixels | **0** |
| Max channel error | **10** |
| Mean max-channel error | 0.000204126 |
| Mean absolute error (B,G,R,A) | 6.8388e-05, 8.9629e-05, 9.5328e-05, 0 |
| First differing pixel | (402, 0) |
| Differing bounds | x 342, y 0, width 267, height 141 |
| Live SHA-256 | `ab379ee45973b64ad7d2cd8ce3094b76f086ce71389823fccdcbe842afaf494c` |
| Control SHA-256 | `41903af84cf2f6d3e2aaaf6c43a8bccaa6b25c1e3f6c5c2db4ee185f909b899a` |
| Live path | `.local/m7/pr4/capture-20261121c/frame/blobs/default-checkpoint-ab379e…bin` |
| Control path | `.local/m7/pr4/control-out-b/renderer-run/result.bgra` |

The distribution is a 1-LSB effect: 359 one-unit channel differences
(R 140, G 136, B 112), 9 two-unit, and only a handful up to 10; both signs
occur. The residual is confined to the painted terrain tile. Every pixel the
draw did not touch is exact, so the colour seed, FBO readback, orientation,
channel order and framebuffer format are exact; the difference is in the
draw's own raster/filter path.

Qualification: `status=unqualified`, `qualified=false`,
`metal_blame_allowed=false`, `missing_observation` names the residual;
statistics and both digests are preserved. The tool exit code is 0 with a
non-qualified verdict (not an error).

## 5. Discriminating hypotheses tested offline

All tests reuse the sealed capture; no additional EU4 launch.

| # | Hypothesis | Change | Result |
| --- | --- | --- | --- |
| H1 | GL dithering on the FBO explains the 1-LSB residual | `GL_DITHER` disabled in the control input | **Refuted**: byte-identical control SHA `41903a…` and identical 347/10 residual |
| H2 | 32-bit depth representation (fixed vs float) is the difference | diagnostic build with `GL_DEPTH_COMPONENT32F` | **Refuted**: byte-identical control SHA and residual |
| H3 | Compressed-block textures lose the draw relative to decoded textures | decoded RGBA8 levels substituted for the original DXT blocks | **Refuted**: 18,246 mismatched pixels / max 10 (much worse); the original compressed blocks are the correct representation |
| H4 | The drawable's depth width is not 32 | 24/16-bit target variants | **Not a discriminator**: the driver cannot create a 24/16-bit legacy drawable that reports 24/16 depth (it reports 32 and the control refuses/does not complete) |

These rule out dither state, depth fixed/float representation and texture
representation as the cause. The residual remains a small draw-path
precision/rounding difference.

## 6. Missing observable and next scientific step

The control's target matches every **queried** drawable property (samples,
sample buffers, depth/stencil/RGBA bit depths, extent), but the capture does
not record the default drawable's **internal pixel format / attachment
component types and render-target path** (the CGL pixel-format attributes and
the window layer's internal colour/depth format). An offscreen RGBA8 FBO with
the same bit depths is not proof that its rasterisation, texture-filter and
depth-comparison rounding are the window's. The residual is consistent with
that remaining difference: untouched pixels are exact, while 1.2 % of the
drawn tile is 1 LSB (plus a few larger edge values).

The remaining observable is owned by #278, which is an M7 scientific
continuation rather than an engineering item from the archived harness review.
The capture-only plan is recorded in
[issue-278-capture-plan.md](issue-278-capture-plan.md). It records the
effective live CGL pixel-format attributes, the default drawable's colour/
depth/stencil component formats where queryable, the window/back-buffer and
render-target path, renderer/profile and sample configuration, and explicit
unavailable values where the driver cannot expose a property. Queries must be
verified on a synthetic native context before any new EU4 launch, and at most
one newly justified sealed capture is permitted. The new seal's live output
must be compared against an independently authenticated GL control; the 904 and
90825686 seals must not be reused or relabelled. The exact BGRA criterion
remains zero differences. If an equivalent offscreen target cannot be
established, investigate a controlled original-drawable or in-process
diagnostic before further speculative replay changes.

## 7. Metal comparison

The GL control is unqualified, so `metal_blame_allowed` remains false. The
existing offline Metal attempt for this seal did **not** produce a replay image
or comparison statistics: `.local/m7/pr4/shadow/` contains 91 translated shader
artifacts plus `shader-receipt.json` (`artifact_count=91`,
`checkpoint_count=10`) and a checkpoint request, but no Metal result BGRA for
draw 18 / checkpoint 19. The Metal replay is therefore **incomplete**, no
same-seal Metal statistic exists, and none is invented here. It is not combined
with historical 904 outputs and cannot authorize Metal attribution while the GL
control differs.

## 8. Pull requests, exact heads and independent reviews

| Stage | Scope | Reviewed head | Reviewer verdict | Integration |
| --- | --- | --- | --- | --- |
| PR1 | Qualification integrity, reference isolation, signed execution contract | `dd3d2bdb76c5a6186f6a7bfedb033647068591ea` | APPROVE (`review-pr1`, exact head) | commit on the #279 integration branch |
| PR2 | Selected-draw capture completeness | `90076b7d8ffc080b962800e990a6306f7430fef4` | APPROVE (`review-pr2`, exact head) | commit on the #279 integration branch |
| PR2a | Checkpoint target ordering (live-launch fix) | `28909cda80e63016af744b774ac0e4bd49fe4e48` | APPROVE (`review-pr2`, exact head, two-event production probe) | commit on the #279 integration branch |
| PR3 | Independent original-OpenGL renderer | `93a7c9110c15be49cac922ac5f261753e2bed005` | APPROVE (`review-pr3`, exact head) | commit on the #279 integration branch |
| PR3a | 16-sampler / 12-level capacity | `92f84b4137f15fb8ded80f2b763d28b064dea010` | APPROVE (`review-pr3`, exact head, rebuild digest checked) | commit on the #279 integration branch |
| PR3b | Registered authority | `ec034fbd67d20c0b027d95acbf00efc24c2dc4b8` | APPROVE (`review-pr3`, exact head, digests/build/key verified) | commit on the #279 integration branch |

Those intermediate reviews are **not** merges to `main` and are not the final
review. `main` was `1ebaddd640f5f2cec6d137de1b130b9e3ba59f66` before #279;
the intermediate stages live only on the `m7/final-integration` branch that
#279 integrates. `ebf902a7` (registry documentation) and `c15438bc`
(attestation wording) are also commits on that branch, not on `main`. The
historical present-904 seal is unchanged; the 904 bytes and measurements were
not regenerated, relabelled or combined. The final #279 head and its independent
review are recorded in the completion report after the post-review fixes.

## 9. Test evidence and launch count

| Selection | Result | Run |
| --- | --- | --- |
| PR1 focused (`qualification`, `ed25519`, `agent_tools`, `gl_control`, `m6_frame`, `frame_graph`) | 329 ran / 328 executed / 1 skipped / 0 failures | independent PR1 re-review |
| PR2 focused (capture/qualification/graph/agent/checkpoints/writer) | 459 ran / 457 executed / 2 skipped / 0 failures | independent PR2 confirmation |
| PR2a focused (`m6_frame`, `frame_trace_writer`) | 105 ran / 0 failures | independent PR2 ordering review |
| PR3 preflight | 22 ran / 0 failures | independent PR3 review |
| PR3a capacity preflight | 223 ran / 0 failures | independent PR3 capacity review |
| Full verbose offline discovery on the PR1 base | exit 0, no failures | `EU4_TEST_PROFILE=offline EU4_OFFLINE_ONLY=1 python3.14 -m unittest discover -s tests -v` |
| Final full offline gate attempts | **did not complete** — two attempts hung in pre-existing native harnesses (`test_gateway_runtime_harness`, and an OpenGL-polling host), with no test failure produced; the run was stopped rather than claimed | `test_runner run --profile offline --label m7-final`; bounded verbose rerun |
| Final changed-module suites | 329 + 459 + 105 + 22 + 223 ran, 0 failures | focused runs listed above |
| End-to-end qualifier probe (synthetic frame + real driver/native/signature) | `qualified=true`, exact, attribution allowed | independent PR3 review |

EU4 launches for this experiment: **3** — two configuration hypotheses that
failed closed without a seal (target-guard ordering; event-index shift), and
one sealed observation. No launch was made after the seal. The Metal replay is
offline. Launch 1 and 2 each consumed one process, were terminated, and
restored settings; no pixel result was claimed from an unsealed frame.

The final full-suite gate is recorded as **incomplete, not green**: after the
seal, two attempts to run the whole offline discovery stopped in pre-existing
native harnesses (one OpenGL-polling subprocess; one
`test_gateway_runtime_harness`) that are unrelated to the changed files. The
runner produced no test failure; the runs were stopped rather than converted
into a pass. The earlier full verbose discovery on the PR1 base exited 0, and
every changed-module suite is green at the final heads. The gateway/runtime
harness hang is an environment limitation of this session, not evidence of
pixel correctness.

## 10. Decision

**Case 2 — non-exact scientific stop.** The independently reconstructed
original-API OpenGL control executes the selected draw from the new seal's
authenticated inputs and produces a valid, signed, authority-bound image that
differs from live GL by 347 of 1,930,176 pixels (max channel 10, alpha 0). The
exact criterion is not lowered, the Metal shader is not changed, no authority
is manufactured, and `metal_blame_allowed` stays false. Issue #270 is closed as
**unqualified**, with the precise remaining observable (the default drawable's
internal pixel/depth format and render-target path) transferred to a separately
owned issue.
