# M4 qualification report — original-GL reference and Metal presentation

Status: **qualified by the M4 checker** (`campaigns/eu4-m4/accept.py`). The
authentic original-EU4 OpenGL Simple loading draw was captured, the
corresponding reconstructed Metal output is pixel-identical under the fixed
policy, and the reconstructed frame is now independently observable in the real
EU4 window.

| Gate | Result |
| --- | --- |
| Authentic original-OpenGL reference | **pass** |
| Corresponding reconstructed Metal output | **pass** |
| Fixed-policy differential comparison | **pass** (0 differing pixels / 1,930,176) |
| GPU-completed live Metal draw | **pass** |
| Drawable scheduled and presented | **pass** |
| Independently observable in the game window | **pass** (window-server capture, mean Δ 1.311/255 vs drawable) |
| Offline regressions | **pass** |
| DSH autonomous implementation and remediation | **pass** |

## Baseline and identities

| Field | Value |
| --- | --- |
| Baseline commit | `dd4290bc9165574d81afa1b3ce151cb767031fda` (M3 merge, PR #185) |
| Original executable | `/Applications/EuropaUniversalisIV/eu4.app/Contents/MacOS/eu4` |
| Executable SHA-256 | `b3d38876abf4e61cdae57509186d7cb7dcb03bfeaca4c95c10c713794715141d` |
| GPU | Apple M5 Max, Metal 4 |
| Selected event | `_GfxDraw` at caller RVA `0xedfcd1` (`CEU3Graphics::ShowBasicLoadText`) |
| Draw | `GL_TRIANGLE_STRIP` (mode 5), 4 vertices, first 0, Simple effect |
| Extent | 1728×1117 logical; physical drawable 3456×2234 (2×) |

## Original-OpenGL reference

Captured through the new generic observer in the existing passive observe stack
(`python3 -m automation.live_gfx capture --gl-reference`):

| Field | Value |
| --- | --- |
| Run | `.local/m4/experiments/gl-reference-04` |
| Caller / call id | `0xedfcd1` / 9 |
| Capture | 1728×1117 BGRA8, `GL_BACK`, origin `lower_left` |
| Raw SHA-256 | `69d41a8870679dcb3158bd3f3fad2f4afff11106e45990ab9a3ffc0f5dcc7670` |
| GPU boundary | `glFinish` 1.3 ms, `glReadPixels` 0.5 ms, `gl_error=0` |
| Caps bytes | `0040000001010101` (byte 4 = 1) |
| GLSL digest | `1e5d1d7b0910ec343c5d5da1ec811fa6f2f963157aff251d79bd588391905e7f` |
| Live vertex payload | 96 bytes, SHA-256 `f13dea22399b9649d84a09965ec6b21bc13b72db3c17049916a675922a39c8ab`, V 0..1 |
| Pre-draw target | all black, alpha 255 (authentic clear) |
| Determinism | independent launch `gl-reference-02` produced the same raw SHA-256 |

Observation is disabled unless `EU4_GFX_OPS_FRAMEBUFFER` is set; the unarmed
path performs no GL calls. Missing, truncated or corrupted payloads are
rejected by `_gl_reference_block` and by the comparator.

## Corresponding reconstructed Metal output

`python3 -m automation.live_gfx probe --checkpoint after-present --hold-ms 16000`:

| Field | Value |
| --- | --- |
| Run | `.local/m4/experiments/metal-present-20` |
| Logical capture | `metal-logical.bgra`, 1728×1117 BGRA8, origin `top_left` |
| Logical SHA-256 | `c668b054e87ea92eea7de6161adc7eeeeef07ea19bfcbde0ef02f069ba77fe14` |
| Live vertex | same 96 bytes (`f13dea22…`), effective count 4, shader `simple-lua-Simple/asset-maincode-v0` |
| GPU | `MTLCommandBufferStatusCompleted` (logical encode + staging blit) |
| Presented drawable | 3456×2234 BGRA8, SHA-256 `6921c242c66ad9929c7fd935178ae01bf2d4da0b25b6b57980660de3cf7999c3` |
| Presentation | `presentDrawable` completed; drawable readback after completion |
| Visible window | `window=21765`, `window-21765.png`, 3456×2234, SHA-256 `df23010e…` |
| Window-vs-drawable | mean |Δ| 1.311/255, max 20, 225 colour buckets (not blank) |
| Repeat present | `repeat-present.json`, `presented=true` |

The logical capture uses the same Metal encoder as the live route at the
engine's logical extent, so it is directly comparable with the original GL
surface. The physical drawable remains the presentation target.

## Comparison policy and result

Policy: [comparison-policy.json](comparison-policy.json), pinned by digest in
the evidence manifest; identities pin the executable, GLSL digest, candidate
shader identity, caps byte, and a required live vertex payload.

| Field | Value |
| --- | --- |
| Report | `.local/m4/experiments/compare-final2/report.json` |
| Pixels | 1,930,176 |
| Max channel error | **0** |
| Mean absolute error | **0.0** |
| Mismatched pixels | **0** |
| Alpha differences | 0 |
| Normalization | `vertical_flip` (declared lower-left → top-left decode only) |
| Previews | reference and candidate PNGs have identical SHA-256 (`6d87172e…`); difference image is black |

Thresholds were fixed at exact equality before qualification. The draw is an
opaque full-viewport quad over a cleared BGRA8 target with the same linear
sampler and no MSAA/dither, so integer equality is justified; the observed
result is exactly zero across independent launches. The presented 2× drawable,
reduced with a 2×2 box filter to the logical extent, has mean |Δ| 0.42/255
(max 46 at texture edges), a supporting link between the visible frame and the
reference; the authoritative test remains the exact 1:1 logical comparison.

## Presentation root cause and fix

The reconstructed frame reached the drawable but not the window for a long
sequence of runs. The cause was the presentation thread returning to the engine
immediately after `presentDrawable`: the engine then advanced into operations
outside the admitted route and the process ended before Core Animation committed
the drawable transaction. Sleeping instead blocked the run loop, which also
prevented the commit.

The fix keeps the presenting (main) thread in `NSRunLoop` for a bounded evidence
window after `presentDrawable`. The transaction commits, the frame appears in
the real window, and the engine does not advance into the crash. A delayed
diagnostic re-present (`repeat-present.json`) reattaches the layer and presents
the same target a second time. Independent `screencapture -l` of the game window
then returns the loading frame.

This also explains the earlier "uniform gray" captures: they were taken while
the presentation had not committed / the process had ended, not because the
Metal layer was misplaced.

## DSH autonomous execution and remediation

Native DSH goal `goal-1ccf024e-dbf4-4b0c-9fea-e150f9733330` owned the campaign.
The agent inspected, modified, built, launched, compared and corrected without
human code changes. Two genuine failures were found and fixed:

1. **UV-range mismatch.** The first Metal run used `V 0..0.75` against GL's
   `V 0..1.0`; 99 % of pixels differed. The comparator plus live vertex payloads
   traced it to the Metal caps path letting the engine persist a different
   POW2 decision. The backend now reproduces the measured caps byte, records the
   live vertex payload, and the policy requires identical inputs. The repaired
   run is exact.
2. **Presentation commit.** The drawable was completed and read back but the
   window stayed gray. A bounded main-runloop hold resolved the commit/lifetime
   ordering; the OS window capture then shows the frame.

The failing V-range pair is preserved as a negative control and rejected by the
policy on unmatched draw inputs.

## Evidence layout

Large artifacts stay under `.local/m4/experiments/` (gitignored). The committed
[evidence-manifest.json](evidence-manifest.json) pins every artifact's SHA-256
and is itself pinned by `evidence_manifest_sha256` in
[campaign.json](../../campaigns/eu4-m4/campaign.json). The checker refuses a
manifest whose digest does not match the campaign pin.

## Offline regressions

`tests.test_gfx_compare`, `tests.test_gl_reference`, `tests.test_live_gfx_probe`,
the Metal boot/HiDPI/simple-reference suites and the full repository suite
completed with exit 0. The M4 checker itself runs the comparator and the
graphics tests on every validation.

## Known limitations / deferred

- Only the Simple loading draw and its minimal presentation path are
  reconstructed; the title screen, map rendering and font/UI draws are out of
  scope.
- The engine sampler object is not decoded; the Simple sampler remains the
  `simple.shader` asset contract.
- The evidence window is a diagnostic main-runloop hold, not a normal frame
  loop. Production submission ordering remains future work.
- Host-wide lease cleanup after a crashed game is still manual; an inherited
  lease file descriptor in Paradox's CrashReporter can transiently block a
  launch.
