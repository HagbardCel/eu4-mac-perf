# M6 qualification report — complete title/main-menu frame (interim)

Status: **full-frame pixel equivalence passed.** The complete authentic
title/main-menu frame replays through the reconstructed Metal backend with zero
differing pixels under the exact comparison policy. Live in-game Metal
presentation of the title scene is the remaining M6 workstream and is recorded
separately below.

## Reference scene

| Field | Value |
| --- | --- |
| Executable | `/Applications/EuropaUniversalisIV/eu4.app/Contents/MacOS/eu4` |
| Executable SHA-256 | `b3d38876abf4e61cdae57509186d7cb7dcb03bfeaca4c95c10c713794715141d` |
| Build | EU4 v1.37.5.0 Inca (491d), GOG, x86-64 |
| GL identity | Apple M5 Max, `2.1 Metal - 91.7` |
| Frame boundary | `CGraphics::PresentScene` entry/exit (`0x149daf0`) |
| Selection | first frame after 240 s (the menu replaces the loading screen) |
| Frame | 1728 x 1117, BGRA8, lower-left, `frame-reference.bgra` |
| Reference SHA-256 | `c3dab19c…` |
| Events / draws | 378 / 369 (323 indexed, 46 arrays) |
| Shader pairs | 35 (18 programs, 62 stage sources) |
| Textures | 69 distinct |
| Trace | `.local/m6/frames/menu-15/frame` (gitignored) |

The raw trace is machine-readable (`gfx-frame-trace/v1`) and its manifest,
event payloads and reference digest validate (`m6_frame.load_trace`); coverage
reports `ok` with no missing dependency.

## Tooling (reusable)

- `benchmark/eu4_gfx_frame_trace.{c,h}` — complete original-GL frame trace with
  effective per-draw GL state and resources, content-addressed blobs, per-frame
  caches, and two frame-boundary modes (present, scene).
- `benchmark/eu4_gfx_frame_replay.mm` — offline Metal execution of a captured
  frame through the shared encoder, one render pass per event, persistent
  targets, GL/MTL state mapping and readback.
- `automation/m6_frame.py` — capture/translate/replay/compare orchestration.
- `automation/gfx_shader_translate.py` — GLSL 1.20 → MSL (glslang + SPIRV-Cross)
  with explicit layouts and a hash-keyed runtime shader cache.
- `automation/m6_compare.py` — fail-closed differential with region attribution.

## Full-frame Metal execution

378/378 events encoded, 0 failed draws, `MTLCommandBufferStatusCompleted`,
readback of the engine logical extent (1728 x 1117 BGRA8).

## Differential (exact policy, row-origin normalization only)

| Metric | Value |
| --- | --- |
| Pixels | 1,930,176 |
| Exact pixels | 1,930,176 (100%) |
| Mismatched | 0 |
| Max channel error | 0 |
| Mean max-channel error | 0 |
| Mean absolute error (B,G,R,A) | 0, 0, 0, 0 |
| Alpha | compared; equal |
| Replay determinism | two independent runs produce the reference SHA-256 |

The Metal readback is byte-identical to the original-GL `GL_BACK` reference:
Metal `metal-frame.bgra` SHA-256 equals the GL reference digest
`c3dab19c…`. The remaining discrepancy was traced to a state-decoding bug:
recorded GL enums are JSON strings, and the replay's numeric-only helper fell
back to linear filtering for every sampler. Parsing both string and numeric
forms selects the captured filter/wrap/compare state and closes the frame
exactly.

## Isolated draw evidence (reusable capability)

The frame tracer can capture per-draw pre/post target images for selected
callers, and the replay can seed a marked draw with its authentic GL pre-image
and read the isolated result back. This separates one draw's contribution from
the rest of the frame and is independent of the title scene:

| Draw | Program | Result before the enum fix | Result after |
| --- | --- | --- | --- |
| 348 | font (texture 7) | 0 differing pixels | 0 |
| 354 / 357 / 359 / 361 / 365 | font | 2.5k-6.5k differing | 0 (full-frame) |
| 362 / 363 | buttonstate | ~10k differing | 0 (full-frame) |

## Live execution status (remaining M6 workstream)

- Runtime shader cache loading and exact compiler-input admission work: a live
  probe loads the cache, admits the title scene's stages, and keeps the existing
  Simple/sprite identity dispatch.
- The generic live encoder executes real engine draws through the translated
  MSL (a font draw reached, encoded, GPU completed, pixels captured).
- Texture format 16 (GL_LUMINANCE8) and 15 (GL_ALPHA8) are now reconstructed by
  expanding to BGRA8 with the exact GL component semantics.
- Not yet done: live render targets, sampler state decode, non-default blend and
  raster descriptors, depth attachment, and three clean menu starts.

## Not yet done

- Live EU4 Metal execution of the title scene (three clean starts).
- The campaign checker, final evidence manifest and PR set.
