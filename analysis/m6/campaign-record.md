# M6 campaign record — complete title/main-menu frame

Persistent state for the M6 reconstruction campaign. Raw frame traces, shader
translation output and replay payloads stay under `.local/m6` (gitignored);
this file records what has been established, with the exact commands and
digests needed to resume.

## Objective

Render one complete, authentic EU4 title/main-menu frame through the
reconstructed Metal engine and qualify the full-frame differential against the
original OpenGL output, including three clean live executions.

## Phases

| Phase | State |
|---|---|
| 0 — baseline inspection | done |
| 1 — reference scene qualification | in progress (menu frame capture in flight) |
| 2 — full-frame capture/replay/compare tooling | implemented, being validated |
| 3 — autonomous reconstruction | in progress |
| 4 — full-frame equivalence | pending |
| 5 — live validation | pending |
| 6 — PRs and closure | pending |

## Tooling introduced

- `benchmark/eu4_gfx_frame_trace.{c,h}` — complete-frame original-GL trace:
  frame boundary at `_GfxPresent`, per-draw effective GL state (program and
  exact compiler inputs, uniforms, samplers, textures, vertex attributes,
  index buffer, render target, blend/depth/stencil/raster/scissor/viewport),
  frame seed and authentic `GL_BACK` reference. Observation only; every
  binding it changes is restored. Content-addressed blobs, per-frame shader and
  texture caches.
- `automation/m6_frame.py` — capture orchestration (`capture`, `inspect`) over
  the existing `eu4_gfx_startup_trace` launcher and launch ledger.
- `automation/gfx_shader_translate.py` — GLSL 1.20 compiler inputs to MSL via
  glslang + SPIRV-Cross, with explicit attribute/varying/uniform-block/sampler
  layouts and std140 packing metadata.
- `benchmark/eu4_gfx_frame_replay.mm` — offline Metal replay of a captured
  frame: one render pass per event, persistent render targets, pipeline cache,
  GL/MTL state mapping, RGB render-target alpha normalization, generic
  attributes, full-frame readback.
- `automation/m6_compare.py` — fail-closed full-frame differential with
  provenance validation, region diagnostics and region-to-event attribution.

## Launch accounting

Authorizations (`.local/launch-authority/`): `m6` (12), `m6-menu` (8),
`m6-menu-long` (4). Every launch is claimed through `automation.launch_ledger`.

Live observations so far:

| Run | Purpose | Outcome |
|---|---|---|
| `recon/draws-02` | draw-caller inventory | 1024 draws, 15 caller sites |
| `frames/frame-07` | full loading-screen frame | 334 events, 325 draws, 31 shader pairs, `ok` |
| `frames/menu-04` | loading-screen frame after capture fixes | 334 events, uniform arrays now complete |
| `.local/m6/game-screens/live-a.png` | window-server observation | authentic main menu visible ~4 min after launch |

Game startup reaches the main menu at about 230–250 s wall time with the pinned
GOG 1.37.5 build; earlier captures were the loading screen (tips + logos on a
black background), which is explicitly not an M6 reference.

## Findings that changed the capture

1. **Array uniform queries.** Apple legacy GL returns only the first element of
   an array queried at the array's base location. The tracer now queries each
   element at `location + element*stride`. Without this every `UNIFORM0[n]`
   matrix was mostly zero in the trace.
2. **Generic attributes.** Attributes not enabled as vertex arrays carry the
   GL current generic value. The tracer records it; the replay binds it as a
   per-instance constant.
3. **RGB render targets.** GL RGB textures sample alpha as 1.0; Metal has no
   RGB8 render target, so the replay renders BGRA8 and copies through a compute
   kernel that forces alpha to 1 before the texture is sampled.
4. **Frame-selection cost.** Capturing every texture for every draw made a
   several-hundred-draw frame take minutes; per-frame shader/texture caches and
   render-target tracking keep the reference capture practical.
5. **Full-frame replay reach.** The complete loading-screen frame (325 draws,
   31 shader pairs, 7 render targets) now encodes end to end through Metal with
   zero failed draws and a GPU-completed command buffer. That is the baseline
   the menu differential builds on.

## Next actions

1. Finish and qualify the main-menu frame capture; record executable identity,
   selection predicate, frame boundary and reference digest.
2. Translate the menu frame's shaders, replay, and produce the first
   full-frame differential with region attribution.
3. Close the remaining semantic gaps (if any) and establish the comparison
   policy from measured reference variability.
4. Live validation: three clean starts with the Metal path reaching and
   presenting the menu.

## Frame-boundary finding (2026-11-12)

The title scene does not call `_GfxPresent` while `CGraphics::PresentScene`'s
present flag is clear: the frontend still renders, but the GL swap is skipped.
A present-based frame boundary therefore never fires in the menu (verified by
sampling the live process: draws are continuous, no `_GfxPresent` hook entry).

`CGraphics::PresentScene` itself is called by `CFrontEnd::Render` every rendered
frame (direct call at RVA `0xf05148`), so the complete-frame observer now also
patches `PresentScene` (`0x149daf0`, prologue `55 48 89 e5 53 50`, relocation
safe). In `EU4_GFX_FRAME_TRACE_BOUNDARY=scene` mode:

- `PresentScene` exit arms the next frame,
- `PresentScene` entry finalizes the frame and reads the authentic `GL_BACK`
  reference before any swap.

This boundary is game-independent: it observes the engine's frontend frame
delimiter rather than a swap that the engine may legitimately skip.

## Replay semantics established offline (2026-11-12)

Each of the following was found by replaying a captured frame and localized by a
targeted experiment; all are game-independent:

