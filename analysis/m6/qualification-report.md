# M6 qualification report — title scene through Metal (interim)

Status: **offline captured-state replay passes; live visual acceptance is not
met.** The canonical 326-event title-menu frame replays byte-identically
through Metal, and the isolated terrain draw now starts from the authentic GL
pre-image and reproduces its contribution to within a handful of channel
steps. A post-fix live launch renders the complete title scene and removes the
magenta palette, but its map is roughly twice as bright as the authentic
reference and the UI gate is also below threshold, so M6 is **not accepted**
and PR #189 remains Draft.

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
| Superseded | `menu-15` (observer unit-0 corruption) and `menu-audit` (undirected) |

The canonical trace is the corrected menu-caller occurrence-300 capture with
the non-invasive observer. It remains the only valid pixel reference.

## Harness correction (PR #189, Phase 1)

`benchmark/eu4_gfx_frame_replay.mm` encoded a synthetic black clear into an
uncommitted command buffer, uploaded the authentic pre-image in a separately
committed blit, and committed the clear-bearing buffer afterwards. GPU order
became upload -> clear -> draw, destroying the pre-image. That defect produced
the earlier all-black isolated terrain candidate.

The isolated path now:

1. initializes required attachments (and optionally replays the recorded
   prefix for depth) and drains them;
2. uploads the authentic pre-image and fails clearly if the transfer does not
   complete;
3. captures the pre-draw image so the caller can verify it;
4. encodes the selected draw and drains it;
5. captures the post-draw image.

A supplied pre-image is never followed by a color clear. A synthetic black
clear remains only for the explicit no-pre-image diagnostic. Depth is
inspected: disabled/`GL_NEVER`/`GL_ALWAYS` tests need no reconstruction; a
relevant test without recorded prior depth is reported as
`cleared_unverified`; `--depth-from-prefix` replays the recorded events so the
test has a defined input.

Regression:
`tests/test_m6_frame.py::IsolationOrderingTests.test_pre_image_survives_the_selected_draw`
uploads a distinctive texture, paints a 2x2 region, and requires every other
pixel to survive. It fails under the old ordering and passes after the fix.

## Selected-event interface (Phase 2)

`automation/m6_frame.py replay --only-event N [--depth-from-prefix]` drives the
native host, captures both sides, and records the isolated metadata.
`automation/m6_frame.py isolate --event N` reports the event/caller/target,
GL and Metal pre/post digests, both changed-pixel counts, and the GL–Metal
post-image statistics. The all-events mode is unchanged.

## Terrain isolated evidence (Phase 3)

The 252 terrain pre/post pairs in `.local/m6/frames/menu-terrain/frame` were
reused; no new EU4 capture was taken.

| Event | Program | GL pre = Metal pre | Changed pixels GL / Metal | Post difference |
| --- | --- | --- | --- | --- |
| 20 | 331 | yes (`ecd15f85…`) | 73 / 73 | 18 pixels, max 1/255 |
| 92 | 331 | yes (`8e94fe58…`) | 2177 / 2177 | 685 pixels, max 8/255 |

Both draws have depth testing enabled with the authentic pre-image and replay
the recorded prefix for depth. The exact pre-image match and the near-identical
post-images are an interpretable differential for these draws. This qualifies
those draws only; it does **not** certify the whole terrain pipeline, and the
captured-state full-frame replay still does not independently re-render the
terrain pass.

## First live material divergence (Phase 4)

The live generic route applied the backbuffer HiDPI ratio
(`target/logical`) to every target, including offscreen render targets. The
864x558 bloom/season target is allocated at its logical size, so a full-target
viewport was scaled to 432x279 and rendered into the top-left quarter:

- before: `sample-u1` content covered exactly 25% of the target, alpha mean
  63.75, near-black;
- after the correction: 100% coverage, alpha mean 255, mean 112/112/113.

Correction: `eu4_gfx_draw_route_logical_extent` gives the backbuffer its
window logical extent and every offscreen target its own extent. Native
regression:
`tests/test_gfx_live_draw.py::GfxLiveDrawTests.test_offscreen_viewport_is_not_hidpi_scaled`
(1024/1024 pixels covered; fails with the old route inputs).

After this fix the magenta/pink map palette is gone. The remaining divergence
is the beige/snowy map itself: the live composite MainScene is already the
season-colour-balanced image (mean 112/112/113), while the authentic MainScene
is the uncomposited green terrain map (mean 65/76/73). The resolve trace below
shows this is not a previous-frame feedback bug; the resolve faithfully copies
a current but already-beige terrain batch. The first resolve source is beige
from the first menu resolve onward.

## Post-fix live verification and MainScene lifecycle (Phases 4–6)

The first post-viewport-fix run removed the magenta palette but still failed the
visual gate. The follow-up investigation used the existing `verify-1` artifacts
plus one targeted resolve/order launch before changing code again.

