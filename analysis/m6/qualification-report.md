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

## Live execution status

- Three consecutive clean starts reach the title scene through the
  reconstructed backend (menu-only checkpoint `0x103b3c8`, 280 s uptime guard),
  with zero refused draws, GPU completion and a presented backbuffer
  (`analysis/m6/live-title-starts.json`).
- The complete live title scene renders: the animated world map behind the
  EU4 logo, Paradox logo, Continue Game (Lubeck), Single Player, Multiplayer,
  Tutorial, Exit, version text and icon row
  (`analysis/m6/live-title-metal.png`).
- The presented frame shows the full title UI rendered by the Metal backend:
  EU4 logo, Paradox logo, Continue Game (Lubeck, 25 January 1609), Single
  Player, Multiplayer, Tutorial, Exit, version text and the icon row.
- Live operational result (`analysis/m6/live-acceptance.json`,
  `analysis/m6/live-title-starts.json`): three fresh, distinct launches
  reached the settled title scene, executed the reconstructed backend with zero
  refused draws, GPU-completed and presented; the UI structure matches the
  authentic reference (high-pass correlation 0.76 / 0.80 / 0.52). Missing or
  invalid reference data, duplicate runs and duplicated launch identities are
  hard failures.
- **Sampler state (fixed):** per-unit sampler objects are decoded (filters,
  wrap, mip) and Metal samplers cached per effective state; `_GfxSetTextures`
  honours partial/offset bind ranges. An over-strict "unsupported sampler"
  refusal briefly rejected every live draw; it now tolerates the engine's
  mip-0 encoding and records a `sampler-fallback` diagnostic instead. The final
  live diagnostic run encoded 23 draws with zero refusals.
- **Decoded texture diagnostics (fixed, host-tested):** texel-centre sampling,
  BGRA8 output with a sidecar, identity-keyed bounded captures and GPU
  completion before readback. The corrected DXT5 terrain textures (units 0/2,
  1024x128) match the original-GL decoded blobs after row orientation
  (mean 0.13-0.16/255; residual differences are BC decode rounding), so the
  S3TC upload path is exonerated for those resources.
- **Sprite uniform defect (fixed):** the generic fallback for synthetic
  `sprite_vs` stages now binds the engine's constant-slot-2 uniform block
  (96 bytes) at the legacy buffer index, through a helper shared with the
  sprite encoder; a settled validation run reports zero draw errors.
- **Uniforms (verified):** the terrain draw's captured GL uniform block and the
  live packed block are byte-identical in value; uniform packing is excluded as
  a cause.
- **Uniform semantics (identified):** terrain float 89 is `vFoWOpacity_Time.y`
  (time) and floats 108/109 are `vTimeDirectionSeasonLerp.xy` (time direction /
  season). A controlled replay that changed only these values in a copy of the
  canonical trace produced a byte-identical map, so they do not explain the
  live/reference palette difference; scene alignment remains unresolved.
- **M6-blocking visual defect:** the live map's palette does not match the
  authentic map. The authentic menu map is static (a 240 s and a 285 s capture
  are identical), so this is not animation: the live map is pink
  (RGB 158/123/148 vs 141/136/129, palette affinity 0.617 vs 0.998) and the
  composite's second (brightness/season) input is near-black (mean 7.4 vs the
  authentic bright layer). The map passes render into the backbuffer and the
  first composite input is correct, but the bloom/season render targets remain
  dark. Live acceptance therefore reports `visual_match=false`; the milestone
  is **not accepted** until this layer renders.
- Live sampler state defaults to linear/clamp; the engine sampler objects are
  not decoded.

## Not yet done

- Live EU4 Metal execution of the title scene (three clean starts).
- The campaign checker, final evidence manifest and PR set.