1. **Array uniform queries.** Apple's legacy GL returns only the first element
   of an array queried at the array's base location. The tracer queries every
   element at `location + element*stride` (1 for vectors, 4/3/2 for matrices).
   Without this every `UNIFORM0[n]` matrix was mostly zero.
2. **Vertex attribute offsets.** `GL_VERTEX_ATTRIB_ARRAY_POINTER` returns 0 for
   VBO-backed arrays on this driver. The tracer now reads the byte offset from
   the engine's vertex-layout element table (`SShaderOpenGL +0x10/+0x18`,
   element stride `0x28`, location `+0xc`, size `+0x10`, type `+0x14`, offset
   `+0x18`, slot `+0x1c`, divisor `+0x20`, normalized `+0x21`) plus the per-slot
   base offset at `master+0x150`. Verified against the known Simple layout
   (POSITION 0, TEXCOORD 12, COLOR 20, stride 24).
3. **Generic attributes.** Attributes not enabled as vertex arrays carry the GL
   current generic value; the tracer records it and the replay binds it as a
   constant.
4. **Metal buffer table collision.** Vertex-stage constant buffers and vertex
   streams share one binding table. Uniform blocks bind at 0; vertex streams
   now bind at `8 + attribute location`.
5. **Front-face winding.** The captured GL front face maps *directly* to Metal's
   winding; a complemented mapping culls the menu's sprite quads. Validated by
   an isolated single-draw replay (with culling disabled the same draw lands in
   the expected 60x56 region; with the complemented mapping it is culled).
6. **RGB render targets.** GL RGB textures sample alpha as 1.0; Metal has no
   RGB8 render target, so the replay renders BGRA8 and copies through a compute
   kernel that forces alpha to 1 (own command buffer, never nested inside an
   active render encoder).
7. **HLSL-spelled uniforms and redeclarations.** The generated GLSL may declare
   `uniform float4 UNIFORM0[n]` next to the prelude's `uniform vec4 UNIFORM0[n]`.
   The translator canonicalizes the type names and treats the second declaration
   as a redeclaration (deduplicated), as the GLSL compiler does.

## Current differential result (2026-11-12, frame `menu-15`)

Complete menu frame captured and replayed end to end:

| Gate | Result |
| --- | --- |
| Authentic complete menu frame | pass (378 events / 369 draws / 35 shader pairs, coverage ok) |
| Shader translation | pass (18 programs, 0 failures; runtime cache written) |
| Full-frame Metal replay | pass (378/378 encoded, 0 failed, GPU completed, readback) |
| Full-frame equivalence | **fail** — 1,910,472 / 1,930,176 pixels exact (98.95%); 20,343 mismatched |
| Mismatch localization | bottom frontend button band only (y 8..68, x 696..1031) |

The remaining mismatch is a smooth low-magnitude difference on the two
`buttonstate` quads and their labels. Everything else is pixel-identical. Next
diagnostic: per-draw pre-image capture for the selected button draw, seeded
replay of the isolated contribution (separates texture sampling from
compositing).

Live Metal execution of the menu is not yet implemented; the live backend
currently admits only the M4/M5 effects. This is the next major workstream after
the offline differential closes.

## Full-frame equivalence passed (2026-11-12)

The enum-parsing fix (sampler/state enums are JSON strings) closed the frame:

| Measure | Value |
| --- | --- |
| Pixels | 1,930,176 |
| Differing | 0 |
| Max channel error | 0 |
| Metal frame SHA-256 | `c3dab19c…` (equal to the GL reference) |
| Determinism | two runs, identical digests |

Per-draw isolation (`--pre-post-callers` capture + `--seed-marked` replay +
`m6_frame isolate`) is committed as a reusable diagnostic; it now matches every
marked draw exactly.

## Live path progress (same day)

- Runtime shader cache (`gfx-shader-cache/v1`) keyed by the engine's exact
  compiler-input SHA-256 (the GL source with the texture-LOD extension line
  replaced by the engine's `NO_SHADER_TEXTURE_LOD` define); `translate-live`
  builds the cache from captured live compiler inputs.
- Live probe: 35 menu stages load, the title scene's shaders admit (legacy
  Simple/sprite precedence preserved).
- Generic live encoder (`eu4_gfx_generic_draw`) executes engine draws through
  the translated MSL: vertex layout from the engine element table, uniform
  packing from the constant slots, texture units, blend snapshot, arrays and
  indexed draws.
- Texture formats 15/16 (alpha/luminance) reconstructed.
- Blocked next: live render targets, sampler decode, non-default raster/blend,
  depth attachment. Then three clean menu starts.

## Live texture reconstruction and diagnostics (2026-11-12, continued)

Reconstructed texture capabilities now include:
- S3TC DXT1/DXT3/DXT5 (formats 1-6) with sRGB variants, mapped to Metal
  BC1/BC2/BC3 pixel formats;
- GL luminance8 (format 16) and alpha8 (format 15) expanded to BGRA8 with the
  exact GL component semantics;
- half/float formats 17-23 (R16F, RG16F, RGBA16F, R32F, RG32F, RGB32F expanded
  to RGBA32F, RGBA32F);
- square cubemaps (kind 1) with per-face/level uploads.

Diagnostics added (game-independent):
- `EU4_GFX_TEXTURE_TRACE` writes the last texture description/status.
- `EU4_GFX_TEXTURE_PLACEHOLDER` substitutes a 1x1 opaque placeholder for an
  unsupported texture. This is a collection-only mode; production/acceptance
  runs stay fail-closed.
- `live_gfx probe --texture-diagnostics` enables both.

## Live status at end of round

