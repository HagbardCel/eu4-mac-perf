# M6 qualification report — complete title/main-menu frame (interim)

Status: **reference and full-frame replay established; full-frame equivalence
not yet closed.** This report records the current evidence so the campaign can
resume at the exact remaining discrepancy. It does not claim M6 completion.

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
| Exact pixels | 1,910,472 (98.95%) |
| Mismatched | 20,343 (1.05%) |
| Max channel error | 121 |
| Mean max-channel error | 0.178 |
| Mean absolute error (B,G,R,A) | 0.106, 0.133, 0.132, 0.027 |
| Alpha | compared; means 252.05 (GL) vs 252.17 (Metal) |

The mismatch is **localized**: the whole frame is exact except the bottom
frontend button band (`Single Player` / `Multiplayer` / `Tutorial` / `Credits` /
`Options` region, framebuffer y 8..68, x 696..1031). The map, logo, panels and
remaining UI are pixel-identical. The diff is a smooth low-magnitude difference
across the two `buttonstate` quads plus their labels, with individual glyph
pixels up to 121.

## Remaining blocker (next action)

Localize the button-band difference. Current evidence:

- The `buttonstate` draws (program 43, callers `0x15048ba`) use a 168x63 RGBA8
  texture with nearest min/mag and alpha blending over the map.
- The same font shader renders other labels exactly, so the font stage itself is
  not globally wrong; the band difference is consistent with the button quad's
  sampled texels (a sub-texel/pixel offset or blend-precision difference).
- Next diagnostics: capture the pre-draw target image for the selected button
  draw (per-draw pre-image option), replay it seeded with that authentic prior
  content, and compare the isolated contribution; that separates quad sampling
  from compositing.

## Not yet done

- Full-frame equivalence has not passed the committed policy.
- Live EU4 Metal execution of the menu (three clean starts) has not been
  implemented/validated; the current live backend still admits only the
  M4/M5 effects.
- The campaign checker and final evidence manifest are not yet written.
