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
is later in the chain:

- the live composite's MainScene input is already the season-colour-balanced
  (beige) image, mean 112/112/113;
- the authentic composite MainScene is the uncomposited green terrain map,
  mean 65/76/73;
- the presented live map is therefore about twice as bright as the reference
  (map mean 144 vs 72, palette affinity 0.435 against a 0.85 floor).

That is consistent with a render-target lifecycle/resolve-order defect in
which a previously composited frame is fed back as MainScene and the bloom add
compounds brightness. It is recorded as the remaining blocker; no speculative
brightness adjustment was applied.

## Post-fix live verification (Phase 6)

Run `.local/m6/live-final/verify-1`, identity `1794635347-64349`, pid 64418:

- executed, draw encoded, GPU completed, presented, zero refused draws;
- UI high-pass correlations 0.755 / 0.821 / 0.482 (logo region below 0.5);
- map mean 144.023, structure present, palette affinity 0.435;
- `visual_match=false`, `accepted=false`.

The run was not repeated blindly. The three clean-start qualification was
therefore not attempted and is not claimed. The pre-fix `final-1..3` runs are
historical (pink palette) and must not be reused.

## Offline captured-state qualification

The final implementation was re-run against the canonical frame:

- 326/326 events encoded, 0 failed, `MTLCommandBufferStatusCompleted`;
- 1,930,176 pixels compared, 0 differing, alpha equal;
- reference and candidate SHA-256 both `ff2f4e2b…`;
- independent files, alias rejection active.

This is a captured-state replay. It does not validate live terrain
reconstruction, the resolve, or the composite lifecycle.

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
and the Paradox-logo UI correlation. The next work is the resolve/composite
lifecycle (why MainScene is a composited beige frame), followed by the legacy
sprite/text composition defect, then three new clean starts.