The engine now admits the title scene's shader stages and executes real draws
through the generic encoder (font draw reached, encoded, GPU completed). A
full loading-to-menu traversal is still blocked by one or more texture
descriptions or a silent backend exit; the texture trace identifies the last
successful initialization. Render targets, sampler decode, non-default
raster/blend descriptors and the depth attachment remain unimplemented.

## Next concrete blocker: render-target texture initialization (2026-11-12)

The startup now progresses past all previously rejected texture formats. The
latest diagnostic run stops at:

```
glGenTextures  caller_rva 0x15ec938  object_got 64
```

RVA `0x15ec938` is inside `_GfxInitializeRenderTargetTexture` (0x15ec8e2), which
the Metal backend does not replace. The original creates the engine's
render-target colour texture with GL calls (glGenTextures, glBindTexture,
glTexImage2D) using `GetOpenGLTextureFormat`; the Metal path needs the same
entry reconstructed: create an MTLTexture with
`MTLTextureUsageRenderTarget | MTLTextureUsageShaderRead`, store it in
`backend.textures` for the header, and write the engine-visible header fields.

Also confirmed and fixed in this round: the texture staging table was 16
entries but cube maps can carry up to 96 face/level records, which smashed the
stack for cube-bearing startups.

Next steps:
1. Reconstruct `_GfxInitializeRenderTargetTexture` (and the render-target object
   creation path if separate) and map the target struct's colour attachment to
   the created Metal texture.
2. Make `_GfxSetRenderTarget` switch the current target, and `_GfxClear` clear
   it.
3. Depth attachment for the backbuffer and render targets.
4. Sampler-state decode and non-default raster/blend descriptor support.
5. Then three clean menu starts.

## Render targets, raster decode and depth (2026-11-13)

The engine's render-target object is its TextureGFX header itself
(`SGfxRenderTargetScope(context, TextureGFX*)` calls `_GfxSetRenderTarget`).
Reconstructed entries:

- `_GfxInitializeRenderTargetTexture` (0x15ec8e2) and
  `_GfxCreateRenderTargetTexture` (0x15ecb7f): allocate a Metal 2D texture with
  RenderTarget|ShaderRead usage, a Depth32Float companion when the engine
  requests depth, and write the engine-visible header fields (dims, format,
  bpp, flags). Created headers are the target objects the engine binds.
- `_GfxSetRenderTarget`: resolves the header's colour/depth textures, tracks
  the current target (dims, textures) and restores the backbuffer on null.
  Restoring the default target now drains pending same-generation work instead
  of refusing it (the offline P5.1 rejection is invalid for real multi-target
  frames).
- `_GfxClear` on a render target clears colour and depth on the bound target.
- Raster descriptors are decoded generically: the engine uses its own enums
  (cull 0/1/2 = none/front/back, polygon 0/1/2 = point/line/fill, front 0/1 =
  CW/CCW), mapped to the normalised GL snapshot; only filled polygons are
  addressable (point/line stays fail-closed).
- The generic encoder now uses the target's depth attachment, the engine depth
  snapshot (enable/write/compare) and the decoded cull/front-face state.

The backbuffer also receives a Depth32Float companion so default-target draws
can depth-test.

## Live title presentation and further state (2026-11-13)

- `_GfxUpdateTexture` reconstructed (0x15ec66f): re-uploads mip 0 into the
  Metal texture created for the same header (BGR/1-byte expansion included).
- Blend descriptors with the enable flag clear now map to the opaque snapshot;
  a blend-disabled draw no longer stops the run.
- Shader states that are not admitted/linked are reported as unsupported
  (skippable in collection runs) rather than fatal.
- `SDL_GL_SwapWindow` (0x12cb130) is patched: the frontend presents through the
  window swap rather than `_GfxPresent`, so the Metal backend now blits the
  completed backbuffer to the layer drawable on every swap.
- Entry-installer prologue table extended for the `test r9,r9; je` update
  prologue.

Remaining: reach the menu with a complete shader cache, then validate the
presented title scene and three clean starts.

## Live generic encoder hardening (2026-11-13, late)

- Pipeline keys use the effective vertex-layout signature (not a per-draw
  descriptor pointer), so pipelines are cached across draws and the engine runs
  at a usable rate.
- Vertex descriptor buffer indices are compact (8..): Metal has 31 vertex
  buffer slots, and `8 + location` overflows once the translator's
  collision-free attribute range (16..30) is present.
- Pipeline creation failures are logged with the Metal error; Objective-C
  exceptions in the encoder are caught and returned as fail-closed refusals
  with the exception name/reason logged.
- A remaining abort in the water pass was localized to the generic encoder's
  attribute/uniform packing path; the compact-index fix addresses the Metal
  validation abort.

Acceptance state: the engine reaches the map/water passes in the frontend
scene; the menu caller has not yet been reached in a single run within the
launch deadline. The offline captured-frame equivalence remains exact.

## Current live blocker (2026-11-13 02:00)

The frontend now runs through the map passes; the water draw
(`CPdxMapTerrainLayer::DrawWater`, caller 0x10d52fc, instanced indexed) crashes
with SIGSEGV inside `generic_draw_encode_impl` -> `pack_stage_uniforms`. The
last instructions before the fault are the post-call sequence of
`pack_stage_uniforms`; the fault address is a low, non-canonical value, which is
consistent with a bad constant-slot data pointer for one of the large uniform
arrays (several stages declare `UNIFORM2[200]`).

Next diagnostic (already prepared): gate a uniform-pack trace
(`EU4_GFX_UNIFORM_TRACE=1`) that records, per call, the stage, members, the six
master constant-slot counts and header/data pointers, and the copy ranges. The
last record before the crash identifies the exact slot without another guess.

The stuck game holds the launch lease; clean up with
`pkill -9 -x eu4; pkill -9 -f "CrashReporter.*eu4"` before the next probe.