### Resolve sequence (observed, not inferred)

The targeted trace records 1,753 `_GfxResolve` calls. The first call is:

| Field | Value |
| --- | --- |
| Sequence | 1 (draw sequence 737, clear sequence 113) |
| Source | gameColorTarget, 3456 x 2234, BGRA8Unorm |
| Destination | MainScene header, 1728 x 1117, BGRA8Unorm |
| Status | `scaled_ok` |
| Source mean | 113.17 / 112.21 / 113.04, alpha 247.13 |
| Destination mean | 113.23 / 112.26 / 113.06, alpha 247.13 |

The destination hash equals the MainScene sample at the composite. The
frame-start default clear immediately preceding the terrain batch is valid:
sequence 109, flags 7, color `[0,0,0,1]`, status 0, geometry/clear generation
1, and it targets the same `gameColorTarget` pointer as the terrain draws.
There is no missing clear, failed resolve or wrong-frame source in this
interval.

### What the terrain draw does

The live terrain draw at caller `0x10ce96e` has vertex positions and UVs
byte-identical to captured event 24:

- position offset 1,439,536, UV offset 1,439,548, stride 28;
- first live position `(2719.0, 32.8, 1188.0)` and UV `(0,0,1)` match the
  captured event 24 values exactly.

Its textures, uniforms, depth/blend/raster state and shader translation also
match the capture. Replaying event 24 with the live time/season uniforms
changes only ~244 pixels and leaves its mean colour unchanged.

### Corrected interpretation: frame composition

The earlier hypothesis that MainScene was a previous composite fed back is
**not supported** by the resolve trace. The resolve faithfully copies a beige
*current* backbuffer. A caller-level order trace shows why the live frame is
not equivalent to the reference:

| Batch | Reference (`menu-occ300`) | Live (`live-order`, 280 s and 320 s) |
| --- | --- | --- |
| Draws before resolve/composite | 276 | 256 |
| Terrain caller `0x10ce96e` | 252 | 231 |
| Map-layer caller `0x10d4e50` | 6 | 9 |

The composition is stable at both 280 s and 320 s minimum uptime: it is not a
startup transient. The live terrain draw inputs and a single isolated draw
match the capture, and the resolve is correct; the frames simply do not
represent the same map update composition/state. The remaining acceptance
failure is therefore **frame/reference alignment**, not a demonstrated resolve
or single-draw rendering defect. No speculative renderer change was applied.

### Sampler effective state (fixed)

The live sampler decode conflated the engine's base min filter and mip filter:
every unit decoded as min `0x2601`, mip `0x2701`, and the encoder disabled
mipmapping for all textures. The capture uses `GL_LINEAR_MIPMAP_NEAREST`
(`0x2701`) exactly for textures with more than one level and `GL_LINEAR` for
single-level textures. `eu4_gfx_effective_min_filter` now selects the engine
mip filter when the bound texture has mip levels and the base min otherwise.
Regression:
`test_sampler_effective_min_filter_uses_texture_mip_state`.
This improved palette affinity only marginally (0.435 -> 0.452), so it is a
correctness fix, not the root cause of the beige map.

### Latest live verification

Run `.local/m6/live-verify2/run-1`, identity `1794640399-68635`:

- executed, draw encoded, GPU completed, presented, zero refused draws;
- UI high-pass correlations 0.757 / 0.825 / 0.484 (logo below 0.5);
- map mean 143.4 vs 71.9, palette affinity 0.452 (floor 0.85);
- `visual_match=false`, `accepted=false`.

The three clean-start qualification was not attempted and is not claimed.

## Offline captured-state qualification

The final implementation was re-run against the canonical frame:

- 326/326 events encoded, 0 failed, `MTLCommandBufferStatusCompleted`;
- 1,930,176 pixels compared, 0 differing, alpha equal;
- reference and candidate SHA-256 both `ff2f4e2b…`;
- independent files, alias rejection active.

This is a captured-state replay. It does not validate the live terrain batch,
the resolve, or the composite lifecycle.

## Tests run on the final head

- `tests.test_m6_frame` — 26 passed.
- `tests.test_gfx_live_draw` — 21 passed (including the offscreen viewport
  regression).
- `tests.test_m4_accept`, `tests.test_m5_accept`, `tests.test_m5_replay_cache`,
  `tests.test_m6_live_accept` — 41 passed.
- `tests.test_gfx_texture` — 1 passed.

## Verdict

**DO NOT MERGE.** The harness ordering defect and the offscreen viewport defect
are fixed and regression-tested, and the isolated terrain draws are now
credible evidence. Live M6 acceptance still fails on the map colour/brightness
and the Paradox-logo UI correlation. The resolve copy is faithful; the next
work is the cumulative live terrain batch (blend order/state or an overlay
pass) that turns a correct single draw into a beige map, followed by the
legacy sprite/text composition defect, then three new clean starts.
