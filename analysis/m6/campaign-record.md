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