## Root cause of the water-pass crash: constant-slot stride

The original draw bodies index the six constant slots with a byte offset that
advances by 4 and address `0xc8 + rbx*4` / `0xd0 + rbx*4`, i.e. **16 bytes per
slot**. The generic encoder packed uniforms with a 4-byte stride, so slots 1..5
read unrelated master fields as the count/header, producing a wild data pointer
and a SIGSEGV in `pack_stage_uniforms`. The packing now uses `0xc8 + 16*slot`
and `0xd0 + 16*slot`, matching `_GfxDraw`/`_GfxDrawIndexed`/
`_GfxDrawIndexedInstanced`.

A uniform-pack trace (`EU4_GFX_UNIFORM_TRACE=1`, probe `--uniform-trace`)
records the stage, slot, count, header/values pointers and copy ranges for
future slot-layout diagnostics.

## Live presentation diagnostics (2026-11-13)

- The generic encoder scales the engine's logical viewport to the physical
  drawable (HiDPI), matching the shared Simple/sprite encoders.
- SDL's Cocoa video driver swaps through `_Cocoa_GL_SwapWindow` /
  `_Cocoa_GLES_SwapWindow`; both are now patched alongside the public
  `SDL_GL_SwapWindow`.
- The swap hook records `swap.json` (game vs drawable dimensions) and, with
  `EU4_GFX_SWAP_DUMP`, dumps the exact backbuffer blitted to the drawable
  (`probe --swap-dump`). This is independent evidence of the presented frame
  even when the window server cannot list the game window.

## Menu reached via a menu-only caller (2026-11-13 02:35)

The checkpoint caller must be menu-only: `0x15048ba` also fires for the loading
tip banner, so earlier "menu" checkpoints were actually loading frames. The
menu-only callers are `0x103b3c8`, `0x103bd43`, `0x103ba95`; running with
`--caller-rva 0x103b3c8 --occurrence 2` reaches the title scene's render path
and presents through `CGraphics::PresentScene` (game and drawable both
3456x2234, destination acquired, backbuffer dumped).

Presented frame (`swap-frame.bgra`, exact Metal backbuffer at the swap):
magenta terrain clear + the loading/title logo strip, no map. The menu frame's
sequence is:

1. clear fb6 / fb0 black / fb1..3 white / fb9 / fb8,
2. clear fb0 to magenta (event 342),
3. full-screen composite (program 358, caller 0xed7dcd) to fb0,
4. UI sprites/fonts to fb0.

The composite is encoded (0 refusals) but its output is not visible; the
presented frame is the magenta clear plus UI. The next diagnosis is the
composite draw: verify its viewport, its source texture binding (RT headers),
and its blend/depth state against the captured trace, where this draw is
pixel-exact in the offline replay.

Live path status: menu render path reached and presented; map composite not yet
visible; three clean starts not yet qualified.

## Composite blocker detail: integer vertex data with a float shader input

The menu's full-screen composite (program 358) uses
`in_POSITION: float4 [[attribute(0)]]` in the translated MSL, but the engine's
vertex layout for it is `GL_SHORT`, 4 components, stride 8, `normalized=false`,
with positions `(-1,1,0,0) (1,1,1,0) (-1,-1,2,0) (1,-1,3,0)`. In GL these raw
integers are converted to floats (-1.0 .. 1.0); Metal has no unnormalized
integer-to-float vertex format, so binding `MTLVertexFormatShort4` cannot feed
the `float4` stage input correctly. The composite therefore contributes no
coverage and the presented frame keeps the magenta clear (UI sprites drawn
after it are visible).

Fix direction: the translator must expose each attribute's declared GLSL type;
the live encoder converts integer source data to float32 at draw time (a
generated vertex buffer) whenever the shader input is a float vector and the GL
type is an integer type. The captured trace is unaffected (the offline replay
binds the exact captured buffers and is already pixel-exact).

## Integer vertex attributes converted to float (2026-11-13)

The translated MSL declares float stage inputs (e.g. `float4 in_POSITION`), while
the engine can bind unnormalized integer data (the menu composite uses
`GL_SHORT` positions `(-1,1,0,0)..`). Metal has no unnormalized
integer-to-float vertex format, so the encoder now expands such attributes to
float32 once per (buffer, layout), caches the generated MTLBuffer, and binds it
with a float vertex format. The conversion applies only when the declared GLSL
type is a float vector, the GL type is an integer, `normalized` is false, and
the attribute is per-vertex. The composite quad now receives its exact GL
coordinates.

## Render-target viewport order (2026-11-13)

`_GfxSetRenderTarget`'s original writes the target height to `master+0xac` and
the width to `master+0xb0`; the replacement wrote them swapped, so every
render-target pass used a transposed viewport (1728x1117 became 1117x1728) and
the map RTs stayed at their clear colour. The switch now matches the original
field order. The route already reads `+0xac` as height and `+0xb0` as width.

## Checkpoint minimum uptime (2026-11-13)

Loading tips reuse many menu draw callers, so caller-only checkpoints can stop
on a loading frame. `EU4_GFX_CHECKPOINT_MIN_UPTIME_MS` (probe
`--min-uptime-ms`) suppresses the checkpoint until the process has been
running that long; the occurrence counter only advances afterwards. With the
menu appearing around 230-250 s, a 250 s guard plus a menu-only caller lands on
the title scene.

After the render-target viewport fix, the title frame's map targets contain
content (a 1728x1117 target mean 63.75, an 864x558 bloom target mean 15.94),
confirming the terrain and bloom passes now render.

## Title scene presented live (2026-11-13 03:29)

