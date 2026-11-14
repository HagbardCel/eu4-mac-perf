# M6 qualification report — authentic title scene through Metal

Status: **M6 accepted.** The canonical 326-event title-menu frame replays
pixel-identically through Metal, the Credits/Options text rendering contract is
corrected at the generic render-target sampling boundary, and the three fresh
independent settled launches listed below pass the acceptance policy including
the new text-orientation check.

## Reference scene

| Field | Value |
| --- | --- |
| Executable | `/Applications/EuropaUniversalisIV/eu4.app/Contents/MacOS/eu4` |
| Executable SHA-256 | `b3d38876abf4e61cdae57509186d7cb7dcb03bfeaca4c95c10c713794715141d` |
| Build | EU4 v1.37.5.0 Inca (491d), GOG, x86-64 |
| GL identity | Apple M5 Max, `2.1 Metal - 91.7` |
| Canonical trace | `.local/m6/frames/menu-occ300/frame` |
| Frame | 1728 x 1117, BGRA8, lower-left, reference SHA-256 `ff2f4e2b…` |
| Events / draws | 326 / 315 |
| Scene mod | Proper 2K UI Enhanced (enabled in the authentic session) |

`menu-15` (observer unit-0 corruption) and `menu-audit` (undirected) remain
superseded. The canonical trace is the only valid pixel reference.

## Offline captured-state replay

`python3 -m automation.m6_frame replay` encodes the canonical frame through the
shared Metal path:

- 326/326 events encoded, 0 failed, `MTLCommandBufferStatusCompleted`;
- 1,930,176 pixels compared, **0 differing including alpha**;
- reference and candidate SHA-256 both `ff2f4e2b…`;
- independent files with alias rejection.

This is a captured-state replay. It does not independently re-render the
upstream terrain pass; the live terrain contribution is qualified by the
settled-frame launches below.

## Text-rendering root cause and correction

### Original GL contract

- `CTextSprite::RecalculateVertices` (`0x1505272`) reads the texture extent
  (`+0x23c` width, `+0x238` height), the sprite rect (`+0x1b0..+0x1b6`) and the
  frame count (`+0x248`), and emits a rotated 4-vertex primitive at 20-byte
  stride. With one frame the V coordinate is **1 at the quad bottom and 0 at
  the top**; the UV convention is the GL bottom-up texture convention. The
  vertices are stored through `CPrimitivesContainer::UpdateVertices` at
  `this+0x250`. Geometry is uploaded only while `this+0x230` is null
  (first-time initialization); later calls reuse the cached container.
- `CTextSprite::RenderToContext` (`0x150455c`) composes
  `M_argument * M_texture(this+0x228+0x450)`, appends `this+0x3c` and the
  frame-dependent V offset `frame * this+0x27c` into a 96-byte block, updates
  constant slot 3, binds the texture at `this+0x230` at unit 0, selects the
  effect's sampler (`+0x20`), blend state (`+8`) and shader (`+0`), then calls
  `CPrimitivesContainer::Render(this+0x250, context)`. The frame index is
  `(param7 - 1) % this+0x248`, overridden by `this+0x54 - 1` when nonzero.
- `CPrimitivesContainer::Render` (`0x14d2c44`) binds one vertex buffer and the
  primitive type; it calls `_GfxDrawIndexed` only when `this+0x18 != 0`, else
  `_GfxDraw`. The text container has no index buffer, so events 310/311 are
  `glDrawArrays` (mode 5, 4 vertices). The indexed base-vertex contract is not
  involved in this defect.

GL events 310/311 bind the 168x63 RGBA8 textures 452/453 at unit 0 with
linear/clamp sampling, alpha blending, program 43 and caller `0x15048ba`.

### First proven divergence

The live vertex trace for the same shader stage shows the **same** first
position `(0,0,1)`, first UV `(0,1)`, stride 20 and UV offset 12 as GL.
Sample dumps record the two 168x63 texture headers with `render_target=1`;
the 1537x192 and 111x111 control textures record `render_target=0`.

The live 168x63 dumps match the GL capture in the **same** orientation
(3.5%/4.0% differing pixels, mean |Δ| 7.7/8.7, differences confined to glyph
antialiasing; all flips differ by 67-77%). The normal-upload controls are the
exact vertical flip of the GL raw rows under the dump's bottom-up convention.

Texel contents therefore agree, the UVs agree, and the first divergence is the
**render-target row origin**: Metal stores a render target top-left while the
engine's GL UVs address row 0 as the bottom. Ordinary uploads sample correctly,
which is why the same shader renders the Tutorial/Exit controls upright and
only the two render-target labels are reflected.

Evidence: [texture-orientation.json](texture-orientation.json),
[menu-frame-evidence.json](menu-frame-evidence.json) (`text_rendering`),
[ref crop](ref-buttons-crop.png), [defective crop](live-title-buttons-crop.png).

### Minimal correction

`eu4_gfx_prepare_sampled_textures` runs before the draw encoder opens, converts
each **bound render-target** texture into a cached GL-row shadow by encoding the
existing V-flipping resolve into the draw's own command buffer, and records the
target generation. `eu4_gfx_texture_for_sampling` binds the shadow. Ordinary
textures, `_GfxResolve` copies and engine sprite UVs are untouched; the change
is generic for any directly sampled render target. No Credits/Options-specific
branch exists.

Native regression: `tests/gfx_live_draw_host.mm --sampled-rt-orientation`
renders a per-pixel asymmetric 4x4 pattern into a render target, checks GPU
completion, requires the exact vertically converted rows, and verifies an
ordinary texture is returned unchanged. With the pre-fix raw-render-target path
it fails (`render target was not converted for sampling`); it passes after the
correction. The test is wired into `tests/test_gfx_live_draw.py`.

