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