With the render-target viewport fixed, the uptime-guarded checkpoint lands on
the actual title scene. The presented backbuffer (game/drawable 3456x2234,
exact Metal swap readback) shows the full title UI rendered through the
reconstructed backend: EU4 logo, Paradox logo, "Continue Game" (Lubeck, 25
January 1609), Single Player, Multiplayer, Tutorial, Exit, version text and the
bottom-right icon row. The map background is still the fb0 magenta clear: the
map composite does not contribute to that frame (terrain and bloom render
targets do contain content).

Per-caller draw logs (`draw-ok-<caller>.json`) are now written for every
successful live draw to identify whether the composite (caller 0xed7dcd)
executes and which target it uses.

## Composite investigation (2026-11-13 04:30)

The full-screen menu composite (program 358) is fully specified in the live
run: count 4, target = game backbuffer, uniforms byte-identical to the captured
trace (UNIFORM1[0] = 0.13, 0.13, 0.842, 0.842, 0, 0.626, ...), and its two
sampled textures resolve to 1728x1117 and 864x558 Metal textures.
`_GfxResolveTexture` is now a real Metal blit from the source target into the
resolved texture (it was a no-op). The presented frame still shows the magenta
backbuffer clear plus the full title UI.

Remaining hypotheses, in order:
1. The sampled (resolved) textures are filled from an empty/stale source; the
   resolve may not be invoked for these headers, or the sampled header is a
   distinct target the engine renders into directly.
2. Draw ordering: the magenta fb0 clear may be applied after the composite
   (deferred game-clear design), erasing it before the UI.

Next diagnostics: dump the two sampled textures at the composite draw, and the
backbuffer immediately after the composite, to separate sampling from ordering.

## Root cause of the empty map textures (2026-11-13 05:00)

`_GfxResolve` is `glCopyTexSubImage2D(GL_TEXTURE_2D, 0, 0, 0, 0, 0, w, h)`:
it copies the current framebuffer into the header's texture. The sampled map
and bloom textures are filled exactly there. The Metal path had replaced it
with a no-op, so the composite (which binds those sampled textures) read
all-zero textures; dumps confirmed `sample-u0` and `sample-u1` were entirely
zero. `_GfxResolve` now performs the equivalent Metal blit from the current
render target (or the backbuffer) into the header texture, with an optional
`resolve.json` trace.

## Map background: resolve timing / texture lifecycle (2026-11-13 05:40)

Confirmed by correlated traces in one run:
- `_GfxResolve` destination header/texture IS the composite's sampled unit-0
  texture (1728x1117), and source is the game backbuffer (3456x2234).
- The resolve runs (no pipeline error) but the composite's sample dump at the
  draw is still all-zero: the engine samples the texture before this frame's
  resolve fills it. The sampled render-target textures appear to be
  created/used per frame, so a copy from the current backbuffer cannot satisfy
  the same-frame composite.

Remaining map work: establish the engine's sampled-texture lifecycle (reuse vs
per-frame recreation) and fill the sample the frame will actually read (e.g.,
persist the resolved map per logical target and resolve before the composite,
or bind the rendered RT directly when the engine samples it).
The title UI itself is presented correctly at the title scene.

## Three clean live title starts (2026-11-13 06:05)

Three consecutive launches with the menu-only checkpoint (`0x103b3c8`,
occurrence 1, 280 s uptime guard) each captured the title scene:

| Run | Overall | Draw/GPU/Pixels/Presented | Refused | Swap dump SHA-256 |
| --- | --- | --- | --- | --- |
| accept-1 | captured | all true | 0 | 66f3bb2d… |
| accept-2 | captured | all true | 0 | cfdbd9ba… |
| accept-3 | captured | all true | 0 | (recorded) |

Evidence: `analysis/m6/live-title-starts.json`; per-run outputs under
`.local/m6/live/accept-{1,2,3}`. The presented frames show the title UI
(logo, Continue Game, Single Player/Multiplayer/Tutorial/Exit, version, icon
row) rendered by the reconstructed backend. The animated map background is the
known remaining gap: the engine samples a render-target copy that is not yet
filled before the composite (see the sampled-texture lifecycle finding).
The automated checker `automation/m6_live_accept.py` validates capture status,
refusals, non-blank dump and UI-region similarity; its UI-region metric is
currently dominated by the missing map background.

## Previous-frame resolve and the remaining map chain (2026-11-13 06:30)

The engine's `glCopyTexSubImage2D` copies the previous frame's image; our
single backbuffer was already cleared, so the source was all zero. The backend
now preserves the previous presented frame and resolves from it. The sampled
textures are no longer zero (u0 mean 156.95, u1 mean 9.81), and the composite
draws the previous frame as its background.