## Earlier live contract corrections (retained)

- **Offscreen viewport.** The backbuffer HiDPI ratio was applied to offscreen
  targets; each target now uses its own extent
  (`test_offscreen_viewport_is_not_hidpi_scaled`).
- **Resolve orientation.** `_GfxResolve` copies preserve the GL lower-left row
  convention; `resolve_scaled` flips V and serves every resolve
  (`test_resolve_orientation`).
- **Sampler effective state.** The engine's base min filter and mip filter are
  decoded separately (`test_sampler_effective_min_filter_uses_texture_mip_state`).
- **Shader capability.** `GLEW_ARB_shader_texture_lod` is reported present so
  the authentic 78-source variant is used.
- **Sampler-unit mapping.** Samplers named `texN` bind to unit N, fixing the
  shifted map layers (`test_sampler_binding_preserves_tex_unit_number`).
- **Settled composition.** The settle gate requires 252 terrain calls; the
  settled frame has 252 terrain + 12 map-layer calls, equivalent to the
  reference.

The earlier beige-map and partial-composition descriptions are obsolete; the
settled map is green (mean 72.2, palette affinity 0.967 versus the 0.85 floor).

## Final live verification

One targeted settled launch on the same-command implementation
(`.local/m6/live-text-verify2/run-1`, identity `1794677277-29079`, library
`4e2599…`): it verified the corrected labels before the final guard-only source
delta, so it is supporting evidence and not one of the three final starts.

| Check | Result |
| --- | --- |
| Draw / encode / GPU completion / presentation | all true |
| Refused draws / failed resolves / resolve trace | 0 / 0 / present |
| UI correlations | 0.882 / 0.946 / 0.836 |
| Map mean / structure / palette affinity | 72.245 / true / 0.967 |
| Credits upright vs reflected correlation | 0.905 / 0.486 |
| Options upright vs reflected correlation | 0.937 / 0.513 |
| Text orientation verdict | pass |

`analysis/m6/live-title-final-1728x1117.png` is the **pre-fix** visual and
shows the reflected labels; the corrected final frames are
`.local/m6/live-start{3,4,5}/run-1/swap-frame.bgra` and the tight crops above.

## Three fresh independent starts

The acceptance evidence is [live-acceptance.json](live-acceptance.json) and
[live-title-starts.json](live-title-starts.json).

| Run | Identity | UI correlations | Map mean / affinity | Credits (up/flip) | Options (up/flip) | Accepted |
| --- | --- | --- | --- | --- | --- | --- |
| `live-start3/run-1` | `1794678332-33516` | 0.882 / 0.946 / 0.836 | 72.24 / 0.968 | 0.905 / 0.486 | 0.937 / 0.513 | yes |
| `live-start4/run-1` | `1794678828-35589` | 0.882 / 0.946 / 0.836 | 72.23 / 0.968 | 0.905 / 0.486 | 0.937 / 0.513 | yes |
| `live-start5/run-1` | `1794679203-37056` | 0.882 / 0.946 / 0.836 | 72.23 / 0.967 | 0.905 / 0.486 | 0.937 / 0.513 | yes |

All three runs used library SHA-256 `ac85e360…`, presented the frame, had
zero refused draws, zero failed resolves, a non-empty resolve trace, and
completed on the GPU. `clean_starts = 3`, `distinct_runs = true`,
`distinct_identities = true`, `ok = true`.

## Tests run on the final head

- `tests.test_gfx_live_draw` — 24 passed (including the new
  `test_sampled_render_target_orientation`).
- `tests.test_m6_frame`, `tests.test_m6_live_accept`, `tests.test_m4_accept`,
  `tests.test_m5_accept`, `tests.test_m5_replay_cache`, `tests.test_gfx_texture`
  — 77 passed.
- Additional graphics suites (`test_gfx_shader`, `test_gfx_shader_stage`,
  `test_gfx_simple_draw`, `test_gfx_m5_sprite`, `test_gfx_contracts`,
  `test_gfx_default_target`, `test_gfx_compare`) — pass.
- Native host tests enable the Metal validation layer; no relevant validation
  errors were produced by the new path.

## Verdict

**MERGE** once the final-head CI and independent review are clean and the three
starts below pass. The text orientation defect is corrected at the reusable
render-target sampling boundary; map orientation, palette, composition and the
offline replay remain correct.

## Independent review (final head)

The final head was reviewed independently with no blocking findings. The
reviewer confirmed the same-command-buffer ordering, ordinary-texture
preservation, shadow lifetime and the absence of a feedback/double-flip in the
accepted frames, and re-ran the acceptance on `live-start3/4/5` with results
identical to the recorded evidence. Non-blocking notes, left as documented
follow-ups rather than changing the qualified renderer:

- The cached shadow generation is marked when the flip is encoded, not when
  the command buffer completes. A draw that is refused after preparation would
  leave the entry marked until the engine re-binds the render target (which
  bumps the generation). The accepted runs have zero refused draws; hardening
  this would require a commit-completion hook.
- The native regression exercises the conversion helper; the live starts
  exercise the generic-draw wiring. A route-level integration fixture would
  additionally pin the call site.
- The acceptance map check is palette/structure based, not a spatial map
  orientation test; map orientation is covered by `test_resolve_orientation`
  and the live visual.
- The opt-in sample-texture sidecar can replace an existing BC sidecar; it is
  diagnostic-only and not part of acceptance.
- `_GfxResolveTexture` does not bump a render-target destination generation;
  no accepted draw samples a resolve destination that is also a 0x19 header.
- The flip allocates one sampler state per rebuilt shadow; the affected UI
  render targets are 168x63 and the offline/live qualification shows no
  performance regression.