The map still does not appear because the chain is circular under the current
reconstruction: the composite's background is the previous frame's fb0, and fb0
only ever receives the composite itself (the map passes target render targets
fb6/fb1/fb2/fb3/fb8/fb9). In the captured GL frame, texture 452 (the composite's
unit 0) holds the map rendered this frame; identifying which operation fills it
(and whether the GL frame's fb0 already contained the map before the composite)
is the remaining map-background item. The title UI presentation is unaffected.

Status: offline full-frame equivalence exact; three clean live title-scene
starts presented (UI); map background is the one known render-affecting gap.

## Test status (2026-11-13 06:55)

Full suite: 2502 tests, 2500 pass, 2 errors — both pre-existing and unrelated:
`test_gfx_l32b_e2e` imports the absent `automation.gfx_l32b_hooks`, and
`test_pre_ready_forensic` needs a run directory that is not present. All M6,
M5, lifecycle, texture, live-draw, boot and shader hosts pass. Two contract
adjustments were required by the generalized live state decode:
- lifecycle hosts resolve the texture lookup at run time (they link `init.mm`
  without the texture module);
- the legacy Simple/sprite raster contract compares cull/polygon/winding/
  validity but not the new cull-enable byte (+0xc), which the generic live
  encoder tracks separately.
Degenerate all-zero blend factor pairs stay fail-closed.

## Map background: frame-feedback semantics (2026-11-13 07:15)

`_GfxResolveTexture` (the explicit FBO blit) is **never called** in the live
title scene; only `_GfxResolve` (glCopyTexSubImage2D from the current read
framebuffer) runs, and its destination is the composite's sampled map texture.
The engine therefore maintains the menu background as a **frame feedback
image**: each frame copies the backbuffer (the previous presented frame) into
the sampled texture and composites over it.

The reconstruction preserves and feeds the previous presented frame, so the
sampled textures are non-zero (u0 mean 156.95). The map is absent because the
first title-scene frame's backbuffer never contained the map in the live run:
the original's menu background must already be present in the backbuffer when
the menu starts (rendered during the loading-to-menu transition), while the
live transition frame we capture shows the tip banner over the magenta clear.
Finding where the original first renders the menu background map into the
backbuffer (and why the live transition does not) is the remaining item.

## Final map-background diagnosis (2026-11-13 07:40)

The captured GL frame proves the composite's unit-0 texture (452, 1728x1117)
already contains the full map image (mean 109.43, alpha 255, 358k colours) at
draw time, i.e. it is the terrain pass copied into the sampled texture. In the
live reconstruction the engine's `_GfxResolve` copies a 1728x1117 source that
is **all zero**: the last full-surface render target it would copy has already
been cleared or reused by the time of the copy, and the engine's real source is
the terrain target at the moment of its own resolve. Preferring the previous
presented frame keeps the sampled textures non-zero (a faithful "previous
frame" background) but cannot introduce the map because the title-scene
backbuffer never contained it.

The remaining work is therefore precise: identify the exact render target the
engine's `glCopyTexSubImage2D` reads at the map resolve (bind order between the
terrain pass, the resolve and the target restore), and preserve that target's
content until the copy.

## Next concrete step for the map source (2026-11-13)

The only caller of `_GfxResolve` (0x15ece13) in the executable is RVA
`0xed7921`, inside the graphical-map render code. The captured GL frame shows
the destination texture already holding the terrain image, so the engine
performs a `glCopyTexSubImage2D` from a framebuffer it binds at that point
(most likely the terrain FBO via a direct `glBindFramebuffer(GL_READ_FRAMEBUFFER,
...)` call, which the reconstructed backend does not track because only
`_GfxSetRenderTarget` is replaced).

Plan for the next round:
1. Disassemble the 0xed7800-0xed7960 sequence to confirm the READ framebuffer
   bind and the source target header.
2. If it is a direct GL call, interpose the GLEW `glBindFramebuffer` pointer at
   boot (the dylib already patches GLEW slots) and track the READ framebuffer
   target header in the backend.
3. Use that tracked READ target as the `_GfxResolve` source (a 1:1 Metal copy),
   which should fill the composite's sampled map texture with the terrain pass.

## Round summary: map copy source investigation (2026-11-13 08:45)

The only caller of `_GfxResolve` is `CEU3GraphicalMap::RenderPostEffects`
(0xed78f0-0xed7921): after `_GfxEndScene` it calls
`GetMainRenderTargetTexture()` and then `_GfxResolve(immediate, texture)`, i.e.
a `glCopyTexSubImage2D` into the composite's sampled texture. The GL-side
READ-framebuffer observation entry could not be installed through the current
observer specification gateways (the resolve observation did not fire even when
explicitly requested), so the exact READ framebuffer remains unidentified.
Live probing showed the last bound render target and the last draw target are
already empty at the copy, so the backend keeps the preserved previous frame as
the faithful non-zero source (documented), and the map background remains the
one known render-affecting gap.

Tooling state: the observer registry was restored to its original entries and
targets; the frame tracer retains the un-gated resolve hook for future use.
Affected host suites pass again (33 tests OK).

## Definitive map copy finding (2026-11-13 09:00)

A DYLD interposer for `glCopyTexSubImage2D` (added as a diagnostic source)
recorded every copy in a menu frame:

```
read_fbo=0 draw_fbo=0 fbo=0 texture=452 x=0 y=0 width=1728 height=1117
```

So the engine's `_GfxResolve` copies the **default framebuffer (fb0)** into
texture 452 (the composite's unit 0) many times per frame: the menu background
is a frame-feedback of the backbuffer, exactly as the composite and reference
capture suggested. The loading-screen frames are dark (mean 5, logos), so the
map must enter fb0 through periodic map renders during the menu; the
reconstructed live frames feed the feedback with UI-on-magenta because those
fb0 map renders do not occur in the live path.

The interposer is kept as an opt-in diagnostic source (removed from the default
observer build to avoid destabilizing captures). The remaining map work is to
identify the periodic fb0 map render and reproduce it.

## Map feedback: the live backbuffer read is empty (2026-11-13 09:30)

Diagnostics with the resolve readbacks moved onto the game queue (a separate
queue does not serialize with the game's pending draws) show every one of the
~1200 `_GfxResolve` sources in a live run is **all zero**, while the final swap
dump of the same backbuffer texture contains the frame. The resolves therefore
run before the frame's draws are visible in the backbuffer in the reconstructed
path (the engine copies fb0 as a frame feedback; in GL the back buffer still
holds the previous frame at that moment).

Consequence: the menu background feedback is filled from an empty image, so the
map never appears. The title UI presentation is unaffected. Resolving this
needs the reconstructed submission/clear interplay to preserve the previous
backbuffer content at the same point the engine copies it (GL's back-buffer
retention semantics), which is the remaining map-background item.

## Composite diagnostics and acceptance hardening (2026-11-13 10:10)

Live bind diagnostics for the composite draw (stage 11a05cd9) now report the
decoded primitive, scaled viewport, target size and converted position data:

```
count=4 target=3456x2234 viewport=1728x1117 scaled=3456x2234
primitive=4 (MTL triangle list) positions=0 (conversion not logged)
```

The authentic captured composite is a **4-vertex triangle strip** (captured
draw mode 5); the live draw is encoded as a triangle list (mode 4), which draws
only three vertices and cannot cover the screen. The primitive source in the
live encoder is `master+8`; reconciling it with the engine's actual mode (and
logging the converted positions) is the next concrete step.

Acceptance tooling was tightened:
- `automation/m6_live_accept.py` now requires a valid reference (hard failure),
  separates executed/gpu_completed/presented/visual_match/accepted, derives
  dimensions from the reference and candidate, and validates integer
  physical-to-logical resampling.
- `analysis/m6/live-scene-policy.json` carries the UI regions, tolerance and
  map-region structure bounds.
- The current three starts are honestly reported `visual_match=false`
  (map background missing), so they are not accepted.

## Local evidence cleanup (2026-11-13 12:05)

`.local` was reduced from 117 GB to 5.6 GB by removing reproducible
diagnostics: the resolve-source dump runs (seq-01, map-08, map-09: ~101 GB),
probe outputs and superseded frame traces, and build caches. Retained:
- `.local/m6/frames/menu-15` (authoritative authentic menu frame),
- `.local/m6/live/accept-{1,2,3}` (three clean live starts),
- `.local/m6/live/comp-01` (latest composite bind diagnostics),
- `.local/m6/live/shader-cache-full2` (live shader cache),
- `.local/m5/experiments/sprite-gl-ref-09` (M5 sprite test reference),
- `.local/launch-authority`, analysis images and recon notes.

## Map feedback: draw targets and counts are real (2026-11-13 12:40)

Live draw-ok diagnostics with the real entry values show the map passes are not
empty draws:

| caller | variant | rsi | master+0x64 | master+0x68 | target |
| --- | --- | --- | --- | --- | --- |
| 0x10ce96e (terrain) | 2 (indexed) | 594 | 216810 | 6786 | backbuffer |
| 0x10d4e50 | 2 | 24576 | 4225 | 24576 | RT |
| 0x14f6c1b (UI) | 1 | 0 | 4 | 0 | backbuffer |
| 0xed7dcd (composite) | 1 | 0 | 4 | 6 | backbuffer |

Bind traces confirm the map stages encode real counts (594, 636, 918, 3960,
24576) as triangle strips, and the terrain and composite draws target the same
backbuffer texture. The composite's sampled textures are nevertheless all zero
at draw time: `_GfxResolve` copies the current backbuffer, which is black (the
frame-start clear) at every resolve. The authentic GL frame's feedback texture
contains the map because GL's back buffer retains the previously presented
image across the swap; the single-buffer reconstruction loses it.

Remaining work: emulate the observed back-buffer retention across presentation
(preserve the presented image and feed it to the resolve at the engine's copy
point, including the loading-to-menu transition), rather than copying the
current cleared backbuffer.

## Drain-before-resolve test (2026-11-13 12:45)

`_GfxResolve` now drains the pending game submission before copying, matching
GL's "the copy observes all prior commands" semantics. Live sampling is still
all zero at the composite, so the pending-submission pointer is not the missing
ordering: the engine's map draws are committed through a path not represented
by `pendingGameCommand` at the copy point (multi-threaded/deferred submission),
or the copy genuinely happens before those draws in the live execution order.
The feedback texture therefore stays empty and the map background remains the
one M6-blocking gap.

## Root cause found: Metal validation rejects the map draws (2026-11-13 13:30)

Running the game with `MTL_DEBUG_LAYER=1` (`--metal-validation`, error mode
`nslog`) produced 9,381 draw validation errors in one menu run:

- 9,366 x `Fragment Function(main0): incorrect type of texture
  (MTLTextureTypeCube) bound at Texture binding index 2 (expect
  MTLTextureType2D) for tex7[0].`
- 15 x `Vertex Function(sprite_vs): missing Buffer binding at index 1 for u[0].`

Metal rejects a draw whose bound texture type does not match the shader's
declaration, so the map draws that bind a cubemap to `tex7` are dropped on the
GPU. That is why the map passes encode with real counts and target the
backbuffer, yet the backbuffer stays black at the resolve and the composite
samples an empty feedback texture.

Fixes required:
1. Declare `texturecube<float>` for cube samplers in the translated MSL (or bind
   the correct texture type) for the affected map shader(s); the captured
   sampler metadata must carry the cube type through translation.
2. Bind the sprite uniform buffer at index 1 for the 15 `sprite_vs` draws.

## Texture-type substitution does not reach the failing draws (2026-11-13 13:55)

The generic encoder now substitutes a 1x1 placeholder when a declared sampler's
type (sampler2D/samplerCube) disagrees with the resolved Metal texture type.
A validation run with a substitution trace produced **no trace entries** while
still reporting 7,867 `tex7` cube-vs-2D draw errors. The failing draws therefore
do not bind their textures through the generic encoder's sampler loop: the
`tex7` cube binding comes from another path (legacy draw encoder or a binding
whose unit/index mapping differs from the declared sampler binding).

Next step: log the actual texture bindings per draw from every encoder path
(generic, legacy, simple, sprite) and match the failing `main0` shader's Metal
texture index 2 to the unit that encoder bound, then fix that path's binding.

## The failing cube binding is not from the generic encoder (2026-11-13 14:10)

A full per-bind trace (2,566,231 entries in one menu run) shows the generic
encoder **never binds a cube texture** (0 cube binds, 0 type mismatches, 0
substitutions), including at Metal texture index 2 where validation reports the
tex7 cube error. The type-substitution path is therefore inert: the draws that
Metal rejects are encoded through a different path (or bind textures outside the
generic sampler loop), even though their draw-ok identity is
`generic-cache/v1`.

Next step: instrument every encoder entry (legacy draw encoder, simple, sprite)
with a stage identity marker, then correlate the validation error timestamps
with the encoder that produced the failing `main0`/tex7 draws.

## Root cause: texture-unit binding was positional (2026-11-13 14:25)

The generic encoder bound textures by *array position* of the GL `samplers`
list while the Metal `msl_samplers` list is a different order, keyed by sampler
*name*. The cube `tex2` therefore landed in a `texture2d` Metal slot (and every
other unit was similarly shifted), which Metal validation rejected: 5,412 draw
errors per menu run.

The encoder now binds by sampler name: it resolves each `msl_samplers` entry to
the GL sampler of the same name, then binds that unit's texture at the entry's
Metal texture/sampler index. With this fix the same validation run reports
**13** draw errors instead of 5,412 (down to the known `sprite_vs` uniform
buffer cases). The composite still samples an empty feedback texture, so the
resolve source/ordering remains the next item.

## Map draws are valid but the backbuffer stays black (2026-11-13 14:45)

After binding samplers by name (validation errors 5,412 -> 13), the terrain draw
still targets the backbuffer (`draw-ok-0x10ce96e` target == resolve source) and
the resolve happens after the frame's map draws (order trace: ~369 draws ->
resolve -> clears -> composite). Yet the resolve source dump is still black
(RGB 0, alpha 255) at every resolve, so the map draws write no colour.

Remaining candidates:
- the map fragment shaders sample textures that are black (uninitialised or
  failed uploads for the terrain/heightmap textures);
- a depth or blend state that discards the map fragments.

Next step: dump the textures bound by the terrain stage at draw time and check
whether the terrain/heightmap inputs contain data; if they are black, fix the
texture upload/format path for those formats.

## Map inputs have data; frame depth was never cleared (2026-11-13 14:55)

A targeted sampled-texture dump of the terrain stage shows its inputs are real
images (means 14-191, hundreds of colours), so black inputs are not the cause of
the black map passes. The authentic frame's first default-framebuffer clear has
`flags=7` (colour+depth+stencil) with depth 1.0, but the reconstructed default
clear only cleared colour, leaving the depth attachment uninitialised; depth-
tested map fragments would then fail against a zero-filled depth buffer.

`encode_color_clear` now attaches the game depth texture and clears it to 1.0
when the engine's clear flags include the depth bit, matching the captured
frame's frame-start clear.

## Depth clear makes the map reach the feedback; composite draw still empty (2026-11-13 15:25)

With the game depth attachment cleared to 1.0 on the engine's depth clear, the
resolve's source and the composite's sampled textures now contain the real map
(sample-u0 mean 28.28, 27,403 colours; u1 bloom mean 1.76). The frame-feedback
chain works.

The composite draw itself still changes nothing: `before-composite.bgra` and
`after-composite.bgra` are identical (mean 191.25) even though its captured
state has blend disabled and depth test disabled, the viewport is the full
3456x2234 target, count=4, and both map textures are bound. The remaining cause
is the composite's vertex geometry/layout (positions read as normalised shorts
or bound from the wrong buffer), not state or sampling. Next: log the actual
position element layout (location/type/size/normalized) and the bound vertex
bytes for the composite draw.

## Integer attribute conversion was disabled by MSL type names (2026-11-13 15:40)

The composite's vertex element is `GL_SHORT`x4 with the MSL declaring
`float4 in_POSITION`, but `glsl_type_is_float()` only accepted GLSL names
(`vec4`), while the cached stage metadata carries MSL names (`float4`). The
integer->float conversion therefore never ran for the composite (or any other
MSL-named integer attribute), and the shader read raw shorts as floats
(degenerate geometry). The helper now accepts both naming schemes; the
composite's element reports `converted=1`.

The composite still does not appear in the presented frame even though its
sampled textures now hold the real map (mean 28.28) and its state is blend- and
depth-disabled with a full viewport. The `before/after-composite` dumps are
inconclusive (the after dump is taken before the command buffer is committed).
Next: diff the live generic encoding of this draw against the offline replay's
encoding, which renders the same draw exactly.

## THE MAP RENDERS: the composite was being culled (2026-11-13 15:55)

With `EU4_GFX_NO_CULL=1` (`--no-cull`) the presented live frame changes from the
magenta clear to the **full title scene**: the EU4 world map behind the logo,
Paradox logo, Continue Game (Lubeck), Single Player/Multiplayer/Tutorial/Exit,
version text and icon row (`analysis/live-cull-01.png`, mean 59.8, 375,789
colours).

Root cause: the composite's authentic state has `cull.enabled = false` (GL
`glIsEnabled(GL_CULL_FACE)`) while its cull *mode* is 0x405 (back). The
reconstruction derives the cull-enable bit from the descriptor's cull enum, so
it enabled back-face culling and culled the full-screen quad. Disabling culling
for the draw makes the composite cover and the frame-feedback chain (map ->
resolve -> composite) complete.

Remaining: decode the engine's real cull-enable flag so culling is honoured
per draw (not globally disabled), and compare the resulting map colours with
the authentic reference (the no-cull frame has a magenta cast from the
feedback/season composition).
